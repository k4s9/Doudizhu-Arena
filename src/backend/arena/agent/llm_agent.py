"""LLM Agent — wraps an LLM provider with prompt building, parsing, and retry logic.

Implements the full Agent protocol including reflect() and summarize().
"""

from __future__ import annotations

import logging

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
    ) -> None:
        self._agent_id = agent_id
        self._seat = seat
        self._provider = provider
        self._system_prompt_override = system_prompt_override
        self._memory = MemoryManager.with_long_term(agent_id, long_term_memory)
        self._consecutive_failures = 0
        self._seat_teams: dict[str, str] = {}
        self._last_reasoning = ""

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

    async def decide_bid(self, ctx: AgentContext) -> int:
        """Decide a bid via LLM with retry on parse failure."""
        system, user = build_bidding_prompt(
            ctx, self._memory, self._agent_id, self._system_prompt_override
        )

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self._provider.generate(user, system)
                bid, reasoning = parse_bid_response(response, ctx.current_high_bid)
                self._last_reasoning = reasoning
                self._consecutive_failures = 0
                logger.info(
                    "Agent %s bidding: bid=%d reasoning=%s",
                    self._agent_id, bid, reasoning[:200],
                )
                return bid
            except ParseError as e:
                if attempt < MAX_RETRIES:
                    user = self._append_retry_feedback(user, str(e), attempt + 1)
                else:
                    self._consecutive_failures += 1
                    logger.warning(
                        "Agent %s bidding parse failed after %d retries: %s",
                        self._agent_id, MAX_RETRIES, e,
                    )
                    return 0
            except LLMProviderError as e:
                self._consecutive_failures += 1
                logger.error("Agent %s LLM error during bidding: %s", self._agent_id, e)
                if attempt < MAX_RETRIES:
                    continue
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

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self._provider.generate(user, system)
                cards, reasoning = parse_play_response(response, hand, ctx.current_trick)
                self._last_reasoning = reasoning
                self._consecutive_failures = 0
                logger.info(
                    "Agent %s playing: cards=%s reasoning=%s",
                    self._agent_id,
                    [str(c) for c in cards],
                    reasoning[:200],
                )
                return cards
            except ParseError as e:
                if attempt < MAX_RETRIES:
                    user = self._append_retry_feedback(user, str(e), attempt + 1)
                else:
                    self._consecutive_failures += 1
                    logger.warning(
                        "Agent %s play parse failed after %d retries: %s",
                        self._agent_id, MAX_RETRIES, e,
                    )
                    return []
            except LLMProviderError as e:
                self._consecutive_failures += 1
                logger.error("Agent %s LLM error during play: %s", self._agent_id, e)
                if attempt < MAX_RETRIES:
                    continue
                return []

        return []

    async def reflect(self, ctx: AgentContext) -> dict[str, str]:
        """Post-hand reflection via LLM."""
        system, user = build_reflection_prompt(
            ctx, self._memory, self._agent_id, self._system_prompt_override
        )

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self._provider.generate(user, system)
                result = parse_reflection_response(response)
                logger.info(
                    "Agent %s reflection: %s",
                    self._agent_id, result["reflection"][:200],
                )
                return result
            except ParseError as e:
                if attempt < MAX_RETRIES:
                    user = self._append_retry_feedback(user, str(e), attempt + 1)
                else:
                    logger.warning(
                        "Agent %s reflection parse failed: %s", self._agent_id, e
                    )
                    return {"reflection": "", "short_term_memory": ""}
            except LLMProviderError as e:
                logger.error("Agent %s LLM error during reflection: %s", self._agent_id, e)
                if attempt < MAX_RETRIES:
                    continue
                return {"reflection": "", "short_term_memory": ""}

        return {"reflection": "", "short_term_memory": ""}

    async def summarize(self, ctx: AgentContext) -> dict[str, str]:
        """Post-match summary via LLM."""
        system, user = build_summary_prompt(
            ctx, self._memory, self._agent_id, self._system_prompt_override
        )

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self._provider.generate(user, system)
                result = parse_summary_response(response)
                logger.info(
                    "Agent %s summary: %s",
                    self._agent_id, result["summary"][:200],
                )
                return result
            except ParseError as e:
                if attempt < MAX_RETRIES:
                    user = self._append_retry_feedback(user, str(e), attempt + 1)
                else:
                    logger.warning(
                        "Agent %s summary parse failed: %s", self._agent_id, e
                    )
                    return {"summary": "", "long_term_memory": ""}
            except LLMProviderError as e:
                logger.error("Agent %s LLM error during summary: %s", self._agent_id, e)
                if attempt < MAX_RETRIES:
                    continue
                return {"summary": "", "long_term_memory": ""}

        return {"summary": "", "long_term_memory": ""}

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _append_retry_feedback(original_user: str, error: str, attempt: int) -> str:
        """Append error feedback to the user prompt for a retry attempt."""
        feedback = (
            f"\n\n---\n## 第{attempt}次重试反馈\n"
            f"上次回复有误：{error}\n"
            f"请修正错误，重新输出完整的 JSON 回复。"
        )
        return original_user + feedback
