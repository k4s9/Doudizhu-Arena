"""Fault injection and public workflow regressions for the second audit round."""
import asyncio
import json
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from arena.api.routes.evaluation import _preflight, get_template
from arena.api.ws import build_persisted_match_state, match_ws
from arena.db.repository import DatabaseRepository
from arena.evaluation.mock import ReliabilityMockProvider
from arena.evaluation.report import build_report
from arena.evaluation.runner import EvaluationRunner
from arena.evaluation.spec import ExperimentSpec, PreflightError, build_run_manifest, load_experiment_spec, validate_execution

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope='module')
def completed_db(tmp_path_factory):
    path = tmp_path_factory.mktemp('complete') / 'baseline.db'
    repo = DatabaseRepository(str(path)); repo.init()
    spec = load_experiment_spec(ROOT / 'src/backend/evaluation/experiments/mock-v2.yaml')
    manifest = build_run_manifest(spec, ROOT, repo)
    runner = EvaluationRunner(repo, ROOT)
    rid = runner.create_run(spec, manifest)
    asyncio.run(runner.run(rid, spec, manifest, mock=True))
    assert build_report(repo, rid)[1]['complete']
    repo.close()
    return path, rid


@pytest.fixture
def evidence(tmp_path, completed_db):
    path, rid = completed_db
    repo = DatabaseRepository(str(tmp_path / 'fault.db')); repo.init()
    source = sqlite3.connect(path)
    source.backup(repo.conn); source.close()
    yield repo, rid
    repo.close()


@pytest.mark.parametrize('fault, expected', [
    ("UPDATE decisions SET resolution='model_first' WHERE decision_id=(SELECT decision_id FROM decisions WHERE resolution='system_fallback' LIMIT 1)", 'model resolution'),
    ("UPDATE call_evidence SET raw_output='changed' WHERE call_id=(SELECT id FROM llm_call_logs WHERE success=1 LIMIT 1)", 'output hash'),
    ("UPDATE table_hands SET winner_team='invalid',final_score=99999 WHERE status='finished'", 'terminal score'),
    ("UPDATE hands SET diff_score_red=99999", 'hand score'),
    ("UPDATE matches SET score_blue=99999", 'match score'),
    ("UPDATE budget_ledger SET call_id='orphan' WHERE reservation_id=(SELECT reservation_id FROM budget_ledger LIMIT 1)", 'budget'),
    ("UPDATE budget_ledger SET actual_usd=123 WHERE reservation_id=(SELECT reservation_id FROM budget_ledger LIMIT 1)", 'budget amount'),
])
def test_faults_fail_integrity(evidence, fault, expected):
    repo, rid = evidence
    before = sum(m['model_success']['numerator'] for m in build_report(repo, rid)[3])
    repo.conn.execute(fault); repo.conn.commit()
    _, integrity, _, metrics, _, _ = build_report(repo, rid)
    assert not integrity['complete']
    assert any(expected in issue for issue in integrity['issues'])
    if "resolution='model_first'" in fault:
        assert sum(m['model_success']['numerator'] for m in metrics) == before


def test_finished_websocket_returns_authoritative_snapshot(evidence):
    from types import SimpleNamespace
    from starlette.websockets import WebSocket
    repo, rid = evidence
    mid = repo.get_evaluation_tasks(rid)[0]['match_id']
    expected = build_persisted_match_state(repo, repo.get_match(mid))
    async def run():
        incoming = iter([{'type': 'websocket.connect'}, {'type': 'websocket.disconnect', 'code': 1000}])
        outgoing = []
        async def receive(): return next(incoming)
        async def send(message): outgoing.append(message)
        app = SimpleNamespace(state=SimpleNamespace(db_repo=repo, active_matches={}))
        ws = WebSocket({'type': 'websocket', 'app': app}, receive, send)
        await asyncio.wait_for(match_ws(ws, mid), 1)
        return json.loads(next(m['text'] for m in outgoing if m['type'] == 'websocket.send'))
    event = asyncio.run(run())
    assert event['type'] == 'match_state' and event['payload'] == expected
    assert expected['status'] == 'finished' and expected['watermark'] > 0
    assert set(expected['tables']) == {'A', 'B'}
    assert all(t['phase'] in ('finished', 'void') and t['turn_timer'] is None for t in expected['tables'].values())


