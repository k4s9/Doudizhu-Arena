"""Learning boundaries: real game state, persistence failures and late responses."""
import asyncio
from dataclasses import replace

import pytest

from arena.agent.learning import run_learning
from arena.agent.llm_agent import LLMAgent
from arena.agent.memory import MemoryManager, MAX_LONG_TERM_CHARS, MAX_SHORT_TERM_CHARS
from arena.agent.random_agent import RandomAgent
from arena.agent.context import ContextBuilder
from arena.db.repository import DatabaseRepository
from arena.llm.base import AbstractLLMProvider
from arena.llm.logging import LoggingLLMProvider
from arena.tournament.match import MatchConfig, MatchRunner
from arena.tournament.seating import assign_seating


class Learner(RandomAgent):
    def __init__(self, agent_id, seed):
        super().__init__(agent_id, seed=seed)
        self.reflections = []
        self.summaries = []

    async def decide_bid(self, ctx):
        return 3 if ctx.current_high_bid < 3 else 0

    async def reflect(self, ctx):
        self.reflections.append(ctx)
        return {"reflection": "keep control", "short_term_memory": "watch the landlord"}

    async def summarize(self, ctx):
        self.summaries.append(ctx)
        return {"summary": "reviewed", "long_term_memory": "preserve useful pairs"}


def make_runner(repo=None, agent_type=Learner, **kwargs):
    ids = [f"player-{i}" for i in range(8)]
    agents = {pid: agent_type(pid, i) for i, pid in enumerate(ids)}
    if repo:
        cid = repo.create_player_config("learning", "random", "random", "")
        for pid in ids:
            repo.create_player(cid, pid, player_id=pid)
    config = MatchConfig(**{
        "total_hands": 1, "max_tiebreaker_hands": 0, "ko_enabled": False,
        "seed": "memory-lifecycle", **kwargs,
    })
    runner = MatchRunner(config, assign_seating(ids[:4], ids[4:], seed="memory"),
                         agents, db_repo=repo)
    return runner


@pytest.fixture
def repo(tmp_path):
    repo = DatabaseRepository(str(tmp_path / "memory.db"))
    repo.init()
    yield repo
    repo.close()


def test_no_database_reflection_and_reused_agents_have_isolated_matches():
    async def run():
        first = make_runner()
        await first.run()
        assert first.match_id
        assert all(len(a.reflections) == 1 for a in first.agents.values())
        assert all(a.memory.get_short_term(first.match_id) == "watch the landlord"
                   for a in first.agents.values())
        assert all(a.memory.get_long_term() == "preserve useful pairs" for a in first.agents.values())
        second = MatchRunner(replace(first.config, enable_reflection=False), first.seating, first.agents)
        await second.run()
        assert first.match_id != second.match_id
        assert all(a.memory.get_short_term(second.match_id) == "" for a in second.agents.values())
        assert all(len(a.reflections) == 1 for a in first.agents.values())
        assert all(a.memory._current_match_id == "" for a in first.agents.values())
    asyncio.run(run())


def test_reused_llm_agents_detach_database_and_previous_match_context(repo):
    class PassingProvider(AbstractLLMProvider):
        provider_name = "mock"
        model = "passing"

        async def generate(self, user_prompt, system_prompt=""):
            return ('{"bid":0,"reasoning":"pass",'
                    '"reflection":"reviewed","short_term_memory":"new hand memory",'
                    '"summary":"reviewed","long_term_memory":"remember passes"}')

    class PassingLLMAgent(LLMAgent):
        def __init__(self, agent_id, seed):
            super().__init__(agent_id, LoggingLLMProvider(PassingProvider(), repo=repo))

    async def run():
        first = make_runner(repo, agent_type=PassingLLMAgent)
        await first.run()
        before_logs = [tuple(row) for row in repo.conn.execute("SELECT * FROM llm_call_logs")]
        before_events = [tuple(row) for row in repo.conn.execute("SELECT * FROM decision_events")]
        assert before_logs and before_events
        for agent in first.agents.values():
            # Run metadata must also be cleared when persistence is detached.
            agent._decision_context["run_id"] = "previous-run"
            agent._provider.set_context(run_id="previous-run")
        second = MatchRunner(first.config, first.seating, first.agents)
        await second.run()
        assert not second.learning_failures
        assert not second.table_a.learning_failures
        assert not second.table_b.learning_failures
        assert all(a.memory.get_short_term(second.match_id) == "new hand memory"
                   for a in second.agents.values())
        for agent in second.agents.values():
            assert agent._repo is None
            assert agent._provider._repo is None
            assert agent.get_observability_context() == {"match_id": second.match_id}
            assert agent._provider._match_id == second.match_id
            assert agent._provider._table_hand_id == ""
            assert agent._provider._run_id == ""
        assert [tuple(row) for row in repo.conn.execute("SELECT * FROM llm_call_logs")] == before_logs
        assert [tuple(row) for row in repo.conn.execute("SELECT * FROM decision_events")] == before_events

    asyncio.run(run())


