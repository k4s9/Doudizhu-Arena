"""Historical results must reproduce without executing current rules or APIs."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/reproduce_fixed_effects.py"
ARCHIVE = ROOT / "docs/reviews/20260926-fixed-effects"


@pytest.fixture
def replay():
    spec = importlib.util.spec_from_file_location("reproduce_fixed_effects", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_archive_rebuilds_all_reports_and_preserves_failed_audit(tmp_path):
    output = tmp_path / "reproduction"
    result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--output", str(output)],
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr + (
        (output / "rebuild.stderr.log").read_text() if (output / "rebuild.stderr.log").exists() else "")
    summary = json.loads((output / "reproduction.json").read_text())
    assert summary["reproduction_complete"] and not summary["all_protocols_passed"]
    assert summary["verified_archive_files"] == 41 and summary["verified_source_files"] == 72
    assert summary["rules_version"] == "duplicate-four-seat-v2-leader-must-play"
    assert summary["network_connection_attempts"] == 0 and summary["archive_unchanged"]
    for name, calls in (("minimax", 292), ("qwen", 198)):
        model = summary["models"][name]
        assert len(model["byte_identical_reports"]) == 8
        assert all(model["byte_identical_reports"].values())
        assert model["physical_calls"] == calls
        assert model["source_database_sha256_before"] == model["source_database_sha256_after"]
        for report in model["byte_identical_reports"]:
            assert (output / name / "report" / report).read_bytes() == (ARCHIVE / name / "report" / report).read_bytes()
    assert summary["models"]["minimax"]["protocol_audit_passed"]
    assert summary["models"]["qwen"]["issues"] == ["output allowance"]
    qwen = json.loads((output / "qwen/report/summary.json").read_text())
    assert qwen["paired"]["playing"]["success_difference_percentage_points"] is None


@pytest.mark.parametrize("artifact", ["corpus.json", "source-snapshot.json.gz", "SHA256SUMS.json"])
def test_tampered_archive_rejected_before_creating_output(replay, tmp_path, artifact):
    archive = tmp_path / "archive"
    shutil.copytree(ARCHIVE, archive)
    with (archive / artifact).open("ab") as handle:
        handle.write(b"tampered")
    output = tmp_path / "result"
    with pytest.raises(ValueError, match="checksum"):
        replay.reproduce(archive, output)
    assert not output.exists()


def test_existing_output_is_never_overwritten(replay, tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep")
    with pytest.raises(FileExistsError, match="new directory"):
        replay.reproduce(ARCHIVE, output)
    assert list(output.iterdir()) == [sentinel] and sentinel.read_text() == "keep"


@pytest.mark.parametrize("path", ["../escape.py", "/absolute.py", "src/../escape.py", "src\\escape.py", "C:/escape.py"])
def test_archive_paths_cannot_escape_destination(replay, path):
    with pytest.raises(ValueError, match="unsafe archive path"):
        replay.relative_path(path)


def test_symlink_archive_and_output_are_rejected(replay, tmp_path):
    alias = tmp_path / "archive-link"
    alias.symlink_to(ARCHIVE, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        replay.reproduce(alias, tmp_path / "result")
    with pytest.raises(ValueError, match="symlink"):
        replay.reproduce(ARCHIVE, alias / "new-output")
    with pytest.raises(ValueError, match="outside the source archive"):
        replay.reproduce(ARCHIVE, ARCHIVE / "unused" / ".." / "new-output")


def test_worker_guard_blocks_connection_before_network_io():
    code = """
import importlib.util, socket, sys
spec = importlib.util.spec_from_file_location('replay', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
attempts = module.install_network_guard()
try:
    socket.getaddrinfo('example.invalid', 443)
except PermissionError:
    assert attempts == ['socket.getaddrinfo']
else:
    raise AssertionError('network guard did not block resolution')
"""
    completed = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(SCRIPT)],
                               capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
