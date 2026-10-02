from __future__ import annotations

import asyncio
from pathlib import Path

from arena.agent.llm_agent import LLMAgent
from arena.agent.memory import MemoryManager
from arena.db.repository import DatabaseRepository
from arena.evaluation.memory_training import (
    MemoryTrainingRunner,
    build_memory_training_plan,
)
from arena.evaluation.spec import (
    build_run_manifest,
    load_experiment_spec,
    load_memory_artifact,
)
from arena.llm.base import AbstractLLMProvider
from arena.tournament.match import MatchConfig, MatchRunner
from arena.tournament.seating import assign_seating


ROOT = Path(__file__).resolve().parents[3]


class FakeTrainingAgent:
    def __init__(self, index: int, initial: str = "") -> None:
        self.memory = MemoryManager.with_long_term(f"fake-{index}", initial)


class SummaryProvider(AbstractLLMProvider):
    @property
    def provider_name(self) -> str:
        return "test"

    @property
    def model(self) -> str:
        return "summary"

    async def generate(self, user_prompt: str, system_prompt: str = "") -> str:
        return '{"summary":"ok","long_term_memory":"frozen lesson"}'


def test_memory_training_plan_is_deterministic_and_disjoint():
    spec = load_experiment_spec(ROOT / "src/backend/evaluation/experiments/reliability-v1.yaml")
    manifest = build_run_manifest(spec, ROOT)
    first = build_memory_training_plan(spec, manifest, ROOT)
    second = build_memory_training_plan(spec, manifest, ROOT)
    assert len(first.training_seeds) == 20
    assert not (set(first.training_seeds) & set(manifest.seeds))
    assert first.plan_sha256 == second.plan_sha256
    assert first.variant_id == "reflection_memory"


def test_memory_training_checkpoints_and_exports_frozen_artifact(tmp_path, monkeypatch):
    repo = DatabaseRepository(str(tmp_path / "arena.db")); repo.init()
    try:
        spec = load_experiment_spec(ROOT / "src/backend/evaluation/experiments/reliability-v1.yaml")
        manifest = build_run_manifest(spec, ROOT)
        plan = build_memory_training_plan(spec, manifest, ROOT)
        runner = MemoryTrainingRunner(repo, ROOT)
        run_id = runner.create_run(spec, manifest, plan)

        def fake_build_agents(run_id, spec, manifest, plan, memories):
            return {
                f"agent-{index}": FakeTrainingAgent(index, memories.get(f"player-{index}", ""))
                for index in range(8)
            }

        async def fake_run_task(task, spec, plan, agents):
            for agent in agents.values():
                previous = agent.memory.get_long_term()
                agent.memory.update_long_term(f"{previous}|{task['seed']}".strip("|"))
            return ""

        monkeypatch.setattr(runner, "_build_agents", fake_build_agents)
        monkeypatch.setattr(runner, "_run_training_task", fake_run_task)
        output = tmp_path / "memory-artifact.json"
        artifact = asyncio.run(
            runner.run(
                run_id, spec, manifest, plan, output, real_models=True,
            )
        )
        checkpoint = repo.get_memory_training_checkpoint(run_id)
        assert checkpoint is not None
        assert checkpoint["completed_task_id"]
        assert all(seed in checkpoint["memories"]["player-0"] for seed in plan.training_seeds)
        memories, artifact_hash = load_memory_artifact(output)
        assert artifact_hash == artifact["artifact_sha256"]
        assert memories == checkpoint["memories"]
        assert artifact["training_seed_set_sha256"] == plan.training_seed_set_sha256
        assert artifact["model_snapshot"]["model"] == manifest.models[0].model
        assert all(task["status"] == "finished" for task in repo.get_evaluation_tasks(run_id))
    finally:
        repo.close()


