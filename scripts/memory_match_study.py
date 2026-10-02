"""Prepare, execute, and read-only audit complete frozen-memory matches.

Run with the doudizhu-arena conda Python and -I -B. All outputs must be new.
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
sys.path.insert(0, str(ROOT / "scripts"))
from memory_effect_study import read_json, write_json


def mock_inputs():
    from memory_effect_study import mock_inputs as fixed_inputs
    from arena.evaluation.spec import canonical_hash
    model, artifact = fixed_inputs()
    model.update(config_name="memory-match-mock-v1", model="memory-match-mock-v1")
    model["parameters"]["max_tokens"] = 256
    artifact["artifact_sha256"] = canonical_hash({k: v for k, v in artifact.items() if k != "artifact_sha256"})
    return model, artifact


def prepare(args):
    from arena.evaluation.memory_match_study import make_match_plan
    if args.mock:
        if args.memory_artifact or args.initial_strategy:
            raise ValueError("mock preparation uses the explicitly synthetic memory fixture")
        model, artifact = mock_inputs()
        initial = "先检查手牌与规则，再选择合法叫分或出牌。"
    else:
        if not args.memory_artifact or not args.initial_strategy or args.budget_usd is None:
            raise ValueError("real preparation requires artifact, initial strategy and explicit budget")
        model, artifact = read_json(args.model_json), read_json(args.memory_artifact)
        initial = args.initial_strategy.read_text(encoding="utf-8").strip()
    seeds = read_json(args.test_seed_file)["seeds"] if args.test_seed_file else ["memory-match-test-v1-001"]
    plan = make_match_plan(ROOT, artifact, initial, model, seeds, {
        "memory_slot": args.memory_slot, "total_hands": args.hands, "max_calls": args.max_calls,
        "retry_limit": args.retry_limit, "max_input_tokens": args.max_input_tokens,
        "budget_limit_usd": args.budget_usd if args.budget_usd is not None else 0,
        "call_timeout_seconds": args.call_timeout_seconds, "task_timeout_seconds": args.task_timeout_seconds,
    }, mock=args.mock)
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "plan.json", plan)
    print(json.dumps({"plan_sha256": plan["sha256"], "mock": plan["mock"],
                      "planned_matches": len(seeds) * 6, "maximum_reserved_usd": plan["maximum_reserved_usd"]}))
    return 0


async def run(args):
    from arena.db.repository import DatabaseRepository
    from arena.evaluation.memory_match_study import MemoryMatchRunner, verify_match_plan
    from arena.evaluation.memory_match_report import export_memory_match_report
    from arena.security.credentials import has_usable_credential
    plan = read_json(args.plan / "plan.json")
    verify_match_plan(plan, ROOT)
    if args.mock != plan["mock"]:
        raise ValueError("execution mode differs from frozen plan")
    credential = os.environ.get(args.api_key_env, "") if args.real_models else ""
    if args.real_models and not has_usable_credential(credential):
        raise ValueError("required credential environment variable is unset or unresolved")
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "plan.json", plan)
    repo = DatabaseRepository(str(args.output / "arena.db"))
    repo.init()
    try:
        runner = MemoryMatchRunner(repo, ROOT, plan)
        rid = await runner.run(mock=args.mock, real_models=args.real_models, credential=credential)
        report = export_memory_match_report(repo, rid, args.output / "report", root=ROOT)
        result = {key: report[key] for key in ("run_id", "complete", "physical_calls")}
        write_json(args.output / "result.json", result)
        print(json.dumps(result))
        return 0 if report["complete"] else 1
    finally:
        repo.close()


def audit(args):
    from arena.db.repository import DatabaseRepository
    from arena.evaluation.memory_match_report import export_memory_match_report
    plan = read_json(args.run_dir / "plan.json")
    repo = DatabaseRepository(str(args.run_dir / "arena.db"))
    repo._conn = sqlite3.connect(f"file:{(args.run_dir / 'arena.db').resolve()}?mode=ro", uri=True)
    repo._conn.row_factory = sqlite3.Row
    try:
        runs = list(repo.conn.execute("SELECT id, manifest_json FROM evaluation_runs"))
        if len(runs) != 1 or json.loads(runs[0]["manifest_json"]) != plan:
            raise ValueError("archived plan/database linkage mismatch")
        report = export_memory_match_report(repo, runs[0]["id"], args.output, root=ROOT)
        print(json.dumps({key: report[key] for key in ("complete", "physical_calls", "issues")}))
        return 0 if report["complete"] else 1
    finally:
        repo.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true")
    mode.add_argument("--model-json", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--memory-artifact", type=Path)
    p.add_argument("--initial-strategy", type=Path)
    p.add_argument("--test-seed-file", type=Path)
    p.add_argument("--memory-slot", default="player-0")
    p.add_argument("--hands", type=int, default=1)
    p.add_argument("--max-calls", type=int, default=2000)
    p.add_argument("--retry-limit", type=int, default=1)
    p.add_argument("--max-input-tokens", type=int, default=32768)
    p.add_argument("--call-timeout-seconds", type=float, default=60)
    p.add_argument("--task-timeout-seconds", type=float, default=600)
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
    return prepare(args) if args.command == "prepare" else asyncio.run(run(args)) if args.command == "run" else audit(args)


if __name__ == "__main__":
    raise SystemExit(main())
