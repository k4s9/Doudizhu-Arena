"""Re-audit an existing real smoke without credentials, writes to its DB, or API calls.

Run with the doudizhu-arena conda Python and -I -B. The output must be new.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import socket
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/backend'))


def audit(run_dir, output):
    from arena.db.repository import DatabaseRepository
    from arena.evaluation.report import build_report, export_report, metric_group, rows
    from arena.evaluation.spec import _canonical_hash, _dependency_hash, source_sha256

    record = json.loads((run_dir / 'run.json').read_text())
    db_path = (run_dir / 'arena.db').resolve()
    repo = DatabaseRepository(str(db_path))
    # Do not call init(): historical evidence must not be migrated or re-encrypted.
    repo._conn = sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)
    repo.conn.row_factory = sqlite3.Row
    repo.conn.execute('PRAGMA query_only=ON')
    try:
        run_id = record['run_id']
        run = repo.get_evaluation_run(run_id)
        manifest, integrity, summary, _, seeds, failures = build_report(repo, run_id)
        checks = {
            'sqlite_integrity': repo.conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok',
            'sqlite_foreign_keys': not repo.conn.execute('PRAGMA foreign_key_check').fetchall(),
            'finished': run['status'] == 'finished',
            'semantic_audit': integrity['complete'],
            'manifest_digest': _canonical_hash({k: v for k, v in manifest.items()
                                               if k != 'manifest_sha256'}) == manifest['manifest_sha256'],
            'run_manifest_link': record['manifest_sha256'] == run['manifest_sha256'] == manifest['manifest_sha256'],
            'frozen_spec_digest': _canonical_hash(manifest['frozen_spec']) == manifest['spec_sha256'],
            'runtime_source': source_sha256(ROOT) == manifest['source_sha256'],
            'dependency_specs': _dependency_hash(ROOT) == manifest['dependency_sha256'],
            'sdk_versions': all(importlib.metadata.version(
                'anthropic' if m['provider'] == 'claude' else 'openai') == m['sdk_version']
                for m in manifest['models']),
        }
        calls = rows(repo, 'SELECT * FROM llm_call_logs WHERE run_id=?', (run_id,))
        decisions = rows(repo, 'SELECT * FROM decisions WHERE run_id=?', (run_id,))
        events = repo.get_decision_events(run_id=run_id)
        aggregate = metric_group(decisions, calls, events)
        tasks = repo.get_evaluation_tasks(run_id)
        variants = []
        for metrics in seeds:
            matches = [t['match_id'] for t in tasks if t['variant_id'] == metrics['variant']]
            tables = [th for mid in matches for hand in repo.get_hands_for_match(mid)
                      for th in repo.get_table_hands_for_hand(hand['id'])]
            variants.append({**metrics, 'table_status_counts': dict(Counter(t['status'] for t in tables))})

        output.mkdir(parents=True, exist_ok=False)
        export_report(repo, run_id, output / 'report')
        original = run_dir / 'report'
        exported = output / 'report'
        original_files = {p.relative_to(original) for p in original.rglob('*') if p.is_file()}
        exported_files = {p.relative_to(exported) for p in exported.rglob('*') if p.is_file()}
        mismatches = [str(p) for p in original_files ^ exported_files]
        for path in sorted(original_files & exported_files):
            old, new = original / path, exported / path
            if path.suffix == '.json':
                equal = json.loads(old.read_text()) == json.loads(new.read_text())
            elif path.suffix == '.csv':
                with old.open(newline='') as a, new.open(newline='') as b:
                    equal = list(csv.DictReader(a)) == list(csv.DictReader(b))
            else:
                equal = old.read_bytes() == new.read_bytes()
            if not equal:
                mismatches.append(str(path))
        checks['original_report_matches_rebuilt'] = not mismatches
        result = {
            'verified_at': datetime.now(timezone.utc).isoformat(),
            'run_id': run_id,
            'model': manifest['models'][0]['model'],
            'started_at': run['started_at'],
            'finished_at': run['finished_at'],
            'source_sha256': manifest['source_sha256'],
            'manifest_sha256': manifest['manifest_sha256'],
            'checks': checks,
            'complete': all(checks.values()),
            'integrity': integrity,
            'original_report_mismatches': sorted(mismatches),
            'aggregate': aggregate,
            'variants': variants,
            'table_status_counts': summary['table_status_counts'],
            'response_models': dict(Counter(c['response_model'] or '(unavailable)' for c in calls)),
            'provider_errors': dict(Counter(c['error_message'] for c in calls if not c['success'])),
            'validation_error_codes': dict(Counter(e['error_code'] for e in events if e['error_code'])),
            'budget_status_counts': dict(Counter(r['status'] for r in rows(
                repo, 'SELECT status FROM budget_ledger WHERE run_id=?', (run_id,)))),
            'failure_case_count': len(failures),
        }
        return result
    finally:
        repo.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    attempts = []

    def deny(*unused, **kwargs):
        attempts.append(True)
        raise RuntimeError('Network disabled during evidence audit')

    originals = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny
    try:
        result = audit(args.run_dir, args.output)
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = originals
    result['network_connection_attempts'] = len(attempts)
    result['complete'] = result['complete'] and not attempts
    (args.output / 'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    hashes = {str(p.relative_to(args.output)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(args.output.rglob('*')) if p.is_file()}
    (args.output / 'SHA256SUMS.json').write_text(json.dumps(hashes, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('model', 'run_id', 'complete', 'checks',
                                            'network_connection_attempts')}, ensure_ascii=False))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
