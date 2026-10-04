"""Exercise storage failure and process loss through the real match executor.

All providers are local mocks. Child processes inherit the conda interpreter and
use a private database; no application service or user match is interrupted.
"""
import asyncio
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from arena.db.repository import DatabaseRepository
from arena.evaluation.budget import BudgetLedger
from arena.evaluation.mock import ReliabilityMockProvider
from arena.evaluation.report import build_report
from arena.evaluation.runner import EvaluationRunner
from arena.evaluation.spec import PreflightError, build_run_manifest, load_experiment_spec
from arena.llm.base import LLMUsage


ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def repo(tmp_path):
    repository = DatabaseRepository(str(tmp_path / "execution.db"))
    repository.init()
    yield repository
    repository.close()


@pytest.fixture
def spec(tmp_path):
    seed_path = tmp_path / "seeds.json"
    seed_path.write_text(json.dumps({"seed_set_id": "fault-boundaries", "seeds": ["fault-1"]}))
    original = load_experiment_spec(ROOT / "src/backend/evaluation/experiments/mock-v2.yaml")
    model = original.models[0].model_copy(update={"pricing": original.models[0].pricing.model_copy(
        update={"input_per_million": 1, "output_per_million": 1},
    )})
    return original.model_copy(update={
        "seed_set_path": str(seed_path), "variants": original.variants[:1],
        "mock_scenario": "valid_first", "models": [model], "budget_limit_usd": 100,
    })


@pytest.fixture(autouse=True)
def isolated_reports(tmp_path, monkeypatch):
    from arena.evaluation import report

    original = report.export_report
    monkeypatch.setattr(report, "export_report", lambda repository, run_id, output:
                        original(repository, run_id, tmp_path / "reports" / run_id))


