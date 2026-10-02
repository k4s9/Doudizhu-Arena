"""Complete-match controls, score reconstruction and offline CLI acceptance."""
import asyncio
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from arena.db.repository import DatabaseRepository
from arena.evaluation.memory_match_study import (
    MemoryMatchMockProvider, MemoryMatchRunner, make_match_plan, schedule,
)
from arena.evaluation.spec import canonical_hash

ROOT = Path(__file__).resolve().parents[3]


def inputs():
    spec = importlib.util.spec_from_file_location("match_cli", ROOT / "scripts/memory_match_study.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli.mock_inputs()


@pytest.fixture
def completed(tmp_path):
    model, artifact = inputs()
    plan = make_match_plan(ROOT, artifact, "固定初始策略：检查当前手牌。", model,
                           ["memory-match-test-v1-001"], {}, mock=True)
    repo = DatabaseRepository(str(tmp_path / "matches.db"))
    repo.init()
    runner = MemoryMatchRunner(repo, ROOT, plan)
    rid = asyncio.run(runner.run(mock=True))
    yield repo, plan, rid
    repo.close()


def test_complete_color_swapped_matches_have_replayable_scores_and_frozen_memory(completed, tmp_path):
    from arena.evaluation.memory_match_report import audit_memory_matches, export_memory_match_report
    repo, plan, rid = completed
    assert repo.get_evaluation_run(rid)["status"] == "finished"
    assert len(schedule(plan)) == 6
    assert repo.conn.execute("SELECT COUNT(*) FROM matches WHERE status='finished'").fetchone()[0] == 6
    assert repo.conn.execute("SELECT COUNT(*) FROM hands WHERE status='finished'").fetchone()[0] == 6
    assert repo.conn.execute("SELECT COUNT(*) FROM table_hands WHERE status='finished'").fetchone()[0] == 12
    assert repo.conn.execute("SELECT COUNT(*) FROM llm_call_logs WHERE phase NOT IN ('bidding','playing')").fetchone()[0] == 0
    assert repo.conn.execute("SELECT COUNT(*) FROM agent_memories").fetchone()[0] == 0
    assert not list(repo.conn.execute("SELECT * FROM players WHERE COALESCE(long_term_memory,'') != ''"))
    audit, rows, _ = audit_memory_matches(repo, rid, root=ROOT)
    assert audit["complete"], audit
    assert len(rows) == 6
    assert all(row["diff_score"] == row["evaluated_score"] - row["opponent_score"] for row in rows)
    report = export_memory_match_report(repo, rid, tmp_path / "report", root=ROOT)
    assert report["complete"], report
    # This mock ignores the memory text. Every matched seed/color has identical outcomes.
    for side in ("red", "blue"):
        assert len({row["evaluated_score"] for row in rows if row["side"] == side}) == 1
    for evidence in repo.conn.execute("SELECT payload_json FROM memory_match_evidence"):
        payload = json.loads(evidence[0])
        assert payload["before"] == payload["after"]
    assert report["physical_calls"] > 0


@pytest.mark.parametrize("corruption", ["score", "raw", "prompt", "ledger", "memory", "baseline"])
def test_match_audit_rejects_corrupted_game_or_call_evidence(completed, corruption):
    from arena.evaluation.memory_match_report import audit_memory_matches
    repo, _, rid = completed
    if corruption == "score":
        repo.conn.execute("UPDATE matches SET score_red=score_red+1 WHERE rowid=(SELECT MIN(rowid) FROM matches)")
    elif corruption in ("raw", "prompt"):
        field = "raw_output" if corruption == "raw" else "system_prompt"
        repo.conn.execute(f"UPDATE call_evidence SET {field}='tampered' WHERE rowid=(SELECT MIN(rowid) FROM call_evidence)")
    elif corruption == "ledger":
        repo.conn.execute("UPDATE budget_ledger SET actual_usd=99 WHERE rowid=(SELECT MIN(rowid) FROM budget_ledger)")
    elif corruption == "memory":
        row = repo.conn.execute("SELECT task_id,payload_json FROM memory_match_evidence LIMIT 1").fetchone()
        payload = json.loads(row["payload_json"])
        payload["after"]["target-0"]["long_term"] = "test-time update"
        payload["sha256"] = canonical_hash({k: v for k, v in payload.items() if k != "sha256"})
        repo.conn.execute("UPDATE memory_match_evidence SET payload_json=? WHERE task_id=?", (json.dumps(payload), row["task_id"]))
    else:
        row = repo.conn.execute("SELECT task_id,payload_json FROM memory_match_evidence LIMIT 1").fetchone()
        payload = json.loads(row["payload_json"])
        next(p for p in payload["roster"] if p["kind"] == "baseline")["baseline_seed"] = "changed baseline"
        payload["sha256"] = canonical_hash({k: v for k, v in payload.items() if k != "sha256"})
        repo.conn.execute("UPDATE memory_match_evidence SET payload_json=? WHERE task_id=?", (json.dumps(payload), row["task_id"]))
    repo.conn.commit()
    assert not audit_memory_matches(repo, rid, root=ROOT)[0]["complete"]


def test_call_cap_stops_game_and_retains_failed_evidence(tmp_path):
    from arena.evaluation.memory_match_report import audit_memory_matches
    model, artifact = inputs()
    plan = make_match_plan(ROOT, artifact, "initial", model, ["memory-match-test-v1-001"], {"max_calls": 1}, mock=True)
    repo = DatabaseRepository(str(tmp_path / "limited.db"))
    repo.init()
    try:
        rid = asyncio.run(MemoryMatchRunner(repo, ROOT, plan).run(mock=True))
        assert repo.get_evaluation_run(rid)["status"] == "failed"
        assert repo.conn.execute("SELECT COUNT(*) FROM llm_call_logs").fetchone()[0] == 1
        assert not audit_memory_matches(repo, rid, root=ROOT)[0]["complete"]
    finally:
        repo.close()


def test_plan_rejects_training_overlap_and_execution_requires_matching_mode(tmp_path):
    model, artifact = inputs()
    with pytest.raises(ValueError, match="overlap"):
        make_match_plan(ROOT, artifact, "initial", model, artifact["training_seeds"], {}, mock=True)
    plan = make_match_plan(ROOT, artifact, "initial", model, ["new-test-seed"], {}, mock=True)
    repo = DatabaseRepository(str(tmp_path / "preflight.db"))
    repo.init()
    try:
        with pytest.raises(ValueError, match="explicit"):
            asyncio.run(MemoryMatchRunner(repo, ROOT, plan).run(real_models=True))
        assert repo.conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0
    finally:
        repo.close()


def test_real_mode_without_credentials_stops_before_constructing_any_provider(tmp_path):
    model, artifact = inputs()
    model.update(provider="openai", model="declared-test-model", base_url="https://example.invalid/v1")
    artifact.update(provenance="real", training_total_hands=1,
                    memories={f"player-{i}": "frozen lesson" for i in range(8)})
    artifact["runtime_model_config"] = {key: model[key] for key in ("provider", "model", "base_url")}
    artifact["runtime_model_config"]["system_prompt_sha256"] = hashlib.sha256(b"").hexdigest()
    artifact["artifact_sha256"] = canonical_hash({k: v for k, v in artifact.items() if k != "artifact_sha256"})
    plan = make_match_plan(ROOT, artifact, "initial", model, ["new-test-seed"], {}, mock=False)
    repo = DatabaseRepository(str(tmp_path / "credential-preflight.db"))
    repo.init()
    try:
        with pytest.raises(ValueError, match="credential"):
            asyncio.run(MemoryMatchRunner(repo, ROOT, plan).run(real_models=True))
        assert repo.conn.execute("SELECT COUNT(*) FROM llm_call_logs").fetchone()[0] == 0
        assert repo.conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0
    finally:
        repo.close()


def test_invalid_model_outputs_are_reported_as_fallback_not_model_legality(tmp_path):
    from arena.evaluation.memory_match_report import audit_memory_matches
    from arena.llm.base import LLMUsage
    model, artifact = inputs()
    plan = make_match_plan(ROOT, artifact, "initial", model, ["memory-match-test-v1-001"], {}, mock=True)
    class Invalid(MemoryMatchMockProvider):
        async def generate(self, user_prompt, system_prompt=""):
            self.last_usage = LLMUsage(5, 5, 10)
            return "invalid output"
    repo = DatabaseRepository(str(tmp_path / "fallback.db"))
    repo.init()
    try:
        rid = asyncio.run(MemoryMatchRunner(repo, ROOT, plan, provider_factory=Invalid).run(mock=True))
        audit, _, _ = audit_memory_matches(repo, rid, root=ROOT)
        assert audit["complete"], audit
        assert repo.conn.execute("SELECT COUNT(*) FROM decisions WHERE run_id=? AND resolution IN ('model_first','model_retry')", (rid,)).fetchone()[0] == 0
        assert repo.conn.execute("SELECT COUNT(*) FROM decisions WHERE run_id=? AND resolution='system_fallback'", (rid,)).fetchone()[0] > 0
    finally:
        repo.close()


def test_full_match_mock_cli_prepare_run_audit_without_key(tmp_path):
    def cli(*args):
        result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "scripts/memory_match_study.py"), *map(str, args)],
                                cwd=ROOT, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout.strip().splitlines()[-1])
    plan, run, audit = (tmp_path / item for item in ("plan", "run", "audit"))
    assert cli("prepare", "--mock", "--output", plan)["planned_matches"] == 6
    result = cli("run", "--mock", "--plan", plan, "--output", run)
    assert result["complete"]
    assert cli("audit", "--run-dir", run, "--output", audit)["complete"]
