"""Native tool-use turns inside the existing decision time/call allowance."""
from __future__ import annotations

import asyncio
import time
import uuid

from ..llm.base import LLMError
from .parser import ParseError, parse_play_response
from .tools.registry import (
    MAX_ARGUMENT_CHARS, MAX_TOOL_CALLS, TOOL_GUIDANCE, TOOL_SCHEMAS, ToolRegistry, canonical, digest,
)


async def decide_with_tools(agent, ctx, system: str, user: str):
    decision_id = getattr(agent, "_active_decision_id", None) or uuid.uuid4().hex
    agent._active_decision_id = None
    agent._last_reasoning = ""
    started = time.monotonic()
    registry = ToolRegistry(ctx)
    system += TOOL_GUIDANCE
    messages = [{"role": "user", "content": user}]
    maximum_calls = agent._retry_limit + 1
    retries = 0
    attempts = 0
    seen_ids = set()

    def metadata(resolution, reason=None):
        agent._last_decision_meta = {
            "decision_id": decision_id, "retry_count": retries,
            "model_call_count": attempts, "tool_call_count": min(registry.calls, MAX_TOOL_CALLS),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "resolution": resolution, "reason": reason,
        }

    try:
        for attempt in range(1, maximum_calls + 1):
            attempts = attempt
            final = attempt == maximum_calls
            allow_tools = not final and registry.calls < MAX_TOOL_CALLS
            agent._set_provider_call_context("playing", decision_id, attempt)
            response = None
            error = None
            try:
                turn = await agent._provider.generate_turn(
                    messages, system, TOOL_SCHEMAS, allow_tools=allow_tools,
                )
                if turn.tool_calls:
                    calls = turn.tool_calls
                    # Validate before storing/replaying a malformed assistant turn.
                    if (not allow_tools or len(calls) > MAX_TOOL_CALLS - registry.calls
                        or any(not isinstance(call.id, str) or not 1 <= len(call.id) <= 128
                               or call.id in seen_ids or not isinstance(call.name, str)
                               or not 1 <= len(call.name) <= 64
                               or not isinstance(call.arguments, str)
                               or len(call.arguments) > MAX_ARGUMENT_CHARS for call in calls)
                        or len({call.id for call in calls}) != len(calls)):
                        raise LLMError("invalid tool-call IDs or tool call limit exceeded")
                    messages.append({
                        "role": "assistant", "content": turn.text or None,
                        "tool_calls": [{"id": call.id, "type": "function", "function": {
                            "name": call.name, "arguments": call.arguments}} for call in calls],
                    })
                    for call in calls:
                        seen_ids.add(call.id)
                        # The worker has no DB/provider handle. Cancellation can only
                        # leave bounded local computation, never a late write/action.
                        result = await asyncio.to_thread(registry.execute, call.name, call.arguments)
                        encoded = canonical(result)
                        if agent._repo is not None:
                            data = result.get("data", {})
                            stats = data.get("stats", data.get("search", {}))
                            agent._repo.add_agent_tool_call(
                                decision_id=decision_id, player_id=agent.agent_id,
                                model_attempt=attempt, call_index=registry.calls,
                                tool_call_id=call.id, tool_name=call.name,
                                tool_version=result["version"], state_hash=registry.state_hash,
                                arguments_sha256=digest(call.arguments), response_sha256=digest(encoded),
                                elapsed_ms=result["elapsed_ms"], output_chars=len(encoded),
                                status="ok" if result["ok"] else "error",
                                metadata_json=canonical({"cached": result["cached"], "search": stats,
                                    "status": data.get("status"),
                                    "exact": [plan.get("exact") for plan in data.get("plans", [])]}),
                            )
                        messages.append({"role": "tool", "tool_call_id": call.id, "content": encoded})
                    metadata("awaiting_model")
                    continue
                response = turn.text
                action, reasoning = parse_play_response(response, list(ctx.hand_cards), ctx.current_trick)
            except (ParseError, LLMError) as exc:
                error = exc
            elapsed = int((time.monotonic() - started) * 1000)
            agent._record_event(
                "playing", attempt, decision_id=decision_id, response=response, error=error,
                is_illegal=isinstance(error, ParseError), is_retry=retries > 0,
                is_fallback=bool(error and final),
                fallback_reason=agent._classify_error(error) if error and final else None,
                latency_ms=elapsed if error is None or final else None,
            )
            if error is None:
                agent._consecutive_failures = 0
                agent._last_reasoning = reasoning
                metadata("model_retry" if retries else "model_first")
                return action
            if final:
                agent._consecutive_failures += 1
                metadata("system_fallback", agent._classify_error(error))
                return []
            retries += 1
            feedback = agent._append_retry_feedback("", str(error), retries) if agent._rule_feedback else (
                "请重新独立生成一个完整 JSON 回复。")
            messages.append({"role": "user", "content": feedback})
    except asyncio.CancelledError:
        metadata("cancelled")
        raise
    raise AssertionError("unreachable")
