"""Offline integration: real algorithms, native turns, evidence and boundaries."""
import asyncio
import copy
import json

import pytest
from fastapi import HTTPException

from arena.agent.base import AgentContext
from arena.agent.llm_agent import LLMAgent
from arena.agent.tools.registry import MAX_ARGUMENT_CHARS, ToolRegistry
from arena.api.routes.match import _match_config_from_dict
from arena.db.repository import DatabaseRepository
from arena.engine.card import Card
from arena.engine.deck import Deck
from arena.llm.base import AbstractLLMProvider, LLMResponse, LLMUsage, ToolCall
from arena.llm.logging import LoggingLLMProvider
from arena.tournament.match import MatchConfig, MatchRunner
from arena.tournament.seating import assign_seating


def context():
    deal = Deck("native-tools-test").deal()
    hand = list(deal.dealer_hand + deal.dizhu_cards)
    return AgentContext(
        seat="S", role="landlord", hand_cards=hand, hand_size=20,
        landlord="S", dizhu_cards=deal.dizhu_cards, active_players=["S", "E", "N"],
        player_hand_sizes={"S": 20, "E": 17, "N": 17, "W": 0},
    )


def arguments(ctx):
    return json.dumps({"actions": [{"cards": [str(ctx.hand_cards[0])]}]})


def final(ctx):
    return LLMResponse(json.dumps({"reasoning": "使用计算结果选择合法出牌",
        "action": {"type": "play", "cards": [str(ctx.hand_cards[0])]}}))


class ScriptedProvider(AbstractLLMProvider):
    provider_name = "scripted"
    model = "offline"
    supports_tools = True

    def __init__(self, responses):
        self.responses = iter(responses)
        self.turns = []
        self.text_calls = []
        self.last_usage = None

    async def generate(self, user_prompt, system_prompt=""):
        self.text_calls.append((user_prompt, system_prompt))
        return next(self.responses).text

    async def generate_turn(self, messages, system_prompt="", tools=None, *, allow_tools=True):
        self.turns.append(copy.deepcopy((messages, system_prompt, tools, allow_tools)))
        self.last_usage = LLMUsage(100, 20, 120)
        return next(self.responses)


def test_native_tool_then_final_action_and_persisted_evidence(tmp_path):
    ctx = context()
    raw = ScriptedProvider([LLMResponse(tool_calls=(
        ToolCall("c1", "compare_hand_plans", arguments(ctx)),)), final(ctx)])
    repo = DatabaseRepository(str(tmp_path / "tools.db"))
    repo.init()
    try:
        provider = LoggingLLMProvider(raw, repo=repo, agent_id="player")
        agent = LLMAgent("player", provider, enable_tools=True)
        agent.set_observability_context(repo)
        agent.begin_decision("decision-1")
        actual = asyncio.run(agent.decide_play(ctx))
        assert actual == [ctx.hand_cards[0]]
        result = json.loads(raw.turns[1][0][-1]["content"])
        assert result["ok"] and result["data"]["plans"][0]["valid"]
        assert raw.turns[1][0][-1]["role"] == "tool"
        assert raw.turns[1][0][-1]["tool_call_id"] == "c1"
        meta = agent.get_last_decision_meta()
        assert meta["model_call_count"] == 2 and meta["tool_call_count"] == 1
        assert meta["retry_count"] == 0 and meta["resolution"] == "model_first"
        row = dict(repo.conn.execute("SELECT * FROM agent_tool_calls").fetchone())
        assert row["decision_id"] == "decision-1" and row["tool_name"] == "compare_hand_plans"
        assert len(row["state_hash"]) == 64 and row["output_chars"] > 0
        assert repo.conn.execute("SELECT count(*) FROM llm_call_logs").fetchone()[0] == 2
        evidence = repo.conn.execute("SELECT user_prompt FROM call_evidence").fetchall()
        assert json.loads(evidence[1][0])["messages"][-1]["role"] == "tool"
    finally:
        repo.close()


@pytest.mark.parametrize("raw", ["{", "[]", '{"actions":[],"seed":"secret"}',
    '{"actions":[],"actions":[]}', "x" * (MAX_ARGUMENT_CHARS + 1)])
def test_invalid_tool_arguments_are_structured_errors(raw):
    result = ToolRegistry(context()).execute("compare_hand_plans", raw)
    assert result["ok"] is False and result["error"]["code"] == "invalid_tool_request"


def test_unknown_tool_error_can_be_followed_by_final_action():
    ctx = context()
    provider = ScriptedProvider([LLMResponse(tool_calls=(ToolCall("bad", "read_database", "{}"),)), final(ctx)])
    agent = LLMAgent("p", provider, enable_tools=True)
    assert asyncio.run(agent.decide_play(ctx)) == [ctx.hand_cards[0]]
    result = json.loads(provider.turns[1][0][-1]["content"])
    assert not result["ok"] and "unknown tool" in result["error"]["message"]