def test_settlement_happens_without_summary_and_is_idempotent(repo):
    runner = make_runner(repo, enable_summary=False)
    result = asyncio.run(runner.run())
    assert repo.get_match(runner.match_id)["status"] == "finished"
    assert all(repo.get_player(pid)["matches_played"] == 1 for pid in runner.agents)
    assert not repo.settle_match(runner.match_id, result.winner, runner.seating.agent_teams)
    assert all(repo.get_player(pid)["matches_played"] == 1 for pid in runner.agents)
    assert all(not a.summaries for a in runner.agents.values())


def test_summary_failure_preserves_settlement_and_committed_memory(repo, monkeypatch):
    runner = make_runner(repo)
    def fail(*args, **kwargs):
        raise OSError("simulated disk error")
    monkeypatch.setattr(repo, "save_long_term_memory", fail)
    asyncio.run(runner.run())
    assert all(repo.get_player(pid)["matches_played"] == 1 for pid in runner.agents)
    assert all(a.memory.get_long_term() == "" for a in runner.agents.values())
    failures = repo.list_memory_failures(match_id=runner.match_id)
    assert len(failures) == 8
    assert all(f["stage"] == "persistence" and f["phase"] == "summary" for f in failures)


def test_reflection_persistence_failure_preserves_memory_and_match(repo, monkeypatch):
    runner = make_runner(repo, enable_summary=False)

    def fail(*args, **kwargs):
        raise OSError("simulated disk error")

    monkeypatch.setattr(repo, "save_reflection_memory", fail)
    asyncio.run(runner.run())
    assert repo.get_match(runner.match_id)["status"] == "finished"
    assert all(a.memory.get_short_term(runner.match_id) == "" for a in runner.agents.values())
    assert not repo.conn.execute("SELECT 1 FROM reflections").fetchone()
    failures = repo.list_memory_failures(match_id=runner.match_id)
    assert len(failures) == 8
    assert all(f["stage"] == "persistence" and f["phase"] == "reflection" for f in failures)


def test_void_hands_and_tiebreakers_keep_actual_cards_roles_and_completed_count(repo):
    class PassingLearner(Learner):
        async def decide_bid(self, ctx):
            return 0

    runner = make_runner(repo, agent_type=PassingLearner, max_tiebreaker_hands=2)
    result = asyncio.run(runner.run())
    assert result.winner == "tie"
    assert result.total_hands_played == 3
    assert result.tiebreaker_hands == 2
    assert len(result.hand_results) == 3
    for pid, agent in runner.agents.items():
        assert len(agent.reflections) == 3
        for ctx in agent.reflections:
            assert ctx.role == "void"
            assert ctx.winner_role == "void"
            assert ctx.hand_score == 0
            initial = result.hand_results[ctx.hand_num - 1].table_a.initial_hands
            assert {seat: set(cards) for seat, cards in ctx.remaining_hands.items()} == {
                seat: set(cards) for seat, cards in initial.items()
            }
            assert sum(len(cards) for cards in ctx.remaining_hands.values()) == 51
            assert "流局" in ContextBuilder.build_reflection_context_text(ctx)
        summary = agent.summaries[0]
        assert len(summary.match_summary) == 3
        assert all(hand["role"] == "void" for hand in summary.match_summary.values())
        assert sum(hand["is_tiebreaker"] for hand in summary.match_summary.values()) == 2
        assert "比赛结果：平局" in ContextBuilder.build_summary_context_text(summary)


