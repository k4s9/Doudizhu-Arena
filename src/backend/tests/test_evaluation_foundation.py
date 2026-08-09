"""Regression tests for phases 1-3 of the reliability evaluation foundation."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path

import pytest

from arena.agent.base import AgentContext
from arena.agent.llm_agent import LLMAgent
from arena.db.repository import DatabaseRepository
from arena.evaluation.spec import (
    PreflightError, build_run_manifest, load_experiment_spec, load_memory_artifact,
)
from arena.llm.base import AbstractLLMProvider
from arena.llm.logging import LoggingLLMProvider


ROOT = Path(__file__).resolve().parents[3]


class InvalidThenValidProvider(AbstractLLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "test"

    @property
    def model(self) -> str:
        return "invalid-then-valid"

    async def generate(self, user_prompt: str, system_prompt: str = "") -> str:
        self.calls += 1
        if self.calls == 1:
            return "not json"
        return '{"reasoning": "valid", "bid": 1}'


class AlwaysInvalidProvider(AbstractLLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "test"

    @property
    def model(self) -> str:
        return "always-invalid"

    async def generate(self, user_prompt: str, system_prompt: str = "") -> str:
        self.calls += 1
        return "not json"


def test_manifest_is_deterministic_and_has_frozen_seed_set():
    spec = load_experiment_spec(ROOT / "src/backend/evaluation/experiments/reliability-v1.yaml")
    first = build_run_manifest(spec, ROOT)
    second = build_run_manifest(spec, ROOT)
    assert len(first.seeds) == 100
    assert first.seeds == second.seeds
    assert first.manifest_sha256 == second.manifest_sha256
    assert first.source_revision
    assert first.dependency_sha256
    assert isinstance(first.source_dirty, bool)
    assert first.models[0].sdk_version


def test_manifest_rejects_overlapping_training_and_eval_seeds(tmp_path: Path):
    seed_path = tmp_path / "seeds.json"
    seed_path.write_text('{"seed_set_id":"same","seeds":["one"]}', encoding="utf-8")
    spec = load_experiment_spec(ROOT / "src/backend/evaluation/experiments/reliability-v1.yaml")
    bad = spec.model_copy(update={"seed_set_path": str(seed_path), "training_seed_set_path": str(seed_path)})
    with pytest.raises(PreflightError, match="overlap"):
        build_run_manifest(bad, ROOT)


def test_frozen_memory_artifact_is_hashed_and_loaded(tmp_path: Path):
    artifact = tmp_path / "memory.json"
    artifact.write_text(
        '{"memory_artifact_id":"trained-v1","memories":{"player-0":"保持牌型完整"}}',
        encoding="utf-8",
    )
    memories, artifact_hash = load_memory_artifact(artifact)
    assert memories == {"player-0": "保持牌型完整"}
    spec = load_experiment_spec(ROOT / "src/backend/evaluation/experiments/reliability-v1.yaml")
    spec = spec.model_copy(update={"memory_artifact_path": str(artifact)})
    manifest = build_run_manifest(spec, ROOT)
    assert manifest.memory_artifact_sha256 == artifact_hash


def test_decision_events_capture_invalid_retry_and_success(tmp_path: Path):
    repo = DatabaseRepository(str(tmp_path / "evaluation.db"))
    repo.init()
    try:
        match_id = repo.create_match("event test", {}, "event-seed")
        experiment_id = repo.create_experiment("event-experiment", {}, "event-spec")
        run_id = repo.create_evaluation_run(experiment_id, {}, "event-manifest")
        hand_id = repo.create_hand(match_id, 1, "S", "W", "event-seed/hand-1")
        table_hand_id = repo.create_table_hand(hand_id, "A")
        provider = LoggingLLMProvider(InvalidThenValidProvider(), repo=repo, agent_id="player-1")
        agent = LLMAgent("player-1", provider, retry_limit=1)
        agent.set_observability_context(
            repo, run_id=run_id, variant_id="rule_feedback",
            match_id=match_id, table_hand_id=table_hand_id,
        )
        result = asyncio.run(agent.decide_bid(AgentContext(
            seat="S", role="bidding", hand_cards=[], hand_size=17,
            current_high_bid=0, current_high_bidder="", landlord="",
            active_players=["S", "E", "N"], player_hand_sizes={"S": 17, "E": 17, "N": 17, "W": 0},
        )))
        events = repo.get_decision_events(match_id=match_id)
        assert result == 1
        assert len(events) == 2
        assert events[0]["is_illegal"] == 1
        assert events[0]["error_code"] == "json_format"
        assert events[1]["is_retry"] == 1
        assert events[1]["output_sha256"]
        assert events[0]["decision_id"] == events[1]["decision_id"]
        calls = [dict(row) for row in repo.conn.execute(
            "SELECT * FROM llm_call_logs ORDER BY created_at, attempt"
        ).fetchall()]
        assert len(calls) == 2
        assert calls[0]["run_id"] == run_id
        assert calls[0]["variant_id"] == "rule_feedback"
        assert calls[0]["match_id"] == match_id
        assert calls[0]["decision_id"] == events[0]["decision_id"]
        assert [call["attempt"] for call in calls] == [1, 2]
        assert agent.get_last_decision_meta()["retry_count"] == 1
    finally:
        repo.close()


def test_evaluation_tables_and_manifest_are_persisted(tmp_path: Path):
    repo = DatabaseRepository(str(tmp_path / "evaluation.db"))
    repo.init()
    try:
        spec = load_experiment_spec(ROOT / "src/backend/evaluation/experiments/reliability-v1.yaml")
        manifest = build_run_manifest(spec, ROOT)
        experiment_db_id = repo.create_experiment(spec.experiment_id, spec.model_dump(mode="json"), manifest.spec_sha256)
        run_id = repo.create_evaluation_run(experiment_db_id, asdict(manifest), manifest.manifest_sha256)
        snapshot_id = repo.add_model_snapshot(run_id, asdict(manifest.models[0]))
        assert experiment_db_id and run_id and snapshot_id
        tables = {row[0] for row in repo.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"experiments", "evaluation_runs", "model_snapshots", "decision_events"} <= tables
        llm_columns = {row[1] for row in repo.conn.execute("PRAGMA table_info(llm_call_logs)")}
        assert {"run_id", "variant_id", "match_id", "decision_id", "attempt"} <= llm_columns
    finally:
        repo.close()


def test_reflection_and_summary_honor_variant_retry_limit(tmp_path: Path):
    repo = DatabaseRepository(str(tmp_path / "evaluation.db"))
    repo.init()
    provider = AlwaysInvalidProvider()
    try:
        experiment_id = repo.create_experiment("phase-experiment", {}, "phase-spec")
        run_id = repo.create_evaluation_run(experiment_id, {}, "phase-manifest")
        agent = LLMAgent("player-1", provider, retry_limit=0, rule_feedback=False)
        agent.set_observability_context(repo, run_id=run_id, variant_id="baseline_no_feedback")
        ctx = AgentContext(seat="S", role="farmer", hand_cards=[], hand_size=0, match_id="match-1")
        reflection = asyncio.run(agent.reflect(ctx))
        summary = asyncio.run(agent.summarize(ctx))
        assert reflection == {"reflection": "", "short_term_memory": ""}
        assert summary == {"summary": "", "long_term_memory": ""}
        assert provider.calls == 2
        events = repo.get_decision_events(run_id=run_id)
        assert [event["phase"] for event in events] == ["reflection", "summary"]
        assert all(event["is_illegal"] == 1 for event in events)
        assert all(event["is_fallback"] == 1 for event in events)
    finally:
        repo.close()
