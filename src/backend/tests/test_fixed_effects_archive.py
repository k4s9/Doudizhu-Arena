"""Public evidence must not contain plaintext or encrypted API credentials."""
import hashlib
import importlib.util
from pathlib import Path
import sqlite3

from arena.db.repository import DatabaseRepository
from arena.evaluation.fixed_study import DDL

ROOT = Path(__file__).resolve().parents[3]


def test_public_database_never_copies_secret_bytes_or_unrelated_configs(tmp_path, monkeypatch):
    monkeypatch.setenv("DOUDIZHU_CREDENTIAL_MASTER_KEY", "fixed-archive-test-master-key")
    path = tmp_path / "private.db"
    repo = DatabaseRepository(str(path))
    repo.init()
    repo.conn.executescript(DDL)
    secret = "fixed-archive-test-secret-do-not-copy"
    selected = repo.create_player_config("selected", "openai", "model", secret)
    repo.create_player_config("unrelated", "openai", "other", "unrelated-secret-do-not-copy")
    repo.create_player(selected, "benchmark-player")
    encrypted = repo.conn.execute("SELECT api_key FROM player_configs WHERE id=?", (selected,)).fetchone()[0]
    assert encrypted.startswith("enc:v1:")
    repo.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    spec = importlib.util.spec_from_file_location("archive_fixed_effects", ROOT / "scripts/archive_fixed_effects.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    destination = tmp_path / "public.db"
    module.public_database(path, destination)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    payload = destination.read_bytes()
    assert secret.encode() not in payload and encrypted.encode() not in payload
    assert b"unrelated-secret-do-not-copy" not in payload
    with sqlite3.connect(destination) as conn:
        assert conn.execute("SELECT name,api_key FROM player_configs").fetchall() == [("selected", "")]
        assert conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 1
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