@pytest.mark.parametrize("phase", ["bidding", "playing"])
@pytest.mark.parametrize("write", ["thought", "decision"])
def test_action_commit_failure_stops_real_executor(repo, spec, monkeypatch, phase, write):
    """Fail after a real write: SAVEPOINT must undo action, thought and result."""
    manifest = build_run_manifest(spec, ROOT, repo)
    runner = EvaluationRunner(repo, ROOT)
    run_id = runner.create_run(spec, manifest)
    failures = []
    original_thought = repo.add_agent_thought
    original_resolve = repo.resolve_decision

    def record_failure(table_hand_id):
        row = repo.conn.execute(
            "SELECT * FROM decisions WHERE table_hand_id=? AND phase=? ORDER BY started_at DESC LIMIT 1",
            (table_hand_id, phase),
        ).fetchone()
        action_table = "bidding_records" if phase == "bidding" else "play_actions"
        # The fault occurs after the engine and SQLite have both seen the action.
        assert repo.conn.execute(f"SELECT 1 FROM {action_table} WHERE table_hand_id=?", (table_hand_id,)).fetchone()
        failures.append(dict(row))
        raise sqlite3.OperationalError(f"injected {phase} {write} write failure")

    def fail_thought(table_hand_id, **data):
        result = original_thought(table_hand_id, **data)
        if data["phase"] == phase:
            record_failure(table_hand_id)
        return result

    def fail_decision(decision_id, **data):
        result = original_resolve(decision_id, **data)
        decision = repo.conn.execute("SELECT * FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
        if decision["phase"] == phase and data.get("action_id"):
            record_failure(decision["table_hand_id"])
        return result

    monkeypatch.setattr(repo, "add_agent_thought" if write == "thought" else "resolve_decision",
                        fail_thought if write == "thought" else fail_decision)
    asyncio.run(runner.run(run_id, spec, manifest, mock=True))

    assert failures, "the failure must pass through the actual action commit"
    task, = repo.get_evaluation_tasks(run_id)
    assert task["status"] == repo.get_evaluation_run(run_id)["status"] == "failed"
    assert f"injected {phase} {write}" in task["failure_reason"]
    assert not runner.active_matches and not runner._detached
    attempt, = repo.conn.execute("SELECT * FROM task_attempts").fetchall()
    assert attempt["status"] == "failed" and attempt["finished_at"] is not None
    assert repo.get_match(task["match_id"])["status"] == "failed"

    for failed in failures:
        decision = dict(repo.conn.execute("SELECT * FROM decisions WHERE decision_id=?", (failed["decision_id"],)).fetchone())
        assert decision["action_id"] is None and decision["actual_action"] is None
        action_table = "bidding_records" if phase == "bidding" else "play_actions"
        assert not repo.conn.execute(f"SELECT 1 FROM {action_table} WHERE table_hand_id=?",
                                     (failed["table_hand_id"],)).fetchone()
        assert not repo.conn.execute("SELECT 1 FROM agent_thoughts WHERE table_hand_id=? AND phase=?",
                                     (failed["table_hand_id"], phase)).fetchone()
        table_hand = repo.conn.execute("SELECT * FROM table_hands WHERE id=?", (failed["table_hand_id"],)).fetchone()
        assert table_hand["status"] == "failed" and not table_hand["winner_team"]
        events = [json.loads(row[0]) for row in repo.conn.execute("SELECT event_json FROM match_events WHERE match_id=?", (task["match_id"],))]
        success_types = {"bidding_update", "bidding_complete"} if phase == "bidding" else {"card_played", "pass", "hand_ended"}
        assert not any(event["type"] in success_types and event["payload"].get("table") == table_hand["table"] for event in events)
        assert not any(event["type"] == "match_ended" for event in events)
    _, integrity, summary, *_ = build_report(repo, run_id)
    assert integrity["run_status"] == "failed" and summary["task_counts"] == {"failed": 1}


def test_dispatch_evidence_failure_rolls_back_and_never_invokes_provider(repo, spec, monkeypatch):
    """A failed pre-dispatch transaction must neither spend nor fabricate a call."""
    physical_calls = []
    original_generate = ReliabilityMockProvider.generate
    original_bind = BudgetLedger.bind_call

    async def tracked_generate(self, *args):
        physical_calls.append(True)
        return await original_generate(self, *args)

    def fail_after_binding(self, reservation, call_id):
        original_bind(self, reservation, call_id)
        assert self.repo.conn.execute("SELECT 1 FROM call_evidence WHERE call_id=?", (call_id,)).fetchone()
        raise sqlite3.OperationalError("injected dispatch evidence failure")

    monkeypatch.setattr(ReliabilityMockProvider, "generate", tracked_generate)
    monkeypatch.setattr(BudgetLedger, "bind_call", fail_after_binding)
    manifest = build_run_manifest(spec, ROOT, repo)
    runner = EvaluationRunner(repo, ROOT)
    run_id = runner.create_run(spec, manifest)
    asyncio.run(runner.run(run_id, spec, manifest, mock=True))
    assert repo.get_evaluation_run(run_id)["status"] == "failed"
    assert not physical_calls
    for table in ("llm_call_logs", "call_evidence", "budget_ledger", "bidding_records"):
        assert not repo.conn.execute(f"SELECT 1 FROM {table}").fetchone()


def test_cancelled_late_call_keeps_unknown_usage_and_reserved_cost(repo, spec, monkeypatch):
    """Cancellation fences actual LoggingLLMProvider calls before late success."""
    spec = spec.model_copy(update={"cancellation_deadline_seconds": 0.01})
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def late_output(self, *args):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
            self.last_response_model = "late-response-model"
            self.last_system_fingerprint = "late-response-fingerprint"
            self.last_usage = LLMUsage(123, 45, 168)
            return '{"bid":3}'

    monkeypatch.setattr(ReliabilityMockProvider, "generate", late_output)
    manifest = build_run_manifest(spec, ROOT, repo)
    runner = EvaluationRunner(repo, ROOT)
    run_id = runner.create_run(spec, manifest)

    async def scenario():
        job = asyncio.create_task(runner.run(run_id, spec, manifest, mock=True))
        await asyncio.wait_for(entered.wait(), 2)
        runner.cancel()
        await asyncio.wait_for(job, 1)
        before = [tuple(row) for row in repo.conn.execute("SELECT * FROM llm_call_logs ORDER BY id")]
        release.set()
        if runner._detached:
            await asyncio.wait_for(asyncio.gather(*runner._detached, return_exceptions=True), 2)
        assert [tuple(row) for row in repo.conn.execute("SELECT * FROM llm_call_logs ORDER BY id")] == before

    asyncio.run(scenario())
    ledger = repo.conn.execute("SELECT * FROM budget_ledger").fetchall()
    assert len(ledger) == 2
    assert all(row["status"] == "unknown" and row["actual_usd"] is None and row["reserved_usd"] > 0 for row in ledger)
    assert {row["call_id"] for row in ledger} == {row[0] for row in repo.conn.execute("SELECT id FROM llm_call_logs")}
    assert all(tuple(row) == (0, None, None) for row in repo.conn.execute("SELECT success,prompt_tokens,completion_tokens FROM llm_call_logs"))
    assert not repo.conn.execute("SELECT 1 FROM bidding_records").fetchone()
    assert not repo.conn.execute("SELECT 1 FROM decisions WHERE action_id IS NOT NULL").fetchone()
    _, integrity, summary, *_ = build_report(repo, run_id)
    assert integrity["complete"] and integrity["run_status"] == "cancelled"
    assert summary["budget_exposure_usd"] == pytest.approx(sum(row["reserved_usd"] for row in ledger))
    assert summary["cost_coverage"]["numerator"] == 0


_PROCESS_WORKER = r'''
import asyncio
import json
from pathlib import Path
import socket
import sys

root, database, spec_path, ready_path, mode = map(Path, sys.argv[1:])
sys.path.insert(0, str(root / "src/backend"))
from arena.db.repository import DatabaseRepository
from arena.evaluation.mock import ReliabilityMockProvider
from arena.evaluation.report import build_report
from arena.evaluation.runner import EvaluationRunner
from arena.evaluation.spec import build_run_manifest, load_experiment_spec, load_manifest

def deny_network(*args, **kwargs):
    raise AssertionError("process recovery test must stay offline")
socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny_network

repo = DatabaseRepository(str(database))
repo.init()
spec = load_experiment_spec(spec_path)
runner = EvaluationRunner(repo, root)
if str(mode) == "start":
    manifest = build_run_manifest(spec, root, repo)
    run_id = runner.create_run(spec, manifest)
    original_generate = ReliabilityMockProvider.generate

    async def stop_in_provider(self, user_prompt, system_prompt=""):
        finished = repo.conn.execute("SELECT 1 FROM task_attempts WHERE status='finished'").fetchone()
        current = repo.conn.execute("SELECT match_id FROM task_attempts WHERE status='running'").fetchone()
        progressed = current and repo.conn.execute(
            "SELECT 1 FROM play_actions a JOIN table_hands t ON t.id=a.table_hand_id "
            "JOIN hands h ON h.id=t.hand_id WHERE h.match_id=?", (current[0],),
        ).fetchone()
        if finished and progressed:
            ready_path.write_text(json.dumps({"run_id": run_id}))
            await asyncio.Event().wait()
        return await original_generate(self, user_prompt, system_prompt)

    ReliabilityMockProvider.generate = stop_in_provider
else:
    run_id = json.loads(ready_path.read_text())["run_id"]
    manifest = load_manifest(json.loads(repo.get_evaluation_run(run_id)["manifest_json"]))
try:
    asyncio.run(runner.run(run_id, spec, manifest, mock=True))
    integrity = build_report(repo, run_id)[1]
    print(json.dumps({"status": repo.get_evaluation_run(run_id)["status"], "integrity": integrity}))
finally:
    repo.close()
'''


@pytest.mark.slow
def test_terminated_process_recovers_expired_lease_without_replaying_finished_task(tmp_path, spec):
    """Terminate inside a real mock request and restart from durable evidence.

    Wait for the real 30-second executor lease: no fabricated running attempts,
    edited deadlines, or replaced recovery/claim implementation is involved.
    A private source snapshot keeps concurrent workspace edits out of the run.
    """
    snapshot = tmp_path / "source"
    shutil.copytree(ROOT / "src/backend/arena", snapshot / "src/backend/arena",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("src/backend/pyproject.toml", "src/backend/requirements.txt", "src/frontend/package-lock.json"):
        destination = snapshot / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
    seed_path = tmp_path / "process-seeds.json"
    seed_path.write_text(json.dumps({"seed_set_id": "process-fault", "seeds": ["process-1", "process-2"]}))
    spec = spec.model_copy(update={"seed_set_path": str(seed_path)})
    spec_path = tmp_path / "process-spec.json"
    spec_path.write_text(spec.model_dump_json())
    worker_path = tmp_path / "worker.py"
    worker_path.write_text(_PROCESS_WORKER)
    database, ready_path = tmp_path / "process.db", tmp_path / "ready.json"
    arguments = [sys.executable, "-I", "-B", str(worker_path), str(snapshot), str(database), str(spec_path), str(ready_path)]
    child = subprocess.Popen([*arguments, "start"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 15
        while not ready_path.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready_path.exists(), "isolated executor did not reach the provider fault boundary"
        child.terminate()
        stdout, stderr = child.communicate(timeout=5)
        assert child.returncode < 0, stdout + stderr
    finally:
        if child.poll() is None:
            child.terminate()
            child.communicate(timeout=5)

    run_id = json.loads(ready_path.read_text())["run_id"]
    repository = DatabaseRepository(str(database))
    repository.init()
    try:
        original_manifest = repository.get_evaluation_run(run_id)["manifest_json"]
        finished, = repository.conn.execute("SELECT * FROM task_attempts WHERE status='finished'").fetchall()
        finished = dict(finished)
        interrupted, = repository.conn.execute("SELECT * FROM task_attempts WHERE status='running'").fetchall()
        interrupted = dict(interrupted)
        pending_calls = repository.conn.execute(
            "SELECT c.id,e.raw_output,e.system_prompt,e.user_prompt,b.status,b.actual_usd "
            "FROM llm_call_logs c JOIN call_evidence e ON e.call_id=c.id "
            "JOIN budget_ledger b ON b.call_id=c.id WHERE e.provider_status='pending'",
        ).fetchall()
        assert pending_calls
        assert all(row["raw_output"] is None and row["system_prompt"] and row["user_prompt"]
                   and row["status"] == "reserved" and row["actual_usd"] is None for row in pending_calls)
        completed_match = repository.get_match(finished["match_id"])
        completed_events = [tuple(row) for row in repository.conn.execute("SELECT * FROM match_events WHERE match_id=? ORDER BY seq", (finished["match_id"],))]
        old_actions = [tuple(row) for row in repository.conn.execute(
            "SELECT a.* FROM play_actions a JOIN table_hands t ON t.id=a.table_hand_id "
            "JOIN hands h ON h.id=t.hand_id WHERE h.match_id=? ORDER BY a.id", (interrupted["match_id"],),
        )]
        assert old_actions
        # A terminated OS process does not license immediate takeover of its lease.
        with pytest.raises(PreflightError, match="live executor"):
            EvaluationRunner(repository, snapshot)._claim(run_id)
        expires_at = repository.conn.execute("SELECT expires_at FROM evaluation_leases WHERE run_id=?", (run_id,)).fetchone()[0]
        while time.time() <= expires_at:
            time.sleep(min(0.1, max(0.001, expires_at - time.time())))
        result = subprocess.run([*arguments, "resume"], capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stdout + result.stderr
        outcome = json.loads(result.stdout)
        assert outcome["status"] == "finished"
        assert outcome["integrity"]["complete"], outcome["integrity"]["issues"]
        assert repository.get_evaluation_run(run_id)["manifest_json"] == original_manifest
        assert dict(repository.conn.execute("SELECT * FROM task_attempts WHERE id=?", (finished["id"],)).fetchone()) == finished
        assert repository.get_match(finished["match_id"]) == completed_match
        assert [tuple(row) for row in repository.conn.execute("SELECT * FROM match_events WHERE match_id=? ORDER BY seq", (finished["match_id"],))] == completed_events
        old = repository.conn.execute("SELECT * FROM task_attempts WHERE id=?", (interrupted["id"],)).fetchone()
        assert old["status"] == "interrupted" and old["finished_at"] is not None
        assert repository.get_match(interrupted["match_id"])["status"] == "interrupted"
        attempts = repository.conn.execute("SELECT * FROM task_attempts WHERE task_id=? ORDER BY attempt_index", (interrupted["task_id"],)).fetchall()
        assert [(row["attempt_index"], row["status"]) for row in attempts] == [(1, "interrupted"), (2, "finished")]
        assert attempts[1]["match_id"] != interrupted["match_id"]
        assert [tuple(row) for row in repository.conn.execute(
            "SELECT a.* FROM play_actions a JOIN table_hands t ON t.id=a.table_hand_id "
            "JOIN hands h ON h.id=t.hand_id WHERE h.match_id=? ORDER BY a.id", (interrupted["match_id"],),
        )] == old_actions
        assert repository.conn.execute("SELECT COUNT(*) FROM task_attempts").fetchone()[0] == 3
        lost_calls = repository.conn.execute(
            "SELECT c.*,e.provider_status,b.status AS budget_status,b.actual_usd,b.reserved_usd "
            "FROM llm_call_logs c JOIN call_evidence e ON e.call_id=c.id "
            "JOIN budget_ledger b ON b.call_id=c.id WHERE e.provider_status='interrupted'",
        ).fetchall()
        assert lost_calls
        assert {row["id"] for row in lost_calls} == {row["id"] for row in pending_calls}
        assert all(not row["success"] and row["prompt_tokens"] is None and row["completion_tokens"] is None
                   and row["actual_usd"] is None and row["budget_status"] == "unknown" and row["reserved_usd"] > 0
                   for row in lost_calls)
    finally:
        repository.close()