def test_summary_uses_actual_hands_roles_scores_and_tiebreakers():
    async def run():
        runner = make_runner(enable_summary=False)
        await runner.run()
        record = runner._hand_records[0]
        runner.config.total_hands = 20
        runner._hand_records.append(replace(record, hand_num=21, is_tiebreaker=True))
        await runner._run_match_summary("red", "now")
        for pid, agent in runner.agents.items():
            ctx = agent.summaries[0]
            assert list(ctx.match_summary) == [1, 21]
            table, seat = runner.seating.agent_seats[pid]
            hand = record.table_a if table == "A" else record.table_b
            expected_role = "idle" if seat == hand.effective_idle else "landlord" if seat == hand.landlord else "farmer"
            assert ctx.match_summary[1]["role"] == expected_role
            assert ctx.match_summary[1]["table_score"] == hand.score.final_score
            assert ctx.match_summary[21]["is_tiebreaker"]
            assert "实际完成副数：2" in ContextBuilder.build_summary_context_text(ctx)
            assert agent.reflections[0].hand_score == hand.score.final_score
    asyncio.run(run())


def test_summary_counts_only_hands_played_before_ko(monkeypatch):
    from arena.engine.scoring import calculate_diff_score

    async def run():
        sample = make_runner(enable_summary=False)
        await sample.run()
        record = sample._hand_records[0]
        runner = make_runner(total_hands=20, ko_enabled=True)
        score = replace(record.table_a.score, winner_team="red", final_score=12)
        table_a = replace(record.table_a, score=score)
        table_b = replace(record.table_b, score=score)
        red_diff, blue_diff = calculate_diff_score(score, score)

        async def hand_pair(hand_num, match_seed, is_tiebreaker):
            return replace(
                record, hand_num=hand_num, table_a=table_a, table_b=table_b,
                red_diff=red_diff, blue_diff=blue_diff,
            )

        monkeypatch.setattr(runner, "_run_hand_pair", hand_pair)
        result = await runner.run()
        assert result.ko_result.triggered
        assert result.total_hands_played == 11
        for agent in runner.agents.values():
            summary = agent.summaries[0]
            assert list(summary.match_summary) == list(range(1, 12))
            assert "实际完成副数：11" in ContextBuilder.build_summary_context_text(summary)

    asyncio.run(run())


def test_memory_limits_reject_updates_without_erasing_valid_memory():
    memory = MemoryManager.with_long_term("p", "existing")
    for bad in (None, {}, " ", "x" * (MAX_LONG_TERM_CHARS + 1)):
        with pytest.raises(ValueError):
            memory.update_long_term(bad)
        assert memory.get_long_term() == "existing"
    for index in range(30):
        memory.start_match(str(index))
        memory.update_short_term("bounded")
    assert len(memory.short_term) <= 8
    for bad in (None, {}, " ", "x" * (MAX_SHORT_TERM_CHARS + 1)):
        with pytest.raises(ValueError):
            memory.update_short_term(bad)
        assert memory.get_short_term() == "bounded"
    memory.start_match("29")
    assert memory.get_short_term() == ""
    memory.end_match()
    with pytest.raises(ValueError):
        memory.update_short_term("no active match")


@pytest.mark.parametrize("phase", ["reflect", "summarize"])
def test_late_model_response_cannot_write_after_learning_timeout(repo, phase):
    class LateProvider(AbstractLLMProvider):
        provider_name = "mock"
        model = "late"
        def __init__(self, gate):
            self.gate = gate
        async def generate(self, user_prompt, system_prompt=""):
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                await self.gate.wait()
            return ('{"summary":"late","long_term_memory":"must not be saved"}'
                    if phase == "summarize" else
                    '{"reflection":"late","short_term_memory":"must not be saved"}')

    async def run():
        gate = asyncio.Event()
        agent = LLMAgent("late", LoggingLLMProvider(LateProvider(gate), repo=repo))
        agent.set_observability_context(repo)
        from arena.agent.base import AgentContext
        ctx = AgentContext(seat="S", role="", hand_cards=[], hand_size=0)
        with pytest.raises(TimeoutError):
            await run_learning(agent, phase, ctx, .01)
        at_deadline = [tuple(row) for row in repo.conn.execute("SELECT * FROM llm_call_logs")]
        assert len(at_deadline) == 1  # Preserve the cancelled physical call, usage unknown.
        gate.set()
        await asyncio.sleep(.01)
        assert agent.memory.get_long_term() == ""
        assert [tuple(row) for row in repo.conn.execute("SELECT * FROM llm_call_logs")] == at_deadline
        assert repo.conn.execute("SELECT provider_status FROM call_evidence").fetchone()[0] == "cancelled"
        assert repo.conn.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == 0
    asyncio.run(run())


