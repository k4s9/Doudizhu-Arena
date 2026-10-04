"""LLM call logging — wraps an AbstractLLMProvider with logging to file + DB.

Logs full prompt/response text to a local log file via Python logging.
Optionally inserts a row into llm_call_logs table if a repository is provided.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict

from .base import AbstractLLMProvider, LLMError, LLMResponse, LLMUsage

# Maximum characters per prompt section in the standard (INFO-level) log entry.
_PROMPT_TRUNCATION = 2000

logger = logging.getLogger("llm_calls")
raw_logger = logging.getLogger("raw_prompts")


def log_raw_prompt(
    phase: str,
    system_prompt: str,
    user_prompt: str,
    *,
    agent_id: str = "",
) -> None:
    """Write the full, untruncated system and user prompts to raw_prompts log.

    This is always available but only emits output when the raw_prompts
    logger has a handler configured at DEBUG level or below.

    Args:
        phase: The detected phase name (bidding, playing, reflection, summary).
        system_prompt: Full system prompt text.
        user_prompt: Full user prompt text.
        agent_id: Optional agent identifier.
    """
    if not raw_logger.isEnabledFor(logging.DEBUG):
        return
    entry = (
        f"\n{'='*60}\n"
        f"RAW PROMPT | agent={agent_id} | phase={phase}\n"
        f"--- SYSTEM PROMPT ({len(system_prompt)} chars) ---\n"
        f"{system_prompt}\n"
        f"--- USER PROMPT ({len(user_prompt)} chars) ---\n"
        f"{user_prompt}\n"
        f"{'='*60}\n"
    )
    raw_logger.debug(entry)


class LoggingLLMProvider(AbstractLLMProvider):
    """Wrapper around an LLM provider that logs all calls.

    Usage:
        provider = LoggingLLMProvider(
            claude_provider,
            repo=db_repo,
            agent_id="agent-1",
        )
        text = await provider.generate(user_prompt, system_prompt)
    """

    def __init__(
        self,
        inner: AbstractLLMProvider,
        *,
        agent_id: str = "",
        repo: object | None = None,
        table_hand_id: str = "",
        budget=None,
        execution_scope=None,
    ) -> None:
        self.budget = budget
        self.execution_scope = execution_scope
        self._inner = inner
        self._agent_id = agent_id
        self._repo = repo
        self._table_hand_id = table_hand_id
        self._run_id = ""
        self._variant_id = ""
        self._match_id = ""
        self._decision_id = ""
        self._attempt: int | None = None
        self._phase = ""
        self.last_usage: LLMUsage | None = None
        self.last_thinking: str | None = None

    @property
    def provider_name(self) -> str:
        return self._inner.provider_name

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def supports_tools(self) -> bool:
        return self._inner.supports_tools

    def set_observability_context(self, repo, **context) -> None:
        """Rebind the evidence sink and discard identifiers from the previous hand.

        Budget and execution guards belong to the surrounding task and survive
        hand changes. The supplied repository may itself be guarded.
        """
        self._repo = repo
        for name in ("agent_id", "table_hand_id", "run_id", "variant_id", "match_id"):
            setattr(self, f"_{name}", context.get(name, ""))
        self._decision_id = ""
        self._attempt = None
        self._phase = ""
        self.last_usage = None
        self.last_thinking = None

    def set_context(
        self, *, agent_id: str | None = None, table_hand_id: str | None = None,
        run_id: str | None = None, variant_id: str | None = None,
        match_id: str | None = None, decision_id: str | None = None,
        attempt: int | None = None, phase: str | None = None, **context,
    ) -> None:
        """Update logging context between calls."""
        if agent_id is not None:
            self._agent_id = agent_id
        if table_hand_id is not None:
            self._table_hand_id = table_hand_id
        if run_id is not None:
            self._run_id = run_id
        if variant_id is not None:
            self._variant_id = variant_id
        if match_id is not None:
            self._match_id = match_id
        if decision_id is not None:
            self._decision_id = decision_id
        if attempt is not None:
            self._attempt = attempt
        if phase is not None:
            self._phase = phase

    async def generate(
        self,
        user_prompt: str,
        system_prompt: str = "",
    ) -> str:
        return await self._generate_logged(
            user_prompt, system_prompt,
            lambda: self._inner.generate(user_prompt, system_prompt),
        )

    async def generate_turn(
        self,
        messages: list[dict],
        system_prompt: str = "",
        tools: list[dict] | None = None,
        *,
        allow_tools: bool = True,
    ) -> LLMResponse:
        # Count and preserve the complete conversation and schemas on every
        # physical call, including assistant requests and tool result text.
        user_prompt = json.dumps(
            {"messages": messages, "tools": tools, "allow_tools": allow_tools},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        return await self._generate_logged(
            user_prompt, system_prompt,
            lambda: self._inner.generate_turn(
                messages, system_prompt, tools, allow_tools=allow_tools,
            ),
            encode_response=lambda response: json.dumps(
                asdict(response), ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ),
        )

    async def _generate_logged(
        self, user_prompt, system_prompt, invoke,
        encode_response=lambda response: response,
    ):
        import asyncio
        from ..evaluation.budget import BudgetExceeded, EvidenceError
        phase = self._phase or self._detect_phase(system_prompt)
        if self.execution_scope:
            self.execution_scope.check()
        start = time.monotonic()
        raw, error, status = None, None, "error"
        self.last_usage = None
        self.last_thinking = None
        completed = False
        reservation = call_id = None

        # Persist a dispatch intent and its budget together before the first
        # await. A process may die after dispatch, so a missing response must
        # remain an identifiable call with unknown usage, not an orphan reserve.
        if self._repo is not None:
            try:
                with self._repo.atomic():
                    reservation = self.budget.reserve(system_prompt, user_prompt) if self.budget else None
                    call_id = self._repo.add_llm_call_log(
                        player_id=self._agent_id, phase=phase, provider=self.provider_name, model=self.model,
                        success=False, run_id=self._run_id or None, variant_id=self._variant_id or None,
                        match_id=self._match_id or None, decision_id=self._decision_id or None,
                        attempt=self._attempt, table_hand_id=self._table_hand_id or None,
                        error_message='provider call dispatched; outcome pending',
                    )
                    self._repo.conn.execute(
                        'INSERT INTO call_evidence(call_id,system_prompt,user_prompt,raw_output,provider_status) VALUES(?,?,?,NULL,?)',
                        (call_id, system_prompt, user_prompt, 'pending'),
                    )
                    if reservation:
                        self.budget.bind_call(reservation, call_id)
            except BudgetExceeded:
                raise
            except Exception as exc:
                raise EvidenceError('provider call start could not be persisted') from exc
        elif self.budget:
            reservation = self.budget.reserve(system_prompt, user_prompt)

        def persist(call_raw, call_error, call_status, usage):
            nonlocal completed
            if completed:
                return
            budget_error = None
            if self._repo is not None:
                try:
                    with self._repo.atomic():
                        self._repo.conn.execute(
                            '''UPDATE llm_call_logs SET success=?,response_model=?,system_fingerprint=?,
                            prompt_tokens=?,completion_tokens=?,total_tokens=?,latency_ms=?,error_message=? WHERE id=?''',
                            (int(call_status == 'returned'), getattr(self._inner, 'last_response_model', None),
                             getattr(self._inner, 'last_system_fingerprint', None),
                             usage.prompt_tokens if usage else None, usage.completion_tokens if usage else None,
                             usage.total_tokens if usage else None, int((time.monotonic()-start)*1000), call_error, call_id),
                        )
                        self._repo.conn.execute('UPDATE call_evidence SET raw_output=?,provider_status=? WHERE call_id=?',
                                                (call_raw, call_status, call_id))
                        if reservation:
                            try:
                                self.budget.settle(reservation, call_id, usage)
                            except BudgetExceeded as exc:
                                # Actual over-limit usage is evidence too; commit
                                # it before propagating the stop-run signal.
                                budget_error = exc
                except Exception as exc:
                    raise EvidenceError('provider call evidence could not be persisted') from exc
            completed = True
            if reservation and self._repo is None:
                self.budget.settle(reservation, call_id, usage)
            if budget_error:
                raise budget_error

        def expire():
            persist(None, 'cancellation deadline exceeded; late result discarded', 'cancelled', None)

        if self.execution_scope:
            self.execution_scope.check()
            self.execution_scope.pending.add(expire)
        try:
            response = await invoke()
            if self.execution_scope:
                self.execution_scope.check_result()
            raw = encode_response(response)
            status = "returned"  # empty text is returned output, subsequently invalidated
            return response
        except asyncio.CancelledError:
            status, error = "cancelled", "call cancelled or deadline exceeded"
            raise
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            self.last_usage = getattr(self._inner, "last_usage", None)
            self.last_thinking = getattr(self._inner, "last_thinking", None)
            try:
                persist(raw, error, status, self.last_usage)
            finally:
                if self.execution_scope:
                    self.execution_scope.pending.discard(expire)

    @staticmethod
    def _detect_phase(system_prompt: str) -> str:
        if "叫分规则" in system_prompt:
            return "bidding"
        elif "出牌规则" in system_prompt:
            return "playing"
        elif "复盘" in system_prompt:
            return "reflection"
        elif "赛后总结" in system_prompt:
            return "summary"
        return "unknown"
