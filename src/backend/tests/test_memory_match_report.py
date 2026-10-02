"""Read-only report boundaries beyond the runner's happy-path acceptance."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from arena.db.repository import DatabaseRepository
from arena.evaluation.memory_match_report import audit_memory_matches, export_memory_match_report
from arena.evaluation.memory_match_study import MemoryMatchRunner, make_match_plan
from arena.evaluation.spec import canonical_hash

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def report_database(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("report_match_cli", ROOT / "scripts/memory_match_study.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    model, artifact = cli.mock_inputs()
    plan = make_match_plan(ROOT, artifact, "冻结测试策略。", model, ["memory-match-test-v1-001"], {}, mock=True)
    repo = DatabaseRepository(str(tmp_path_factory.mktemp("match-report") / "study.db"))
    repo.init()
    run_id = asyncio.run(MemoryMatchRunner(repo, ROOT, plan).run(mock=True))
    assert repo.get_evaluation_run(run_id)["status"] == "finished"
    yield repo, run_id
    repo.close()


@pytest.fixture
def report_copy(report_database):
    source, run_id = report_database
    repo = DatabaseRepository(":memory:")
    repo._conn = sqlite3.connect(":memory:")
    repo._conn.row_factory = sqlite3.Row
    source.conn.backup(repo.conn)
    yield repo, run_id
    repo.close()


def test_report_excludes_baseline_and_retains_signed_score(report_copy, tmp_path):
    repo, run_id = report_copy
    before = repo.conn.total_changes
    summary = export_memory_match_report(repo, run_id, tmp_path / "report")
    assert summary["complete"], summary
    assert repo.conn.total_changes == before
    assert all(arm["matches"] == 2 and arm["losses"] == 2 and arm["mean_diff_score"] == -6
               for arm in summary["arms"].values())
    count = repo.conn.execute("SELECT COUNT(*) FROM decisions WHERE run_id=?", (run_id,)).fetchone()[0]
    assert sum(arm["metrics"]["all"]["D"] for arm in summary["arms"].values()) == count
    assert all(arm["metrics"]["all"]["autoplay"]["numerator"] == 0 for arm in summary["arms"].values())
    assert summary["paired_by_seed"][0]["frozen_minus_none_mean_diff_score"] == 0
    assert summary["paired_by_seed"][0]["frozen_minus_initial_mean_diff_score"] == 0
    match_rows = json.loads((tmp_path / "report/matches.json").read_text())
    assert all(row["evaluated_score"] == 0 and row["opponent_score"] == 6 and row["diff_score"] == -6 for row in match_rows)


@pytest.mark.parametrize("corruption,expected_issue", [
    ("malformed_payload", "unreadable match evidence"),
    ("malformed_observation", "unreadable match evidence"),
    ("private_observation", "private learning/replay context"),
    ("initial_memory", "initial memory usage content"),
    ("winner", "match winner settlement"),
    ("learning_call", "unexpected reflection/summary/other call"),
    ("hidden_call", "match/run call linkage"),
    ("usage_overflow", "provider prompt_tokens allowance"),
    ("seat", "decision seating linkage"),
])
def test_corrupt_reports_fail_closed_even_when_local_hashes_are_recomputed(report_copy, corruption, expected_issue):
    repo, run_id = report_copy
    if corruption == "malformed_payload":
        repo.conn.execute("UPDATE memory_match_evidence SET payload_json='{' WHERE rowid=1")
    elif corruption in ("malformed_observation", "private_observation"):
        row = repo.conn.execute("SELECT decision_id,observation_json FROM decisions WHERE run_id=? LIMIT 1", (run_id,)).fetchone()
        observation = json.loads(row["observation_json"])
        observation["remaining_hands"] = {"S": ["大王"]}
        repo.conn.execute("UPDATE decisions SET observation_json=?,observation_hash=? WHERE decision_id=?",
                          ("{" if corruption == "malformed_observation" else json.dumps(observation),
                           canonical_hash(observation), row["decision_id"]))
    elif corruption == "initial_memory":
        repo.conn.execute("UPDATE memory_versions SET content='tampered' WHERE rowid=1")
    elif corruption == "winner":
        repo.conn.execute("UPDATE match_settlements SET winner_team='tie' WHERE rowid=1")
    elif corruption == "learning_call":
        repo.conn.execute("UPDATE llm_call_logs SET phase='reflection' WHERE rowid=1")
    elif corruption == "hidden_call":
        repo.conn.execute("UPDATE llm_call_logs SET run_id=NULL WHERE rowid=1")
    elif corruption == "usage_overflow":
        repo.conn.execute("UPDATE llm_call_logs SET prompt_tokens=1000000000 WHERE rowid=1")
    else:
        repo.conn.execute("UPDATE decisions SET seat='X' WHERE rowid=1")
    repo.conn.commit()
    audit, _, _ = audit_memory_matches(repo, run_id)
    assert not audit["complete"], audit
    assert any(expected_issue in issue for issue in audit["issues"]), audit


def test_missing_run_or_evidence_exports_an_explicit_incomplete_report(report_copy, tmp_path):
    repo, run_id = report_copy
    audit, matches, plan = audit_memory_matches(repo, "missing-run")
    assert not audit["complete"] and not matches and not plan
    repo.conn.execute("UPDATE memory_match_evidence SET payload_json='null' WHERE rowid=1")
    repo.conn.commit()
    summary = export_memory_match_report(repo, run_id, tmp_path / "incomplete")
    assert not summary["complete"]
    assert all(arm["matches"] == 0 and arm["win_rate"] is None for arm in summary["arms"].values())


@pytest.mark.parametrize("extra", ["match", "call"])
def test_dedicated_database_rejects_completely_unlinked_extra_work(report_copy, extra):
    repo, run_id = report_copy
    table = "matches" if extra == "match" else "llm_call_logs"
    entry = dict(repo.conn.execute(f"SELECT * FROM {table} LIMIT 1").fetchone())
    entry["id"] = "unaccounted-work"
    if extra == "call":
        entry.update(run_id=None, match_id=None, decision_id=None, table_hand_id=None)
    columns = list(entry)
    repo.conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                      list(entry.values()))
    repo.conn.commit()
    audit, _, _ = audit_memory_matches(repo, run_id)
    assert not audit["complete"]
    assert any("unaccounted matches" in issue if extra == "match" else "unaccounted calls" in issue
               for issue in audit["issues"]), audit
