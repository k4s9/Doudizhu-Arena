"""Native tool contracts through SDK MockTransport and the real budget ledger."""

import asyncio
import copy
import json
import socket

import anthropic
import httpx
import openai
import pytest

from arena.db.repository import DatabaseRepository
from arena.evaluation.budget import BudgetExceeded, BudgetLedger
from arena.llm.base import AbstractLLMProvider, LLMError, LLMResponse, LLMUsage, ToolCall
from arena.llm.claude import ClaudeProvider
from arena.llm.logging import LoggingLLMProvider
from arena.llm.openai import OpenAIProvider


TOOLS = [{
    "name": "compare_hand_plans",
    "description": "Compare selected legal plays without reading opponents' hands.",
    "parameters": {
        "type": "object", "properties": {"preserve_bombs": {"type": "boolean"}},
        "additionalProperties": False,
    },
}]
FINAL = '{"action":{"type":"pass"}}'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Tool provider tests must not open network connections")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


def response_body(provider, *, tool_call=True, usage=True):
    if provider == "openai":
        message = {"role": "assistant", "content": "分析" if tool_call else FINAL}
        if tool_call:
            message["tool_calls"] = [{
                "id": "call-1", "type": "function", "function": {
                    "name": "compare_hand_plans", "arguments": '{ "preserve_bombs": true }',
                },
            }]
        body = {
            "id": "offline", "object": "chat.completion", "created": 0,
            "model": "resolved-model", "system_fingerprint": "offline-fingerprint",
            "choices": [{
                "index": 0, "finish_reason": "tool_calls" if tool_call else "stop",
                "message": message,
            }],
        }
        if usage:
            body["usage"] = {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}
    else:
        content = [{"type": "text", "text": "分析" if tool_call else FINAL}]
        if tool_call:
            content.append({
                "type": "tool_use", "id": "call-1", "name": "compare_hand_plans",
                "input": {"preserve_bombs": True},
            })
        body = {
            "id": "offline", "type": "message", "role": "assistant",
            "model": "resolved-model", "content": content,
            "stop_reason": "tool_use" if tool_call else "end_turn", "stop_sequence": None,
        }
        if usage:
            body["usage"] = {"input_tokens": 12, "output_tokens": 8}
    return body


def install_transport(monkeypatch, provider, handler):
    module, name = (openai, "AsyncOpenAI") if provider == "openai" else (anthropic, "AsyncAnthropic")
    original = getattr(module, name)

    def create(**kwargs):
        assert kwargs["max_retries"] == 0
        return original(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setattr(module, name, create)
    cls = OpenAIProvider if provider == "openai" else ClaudeProvider
    return cls(
        "requested-model", "offline-token", base_url="https://offline.invalid/v1",
        max_tokens=512, temperature=0.2, top_p=0.8,
    )


def assistant_message(response):
    return {
        "role": "assistant", "content": response.text,
        "tool_calls": [{
            "id": call.id, "type": "function",
            "function": {"name": call.name, "arguments": call.arguments},
        } for call in response.tool_calls],
    }


@pytest.mark.parametrize("provider", ["openai", "claude"])
def test_native_tool_round_trip_preserves_history_usage_and_final_turn(monkeypatch, provider):
    requests = []

    async def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response_body(provider, tool_call=len(requests) == 1))

    adapter = install_transport(monkeypatch, provider, handle)
    messages = [{"role": "user", "content": "请比较保留炸弹的方案"}]
    original = copy.deepcopy(messages)

    async def run():
        try:
            assert adapter.supports_tools
            result = await adapter.generate_turn(messages, "system", TOOLS)
            assert result.text == "分析"
            assert len(result.tool_calls) == 1
            call = result.tool_calls[0]
            assert (call.id, call.name) == ("call-1", "compare_hand_plans")
            assert json.loads(call.arguments) == {"preserve_bombs": True}
            if provider == "openai":
                assert call.arguments == '{ "preserve_bombs": true }'
            assert messages == original
            assert adapter.last_usage == LLMUsage(12, 8, 20)
            assert adapter.last_response_model == "resolved-model"
            messages.extend([
                assistant_message(result),
                {"role": "tool", "tool_call_id": call.id, "content": '{"groups":3}'},
            ])
            before_final = copy.deepcopy(messages)
            final = await adapter.generate_turn(messages, "system", TOOLS, allow_tools=False)
            assert final == LLMResponse(FINAL)
            assert messages == before_final
        finally:
            await adapter._client.close()

    asyncio.run(run())
    assert len(requests) == 2
    first, final = requests
    assert first["model"] == "requested-model"
    assert first["max_tokens"] == 512 and first["temperature"] == 0.2 and first["top_p"] == 0.8
    if provider == "openai":
        assert first["tools"] == [{"type": "function", "function": TOOLS[0]}]
        assert first["messages"][0] == {"role": "system", "content": "system"}
        assert final["messages"][1:] == messages
        assert final["tool_choice"] == "none"
        assert adapter.last_system_fingerprint == "offline-fingerprint"
    else:
        assert first["tools"] == [{
            "name": TOOLS[0]["name"], "description": TOOLS[0]["description"],
            "input_schema": TOOLS[0]["parameters"],
        }]
        assert first["system"] == "system"
        assert final["messages"][-2]["content"][-1] == {
            "type": "tool_use", "id": "call-1", "name": "compare_hand_plans",
            "input": {"preserve_bombs": True},
        }
        assert final["messages"][-1] == {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "call-1", "content": '{"groups":3}',
        }]}
        assert final["tool_choice"] == {"type": "none"}
    assert final["tools"] == first["tools"]


