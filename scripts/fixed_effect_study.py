"""Prepare, execute, or independently audit a frozen shared-first effect study.

Use the doudizhu-arena conda Python with -I -B. Every output directory must be new.
The prepare and audit subcommands never make provider calls; run requires an
explicit --real or --mock flag. Each model has its own persistent call ledger.
"""
import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
import logging
from pathlib import Path
import re
import signal
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/backend"))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def seed_namespace(value):
    # Leave room for "fixed-effect-minimax-" and run_one's "-development";
    # the full experiment_id must fit its 64-character specification limit.
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,30}", value):
        raise argparse.ArgumentTypeError("seed namespace must be 1-31 lowercase ASCII letters, digits, '_' or '-'")
    return value


def planned_seeds(split, count, namespace=None):
    if split not in ("development", "test") or count <= 0:
        raise ValueError("a valid split and positive seed count are required")
    if namespace is None:
        # Preserve the exact original seed IDs for archived-study reproduction.
        prefix = "effect-dev-20260926" if split == "development" else "effect-test-20260926"
    else:
        prefix = f"{seed_namespace(namespace)}-{split}"
    return [f"{prefix}-{i:02d}" for i in range(count)]


def prepare(args):
    import yaml
    from arena.evaluation.fixed_corpus import build_corpus, verify_corpus
    from arena.evaluation.fixed_study import FixedProtocol
    namespace = getattr(args, "seed_namespace", None)
    dev = planned_seeds("development", args.development_seeds, namespace)
    test = planned_seeds("test", args.test_seeds, namespace)
    corpus = build_corpus(dev, test)
    verification = verify_corpus(corpus)
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "corpus.json", corpus)
    for split, seeds in (("development", dev), ("test", test)):
        seed_set_id = f"{namespace}-{split}" if namespace else f"effect-{split}-20260926"
        write_json(args.output / f"seeds-{split}.json", {"seed_set_id": seed_set_id, "seeds": seeds})
    for name in ("minimax", "qwen"):
        path = ROOT / f"src/backend/evaluation/experiments/uni-api-{name}-smoke-20260924.yaml"
        spec = yaml.safe_load(path.read_text())
        spec["experiment_id"] = f"fixed-effect-{name}-{namespace or '20260926'}"
        spec["seed_set_path"] = str((args.output / "seeds-test.json").resolve())
        spec["max_calls"] = len(test) * 8 * 5
        spec["models"][0]["system_prompt"] = ""  # Identical corpus prompts across models.
        (args.output / f"{name}.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
    write_json(args.output / "protocol.json", FixedProtocol().model_dump(mode="json"))
    plan = {"kind": "preregistered-fixed-effect-study", "corpus_sha256": corpus["sha256"],
            "primary_model": "minimax", "replication_model": "qwen", "primary_phase": "playing",
            "primary_contrast": "rule_feedback minus generic_retry success rate",
            "selection": "all frozen test observations, no selection by model errors, no optional stopping or prompt tuning on test",
            "development_use": "offline protocol development; development/test seed IDs are disjoint",
            "seed_namespace": namespace,
            "test_split_status": "declared split only; generation does not certify absence of prior development use",
            "max_calls_per_model": len(test) * 8 * 5, "max_inflight_across_two_models": 4,
            "pricing_basis": "existing user-confirmed free Uni API account from 2026-09-24; retained price source/effective date",
            "inference": "95% paired source-seed cluster bootstrap; Minimax primary, Qwen descriptive replication; no joint significance claim",
            "shared_first": "one common generation, then up to two retries per branch on failures; provider failures receive generic feedback in both arms",
            "interpretation": "legality/availability on this fixed rule-policy corpus, not win rate or representative player skill",
            "verification": verification}
    write_json(args.output / "plan.json", plan)
    print(json.dumps(plan, ensure_ascii=False))


async def run_one(args, name, corpus, protocol, controllers):
    from arena.config.settings import settings
    from arena.agent.loader import load_agents_from_yaml
    from arena.db.repository import DatabaseRepository
    from arena.evaluation.fixed_study import FixedStudyRunner
    from arena.evaluation.fixed_mock import FixedStudyMockProvider
    from arena.evaluation.spec import build_run_manifest, load_experiment_spec, validate_execution
    from arena.evaluation.fixed_report import export_fixed_report
    out = args.output / name
    out.mkdir()
    repo = DatabaseRepository(str(out / "arena.db"))
    repo.init()
    try:
        spec = load_experiment_spec(args.plan / f"{name}.yaml")
        spec.seed_set_path = str((args.plan / f"seeds-{args.split}.json").resolve())
        spec.max_calls = sum(i["split"] == args.split for i in corpus["observations"]) * 5
        spec.experiment_id += f"-{args.split}"
        if args.mock:
            spec.models[0].provider = "mock"
            spec.models[0].model = "fixed-study-mock"
            spec.models[0].base_url = "mock://local"
        else:
            load_agents_from_yaml(repo, settings.agents_yaml_path)
        manifest = build_run_manifest(spec, ROOT, repo)
        validate_execution(spec, manifest, ROOT, repo, mock=args.mock)
        runner = FixedStudyRunner(repo, ROOT, spec, manifest, corpus, protocol,
                                  provider_factory=FixedStudyMockProvider if args.mock else None)
        controllers.append(runner)
        write_json(out / "manifest.json", runner.manifest)
        worker = asyncio.create_task(runner.run(real_models=args.real, mock=args.mock))
        while True:
            done, _ = await asyncio.wait({worker}, timeout=30)
            if runner.run_id:
                progress = {"model": name, "run_id": runner.run_id,
                    "calls": repo.conn.execute("SELECT COUNT(*) FROM llm_call_logs WHERE run_id=?", (runner.run_id,)).fetchone()[0],
                    "states": dict(repo.conn.execute("SELECT status,COUNT(*) FROM fixed_items WHERE run_id=? GROUP BY status", (runner.run_id,)).fetchall())}
                with (out / "progress.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(progress) + "\n")
                print(json.dumps(progress), flush=True)
            if done:
                break
        run_id = await worker
        summary = export_fixed_report(repo, run_id, corpus, out / "report", root=ROOT)
        write_json(out / "result.json", {"run_id": run_id, "complete": summary["complete"], "physical_calls": summary["physical_calls"]})
        return {"model": name, "complete": summary["complete"], "calls": summary["physical_calls"]}
    except Exception as exc:
        write_json(out / "failure.json", {"error": f"{type(exc).__name__}: {exc}"})
        raise
    finally:
        repo.close()


async def execute(args):
    from arena.evaluation.fixed_study import FixedProtocol
    from arena.evaluation.fixed_corpus import verify_corpus
    corpus = json.loads((args.plan / "corpus.json").read_text())
    verify_corpus(corpus)
    protocol_raw = json.loads((args.plan / "protocol.json").read_text())
    protocol_raw["split"] = args.split
    protocol = FixedProtocol.model_validate(protocol_raw)
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "corpus.json", corpus)
    write_json(args.output / "plan.json", json.loads((args.plan / "plan.json").read_text()))
    # Archive exact implementation as well as hashes; never include credentials.
    paths = sorted((ROOT / "src/backend/arena").rglob("*.py")) + [Path(__file__).resolve()]
    source = {str(p.relative_to(ROOT)): p.read_text(encoding="utf-8") for p in paths}
    write_json(args.output / "source-snapshot.json", source)
    write_json(args.output / "source-file-sha256.json", {k: hashlib.sha256(v.encode()).hexdigest() for k, v in source.items()})
    controllers = []
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: [r.cancel() for r in controllers])
    names = ("minimax", "qwen") if args.model == "all" else (args.model,)
    results = await asyncio.gather(*(run_one(args, name, corpus, protocol, controllers) for name in names), return_exceptions=True)
    summary = [r if not isinstance(r, BaseException) else {"complete": False, "error": str(r)} for r in results]
    write_json(args.output / "result.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if all(r.get("complete") for r in summary) else 1


def audit(args):
    from arena.db.repository import DatabaseRepository
    from arena.evaluation.fixed_report import export_fixed_report
    corpus = json.loads((args.run_dir.parent / "corpus.json").read_text())
    repo = DatabaseRepository(str(args.run_dir / "arena.db"))
    repo._conn = sqlite3.connect(f"file:{(args.run_dir / 'arena.db').resolve()}?mode=ro", uri=True)
    repo._conn.row_factory = sqlite3.Row
    try:
        run_id = repo.conn.execute("SELECT id FROM evaluation_runs").fetchone()[0]
        frozen = json.loads((args.run_dir / "manifest.json").read_text())
        stored = json.loads(repo.get_evaluation_run(run_id)["manifest_json"])
        if frozen != stored:
            raise ValueError("archived manifest differs from database")
        summary = export_fixed_report(repo, run_id, corpus, args.output, root=ROOT)
        print(json.dumps({"complete": summary["complete"], "physical_calls": summary["physical_calls"]}))
        return 0 if summary["complete"] else 1
    finally:
        repo.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--development-seeds", type=int, default=4)
    p.add_argument("--test-seeds", type=int, default=24)
    p.add_argument("--seed-namespace", type=seed_namespace,
                   help="optional prefix for new seed IDs; omit to reproduce 2026-09-26 IDs; does not certify unseen test data")
    r = sub.add_parser("run")
    r.add_argument("--plan", type=Path, required=True)
    r.add_argument("--output", type=Path, required=True)
    r.add_argument("--model", choices=["all", "minimax", "qwen"], default="all")
    r.add_argument("--split", choices=["development", "test"], default="test")
    mode = r.add_mutually_exclusive_group(required=True)
    mode.add_argument("--real", action="store_true")
    mode.add_argument("--mock", action="store_true")
    a = sub.add_parser("audit")
    a.add_argument("--run-dir", type=Path, required=True)
    a.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    if args.command == "prepare":
        prepare(args)
        return 0
    if args.command == "run":
        return asyncio.run(execute(args))
    return audit(args)


if __name__ == "__main__":
    raise SystemExit(main())
