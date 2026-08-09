"""Command-line entry point for deterministic evaluation runs."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ..config.settings import settings
from ..db.repository import DatabaseRepository
from .runner import EvaluationRunner
from .spec import build_run_manifest, load_experiment_spec


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan or run an LLM reliability evaluation")
    parser.add_argument("--spec", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--real-models", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-id")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[4]
    spec_path = Path(args.spec)
    if not spec_path.exists():
        spec_path = root / args.spec
    if not spec_path.exists():
        spec_path = root / "src/backend" / args.spec
    spec = load_experiment_spec(spec_path)
    manifest = build_run_manifest(spec, root)
    task_count = len(manifest.seeds) * len(manifest.variants) * spec.seat_rotations
    if args.dry_run:
        print(json.dumps({
            "experiment_id": spec.experiment_id, "task_count": task_count,
            "seed_count": len(manifest.seeds), "variants": [v["variant_id"] for v in manifest.variants],
            "manifest_sha256": manifest.manifest_sha256,
        }, ensure_ascii=False, indent=2))
        return

    db_path = str(Path(settings.database_url.replace("sqlite:///", "")))
    repo = DatabaseRepository(db_path)
    repo.init()
    try:
        runner = EvaluationRunner(repo, str(root))
        run_id = args.run_id if args.resume and args.run_id else runner.create_run(spec, manifest)
        asyncio.run(runner.run(run_id, spec, manifest, real_models=True))
        print(json.dumps({"run_id": run_id, "status": repo.get_evaluation_run(run_id)["status"]}))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