@pytest.mark.parametrize("provider", ["openai", "claude"])
def test_tool_gateway_failure_has_no_retries_or_previous_usage(monkeypatch, provider):
    requests = []

    async def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json=response_body(provider))
        return httpx.Response(500, json={"error": {"type": "api_error", "message": "offline failure"}})

    adapter = install_transport(monkeypatch, provider, handle)

    async def run():
        try:
            await adapter.generate_turn([{"role": "user", "content": "first"}], tools=TOOLS)
            assert adapter.last_usage is not None
            with pytest.raises(LLMError):
                await adapter.generate_turn([{"role": "user", "content": "second"}], tools=TOOLS)
            assert adapter.last_usage is None and adapter.last_response_model is None
        finally:
            await adapter._client.close()

    asyncio.run(run())
    assert len(requests) == 2


@pytest.mark.parametrize("provider", ["openai", "claude"])
def test_tool_response_without_usage_remains_unknown(monkeypatch, provider):
    async def handle(request):
        return httpx.Response(200, json=response_body(provider, usage=False))

    adapter = install_transport(monkeypatch, provider, handle)

    async def run():
        try:
            response = await adapter.generate_turn([{"role": "user", "content": "test"}], tools=TOOLS)
            assert response.tool_calls
            assert adapter.last_usage is None
        finally:
            await adapter._client.close()

    asyncio.run(run())


def test_openai_preserves_malformed_argument_json_for_tool_error(monkeypatch):
    async def handle(request):
        body = response_body("openai")
        body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{bad-json"
        return httpx.Response(200, json=body)

    adapter = install_transport(monkeypatch, "openai", handle)

    async def run():
        try:
            result = await adapter.generate_turn([{"role": "user", "content": "test"}], tools=TOOLS)
            assert result.tool_calls[0].arguments == "{bad-json"
            assert adapter.last_usage == LLMUsage(12, 8, 20)
        finally:
            await adapter._client.close()

    asyncio.run(run())


def test_claude_groups_multiple_tool_results_into_one_user_message(monkeypatch):
    requests = []

    async def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response_body("claude", tool_call=False))

    adapter = install_transport(monkeypatch, "claude", handle)
    response = LLMResponse("", (
        ToolCall("one", "compare_hand_plans", "{}"),
        ToolCall("two", "compare_hand_plans", '{"preserve_bombs":true}'),
    ))
    messages = [
        {"role": "user", "content": "compare"}, assistant_message(response),
        {"role": "tool", "tool_call_id": "one", "content": "first"},
        {"role": "tool", "tool_call_id": "two", "content": "second"},
    ]

    async def run():
        try:
            await adapter.generate_turn(messages, tools=TOOLS, allow_tools=False)
        finally:
            await adapter._client.close()

    asyncio.run(run())
    converted = requests[0]["messages"]
    assert len(converted) == 3
    assert [block["tool_use_id"] for block in converted[-1]["content"]] == ["one", "two"]


@pytest.mark.parametrize("invalid_input", [[], None])
def test_claude_rejects_nonobject_tool_input_without_poisoning_next_turn(monkeypatch, invalid_input):
    requests = []

    async def handle(request):
        requests.append(json.loads(request.content))
        body = response_body("claude", tool_call=len(requests) == 1)
        if len(requests) == 1:
            body["content"][-1]["input"] = invalid_input
        return httpx.Response(200, json=body)

    adapter = install_transport(monkeypatch, "claude", handle)
    messages = [{"role": "user", "content": "compare"}]

    async def run():
        try:
            with pytest.raises(LLMError, match="JSON object"):
                await adapter.generate_turn(messages, tools=TOOLS)
            assert adapter.last_usage == LLMUsage(12, 8, 20)
            assert adapter.last_response_model == "resolved-model"
            messages.append({"role": "user", "content": "Please return a valid action."})
            result = await adapter.generate_turn(messages, tools=TOOLS, allow_tools=False)
            assert result == LLMResponse(FINAL)
            assert adapter.last_usage == LLMUsage(12, 8, 20)
        finally:
            await adapter._client.close()

    asyncio.run(run())
    assert len(requests) == 2
    assert all(message["role"] == "user" for message in requests[-1]["messages"])


class TextOnlyProvider(AbstractLLMProvider):
    provider_name = "text-only"
    model = "offline"

    async def generate(self, user_prompt, system_prompt=""):
        return "legacy text"