@pytest.mark.parametrize('delay', [0.001, 0.2])
def test_cancel_discards_late_results_and_bounds_run(tmp_path, monkeypatch, delay):
    repo = DatabaseRepository(str(tmp_path / 'cancel.db')); repo.init()
    spec = load_experiment_spec(ROOT / 'src/backend/evaluation/experiments/mock-v2.yaml').model_copy(update={'cancellation_deadline_seconds': 0.02})
    entered = asyncio.Event()
    async def late_output(self, *args):
        entered.set()
        try: await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(delay)
            return '{"bid":3}'
    monkeypatch.setattr(ReliabilityMockProvider, 'generate', late_output)
    manifest = build_run_manifest(spec, ROOT, repo)
    runner = EvaluationRunner(repo, ROOT)
    rid = runner.create_run(spec, manifest)
    async def run():
        job = asyncio.create_task(runner.run(rid, spec, manifest, mock=True))
        await asyncio.wait_for(entered.wait(), 2)
        started = time.monotonic(); runner.cancel()
        await asyncio.wait_for(job, 0.15)
        assert time.monotonic() - started < 0.15
        assert repo.get_evaluation_run(rid)['status'] == 'cancelled'
        count = repo.conn.execute('SELECT COUNT(*) FROM llm_call_logs').fetchone()[0]
        if runner._detached:
            await asyncio.wait(list(runner._detached), timeout=0.5)
        assert repo.conn.execute('SELECT COUNT(*) FROM llm_call_logs').fetchone()[0] == count == 2
        assert repo.conn.execute('SELECT COUNT(*) FROM bidding_records').fetchone()[0] == 0
        assert not runner.active_matches
        assert build_report(repo, rid)[1]['complete']
    try: asyncio.run(run())
    finally: repo.close()


def test_template_and_preflight_use_current_protocol(tmp_path):
    repo = DatabaseRepository(str(tmp_path / 'api.db')); repo.init()
    spec = ExperimentSpec.model_validate(asyncio.run(get_template('mock')))
    manifest, errors = _preflight(repo, spec)
    assert not errors and len(manifest.seeds) == 2
    assert [v.variant_id for v in spec.variants] == ['single_generation', 'generic_retry', 'rule_feedback']
    assert [v.retry_limit for v in spec.variants] == [0, 2, 2]
    changed = replace(manifest, dependency_sha256='changed')
    with pytest.raises(PreflightError, match='manifest hash'):
        validate_execution(spec, changed, ROOT, repo, mock=True)
    repo.close()


@pytest.mark.parametrize('scenario', ['valid_first', 'play_error'])
def test_mock_covers_playing_for_all_variants(tmp_path, scenario):
    repo = DatabaseRepository(str(tmp_path / 'coverage.db')); repo.init()
    spec = load_experiment_spec(ROOT / 'src/backend/evaluation/experiments/mock-v2.yaml').model_copy(update={'mock_scenario': scenario})
    manifest = build_run_manifest(spec, ROOT, repo)
    runner = EvaluationRunner(repo, ROOT); rid = runner.create_run(spec, manifest)
    asyncio.run(runner.run(rid, spec, manifest, mock=True))
    _, integrity, _, metrics, _, _ = build_report(repo, rid)
    assert integrity['complete'], integrity['issues']
    assert repo.get_evaluation_run(rid)['status'] == 'finished'
    assert all(m['L'] > 0 for m in metrics if m['phase'] == 'playing')
    if scenario == 'valid_first':
        assert {r[0] for r in repo.conn.execute('SELECT DISTINCT resolution FROM decisions')} == {'model_first'}
    else:
        for variant, resolution in [('single_generation', 'system_fallback'), ('generic_retry', 'model_retry'), ('rule_feedback', 'model_retry')]:
            assert repo.conn.execute('SELECT COUNT(*) FROM decisions WHERE phase=? AND variant_id=? AND resolution=?',
                ('playing', variant, resolution)).fetchone()[0] > 0
    repo.close()


@pytest.mark.parametrize('max_calls, expected_code, expected_status', [(1, 1, 'failed'), (2000, 0, 'finished')])
def test_cli_exit_matches_run_outcome(tmp_path, max_calls, expected_code, expected_status):
    spec = load_experiment_spec(ROOT / 'src/backend/evaluation/experiments/mock-v2.yaml').model_copy(update={'max_calls': max_calls})
    path = tmp_path / 'spec.json'
    path.write_text(spec.model_dump_json())
    result = subprocess.run([sys.executable, '-I', '-B', '-m', 'arena.evaluation.run',
        '--spec', str(path), '--mock', '--db', str(tmp_path / 'cli.db'),
        '--output', str(tmp_path / 'report')], capture_output=True, text=True, timeout=60)
    assert result.returncode == expected_code, result.stdout + result.stderr
    assert json.loads(result.stdout)['status'] == expected_status
    assert json.loads(result.stdout)['integrity_complete']
