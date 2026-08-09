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