def test_legacy_provider_remains_instantiable_and_tool_capability_is_explicit():
    provider = TextOnlyProvider()
    logging_provider = LoggingLLMProvider(provider)
    assert not provider.supports_tools and not logging_provider.supports_tools
    assert asyncio.run(logging_provider.generate("user", "system")) == "legacy text"
    with pytest.raises(LLMError, match="does not support"):
        asyncio.run(provider.generate_turn([]))


@pytest.fixture
def repo(tmp_path):
    repository = DatabaseRepository(str(tmp_path / "tools.db"))
    repository.init()
    yield repository
    repository.close()


class TurnsProvider(TextOnlyProvider):
    supports_tools = True

    def __init__(self):
        self.calls = []
        self.last_usage = None
        self.last_response_model = "resolved-offline"
        self.last_system_fingerprint = "offline-fingerprint"

    async def generate_turn(self, messages, system_prompt="", tools=None, *, allow_tools=True):
        self.calls.append((messages, tools, allow_tools))
        self.last_usage = LLMUsage(20, 10, 30)
        if allow_tools:
            return LLMResponse("", (ToolCall("call-1", "compare_hand_plans", "{}"),))
        return LLMResponse(FINAL)


def ledger(repo, *, input_limit=10000):
    return BudgetLedger(
        repo, "offline-tools", 1, 3, input_limit, 512,
        {"input_per_million": 1, "output_per_million": 1},
    )


def test_logging_records_and_accounts_for_every_physical_tool_turn(repo):
    inner = TurnsProvider()
    provider = LoggingLLMProvider(inner, repo=repo, budget=ledger(repo))
    provider.set_context(decision_id="decision-1", attempt=1, phase="playing")
    messages = [{"role": "user", "content": "compare"}]

    async def run():
        response = await provider.generate_turn(messages, "system", TOOLS)
        followup = [*messages, assistant_message(response), {
            "role": "tool", "tool_call_id": "call-1", "content": "tool-result-sentinel",
        }]
        provider.set_context(attempt=2)
        assert await provider.generate_turn(followup, "system", TOOLS, allow_tools=False) == LLMResponse(FINAL)

    asyncio.run(run())
    assert provider.supports_tools and provider.last_usage == LLMUsage(20, 10, 30)
    calls = repo.conn.execute("SELECT * FROM llm_call_logs ORDER BY attempt").fetchall()
    evidence = repo.conn.execute(
        "SELECT e.* FROM call_evidence e JOIN llm_call_logs c ON c.id=e.call_id ORDER BY c.attempt"
    ).fetchall()
    budget = repo.conn.execute("SELECT * FROM budget_ledger").fetchall()
    assert len(calls) == len(evidence) == len(budget) == 2
    assert [call["attempt"] for call in calls] == [1, 2]
    assert {call["decision_id"] for call in calls} == {"decision-1"}
    assert {call["response_model"] for call in calls} == {"resolved-offline"}
    assert {call["total_tokens"] for call in calls} == {30}
    assert {row["status"] for row in budget} == {"settled"}
    assert {row["call_id"] for row in budget} == {call["id"] for call in calls}
    assert json.loads(evidence[0]["raw_output"])["tool_calls"][0]["id"] == "call-1"
    final_input = json.loads(evidence[1]["user_prompt"])
    assert final_input["tools"] == TOOLS and final_input["allow_tools"] is False
    assert final_input["messages"][-1]["content"] == "tool-result-sentinel"
    assert json.loads(evidence[1]["raw_output"])["text"] == FINAL


def test_logging_input_budget_includes_tool_results_and_schemas(repo):
    inner = TurnsProvider()
    provider = LoggingLLMProvider(inner, repo=repo, budget=ledger(repo, input_limit=2000))
    messages = [{"role": "user", "content": "test"}, {
        "role": "tool", "tool_call_id": "call-1", "content": "x" * 2000,
    }]
    with pytest.raises(BudgetExceeded, match="input allowance"):
        asyncio.run(provider.generate_turn(messages, tools=TOOLS))
    assert inner.calls == []
    assert not repo.conn.execute("SELECT * FROM budget_ledger").fetchall()


def test_logging_cancelled_tool_turn_keeps_unknown_cost_and_evidence(repo):
    class Cancelled(TurnsProvider):
        async def generate_turn(self, *args, **kwargs):
            raise asyncio.CancelledError()

    provider = LoggingLLMProvider(Cancelled(), repo=repo, budget=ledger(repo))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(provider.generate_turn([{"role": "user", "content": "test"}], tools=TOOLS))
    row = repo.conn.execute("SELECT provider_status,raw_output FROM call_evidence").fetchone()
    assert tuple(row) == ("cancelled", None)
    budget = repo.conn.execute("SELECT actual_usd,status,call_id FROM budget_ledger").fetchone()
    assert budget["actual_usd"] is None and budget["status"] == "unknown"
    assert budget["call_id"]