def test_match_summary_updates_in_memory_when_database_persistence_is_disabled():
    agent_ids = [f"agent-{index}" for index in range(8)]
    agents = {agent_id: LLMAgent(agent_id, SummaryProvider()) for agent_id in agent_ids}
    runner = MatchRunner(
        MatchConfig(enable_summary=True, persist_long_term_memory=False),
        assign_seating(agent_ids[:4], agent_ids[4:], seed="summary-memory"),
        agents,
    )
    asyncio.run(runner._run_match_summary("red", "2026-08-08T00:00:00Z"))
    assert all(agent.memory.get_long_term() == "frozen lesson" for agent in agents.values())


def test_checkpoint_write_failure_rolls_back_task_completion_and_resume(tmp_path, monkeypatch):
    import json
    import pytest

    repo = DatabaseRepository(str(tmp_path / "training.db"))
    repo.init()
    try:
        spec = load_experiment_spec(ROOT / "src/backend/evaluation/experiments/reliability-v1.yaml")
        seed_file = tmp_path / "ordered-training.json"
        seed_file.write_text(json.dumps({"seed_set_id": "ordered-training", "seeds": ["train-z", "train-a"]}))
        spec.training_seed_set_path = str(seed_file)
        manifest = build_run_manifest(spec, ROOT)
        plan = build_memory_training_plan(spec, manifest, ROOT)
        runner = MemoryTrainingRunner(repo, ROOT)
        rid = runner.create_run(spec, manifest, plan)
        visits = []

        def build(run_id, spec, manifest, plan, memories):
            return {f"agent-{i}": FakeTrainingAgent(i, memories.get(f"player-{i}", "")) for i in range(8)}

        async def task(task, spec, plan, agents):
            visits.append(task["seed"])
            for agent in agents.values():
                agent.memory.update_long_term(agent.memory.get_long_term() + "|" + task["seed"])
            return ""

        monkeypatch.setattr(runner, "_build_agents", build)
        monkeypatch.setattr(runner, "_run_training_task", task)
        save = repo.save_memory_training_checkpoint
        def fail(*args, **kwargs):
            raise RuntimeError("checkpoint disk failure")
        monkeypatch.setattr(repo, "save_memory_training_checkpoint", fail)
        output = tmp_path / "artifact.json"
        with pytest.raises(RuntimeError, match="checkpoint"):
            asyncio.run(runner.run(rid, spec, manifest, plan, output, real_models=True))
        assert not any(t["status"] == "finished" for t in repo.get_evaluation_tasks(rid))
        assert repo.get_memory_training_checkpoint(rid) is None
        monkeypatch.setattr(repo, "save_memory_training_checkpoint", save)
        artifact = asyncio.run(runner.run(rid, spec, manifest, plan, output, real_models=True))
        assert artifact["memories"]["player-0"] == "|train-z|train-a"
        assert visits == ["train-z", "train-z", "train-a"]
        assert repo.get_evaluation_run(rid)["status"] == "finished"
        with pytest.raises(FileExistsError):
            asyncio.run(runner.run(rid, spec, manifest, plan, output, real_models=True))
        assert visits == ["train-z", "train-z", "train-a"]
    finally:
        repo.close()


def test_resume_rejects_completed_tasks_without_matching_checkpoint(tmp_path, monkeypatch):
    import pytest
    from arena.evaluation.spec import PreflightError

    repo = DatabaseRepository(str(tmp_path / "training.db"))
    repo.init()
    try:
        spec = load_experiment_spec(ROOT / "src/backend/evaluation/experiments/reliability-v1.yaml")
        manifest = build_run_manifest(spec, ROOT)
        plan = build_memory_training_plan(spec, manifest, ROOT)
        runner = MemoryTrainingRunner(repo, ROOT)
        rid = runner.create_run(spec, manifest, plan)
        first = repo.get_evaluation_tasks(rid)[0]
        repo.update_evaluation_task(first["id"], "finished")
        def never(*args):
            raise AssertionError("must reject before constructing providers")
        monkeypatch.setattr(runner, "_build_agents", never)
        with pytest.raises(PreflightError, match="checkpoint"):
            asyncio.run(runner.run(rid, spec, manifest, plan, tmp_path / "artifact.json", real_models=True))
    finally:
        repo.close()


