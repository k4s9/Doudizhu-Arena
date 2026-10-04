"""Rebuild the frozen 2026-09-26 public reports offline with their own source.

Use the doudizhu-arena conda Python with -I -B. The output must be a new
directory. Exit 0 means reproduction matched, not that both protocols passed.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "docs/reviews/20260926-fixed-effects"
# This command executes only this reviewed historical snapshot, not arbitrary
# source accompanied by a self-consistent, replaceable checksum manifest.
TRUSTED_CHECKSUMS = "8b137f708480ea23113d0dce40fcfdb03aab98951bfe17f0c1e5dbda1ef3fa2f"
MODELS = ("minimax", "qwen")
REPORTS = ("calls.jsonl", "decisions.jsonl", "integrity.json", "manifest.json",
           "metrics.csv", "report.md", "seed_level.csv", "summary.json")


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def load_json(value):
    def unique_keys(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = item
        return result
    return json.loads(value, object_pairs_hook=unique_keys)


def relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("unsafe archive path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError(f"unsafe archive path: {value}")
    return path


def reject_symlinks(path):
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError(f"symlink is not allowed: {path}")


def read_file(root, name):
    path = root / relative_path(name)
    reject_symlinks(path)
    if not path.is_file():
        raise ValueError(f"missing regular archive file: {name}")
    return path.read_bytes()


def source_hash(hashes):
    backend = {name: value for name, value in hashes.items() if name.startswith("src/backend/arena/")}
    return sha256(json.dumps(backend, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode())


def verify_archive(archive):
    reject_symlinks(archive)
    checksums_bytes = read_file(archive, "SHA256SUMS.json")
    if sha256(checksums_bytes) != TRUSTED_CHECKSUMS:
        raise ValueError("unrecognized archive checksum manifest; no historical code will execute")
    checksums = load_json(checksums_bytes)
    for name, expected in checksums.items():
        if sha256(read_file(archive, name)) != expected:
            raise ValueError(f"archive checksum mismatch: {name}")
    hashes = load_json(read_file(archive, "source-file-sha256.json"))
    snapshot = load_json(gzip.decompress(read_file(archive, "source-snapshot.json.gz")))
    if snapshot.keys() != hashes.keys():
        raise ValueError("source snapshot/checksum coverage mismatch")
    for name, content in snapshot.items():
        relative_path(name)
        if not (name == "scripts/fixed_effect_study.py" or
                (name.startswith("src/backend/arena/") and name.endswith(".py"))):
            raise ValueError(f"unexpected source path: {name}")
        if not isinstance(content, str) or sha256(content.encode()) != hashes[name]:
            raise ValueError(f"source checksum mismatch: {name}")
    for name in MODELS:
        manifest = load_json(read_file(archive, f"{name}/manifest.json"))
        if manifest["run_manifest"]["source_sha256"] != source_hash(hashes):
            raise ValueError(f"frozen backend source mismatch: {name}")
    return checksums, hashes, snapshot


def install_network_guard():
    attempts = []
    def deny_network(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
            attempts.append(event)
            raise PermissionError("network disabled during historical report reproduction")
    sys.addaudithook(deny_network)
    return attempts


def write_json(path, value):
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def rebuild_worker(archive, runtime, output):
    attempts = install_network_guard()
    checksums, hashes, _ = verify_archive(archive)
    for name, expected in hashes.items():
        if sha256(read_file(runtime, name)) != expected:
            raise ValueError(f"restored source mismatch: {name}")
    sys.path.insert(0, str(runtime / "src/backend"))
    import arena
    from arena.db.repository import DatabaseRepository
    from arena.engine.projection import RULES_VERSION
    from arena.evaluation.fixed_report import export_fixed_report
    if Path(arena.__file__).resolve() != runtime / "src/backend/arena/__init__.py":
        raise ValueError("historical runtime import was shadowed")
    corpus = load_json(read_file(archive, "corpus.json"))
    models = {}
    for name in MODELS:
        database = archive / name / "arena.db"
        repo = DatabaseRepository(str(database))
        repo._conn = sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)
        repo._conn.row_factory = sqlite3.Row
        try:
            repo.conn.execute("PRAGMA query_only=ON")
            rows = repo.conn.execute("SELECT id,manifest_json FROM evaluation_runs").fetchall()
            if len(rows) != 1 or load_json(rows[0]["manifest_json"]) != load_json(read_file(archive, f"{name}/manifest.json")):
                raise ValueError(f"database/frozen manifest mismatch: {name}")
            report = output / name / "report"
            summary = export_fixed_report(repo, rows[0]["id"], corpus, report, root=runtime)
            matches = {file: read_file(archive, f"{name}/report/{file}") == read_file(report, file) for file in REPORTS}
            integrity = load_json(read_file(report, "integrity.json"))
            models[name] = {
                "byte_identical_reports": matches,
                "protocol_audit_passed": integrity["complete"], "issues": integrity["issues"],
                "physical_calls": summary["physical_calls"],
                "source_database_sha256_before": checksums[f"{name}/arena.db"],
                "source_database_sha256_after": sha256(read_file(archive, f"{name}/arena.db")),
            }
        finally:
            repo.close()
    verify_archive(archive)
    matched = all(all(model["byte_identical_reports"].values()) for model in models.values())
    expected_audits = models["minimax"]["protocol_audit_passed"] and models["minimax"]["issues"] == [] and (
        not models["qwen"]["protocol_audit_passed"] and models["qwen"]["issues"] == ["output allowance"])
    result = {
        "reproduction_complete": matched and expected_audits and not attempts,
        "all_protocols_passed": all(model["protocol_audit_passed"] for model in models.values()),
        "archive": str(archive), "archive_checksums_sha256": TRUSTED_CHECKSUMS,
        "verified_archive_files": len(checksums), "verified_source_files": len(hashes),
        "runtime_source_sha256": source_hash(hashes), "rules_version": RULES_VERSION,
        "python_executable": sys.executable, "python_version": sys.version,
        "archive_unchanged": True, "network_connection_attempts": len(attempts), "models": models,
    }
    write_json(output / "reproduction.json", result)
    return 0 if result["reproduction_complete"] else 1


def reproduce(archive, output):
    archive, output = archive.absolute(), output.absolute()
    reject_symlinks(archive)
    reject_symlinks(output)
    archive, output = archive.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError(f"output must be a new directory: {output}")
    if output.is_relative_to(archive):
        raise ValueError("output must be outside the source archive")
    _, _, snapshot = verify_archive(archive)
    output.mkdir(parents=True, exist_ok=False)
    runtime = output / "runtime"
    for name, content in snapshot.items():
        path = runtime / relative_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="") as handle:
            handle.write(content)
    command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
               "_worker", str(archive), str(runtime), str(output)]
    with (output / "rebuild.stdout.log").open("x") as stdout, (output / "rebuild.stderr.log").open("x") as stderr:
        completed = subprocess.run(command, cwd=runtime, stdout=stdout, stderr=stderr, check=False)
    if completed.returncode:
        raise RuntimeError(f"reproduction failed; preserved output and logs: {output}")
    result = load_json(read_file(output, "reproduction.json"))
    print(json.dumps({"reproduction_complete": result["reproduction_complete"],
                      "all_protocols_passed": result["all_protocols_passed"],
                      "reports_byte_identical": 16, "result": str(output / "reproduction.json")}))
    return 0


def main():
    if len(sys.argv) == 5 and sys.argv[1] == "_worker":
        return rebuild_worker(*(Path(value) for value in sys.argv[2:]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ARCHIVE,
                        help="an unmodified copy of the frozen 2026-09-26 public archive")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return reproduce(args.archive, args.output)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Reproduction error: {exc}", file=sys.stderr)
        raise SystemExit(1)
