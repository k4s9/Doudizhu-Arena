"""Prepare, run, and audit an independent three-arm frozen-memory comparison.

Use the doudizhu-arena conda Python with -I -B. Prepare/audit never call a model;
run requires --mock or --real-models. All output directories must be new.
"""
import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/backend"))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def mock_inputs():
    from arena.evaluation.spec import canonical_hash
    model = {"config_name": "memory-study-mock", "provider": "mock", "model": "memory-study-mock",
             "base_url": "mock://local", "system_prompt": "", "parameters": {"max_tokens": 128, "temperature": 0, "top_p": None, "seed": 7},
             "pricing": {"input_per_million": 0, "output_per_million": 0, "currency": "USD", "source": "synthetic offline fixture", "effective_date": "2026-10-01"}}
    seeds = ["memory-train-fixture-v1"]
    artifact = {"schema_version": 1, "provenance": "mock", "memory_artifact_id": "synthetic-memory-v1",
                "training_run_id": "synthetic-no-training-run", "training_plan_sha256": canonical_hash("synthetic-fixture"),
                "training_seed_set_id": "synthetic-training-v1", "training_seeds": seeds,
                "training_seed_set_sha256": canonical_hash({"seed_set_id": "synthetic-training-v1", "seeds": seeds}),
                "source_revision": "synthetic", "source_sha256": canonical_hash("synthetic"),
                "dependency_sha256": canonical_hash("synthetic"),
                "model_snapshot": model, "memories": {"player-0": "合成测试经验：检查当前手牌和跟牌规则。这不是训练所得经验。"}}
    artifact["artifact_sha256"] = canonical_hash(artifact)
    return model, artifact


def prepare(args):
    from arena.evaluation.fixed_corpus import build_corpus
    from arena.evaluation.memory_study import MemoryProtocol, make_plan
    if args.mock:
        if args.memory_artifact or args.initial_strategy:
            raise ValueError("mock preparation uses an explicitly synthetic artifact and control")
        model, artifact = mock_inputs()
        initial = "先检查手牌与规则，再选择合法叫分或出牌。"
    else:
        if not args.memory_artifact or not args.initial_strategy or args.budget_usd is None:
            raise ValueError("real preparation requires --memory-artifact, --initial-strategy and --budget-usd")
        model, artifact = read_json(args.model_json), read_json(args.memory_artifact)
        initial = args.initial_strategy.read_text(encoding="utf-8").strip()
    test = read_json(args.test_seed_file)["seeds"] if args.test_seed_file else [
        "memory-test-v1-001", "memory-test-v1-002"]
    corpus = build_corpus(["memory-development-v1-001"], test)
    protocol = MemoryProtocol(memory_slot=args.memory_slot, retry_limit=args.retry_limit,
                              max_input_tokens=args.max_input_tokens,
                              budget_limit_usd=args.budget_usd if args.budget_usd is not None else 0,
                              call_timeout_seconds=args.call_timeout_seconds)
    plan = make_plan(ROOT, corpus, artifact, initial, model, protocol.model_dump(mode="json"), mock=args.mock)
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "plan.json", plan)
    print(json.dumps({"plan_sha256": plan["sha256"], "mock": plan["mock"], "max_calls": plan["max_calls"],
                      "maximum_reserved_usd": plan["maximum_reserved_usd"], "test_seeds": test}))
    return 0


async def run(args):
    from arena.db.repository import DatabaseRepository
    from arena.evaluation.memory_study import MemoryStudyRunner, verify_plan
    from arena.evaluation.memory_study_report import export_memory_report
    plan = read_json(args.plan / "plan.json")
    verify_plan(plan, ROOT)
    if args.mock != plan["mock"]:
        raise ValueError("execution mode differs from frozen plan")
    credential = os.environ.get(args.api_key_env, "") if args.real_models else ""
    if args.real_models:
        from arena.security.credentials import has_usable_credential
        if not has_usable_credential(credential):
            raise ValueError("required credential environment variable is unset or unresolved")
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "plan.json", plan)
    repo = DatabaseRepository(str(args.output / "arena.db"))
    repo.init()
    try:
        runner = MemoryStudyRunner(repo, ROOT, plan)
        run_id = await runner.run(mock=args.mock, real_models=args.real_models, credential=credential)
        report = export_memory_report(repo, run_id, args.output / "report", root=ROOT)
        result = {"run_id": run_id, "complete": report["complete"], "provenance": report["provenance"], "physical_calls": report["physical_calls"]}
        write_json(args.output / "result.json", result)
        print(json.dumps(result))
        return 0 if report["complete"] else 1
    finally:
        repo.close()


def audit(args):
    from arena.db.repository import DatabaseRepository
    from arena.evaluation.memory_study_report import export_memory_report
    plan = read_json(args.run_dir / "plan.json")
    repo = DatabaseRepository(str(args.run_dir / "arena.db"))
    repo._conn = sqlite3.connect(f"file:{(args.run_dir / 'arena.db').resolve()}?mode=ro", uri=True)
    repo._conn.row_factory = sqlite3.Row
    try:
        runs = list(repo.conn.execute("SELECT id,manifest_json FROM evaluation_runs"))
        if len(runs) != 1 or json.loads(runs[0]["manifest_json"]) != plan:
            raise ValueError("archived plan/database linkage mismatch")
        report = export_memory_report(repo, runs[0]["id"], args.output, root=ROOT)
        print(json.dumps({"complete": report["complete"], "physical_calls": report["physical_calls"], "issues": report["issues"]}))
        return 0 if report["complete"] else 1
    finally:
        repo.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true")
    mode.add_argument("--model-json", type=Path, help="ModelSpec JSON without credentials")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--memory-artifact", type=Path)
    p.add_argument("--initial-strategy", type=Path)
    p.add_argument("--test-seed-file", type=Path)
    p.add_argument("--memory-slot", default="player-0")
    p.add_argument("--retry-limit", type=int, default=1)
    p.add_argument("--max-input-tokens", type=int, default=32768)
    p.add_argument("--call-timeout-seconds", type=float, default=60)
    p.add_argument("--budget-usd", type=float)
    r = commands.add_parser("run")
    r.add_argument("--plan", type=Path, required=True)
    r.add_argument("--output", type=Path, required=True)
    mode = r.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true")
    mode.add_argument("--real-models", action="store_true")
    r.add_argument("--api-key-env", default="DDZ_MEMORY_EVAL_API_KEY")
    a = commands.add_parser("audit")
    a.add_argument("--run-dir", type=Path, required=True)
    a.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    if args.command == "prepare":
        return prepare(args)
    if args.command == "run":
        return asyncio.run(run(args))
    return audit(args)


if __name__ == "__main__":
    raise SystemExit(main())