def test_training_uses_frozen_behavior_and_persistent_player_slots(tmp_path, monkeypatch):
    import arena.evaluation.memory_training as training

    repo = DatabaseRepository(str(tmp_path / "training.db"))
    repo.init()
    try:
        spec = load_experiment_spec(ROOT / "src/backend/evaluation/experiments/reliability-v1.yaml")
        model = spec.models[0]
        from arena.evaluation.spec import PriceSpec
        model.pricing = PriceSpec(input_per_million=0, output_per_million=0,
                                  source="offline fixture", effective_date="2026-10-02")
        config = repo.create_player_config(model.config_name, model.provider, model.model, "old-test-key",
                                           base_url="https://frozen.invalid/v1", system_prompt="frozen strategy")
        manifest = build_run_manifest(spec, ROOT, repo)
        plan = build_memory_training_plan(spec, manifest, ROOT)
        runner = MemoryTrainingRunner(repo, ROOT)
        rid = runner.create_run(spec, manifest, plan)
        repo.update_player_config(config, api_key="new-test-key", base_url="https://changed.invalid/v1",
                                  system_prompt="changed strategy")
        constructed = []
        def provider(**kwargs):
            constructed.append(kwargs)
            return SummaryProvider()
        monkeypatch.setattr(training, "OpenAIProvider", provider)
        agents = runner._build_agents(rid, spec, manifest, plan, {"player-0": "checkpoint memory"})
        assert all(kwargs["base_url"] == "https://frozen.invalid/v1" for kwargs in constructed)
        assert all(kwargs["api_key"] == "new-test-key" for kwargs in constructed)
        assert all(agent._system_prompt_override == "frozen strategy" for agent in agents.values())
        assert all(repo.get_player(aid) is not None for aid in agents)
        assert agents[f"{rid}-memory-player-0"].memory.get_long_term() == "checkpoint memory"
        again = runner._build_agents(rid, spec, manifest, plan, {})
        assert list(again) == list(agents)
        assert repo.conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 8
    finally:
        repo.close()


def training_fixture(tmp_path, *, max_calls=2):
    from arena.evaluation.spec import PriceSpec

    repo = DatabaseRepository(str(tmp_path / "budget-training.db"))
    repo.init()
    spec = load_experiment_spec(ROOT / "src/backend/evaluation/experiments/reliability-v1.yaml")
    spec.max_calls = max_calls
    model = spec.models[0]
    model.pricing = PriceSpec(input_per_million=1, output_per_million=2,
                              source="offline fixture", effective_date="2026-10-02")
    repo.create_player_config(model.config_name, model.provider, model.model, "offline-test-key",
                              base_url="https://frozen.invalid/v1", system_prompt="initial strategy")
    manifest = build_run_manifest(spec, ROOT, repo)
    plan = build_memory_training_plan(spec, manifest, ROOT)
    runner = MemoryTrainingRunner(repo, ROOT)
    rid = runner.create_run(spec, manifest, plan)
    return repo, spec, manifest, plan, runner, rid


def test_training_resume_loads_original_behavior_without_rereading_config(tmp_path):
    from arena.evaluation.memory_training import load_training_inputs

    repo, spec, manifest, plan, _, rid = training_fixture(tmp_path)
    try:
        config = repo.get_player_config_by_name(spec.models[0].config_name)
        repo.update_player_config(config["id"], base_url="https://changed.invalid/v1",
                                  system_prompt="new strategy", api_key="rotated-test-key")
        saved_spec, saved_manifest, saved_plan = load_training_inputs(repo, rid)
        assert saved_spec == spec
        assert saved_manifest == manifest
        assert saved_plan == plan
        assert saved_manifest.models[0].base_url == "https://frozen.invalid/v1"
        assert saved_manifest.models[0].system_prompt == "initial strategy"
    finally:
        repo.close()


