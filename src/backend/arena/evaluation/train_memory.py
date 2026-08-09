"""CLI for sequential real-model memory training."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ..config.settings import settings
from ..db.repository import DatabaseRepository
from .memory_training import MemoryTrainingRunner, build_memory_training_plan
from .spec import build_run_manifest, load_experiment_spec


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and freeze LLM agent memory")
    parser.add_argument("--spec", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--real-models", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--output")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[4]
    spec_path = Path(args.spec)
    if not spec_path.exists():
        spec_path = root / args.spec
    if not spec_path.exists():
        spec_path = root / "src/backend" / args.spec
    spec = load_experiment_spec(spec_path)
    manifest = build_run_manifest(spec, root)
    plan = build_memory_training_plan(spec, manifest, root)
    default_output = (
        root / "src/backend/evaluation/memory_artifacts"
        / f"{spec.experiment_id}-{plan.plan_sha256[:12]}.json"
    )
    output = Path(args.output) if args.output else default_output
    if not output.is_absolute():
        output = root / output
    output = output.resolve()
    if root.resolve() not in output.parents:
        raise ValueError("memory artifact output must be inside the project workspace")

    preview = {
        "experiment_id": spec.experiment_id,
        "training_seed_set_id": plan.training_seed_set_id,
        "training_seed_count": len(plan.training_seeds),
        "training_total_hands": plan.total_hands,
        "variant_id": plan.variant_id,
        "plan_sha256": plan.plan_sha256,
        "output": str(output),
    }
    if args.dry_run:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return

    if args.resume and not args.run_id:
        raise ValueError("--resume requires --run-id")
    db_path = str(Path(settings.database_url.replace("sqlite:///", "")))
    repo = DatabaseRepository(db_path)
    repo.init()
    try:
        runner = MemoryTrainingRunner(repo, root)
        run_id = args.run_id if args.resume else runner.create_run(spec, manifest, plan)
        artifact = asyncio.run(
            runner.run(
                run_id, spec, manifest, plan, output,
                real_models=args.real_models,
            )
        )
        print(json.dumps({
            **preview,
            "run_id": run_id,
            "artifact_sha256": artifact["artifact_sha256"],
        }, ensure_ascii=False, indent=2))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