def test_two_tools_use_existing_model_allowance_and_disable_further_calls():
    ctx = context()
    provider = ScriptedProvider([
        LLMResponse(tool_calls=(ToolCall("one", "compare_hand_plans", arguments(ctx)),)),
        LLMResponse(tool_calls=(ToolCall("two", "analyze_threat", '{"event":"can_finish","target_seat":"E"}'),)),
        LLMResponse("not json"), final(ctx),
    ])
    agent = LLMAgent("p", provider, enable_tools=True, retry_limit=3)
    assert asyncio.run(agent.decide_play(ctx)) == [ctx.hand_cards[0]]
    assert [turn[3] for turn in provider.turns] == [True, True, False, False]
    assert agent.get_last_decision_meta()["retry_count"] == 1
    assert agent.get_last_decision_meta()["model_call_count"] == 4
    assert agent.get_last_decision_meta()["tool_call_count"] == 2


def test_provider_ignoring_disabled_tools_cannot_increase_budget():
    ctx = context()
    provider = ScriptedProvider([LLMResponse(tool_calls=(ToolCall("x", "compare_hand_plans", arguments(ctx)),))])
    agent = LLMAgent("p", provider, enable_tools=True, retry_limit=0)
    assert asyncio.run(agent.decide_play(ctx)) == []
    assert len(provider.turns) == 1 and provider.turns[0][3] is False
    assert agent.get_last_decision_meta()["tool_call_count"] == 0
    assert agent.get_last_decision_meta()["resolution"] == "system_fallback"


def test_duplicate_tool_ids_are_rejected_without_computation():
    ctx = context()
    call = ToolCall("same", "compare_hand_plans", arguments(ctx))
    provider = ScriptedProvider([LLMResponse(tool_calls=(call, call)), final(ctx)])
    agent = LLMAgent("p", provider, enable_tools=True)
    assert asyncio.run(agent.decide_play(ctx)) == [ctx.hand_cards[0]]
    assert agent.get_last_decision_meta()["tool_call_count"] == 0


def test_oversized_request_never_enters_replayed_conversation():
    ctx = context()
    oversized = "x" * (MAX_ARGUMENT_CHARS + 1)
    provider = ScriptedProvider([LLMResponse(tool_calls=(
        ToolCall("big", "compare_hand_plans", oversized),)), final(ctx)])
    agent = LLMAgent("p", provider, enable_tools=True)
    assert asyncio.run(agent.decide_play(ctx)) == [ctx.hand_cards[0]]
    assert oversized not in json.dumps(provider.turns[1][0])
    assert agent.get_last_decision_meta()["tool_call_count"] == 0


def test_registry_caches_and_binds_to_public_observation():
    ctx = context()
    registry = ToolRegistry(ctx)
    first = registry.execute("compare_hand_plans", arguments(ctx))
    second = registry.execute("compare_hand_plans", arguments(ctx))
    assert first["data"] == second["data"] and second["cached"]
    assert not registry.execute("compare_hand_plans", arguments(ctx))["ok"]
    hidden = copy.deepcopy(ctx)
    hidden.remaining_hands = {"E": (Card.from_string("♠3"),)}
    hidden.match_id = "other-table-secret"
    assert ToolRegistry(hidden).state_hash == registry.state_hash
    changed = copy.deepcopy(ctx)
    changed.hand_cards = changed.hand_cards[:-1]
    changed.hand_size -= 1
    assert ToolRegistry(changed).state_hash != registry.state_hash
    ctx.hand_cards.clear()
    assert len(registry._ctx.hand_cards) == 20


def test_opponent_suits_never_enter_snapshot_or_hash():
    a, b = context(), context()
    a.play_history = [{"seat": "E", "action_type": "play", "cards": ["♠3"]}]
    b.play_history = [{"seat": "E", "action_type": "play", "cards": ["♥3"]}]
    assert ToolRegistry(a).state_hash == ToolRegistry(b).state_hash
    assert ToolRegistry(a)._ctx.play_history[0]["cards"] == ["3"]


def test_tools_disabled_preserves_legacy_text_path():
    ctx = context()
    provider = ScriptedProvider([final(ctx)])
    agent = LLMAgent("p", provider)
    assert asyncio.run(agent.decide_play(ctx)) == [ctx.hand_cards[0]]
    assert not provider.turns and len(provider.text_calls) == 1