def test_training_budget_includes_learning_and_carries_failed_attempts_on_resume(tmp_path, monkeypatch):
    import pytest
    import arena.evaluation.memory_training as training
    from arena.evaluation.budget import BudgetExceeded
    from arena.llm.base import LLMUsage

    repo, spec, manifest, plan, runner, rid = training_fixture(tmp_path)
    calls = []
    class Metered(SummaryProvider):
        last_usage = LLMUsage(20, 10, 30)
        async def generate(self, user_prompt, system_prompt=""):
            calls.append(system_prompt)
            return await super().generate(user_prompt, system_prompt)
    monkeypatch.setattr(training, "OpenAIProvider", lambda **kwargs: Metered())
    async def learning_task(task, spec, plan, agents):
        provider = next(iter(agents.values()))._provider
        for phase in ("reflection", "summary", "summary"):
            provider.set_context(phase=phase)
            try:
                await provider.generate("fixture", phase)
            except BudgetExceeded:
                pass  # Match summary records failures without undoing settlement.
        return ""
    monkeypatch.setattr(runner, "_run_training_task", learning_task)
    output = tmp_path / "budget-artifact.json"
    try:
        for _ in range(2):
            with pytest.raises(BudgetExceeded):
                asyncio.run(runner.run(rid, spec, manifest, plan, output, real_models=True))
        assert calls == ["reflection", "summary"]
        assert repo.get_memory_training_checkpoint(rid) is None
        assert repo.get_evaluation_run(rid)["status"] == "failed"
        assert not output.exists()
        ledger = list(repo.conn.execute("SELECT status, actual_usd FROM budget_ledger WHERE run_id=?", (rid,)))
        assert len(ledger) == 2
        assert all(row["status"] == "settled" and row["actual_usd"] == 0.00004 for row in ledger)
    finally:
        repo.close()


def test_training_deadline_keeps_unknown_reservation_and_discards_late_result(tmp_path, monkeypatch):
    import pytest
    import arena.evaluation.memory_training as training

    repo, spec, _, _, runner, _ = training_fixture(tmp_path)
    spec.task_timeout_seconds = 0.01
    spec.cancellation_deadline_seconds = 0.01
    manifest = build_run_manifest(spec, ROOT, repo)
    plan = build_memory_training_plan(spec, manifest, ROOT)
    rid = runner.create_run(spec, manifest, plan)
    release = asyncio.Event()
    class Late(SummaryProvider):
        async def generate(self, user_prompt, system_prompt=""):
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
            return await super().generate(user_prompt, system_prompt)
    monkeypatch.setattr(training, "OpenAIProvider", lambda **kwargs: Late())
    async def slow_task(task, spec, plan, agents):
        agent = next(iter(agents.values()))
        await agent._provider.generate("fixture", "summary")
        agent.memory.update_long_term("late memory")
        return ""
    monkeypatch.setattr(runner, "_run_training_task", slow_task)
    output = tmp_path / "late-artifact.json"
    async def exercise():
        with pytest.raises(TimeoutError):
            await runner.run(rid, spec, manifest, plan, output, real_models=True)
        before = [tuple(row) for row in repo.conn.execute("SELECT * FROM llm_call_logs WHERE run_id=?", (rid,))]
        release.set()
        await asyncio.gather(*runner.detached, return_exceptions=True)
        after = [tuple(row) for row in repo.conn.execute("SELECT * FROM llm_call_logs WHERE run_id=?", (rid,))]
        assert before == after and len(after) == 1
    try:
        asyncio.run(exercise())
        ledger = repo.conn.execute("SELECT * FROM budget_ledger WHERE run_id=?", (rid,)).fetchone()
        assert ledger["status"] == "unknown" and ledger["actual_usd"] is None
        assert ledger["reserved_usd"] > 0
        assert repo.get_memory_training_checkpoint(rid) is None
        assert repo.get_evaluation_run(rid)["status"] == "failed"
        assert not output.exists()
    finally:
        repo.close()
