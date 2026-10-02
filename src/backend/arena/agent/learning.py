"""Bounded reflection calls isolated from the live agent's mutable state."""

import asyncio
import copy
import logging
import math

from .llm_agent import LLMAgent
from ..evaluation.execution import ExecutionScope
from ..llm.logging import LoggingLLMProvider

logger = logging.getLogger(__name__)


async def run_learning(agent, phase, ctx, timeout_seconds):
    """Late results cannot update live memory or append database evidence."""
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("learning timeout must be finite and positive")
    shadow = copy.copy(agent)
    if hasattr(agent, "_memory"):
        shadow._memory = copy.deepcopy(agent.memory)
    else:
        shadow.memory = copy.deepcopy(agent.memory)
    scope = ExecutionScope()
    if isinstance(agent, LLMAgent):
        shadow._decision_context = dict(agent._decision_context)
        shadow._repo = scope.guard(agent._repo) if agent._repo else None
        shadow._provider = copy.copy(agent._provider)
        if isinstance(shadow._provider, LoggingLLMProvider):
            provider = shadow._provider
            provider._inner = copy.copy(provider._inner)
            provider._repo = scope.guard(provider._repo) if provider._repo else None
            provider.execution_scope = scope
    task = asyncio.create_task(getattr(shadow, phase)(ctx))

    def consume_result(done):
        if not done.cancelled():
            done.exception()

    try:
        done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
        if not done:
            raise TimeoutError(f"{phase} deadline exceeded")
        return task.result()
    finally:
        try:
            scope.revoke()
        finally:
            if not task.done():
                task.cancel()
                task.add_done_callback(consume_result)


def record_learning_failure(repo, failures, *, player_id, match_id, phase, stage,
                            error, table_hand_id=None):
    failure = {"player_id": player_id, "match_id": match_id, "phase": phase,
               "stage": stage, "error_type": type(error).__name__,
               "table_hand_id": table_hand_id}
    failures.append(failure)
    logger.warning("Agent learning failed: %s", failure)
    if repo:
        try:
            repo.record_memory_failure(player_id, match_id, phase, stage, error,
                                       table_hand_id=table_hand_id)
        except Exception as storage_error:
            logger.error("Could not save learning failure: %s", type(storage_error).__name__)
