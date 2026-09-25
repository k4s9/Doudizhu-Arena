"""Run an explicitly supplied real-model spec in a new, isolated evidence directory.

Uses agents.yaml credentials, the production runner, its call ledger, semantic
audits and report exporter. Run with the doudizhu-arena conda Python and -I -B.
"""
import argparse
import asyncio
import json
import logging
from pathlib import Path
import signal
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/backend'))


async def run(args):
    from arena.config.settings import settings
    from arena.agent.loader import load_agents_from_yaml
    from arena.db.repository import DatabaseRepository
    from arena.evaluation.runner import EvaluationRunner
    from arena.evaluation.spec import build_run_manifest, load_experiment_spec, validate_execution
    from arena.evaluation.report import export_report

    spec = load_experiment_spec(args.spec)
    args.output.mkdir(parents=True, exist_ok=False)
    repo = DatabaseRepository(str(args.output / 'arena.db'))
    repo.init()
    try:
        load_agents_from_yaml(repo, settings.agents_yaml_path)
        manifest = build_run_manifest(spec, ROOT, repo)
        validate_execution(spec, manifest, ROOT, repo)
        runner = EvaluationRunner(repo, str(ROOT))
        run_id = runner.create_run(spec, manifest)
        (args.output / 'run.json').write_text(json.dumps({
            'run_id': run_id, 'spec': str(args.spec.resolve()),
            'model': manifest.models[0].model, 'manifest_sha256': manifest.manifest_sha256,
        }, indent=2) + '\n')
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, runner.cancel)
        worker = asyncio.create_task(runner.run(run_id, spec, manifest, real_models=True))
        while True:
            done, _ = await asyncio.wait({worker}, timeout=30)
            calls = repo.conn.execute('SELECT COUNT(*) FROM llm_call_logs WHERE run_id=?', (run_id,)).fetchone()[0]
            tasks = [{k: task[k] for k in ('variant_id', 'status')} for task in repo.get_evaluation_tasks(run_id)]
            print(json.dumps({'model': manifest.models[0].model, 'run_id': run_id,
                              'calls': calls, 'tasks': tasks}), flush=True)
            if done:
                break
        await worker
        summary = export_report(repo, run_id, args.output / 'report')
        status = repo.get_evaluation_run(run_id)['status']
        print(json.dumps({'status': status, 'integrity_complete': summary['integrity_complete'],
                          'report': str(args.output / 'report')}), flush=True)
        return 0 if status == 'finished' and summary['integrity_complete'] else 1
    finally:
        repo.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    raise SystemExit(asyncio.run(run(args)))
