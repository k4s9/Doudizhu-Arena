"""Read current code; inject faults only into dedicated assessment databases.

Run with the doudizhu-arena conda environment. Never calls a real provider.
"""
import asyncio
import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path('/home/guozy/Doudizhu-Arena')
OUT = Path('/tmp/doudizhu-assessment-20260922')
sys.path.insert(0, str(ROOT / 'src/backend'))
from arena.db.repository import DatabaseRepository
from arena.evaluation.report import build_report
from arena.evaluation.runner import EvaluationRunner
from arena.evaluation.spec import build_run_manifest, load_experiment_spec, validate_execution

results = {}
base = DatabaseRepository(str(OUT / 'mock.db'))
base.init()
run_id = base.conn.execute('SELECT id FROM evaluation_runs ORDER BY rowid DESC LIMIT 1').fetchone()[0]
baseline = build_report(base, run_id)
results['baseline'] = {
    'run_id': run_id, 'integrity': baseline[1]['complete'],
    'task_counts': baseline[2]['task_counts'],
    'tables': baseline[2]['table_status_counts'],
    'calls': sum(m['calls'] for m in baseline[3]),
}

def copy_db(name):
    repo = DatabaseRepository(str(OUT / (name + '.db')))
    repo.init()
    base.conn.backup(repo.conn)
    return repo

repo = copy_db('resolution-fault')
d = dict(repo.conn.execute("SELECT * FROM decisions WHERE resolution='system_fallback' LIMIT 1").fetchone())
invalid = repo.conn.execute('SELECT COUNT(*) FROM decision_events WHERE decision_id=? AND is_illegal=1', (d['decision_id'],)).fetchone()[0]
repo.conn.execute("UPDATE decisions SET resolution='model_first' WHERE decision_id=?", (d['decision_id'],))
repo.conn.commit()
report = build_report(repo, run_id)
results['fallback_mislabeled_as_success'] = {
    'decision_id': d['decision_id'], 'invalid_validations': invalid,
    'integrity_after_fault': report[1]['complete'], 'issues': report[1]['issues'],
    'model_successes_before': sum(m['model_success']['numerator'] for m in baseline[3]),
    'model_successes_after': sum(m['model_success']['numerator'] for m in report[3]),
}
repo.close()

repo = copy_db('terminal-fault')
th = repo.conn.execute("SELECT id FROM table_hands WHERE status='finished' LIMIT 1").fetchone()[0]
repo.conn.execute("UPDATE table_hands SET winner_team='invalid-team', final_score=99999 WHERE id=?", (th,))
repo.conn.execute("UPDATE matches SET score_red=99999, score_blue=99999 WHERE id=(SELECT match_id FROM hands WHERE id=(SELECT hand_id FROM table_hands WHERE id=?))", (th,))
repo.conn.commit()
report = build_report(repo, run_id)
results['corrupted_terminal_result'] = {'integrity_after_fault': report[1]['complete'], 'issues': report[1]['issues']}
repo.close()

repo = copy_db('ledger-fault')
repo.conn.execute("UPDATE budget_ledger SET call_id='nonexistent-call' WHERE reservation_id=(SELECT reservation_id FROM budget_ledger LIMIT 1)")
repo.conn.commit()
report = build_report(repo, run_id)
results['orphan_budget_settlement'] = {'integrity_after_fault': report[1]['complete'], 'cost_coverage': report[2]['cost_coverage']}
repo.close()

from fastapi import FastAPI
from starlette.websockets import WebSocket
from arena.api.ws import match_ws
app = FastAPI()
app.state.db_repo = base
app.state.active_matches = {}
match_id = base.conn.execute("SELECT id FROM matches WHERE status='finished' LIMIT 1").fetchone()[0]
async def websocket_probe():
    incoming = iter([{'type': 'websocket.connect'}, {'type': 'websocket.disconnect', 'code': 1000}])
    outgoing = []
    async def receive():
        return next(incoming)
    async def send(message):
        outgoing.append(message)
    ws = WebSocket({'type': 'websocket', 'app': app, 'path': '/ws/match/' + match_id}, receive, send)
    await asyncio.wait_for(match_ws(ws, match_id), 2)
    event = json.loads(next(m['text'] for m in outgoing if m['type'] == 'websocket.send'))
    results['reconnect_after_finish'] = {'first_event': event, 'has_authoritative_state': 'tables' in event.get('payload', {})}
asyncio.run(websocket_probe())

spec = load_experiment_spec(ROOT / 'src/backend/evaluation/experiments/mock-v2.yaml')
manifest = build_run_manifest(spec, ROOT, base)
drifted = replace(manifest, dependency_sha256='different-dependencies',
                  models=(replace(manifest.models[0], sdk_version='different-sdk'),))
try:
    validate_execution(spec, drifted, ROOT, base, mock=True)
    results['dependency_and_sdk_drift'] = {'rejected': False}
except ValueError as exc:
    results['dependency_and_sdk_drift'] = {'rejected': True, 'error': str(exc)}

async def probe_cancel():
    from arena.evaluation.mock import ReliabilityMockProvider
    repo = DatabaseRepository(str(OUT / 'cancel.db'))
    repo.init()
    cancel_spec = spec.model_copy(update={'cancellation_deadline_seconds': 0.05})
    cancel_manifest = build_run_manifest(cancel_spec, ROOT, repo)
    runner = EvaluationRunner(repo, str(ROOT))
    rid = runner.create_run(cancel_spec, cancel_manifest)
    entered = asyncio.Event()
    async def slow_cleanup(self, *args):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.25)
            raise
    with patch.object(ReliabilityMockProvider, 'generate', slow_cleanup):
        job = asyncio.create_task(runner.run(rid, cancel_spec, cancel_manifest, mock=True))
        await asyncio.wait_for(entered.wait(), 2)
        start = time.monotonic()
        runner.cancel()
        done, pending = await asyncio.wait({job}, timeout=cancel_spec.cancellation_deadline_seconds)
        at_deadline = {'finished_by_deadline': bool(done), 'status_at_deadline': repo.get_evaluation_run(rid)['status']}
        await asyncio.wait_for(job, 3)
        results['cancellation_deadline'] = {
            'configured_seconds': cancel_spec.cancellation_deadline_seconds,
            **at_deadline, 'actual_seconds': time.monotonic() - start,
            'eventual_status': repo.get_evaluation_run(rid)['status'],
        }
    repo.close()

asyncio.run(probe_cancel())
base.close()
(OUT / 'boundary-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
print(json.dumps(results, ensure_ascii=False, indent=2))
