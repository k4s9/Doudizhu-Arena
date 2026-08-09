from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from arena.db.repository import DatabaseRepository
from arena.evaluation.runner import EvaluationRunner
from arena.evaluation.spec import build_run_manifest, load_experiment_spec
from arena.evaluation.spec import PreflightError
from arena.security.credentials import decrypt_secret, encrypt_secret

ROOT = Path(__file__).resolve().parents[3]


def test_credentials_are_encrypted_and_decrypted(monkeypatch):
    monkeypatch.setenv("DOUDIZHU_CREDENTIAL_MASTER_KEY", "test-master-key-for-arena")
    encrypted = encrypt_secret("provider-token")
    assert encrypted.startswith("enc:v1:")
    assert "provider-token" not in encrypted
    assert decrypt_secret(encrypted) == "provider-token"


def test_repository_upgrades_legacy_credentials(tmp_path, monkeypatch):
    db_path = tmp_path / "arena.db"
    monkeypatch.delenv("DOUDIZHU_CREDENTIAL_MASTER_KEY", raising=False)
    repo = DatabaseRepository(str(db_path)); repo.init()
    config_id = repo.create_player_config("legacy", "openai", "model", "plain-token")
    repo.close()
    monkeypatch.setenv("DOUDIZHU_CREDENTIAL_MASTER_KEY", "test-master-key-for-arena")
    repo = DatabaseRepository(str(db_path)); repo.init()
    stored = repo.conn.execute("SELECT api_key FROM player_configs WHERE id = ?", (config_id,)).fetchone()[0]
    assert stored.startswith("enc:v1:")
    assert repo.get_player_config(config_id)["api_key"] == "plain-token"
    repo.close()


def test_runner_finishes_and_skips_finished_tasks(tmp_path, monkeypatch):
    repo = DatabaseRepository(str(tmp_path / "arena.db")); repo.init()
    try:
        spec = load_experiment_spec(ROOT / "src/backend/evaluation/experiments/reliability-v1.yaml")
        spec = spec.model_copy(update={
            "models": [spec.models[0].model_copy(update={"config_name": "runner-model"})],
            "variants": [variant for variant in spec.variants if variant.memory_mode == "disabled"],
        })
        repo.create_player_config("runner-model", "random", "random", "")
        manifest = build_run_manifest(spec, ROOT)
        runner = EvaluationRunner(repo, str(ROOT))
        run_id = runner.create_run(spec, manifest)
        calls = []

        async def fake_run_task(task, spec, manifest):
            calls.append(task["id"])
            return ""

        monkeypatch.setattr(runner, "_run_task", fake_run_task)
        first = repo.get_evaluation_tasks(run_id)[0]
        repo.update_evaluation_task(first["id"], "finished")
        asyncio.run(runner.run(run_id, spec, manifest, real_models=True))
        assert first["id"] not in calls
        assert repo.get_evaluation_run(run_id)["status"] == "finished"
        assert all(row["status"] == "finished" for row in repo.get_evaluation_tasks(run_id))
    finally:
        repo.close()


def test_real_run_rejects_read_only_memory_without_frozen_artifact(tmp_path):
    repo = DatabaseRepository(str(tmp_path / "arena.db")); repo.init()
    try:
        spec = load_experiment_spec(ROOT / "src/backend/evaluation/experiments/reliability-v1.yaml")
        manifest = build_run_manifest(spec, ROOT)
        runner = EvaluationRunner(repo, str(ROOT))
        run_id = runner.create_run(spec, manifest)
        with pytest.raises(PreflightError, match="memory_artifact_path"):
            asyncio.run(runner.run(run_id, spec, manifest, real_models=True))
    finally:
        repo.close()


def test_v3_database_is_upgraded_with_evaluation_correlation_columns(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        PRAGMA user_version = 3;
        CREATE TABLE llm_call_logs (
            id TEXT PRIMARY KEY, table_hand_id TEXT, player_id TEXT,
            phase TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
            prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER,
            latency_ms INTEGER, success INTEGER NOT NULL, error_message TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE decision_events (
            id TEXT PRIMARY KEY, run_id TEXT, variant_id TEXT, match_id TEXT,
            table_hand_id TEXT, player_id TEXT, phase TEXT NOT NULL,
            attempt INTEGER NOT NULL, output_sha256 TEXT, error_code TEXT,
            is_illegal INTEGER NOT NULL DEFAULT 0, is_retry INTEGER NOT NULL DEFAULT 0,
            is_fallback INTEGER NOT NULL DEFAULT 0, is_autoplay INTEGER NOT NULL DEFAULT 0,
            fallback_reason TEXT, latency_ms INTEGER, final_action TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE model_snapshots (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL, config_name TEXT NOT NULL,
            provider TEXT NOT NULL, model TEXT NOT NULL, base_url TEXT,
            parameters_json TEXT NOT NULL, prompt_sha256 TEXT NOT NULL,
            source_revision TEXT NOT NULL, created_at TEXT NOT NULL
        );
    """)
    conn.close()
    repo = DatabaseRepository(str(db_path)); repo.init()
    try:
        llm_columns = {row[1] for row in repo.conn.execute("PRAGMA table_info(llm_call_logs)")}
        event_columns = {row[1] for row in repo.conn.execute("PRAGMA table_info(decision_events)")}
        snapshot_columns = {row[1] for row in repo.conn.execute("PRAGMA table_info(model_snapshots)")}
        assert {"run_id", "variant_id", "match_id", "decision_id", "attempt"} <= llm_columns
        assert {"decision_id", "seat"} <= event_columns
        assert "sdk_version" in snapshot_columns
        assert repo.conn.execute("PRAGMA user_version").fetchone()[0] == 4
    finally:
        repo.close()
