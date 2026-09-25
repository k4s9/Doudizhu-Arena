"""LLM call logging — wraps an AbstractLLMProvider with logging to file + DB.

Logs full prompt/response text to a local log file via Python logging.
Optionally inserts a row into llm_call_logs table if a repository is provided.
"""

from __future__ import annotations

import logging
import time

from .base import AbstractLLMProvider, LLMError, LLMUsage

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
        import asyncio
        from ..evaluation.budget import EvidenceError
        phase = self._phase or self._detect_phase(system_prompt)
        reservation = self.budget.reserve(system_prompt, user_prompt) if self.budget else None
        start = time.monotonic()
        raw, error, status = None, None, "error"
        self.last_usage = None
        self.last_thinking = None
        completed = False

        def persist(call_raw, call_error, call_status, usage):
            nonlocal completed
            if completed:
                return
            call_id = None
            if self._repo is not None:
                try:
                    with self._repo.atomic():
                        call_id = self._repo.add_llm_call_log(
                            player_id=self._agent_id, phase=phase, provider=self.provider_name, model=self.model,
                            success=call_status == 'returned', run_id=self._run_id or None, variant_id=self._variant_id or None,
                            match_id=self._match_id or None, decision_id=self._decision_id or None,
                            attempt=self._attempt, table_hand_id=self._table_hand_id or None,
                            response_model=getattr(self._inner, 'last_response_model', None),
                            system_fingerprint=getattr(self._inner, 'last_system_fingerprint', None),
                            prompt_tokens=usage.prompt_tokens if usage else None,
                            completion_tokens=usage.completion_tokens if usage else None,
                            total_tokens=usage.total_tokens if usage else None,
                            latency_ms=int((time.monotonic()-start)*1000), error_message=call_error)
                        self._repo.conn.execute('INSERT INTO call_evidence(call_id,system_prompt,user_prompt,raw_output,provider_status) VALUES(?,?,?,?,?)',
                            (call_id, system_prompt, user_prompt, call_raw, call_status))
                except Exception as exc:
                    raise EvidenceError('provider call evidence could not be persisted') from exc
            completed = True
            if reservation:
                self.budget.settle(reservation, call_id, usage)

        def expire():
            persist(None, 'cancellation deadline exceeded; late result discarded', 'cancelled', None)

        if self.execution_scope:
            self.execution_scope.check()
            self.execution_scope.pending.add(expire)
        try:
            raw = await self._inner.generate(user_prompt, system_prompt)
            if self.execution_scope:
                self.execution_scope.check_result()
            status = "returned"  # empty text is returned output, subsequently invalidated
            return raw
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
