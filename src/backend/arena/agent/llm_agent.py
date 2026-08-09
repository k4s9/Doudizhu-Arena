"""LLM Agent — wraps an LLM provider with prompt building, parsing, and retry logic.

Implements the full Agent protocol including reflect() and summarize().
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any

from ..engine.card import Card
from ..engine.trick import Trick
from ..llm.base import AbstractLLMProvider, LLMError as LLMProviderError
from .base import Agent, AgentContext, AgentError
from .context import ContextBuilder
from .memory import MemoryManager
from .parser import (
    ParseError,
    parse_bid_response,
    parse_play_response,
    parse_reflection_response,
    parse_summary_response,
)
from .prompts.bidding import build_bidding_prompt
from .prompts.playing import build_playing_prompt
from .prompts.reflection import build_reflection_prompt
from .prompts.summary import build_summary_prompt

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


class LLMAgent:
    """An agent powered by an LLM provider.

    Builds prompts from templates, calls LLM, parses output, retries on failure.
    Tracks consecutive failures for the timeout manager.

    Usage:
        provider = create_provider("claude", "claude-opus-4-7", api_key)
        agent = LLMAgent("agent-1", provider, system_prompt_override="...")
        bid = await agent.decide_bid(ctx)
    """

    def __init__(
        self,
        agent_id: str,
        provider: AbstractLLMProvider,
        *,
        seat: str = "",
        system_prompt_override: str | None = None,
        long_term_memory: str = "",
        retry_limit: int = MAX_RETRIES,
        rule_feedback: bool = True,
    ) -> None:
        self._agent_id = agent_id
        self._seat = seat
        self._provider = provider
        self._system_prompt_override = system_prompt_override
        self._memory = MemoryManager.with_long_term(agent_id, long_term_memory)
        self._consecutive_failures = 0
        self._seat_teams: dict[str, str] = {}
        self._last_reasoning = ""
        self._retry_limit = retry_limit
        self._rule_feedback = rule_feedback
        self._repo: Any | None = None
        self._decision_context: dict[str, str] = {}
        self._last_decision_meta: dict[str, int] = {"retry_count": 0, "latency_ms": 0}

    # ── Agent Protocol ──────────────────────────────────────────────────────

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def seat(self) -> str:
        return self._seat

    @seat.setter
    def seat(self, value: str) -> None:
        self._seat = value

    @property
    def memory(self) -> MemoryManager:
        return self._memory

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def reset_failures(self) -> None:
        self._consecutive_failures = 0

    def get_last_reasoning(self) -> str:
        """Return the reasoning from the last LLM call. Used by TableRunner for WS thought_update."""
        return self._last_reasoning

    def set_seat_teams(self, seat_teams: dict[str, str]) -> None:
        """Provide team assignment info for context building."""
        self._seat_teams = dict(seat_teams)

    def set_observability_context(self, repo: Any | None, **context: str) -> None:
        """Attach the table-scoped sink for privacy-preserving decision events."""
        self._repo = repo
        self._decision_context.update({key: value for key, value in context.items() if value})
        provider = self._provider
        if hasattr(provider, "set_context"):
            provider.set_context(**self._decision_context)

    def get_last_decision_meta(self) -> dict[str, int]:
        return dict(self._last_decision_meta)

    def get_observability_context(self) -> dict[str, str]:
        return dict(self._decision_context)

    async def decide_bid(self, ctx: AgentContext) -> int:
        """Decide a bid via LLM with retry on parse failure."""
        system, user = build_bidding_prompt(
            ctx, self._memory, self._agent_id, self._system_prompt_override
        )

        started = time.monotonic()
        decision_id = uuid.uuid4().hex
        retries = 0
        for attempt in range(self._retry_limit + 1):
            try:
                self._set_provider_call_context("bidding", decision_id, attempt + 1)
                response = await self._provider.generate(user, system)
                bid, reasoning = parse_bid_response(response, ctx.current_high_bid)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                self._record_event(
                    "bidding", attempt + 1, decision_id=decision_id,
                    response=response, is_retry=attempt > 0,
                    latency_ms=elapsed_ms, final_action=json.dumps({"bid": bid}),
                )
                self._last_reasoning = reasoning
                self._last_decision_meta = {"retry_count": retries, "latency_ms": elapsed_ms}
                self._consecutive_failures = 0
                logger.info(
                    "Agent %s bidding: bid=%d reasoning=%s",
                    self._agent_id, bid, reasoning[:200],
                )
                return bid
            except ParseError as e:
                is_final = attempt >= self._retry_limit
                elapsed_ms = int((time.monotonic() - started) * 1000)
                self._record_event(
                    "bidding", attempt + 1, decision_id=decision_id, error=e,
                    is_illegal=True, is_retry=attempt > 0, is_fallback=is_final,
                    fallback_reason="parse_error" if is_final else None,
                    latency_ms=elapsed_ms if is_final else None,
                    final_action=json.dumps({"bid": 0}) if is_final else None,
                )
                if attempt < self._retry_limit:
                    retries += 1
                    if self._rule_feedback:
                        user = self._append_retry_feedback(user, str(e), attempt + 1)
                else:
                    self._consecutive_failures += 1
                    self._last_decision_meta = {"retry_count": retries, "latency_ms": elapsed_ms}
                    logger.warning(
                        "Agent %s bidding parse failed after %d retries: %s",
                        self._agent_id, self._retry_limit, e,
                    )
                    return 0
            except LLMProviderError as e:
                is_final = attempt >= self._retry_limit
                elapsed_ms = int((time.monotonic() - started) * 1000)
                self._record_event(
                    "bidding", attempt + 1, decision_id=decision_id, error=e,
                    is_retry=attempt > 0, is_fallback=is_final,
                    fallback_reason="provider_error" if is_final else None,
                    latency_ms=elapsed_ms if is_final else None,
                    final_action=json.dumps({"bid": 0}) if is_final else None,
                )
                self._consecutive_failures += 1
                logger.error("Agent %s LLM error during bidding: %s", self._agent_id, e)
                if attempt < self._retry_limit:
                    retries += 1
                    continue
                self._last_decision_meta = {"retry_count": retries, "latency_ms": elapsed_ms}
                return 0

        return 0  # unreachable, but safe

    async def decide_play(self, ctx: AgentContext) -> list[Card]:
        """Decide a play via LLM with retry on parse/validation failure."""
        system, user = build_playing_prompt(
            ctx, self._memory, self._agent_id,
            seat_teams=self._seat_teams,
            system_prompt_override=self._system_prompt_override,
        )

        hand = list(ctx.hand_cards)

        started = time.monotonic()
        decision_id = uuid.uuid4().hex
        retries = 0
        for attempt in range(self._retry_limit + 1):
            try:
                self._set_provider_call_context("playing", decision_id, attempt + 1)
                response = await self._provider.generate(user, system)
                cards, reasoning = parse_play_response(response, hand, ctx.current_trick)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                self._record_event(
                    "playing", attempt + 1, decision_id=decision_id,
                    response=response, is_retry=attempt > 0,
                    latency_ms=elapsed_ms,
                    final_action=json.dumps({"cards": [str(card) for card in cards]}, ensure_ascii=False),
                )
                self._last_reasoning = reasoning
                self._last_decision_meta = {"retry_count": retries, "latency_ms": elapsed_ms}
                self._consecutive_failures = 0
                logger.info(
                    "Agent %s playing: cards=%s reasoning=%s",
                    self._agent_id,
                    [str(c) for c in cards],
                    reasoning[:200],
                )
                return cards
            except ParseError as e:
                is_final = attempt >= self._retry_limit
                elapsed_ms = int((time.monotonic() - started) * 1000)
                self._record_event(
                    "playing", attempt + 1, decision_id=decision_id, error=e,
                    is_illegal=True, is_retry=attempt > 0, is_fallback=is_final,
                    fallback_reason="parse_error" if is_final else None,
                    latency_ms=elapsed_ms if is_final else None,
                    final_action=json.dumps({"cards": []}) if is_final else None,
                )
                if attempt < self._retry_limit:
                    retries += 1
                    if self._rule_feedback:
                        user = self._append_retry_feedback(user, str(e), attempt + 1)
                else:
                    self._consecutive_failures += 1
                    self._last_decision_meta = {"retry_count": retries, "latency_ms": elapsed_ms}
                    logger.warning(
                        "Agent %s play parse failed after %d retries: %s",
                        self._agent_id, self._retry_limit, e,
                    )
                    return []
            except LLMProviderError as e:
                is_final = attempt >= self._retry_limit
                elapsed_ms = int((time.monotonic() - started) * 1000)
                self._record_event(
                    "playing", attempt + 1, decision_id=decision_id, error=e,
                    is_retry=attempt > 0, is_fallback=is_final,
                    fallback_reason="provider_error" if is_final else None,
                    latency_ms=elapsed_ms if is_final else None,
                    final_action=json.dumps({"cards": []}) if is_final else None,
                )
                self._consecutive_failures += 1
                logger.error("Agent %s LLM error during play: %s", self._agent_id, e)
                if attempt < self._retry_limit:
                    retries += 1
                    continue
                self._last_decision_meta = {"retry_count": retries, "latency_ms": elapsed_ms}
                return []

        return []

    async def reflect(self, ctx: AgentContext) -> dict[str, str]:
        """Post-hand reflection via LLM."""
        system, user = build_reflection_prompt(
            ctx, self._memory, self._agent_id, self._system_prompt_override
        )

        started = time.monotonic()
        decision_id = uuid.uuid4().hex
        retries = 0
        for attempt in range(self._retry_limit + 1):
            try:
                self._set_provider_call_context("reflection", decision_id, attempt + 1)
                response = await self._provider.generate(user, system)
                result = parse_reflection_response(response)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                self._record_event(
                    "reflection", attempt + 1, decision_id=decision_id,
                    response=response, is_retry=attempt > 0, latency_ms=elapsed_ms,
                    final_action="reflection_saved",
                )
                self._last_decision_meta = {"retry_count": retries, "latency_ms": elapsed_ms}
                logger.info(
                    "Agent %s reflection: %s",
                    self._agent_id, result["reflection"][:200],
                )
                return result
            except ParseError as e:
                is_final = attempt >= self._retry_limit
                elapsed_ms = int((time.monotonic() - started) * 1000)
                self._record_event(
                    "reflection", attempt + 1, decision_id=decision_id, error=e,
                    is_illegal=True, is_retry=attempt > 0, is_fallback=is_final,
                    fallback_reason="parse_error" if is_final else None,
                    latency_ms=elapsed_ms if is_final else None,
                    final_action="empty_reflection" if is_final else None,
                )
                if attempt < self._retry_limit:
                    retries += 1
                    if self._rule_feedback:
                        user = self._append_retry_feedback(user, str(e), attempt + 1)
                else:
                    self._last_decision_meta = {"retry_count": retries, "latency_ms": elapsed_ms}
                    logger.warning(
                        "Agent %s reflection parse failed: %s", self._agent_id, e
                    )
                    return {"reflection": "", "short_term_memory": ""}
            except LLMProviderError as e:
                is_final = attempt >= self._retry_limit
                elapsed_ms = int((time.monotonic() - started) * 1000)
                self._record_event(
                    "reflection", attempt + 1, decision_id=decision_id, error=e,
                    is_retry=attempt > 0, is_fallback=is_final,
                    fallback_reason="provider_error" if is_final else None,
                    latency_ms=elapsed_ms if is_final else None,
                    final_action="empty_reflection" if is_final else None,
                )
                logger.error("Agent %s LLM error during reflection: %s", self._agent_id, e)
                if attempt < self._retry_limit:
                    retries += 1
                    continue
                self._last_decision_meta = {"retry_count": retries, "latency_ms": elapsed_ms}
                return {"reflection": "", "short_term_memory": ""}

        return {"reflection": "", "short_term_memory": ""}

    async def summarize(self, ctx: AgentContext) -> dict[str, str]:
        """Post-match summary via LLM."""
        system, user = build_summary_prompt(
            ctx, self._memory, self._agent_id, self._system_prompt_override
        )

        started = time.monotonic()
        decision_id = uuid.uuid4().hex
        retries = 0
        for attempt in range(self._retry_limit + 1):
            try:
                self._set_provider_call_context("summary", decision_id, attempt + 1)
                response = await self._provider.generate(user, system)
                result = parse_summary_response(response)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                self._record_event(
                    "summary", attempt + 1, decision_id=decision_id,
                    response=response, is_retry=attempt > 0, latency_ms=elapsed_ms,
                    final_action="summary_saved",
                )
                self._last_decision_meta = {"retry_count": retries, "latency_ms": elapsed_ms}
                logger.info(
                    "Agent %s summary: %s",
                    self._agent_id, result["summary"][:200],
                )
                return result
            except ParseError as e:
                is_final = attempt >= self._retry_limit
                elapsed_ms = int((time.monotonic() - started) * 1000)
                self._record_event(
                    "summary", attempt + 1, decision_id=decision_id, error=e,
                    is_illegal=True, is_retry=attempt > 0, is_fallback=is_final,
                    fallback_reason="parse_error" if is_final else None,
                    latency_ms=elapsed_ms if is_final else None,
                    final_action="empty_summary" if is_final else None,
                )
                if attempt < self._retry_limit:
                    retries += 1
                    if self._rule_feedback:
                        user = self._append_retry_feedback(user, str(e), attempt + 1)
                else:
                    self._last_decision_meta = {"retry_count": retries, "latency_ms": elapsed_ms}
                    logger.warning(
                        "Agent %s summary parse failed: %s", self._agent_id, e
                    )
                    return {"summary": "", "long_term_memory": ""}
            except LLMProviderError as e:
                is_final = attempt >= self._retry_limit
                elapsed_ms = int((time.monotonic() - started) * 1000)
                self._record_event(
                    "summary", attempt + 1, decision_id=decision_id, error=e,
                    is_retry=attempt > 0, is_fallback=is_final,
                    fallback_reason="provider_error" if is_final else None,
                    latency_ms=elapsed_ms if is_final else None,
                    final_action="empty_summary" if is_final else None,
                )
                logger.error("Agent %s LLM error during summary: %s", self._agent_id, e)
                if attempt < self._retry_limit:
                    retries += 1
                    continue
                self._last_decision_meta = {"retry_count": retries, "latency_ms": elapsed_ms}
                return {"summary": "", "long_term_memory": ""}

        return {"summary": "", "long_term_memory": ""}

    # ── helpers ─────────────────────────────────────────────────────────────

    def _record_event(
        self, phase: str, attempt: int, decision_id: str,
        response: str | None = None,
        error: Exception | None = None, **flags: Any,
    ) -> None:
        if self._repo is None:
            return
        try:
            self._repo.add_decision_event(
                phase=phase, attempt=attempt, decision_id=decision_id,
                player_id=self._agent_id, seat=self._seat or None,
                output_sha256=hashlib.sha256(response.encode()).hexdigest() if response is not None else None,
                error_code=self._classify_error(error) if error else None,
                **self._decision_context, **flags,
            )
        except Exception:
            logger.exception("Failed to persist decision event")

    def _set_provider_call_context(self, phase: str, decision_id: str, attempt: int) -> None:
        if hasattr(self._provider, "set_context"):
            self._provider.set_context(
                phase=phase, decision_id=decision_id, attempt=attempt,
                **self._decision_context,
            )

    @staticmethod
    def _classify_error(error: Exception) -> str:
        if isinstance(error, LLMProviderError):
            message = str(error).lower()
            if "empty response" in message:
                return "empty_response"
            return "provider_error"
        message = str(error)
        if "未找到 JSON" in message or "未正确闭合" in message or "JSON 解析错误" in message:
            return "json_format"
        if "缺少" in message or "必须是对象" in message or "必须是整数" in message or "必须是非空数组" in message:
            return "schema_error"
        if "不在你的手牌" in message:
            return "card_not_in_hand"
        if "无法识别牌型" in message or "合法牌型" in message:
            return "invalid_pattern"
        if "不能压过" in message:
            return "cannot_beat"
        if "叫分" in message or "'bid'" in message:
            return "invalid_bid"
        return "parse_error"

    @staticmethod
    def _append_retry_feedback(original_user: str, error: str, attempt: int) -> str:
        """Append error feedback to the user prompt for a retry attempt."""
        feedback = (
            f"\n\n---\n## 第{attempt}次重试反馈\n"
            f"上次回复有误：{error}\n"
            f"请修正错误，重新输出完整的 JSON 回复。"
        )
        return original_user + feedback