def test_match_config_switch_reaches_supplied_agents():
    ids = [f"p{i}" for i in range(8)]
    agents = {pid: LLMAgent(pid, ScriptedProvider([])) for pid in ids}
    seating = assign_seating(ids[:4], ids[4:])
    MatchRunner(_match_config_from_dict({"enable_tools": True}), seating, agents)
    assert all(agent._tools_enabled for agent in agents.values())
    MatchRunner(MatchConfig(), seating, agents)
    assert all(not agent._tools_enabled for agent in agents.values())
    with pytest.raises(HTTPException):
        _match_config_from_dict({"enable_tools": "true"})


def test_cancelled_tool_cannot_write_late_evidence(tmp_path, monkeypatch):
    import threading
    entered, release = threading.Event(), threading.Event()
    original = ToolRegistry.execute

    def delayed(self, *args):
        entered.set()
        release.wait(timeout=1)
        return original(self, *args)

    monkeypatch.setattr(ToolRegistry, "execute", delayed)
    ctx = context()
    provider = ScriptedProvider([LLMResponse(tool_calls=(ToolCall("x", "compare_hand_plans", arguments(ctx)),))])
    repo = DatabaseRepository(str(tmp_path / "cancel.db"))
    repo.init()
    agent = LLMAgent("p", provider, enable_tools=True)
    agent.set_observability_context(repo)

    async def run():
        task = asyncio.create_task(agent.decide_play(ctx))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()

    try:
        asyncio.run(run())
        assert agent.get_last_decision_meta()["resolution"] == "cancelled"
        assert repo.conn.execute("SELECT count(*) FROM agent_tool_calls").fetchone()[0] == 0
        assert len(provider.turns) == 1
    finally:
        release.set()
        repo.close()


def test_full_duplicate_match_with_real_tools_and_mock_model(tmp_path):
    import re
    from tests.mock_utils import SmokeMockProvider

    class ToolMatchProvider(SmokeMockProvider):
        supports_tools = True

        async def generate_turn(self, messages, system_prompt="", tools=None, *, allow_tools=True):
            self.last_usage = LLMUsage(100, 20, 120)
            prompt = messages[0]["content"]
            completed = sum(message["role"] == "tool" for message in messages)
            if completed == 0 and allow_tools:
                action = json.loads(self._playing_response(prompt))["action"]
                return LLMResponse(tool_calls=(ToolCall("plan", "compare_hand_plans",
                    json.dumps({"actions": [{"cards": action.get("cards", [])}]})),))
            if completed == 1 and allow_tools:
                target = re.search(r"  ([SENW])：(地主|农民)，剩余", prompt).group(1)
                return LLMResponse(tool_calls=(ToolCall("threat", "analyze_threat",
                    json.dumps({"event": "can_finish", "target_seat": target})),))
            return LLMResponse(self._playing_response(prompt))

    repo = DatabaseRepository(str(tmp_path / "native-match.db"))
    repo.init()
    try:
        config_id = repo.create_player_config("offline tools", "random", "offline", "")
        ids = [repo.create_player(config_id, f"player-{i}") for i in range(8)]
        agents = {pid: LLMAgent(pid, LoggingLLMProvider(ToolMatchProvider(), repo=repo, agent_id=pid))
                  for pid in ids}
        runner = MatchRunner(
            MatchConfig(total_hands=1, max_tiebreaker_hands=0, ko_enabled=False,
                seed="full-tools-match", enable_tools=True,
                enable_reflection=False, enable_summary=False),
            assign_seating(ids[:4], ids[4:]), agents, db_repo=repo,
        )
        result = asyncio.run(runner.run())
        assert result.total_hands_played == 1
        saved = repo.get_match(runner.match_id)
        assert saved["status"] == "finished"
        config = saved["config"]
        assert (json.loads(config) if isinstance(config, str) else config)["enable_tools"]
        decisions = repo.conn.execute("SELECT * FROM decisions WHERE phase='playing'").fetchall()
        assert decisions and all(row["resolution"] == "model_first" for row in decisions)
        tool_rows = repo.conn.execute("SELECT * FROM agent_tool_calls").fetchall()
        assert len(tool_rows) == len(decisions) * 2
        assert {row["tool_name"] for row in tool_rows} == {"compare_hand_plans", "analyze_threat"}
        assert all(row["status"] == "ok" for row in tool_rows)
        assert all(json.loads(row["metadata_json"])["status"] in ("possible", "ruled_out")
                   for row in tool_rows if row["tool_name"] == "analyze_threat")
        calls = repo.conn.execute("SELECT count(*) FROM llm_call_logs WHERE phase='playing'").fetchone()[0]
        assert calls == len(decisions) * 3
    finally:
        repo.close()
