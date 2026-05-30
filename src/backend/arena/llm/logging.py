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
    ) -> None:
        self._inner = inner
        self._agent_id = agent_id
        self._repo = repo
        self._table_hand_id = table_hand_id
        self.last_usage: LLMUsage | None = None
        self.last_thinking: str | None = None

    @property
    def provider_name(self) -> str:
        return self._inner.provider_name

    @property
    def model(self) -> str:
        return self._inner.model

    def set_context(self, *, agent_id: str = "", table_hand_id: str = "") -> None:
        """Update logging context between calls."""
        if agent_id:
            self._agent_id = agent_id
        if table_hand_id:
            self._table_hand_id = table_hand_id

    async def generate(
        self,
        user_prompt: str,
        system_prompt: str = "",
    ) -> str:
        phase = self._detect_phase(system_prompt)
        start = time.monotonic()
        success = False
        error_msg: str | None = None
        self.last_usage = None
        self.last_thinking = None

        try:
            text = await self._inner.generate(user_prompt, system_prompt)
            success = True
            # Capture usage from inner provider
            if hasattr(self._inner, 'last_usage'):
                self.last_usage = self._inner.last_usage
            # Capture thinking/reasoning content if available
            if hasattr(self._inner, 'last_thinking'):
                self.last_thinking = self._inner.last_thinking
            return text
        except (LLMError, Exception) as e:
            error_msg = str(e)
            raise
        finally:
            elapsed_ms = int((time.monotonic() - start) * 1000)

            # Build truncated versions for the standard log entry
            sys_trunc = system_prompt[:_PROMPT_TRUNCATION]
            usr_trunc = user_prompt[:_PROMPT_TRUNCATION]
            if len(system_prompt) > _PROMPT_TRUNCATION:
                sys_trunc += "..."
            if len(user_prompt) > _PROMPT_TRUNCATION:
                usr_trunc += "..."

            # Log to file (INFO: truncated prompts)
            log_entry = (
                f"\n{'='*60}\n"
                f"LLM Call | agent={self._agent_id} | phase={phase} | "
                f"success={success} | latency={elapsed_ms}ms\n"
                f"Provider: {self.provider_name}/{self.model}\n"
                f"Usage: {self.last_usage}\n"
                f"System ({len(system_prompt)} chars): {sys_trunc}\n"
                f"User ({len(user_prompt)} chars): {usr_trunc}\n"
            )
            if self.last_thinking:
                log_entry += f"Thinking: {self.last_thinking[:_PROMPT_TRUNCATION]}...\n" if len(self.last_thinking) > _PROMPT_TRUNCATION else f"Thinking: {self.last_thinking}\n"
            if error_msg:
                log_entry += f"ERROR: {error_msg}\n"
            logger.info(log_entry)

            # DEBUG: full untruncated prompts (only when DEBUG is enabled)
            if logger.isEnabledFor(logging.DEBUG):
                debug_entry = (
                    f"\n{'='*60}\n"
                    f"DEBUG FULL PROMPT | agent={self._agent_id} | phase={phase}\n"
                    f"--- SYSTEM ({len(system_prompt)} chars) ---\n"
                    f"{system_prompt}\n"
                    f"--- USER ({len(user_prompt)} chars) ---\n"
                    f"{user_prompt}\n"
                    f"--- END DEBUG FULL PROMPT ---\n"
                )
                logger.debug(debug_entry)

            # Always write raw prompts to the raw_prompts logger (only
            # emits when the raw_prompts logger is configured at DEBUG).
            log_raw_prompt(
                phase=phase,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                agent_id=self._agent_id,
            )

            # Log to DB
            if self._repo is not None:
                try:
                    self._repo.add_llm_call_log(
                        agent_id=self._agent_id,
                        phase=phase,
                        provider=self.provider_name,
                        model=self.model,
                        success=success,
                        table_hand_id=self._table_hand_id or None,
                        prompt_tokens=self.last_usage.prompt_tokens if self.last_usage else None,
                        completion_tokens=self.last_usage.completion_tokens if self.last_usage else None,
                        total_tokens=self.last_usage.total_tokens if self.last_usage else None,
                        latency_ms=elapsed_ms,
                        error_message=error_msg,
                    )
                except Exception:
                    pass  # DB logging should never crash the main flow

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