@pytest.mark.parametrize("phase", ["reflection", "summary"])
def test_learning_timeouts_finish_match_and_isolate_late_memory(repo, phase):
    async def run():
        gate = asyncio.Event()
        completed = []

        class LateLearner(Learner):
            async def late_update(self):
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    await gate.wait()
                self.memory.update_short_term("late short memory")
                self.memory.update_long_term("late long memory")
                completed.append(self.agent_id)
                return {"reflection": "late", "short_term_memory": "late short memory",
                        "summary": "late", "long_term_memory": "late long memory"}

            async def reflect(self, ctx):
                return await self.late_update()

            async def summarize(self, ctx):
                return await self.late_update()

        runner = make_runner(
            repo, agent_type=LateLearner, enable_reflection=phase == "reflection",
            enable_summary=phase == "summary", reflection_timeout_seconds=.01,
            summary_timeout_seconds=.01,
        )
        try:
            await asyncio.wait_for(runner.run(), timeout=3)
            assert repo.get_match(runner.match_id)["status"] == "finished"
            assert all(repo.get_player(pid)["matches_played"] == 1 for pid in runner.agents)
            failures = repo.list_memory_failures(match_id=runner.match_id)
            assert len(failures) == 8
            assert all(f["phase"] == phase and f["error_type"] == "TimeoutError" for f in failures)
        finally:
            gate.set()
            await asyncio.sleep(.01)
        assert len(completed) == 8
        assert all(a.memory.get_short_term(runner.match_id) == "" for a in runner.agents.values())
        assert all(a.memory.get_long_term() == "" for a in runner.agents.values())

    asyncio.run(run())


def test_cancelling_match_during_reflection_cleans_up_and_marks_interrupted(repo):
    async def run():
        entered = asyncio.Event()
        gate = asyncio.Event()

        class WaitingLearner(Learner):
            async def reflect(self, ctx):
                entered.set()
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    await gate.wait()
                self.memory.update_long_term("late")
                return {"reflection": "late", "short_term_memory": "late"}

        runner = make_runner(repo, agent_type=WaitingLearner)
        task = asyncio.create_task(runner.run())
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=3)
            assert repo.get_match(runner.match_id)["status"] == "interrupted"
            assert all(repo.get_player(pid)["matches_played"] == 0 for pid in runner.agents)
        finally:
            gate.set()
            await asyncio.sleep(.01)
        assert all(a.memory._current_match_id == "" for a in runner.agents.values())
        assert all(a.memory.get_long_term() == "" for a in runner.agents.values())
        assert not repo.conn.execute("SELECT 1 FROM reflections").fetchone()

    asyncio.run(run())


def test_restart_loads_saved_memory_into_next_prompt(repo):
    from arena.agent.prompts.playing import build_playing_prompt
    from arena.agent.base import AgentContext
    runner = make_runner(repo)
    asyncio.run(runner.run())
    pid = next(iter(runner.agents))
    version = repo.list_memory_versions(pid)[0]
    repo.close()
    repo.init()
    agent = LLMAgent(pid, object(), long_term_memory=repo.get_player(pid)["long_term_memory"])
    ctx = AgentContext(seat="S", role="landlord", hand_cards=[], hand_size=0)
    system, user = build_playing_prompt(ctx, agent.memory, "test")
    assert "preserve useful pairs" in system + user
    assert version["match_id"] == runner.match_id
    assert len(repo.list_match_memory_usage(runner.match_id)) == 8
