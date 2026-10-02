"""Archive an effect run with a minimal credential-free SQLite audit database.

Creates new files only. Source databases stay read-only. Physical responses and
ledgers are preserved, while API credentials and unrelated configurations are
never copied. Requires the doudizhu-arena conda Python with -I -B.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/backend"))

TABLES = ("player_configs", "players", "experiments", "evaluation_runs", "model_snapshots",
          "llm_call_logs", "call_evidence", "budget_ledger", "fixed_items", "fixed_calls")


def open_readonly(path):
    from arena.db.repository import DatabaseRepository
    repo = DatabaseRepository(str(path))
    repo._conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    repo._conn.row_factory = sqlite3.Row
    return repo


def public_database(source_path, destination):
    from arena.db.repository import DatabaseRepository
    from arena.evaluation.fixed_study import DDL
    if destination.exists():
        raise FileExistsError(destination)
    source = open_readonly(source_path)
    target = DatabaseRepository(str(destination))
    target.init()
    try:
        target.conn.executescript(DDL)
        config_ids = {r[0] for r in source.conn.execute("SELECT config_id FROM players")}
        with target.atomic():
            for table in TABLES:
                names = [r[1] for r in source.conn.execute(f"PRAGMA table_info({table})")]
                for raw in source.conn.execute(f"SELECT * FROM {table}"):
                    row = dict(raw)
                    if table == "player_configs":
                        if row["id"] not in config_ids:
                            continue
                        # Do not first copy encrypted credentials and later scrub:
                        # SQLite/WAL free pages could retain the original bytes.
                        row["api_key"] = ""
                    marks = ",".join("?" for _ in names)
                    target.conn.execute(f"INSERT INTO {table} ({','.join(names)}) VALUES ({marks})", [row[n] for n in names])
        target.conn.commit()
        assert not target.conn.execute("SELECT 1 FROM player_configs WHERE api_key != ''").fetchall()
        assert not target.conn.execute("PRAGMA foreign_key_check").fetchall()
        target.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        target.conn.execute("PRAGMA journal_mode=DELETE")
    finally:
        target.close()
        source.close()


def archive(args):
    from arena.evaluation.fixed_report import export_fixed_report
    from arena.evaluation.spec import canonical_hash
    corpus = json.loads((args.source / "corpus.json").read_text())
    args.output.mkdir(parents=True, exist_ok=False)
    for name in ("corpus.json", "plan.json", "source-file-sha256.json"):
        shutil.copyfile(args.source / name, args.output / name)
    with gzip.GzipFile(filename="", mode="wb", fileobj=(args.output / "source-snapshot.json.gz").open("wb"), mtime=0) as handle:
        handle.write((args.source / "source-snapshot.json").read_bytes())
    summaries = {}
    for name in ("minimax", "qwen"):
        source_dir = args.source / name
        if not source_dir.is_dir():
            continue
        out = args.output / name
        out.mkdir()
        public_database(source_dir / "arena.db", out / "arena.db")
        shutil.copyfile(source_dir / "manifest.json", out / "manifest.json")
        if (source_dir / "progress.jsonl").exists():
            shutil.copyfile(source_dir / "progress.jsonl", out / "progress.jsonl")
        repo = open_readonly(out / "arena.db")
        try:
            rid = repo.conn.execute("SELECT id FROM evaluation_runs").fetchone()[0]
            manifest = json.loads(repo.get_evaluation_run(rid)["manifest_json"])
            if canonical_hash(manifest) != canonical_hash(json.loads((out / "manifest.json").read_text())):
                raise ValueError("archived manifest mismatch")
            summary = export_fixed_report(repo, rid, corpus, out / "report", root=ROOT)
            if not summary["complete"] and not args.include_incomplete:
                raise ValueError(f"{name}: public evidence audit failed")
            for artifact in (source_dir / "report").iterdir():
                if artifact.is_file() and artifact.read_bytes() != (out / "report" / artifact.name).read_bytes():
                    raise ValueError(f"{name}: rebuilt {artifact.name} differs from original report")
            decisions = [json.loads(line) for line in (out / "report/decisions.jsonl").read_text().splitlines()]
            calls = {r["id"]: r for r in (json.loads(line) for line in (out / "report/calls.jsonl").read_text().splitlines())}
            by_id = {(r["observation_id"], r["arm"]): r for r in decisions}
            discordant = []
            for feedback in decisions:
                if feedback["arm"] != "rule_feedback":
                    continue
                generic = by_id[feedback["observation_id"], "generic_retry"]
                if feedback["success"] == generic["success"]:
                    continue
                call_ids = list(dict.fromkeys(generic["call_ids"] + feedback["call_ids"]))
                discordant.append({"observation_id": feedback["observation_id"], "source_seed": feedback["source_seed"],
                                   "generic_retry": generic, "rule_feedback": feedback,
                                   "physical_calls": [calls[cid] for cid in call_ids]})
            (out / "discordant-cases.json").write_text(json.dumps({"selection": "all pairs with different success outcomes; includes both directions",
                "count": len(discordant), "cases": discordant}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            summaries[name] = summary
        finally:
            repo.close()
    if not summaries:
        raise ValueError("no model run to archive")
    (args.output / "comparison.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksums = {str(p.relative_to(args.output)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(args.output.rglob("*")) if p.is_file()}
    (args.output / "SHA256SUMS.json").write_text(json.dumps(checksums, indent=2) + "\n")
    print(json.dumps({"models": list(summaries), "archived": True,
                      "all_protocols_passed": all(s["complete"] for s in summaries.values()), "files": len(checksums),
                      "bytes": sum(p.stat().st_size for p in args.output.rglob("*") if p.is_file())}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-incomplete", action="store_true",
                        help="Preserve failed/incomplete audits and their original failure labels; never relabel them as passed")
    archive(parser.parse_args())
