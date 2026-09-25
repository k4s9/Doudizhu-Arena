"""Default-safe evaluation CLI: frozen plan, explicit mock or real execution."""
import argparse
import asyncio
import json
from pathlib import Path
from ..config.settings import settings
from ..config.paths import PROJECT_ROOT, project_path
from ..db.repository import DatabaseRepository
from .runner import EvaluationRunner
from .spec import build_run_manifest, load_experiment_spec, load_manifest, ExperimentSpec
from .report import export_report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec',required=True)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run',action='store_true')
    mode.add_argument('--mock',action='store_true')
    mode.add_argument('--real-models',action='store_true')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--run-id')
    parser.add_argument('--db',default=settings.database_path)
    parser.add_argument('--output')
    args=parser.parse_args()
    root=PROJECT_ROOT
    path=Path(args.spec)
    if not path.exists(): path=root/args.spec
    spec=load_experiment_spec(path)
    repo=DatabaseRepository(args.db if args.db == ':memory:' else str(project_path(args.db)));repo.init()
    try:
        manifest=build_run_manifest(spec,root,repo)
        if args.dry_run:
            from dataclasses import asdict
            print(json.dumps(asdict(manifest),ensure_ascii=False,indent=2));return
        runner=EvaluationRunner(repo,str(root))
        if args.resume:
            if not args.run_id: parser.error('--resume requires --run-id')
            run_id=args.run_id
            manifest=load_manifest(json.loads(repo.get_evaluation_run(run_id)['manifest_json']))
            spec=ExperimentSpec.model_validate(manifest.frozen_spec)
        else:
            run_id=runner.create_run(spec,manifest)
        asyncio.run(runner.run(run_id,spec,manifest,real_models=args.real_models,mock=args.mock))
        summary=export_report(repo,run_id,project_path(args.output) if args.output else root/'data/evaluations'/run_id)
        print(json.dumps({'run_id':run_id,'status':repo.get_evaluation_run(run_id)['status'],
                          'integrity_complete':summary['integrity_complete']},indent=2))
        status = repo.get_evaluation_run(run_id)['status']
        return 0 if status == 'finished' and summary['integrity_complete'] else (2 if status == 'cancelled' else 1)
    finally: repo.close()


if __name__=='__main__': raise SystemExit(main())
