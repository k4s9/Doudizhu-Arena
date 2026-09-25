"""Additive reliability storage. Legacy runs are retained during migration."""
from __future__ import annotations

import json
import time
import uuid


DDL = """
CREATE TABLE IF NOT EXISTS task_attempts (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES evaluation_tasks(id),
 attempt_index INTEGER NOT NULL, status TEXT NOT NULL, match_id TEXT,
 started_at REAL NOT NULL, finished_at REAL, failure_reason TEXT,
 UNIQUE(task_id, attempt_index)
);
CREATE TABLE IF NOT EXISTS decisions (
 decision_id TEXT PRIMARY KEY, run_id TEXT, task_attempt_id TEXT, variant_id TEXT,
 match_id TEXT, table_hand_id TEXT, seat TEXT NOT NULL, player_id TEXT,
 phase TEXT NOT NULL, observation_json TEXT NOT NULL, observation_hash TEXT NOT NULL,
 started_at REAL NOT NULL, deadline REAL NOT NULL, resolution TEXT,
 reason TEXT, proposed_action TEXT, actual_action TEXT, action_id TEXT,
 action_seq INTEGER, state_hash_after TEXT, finished_at REAL, latency_ms REAL,
 rules_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_run ON decisions(run_id, variant_id, phase);
CREATE TABLE IF NOT EXISTS call_evidence (
 call_id TEXT PRIMARY KEY REFERENCES llm_call_logs(id), system_prompt TEXT NOT NULL,
 user_prompt TEXT NOT NULL, raw_output TEXT, provider_status TEXT NOT NULL,
 transport_attempt INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS budget_ledger (
 reservation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, call_id TEXT,
 reserved_usd REAL NOT NULL, actual_usd REAL, status TEXT NOT NULL,
 created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS match_events (
 match_id TEXT NOT NULL, seq INTEGER NOT NULL, event_json TEXT NOT NULL,
 PRIMARY KEY(match_id, seq)
);
CREATE TABLE IF NOT EXISTS evaluation_leases (
 run_id TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at REAL NOT NULL
);
"""


def init(conn):
    # SQLite autoindexes cannot be dropped. Preserve the original table instead
    # of deleting historical runs; foreign keys continue to point to the new one.
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='evaluation_runs'").fetchone()[0]
    if "manifest_sha256 TEXT NOT NULL UNIQUE" in sql:
        conn.execute("PRAGMA legacy_alter_table=ON")
        conn.execute("ALTER TABLE evaluation_runs RENAME TO evaluation_runs_v4_archive")
        conn.execute(sql.replace("manifest_sha256 TEXT NOT NULL UNIQUE", "manifest_sha256 TEXT NOT NULL"))
        conn.execute("INSERT INTO evaluation_runs SELECT * FROM evaluation_runs_v4_archive")
        conn.execute("PRAGMA legacy_alter_table=OFF")
    conn.executescript(DDL)
    conn.execute("PRAGMA user_version=5")
    conn.commit()


def begin_decision(repo, **data):
    columns = list(data)
    repo.conn.execute(
        f"INSERT INTO decisions ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        list(data.values()),
    )
    repo.conn.commit()


def resolve_decision(repo, decision_id, **data):
    data["finished_at"] = time.time()
    row = repo.conn.execute("SELECT started_at, resolution FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
    if row is None or row[1] is not None:
        raise ValueError("decision missing or already resolved")
    data["latency_ms"] = max(0, (data["finished_at"] - row[0]) * 1000)
    repo.conn.execute(
        f"UPDATE decisions SET {','.join(key+'=?' for key in data)} WHERE decision_id=?",
        [*data.values(), decision_id],
    )
    repo.conn.commit()


def start_attempt(repo, task_id):
    attempt_id = uuid.uuid4().hex
    with repo.atomic():
        index = repo.conn.execute("SELECT COALESCE(MAX(attempt_index),0)+1 FROM task_attempts WHERE task_id=?", (task_id,)).fetchone()[0]
        repo.conn.execute("INSERT INTO task_attempts(id,task_id,attempt_index,status,started_at) VALUES(?,?,?,'running',?)", (attempt_id, task_id, index, time.time()))
        repo.update_evaluation_task(task_id, "running")
    return attempt_id


def finish_attempt(repo, attempt_id, status, match_id=None, reason=None):
    repo.conn.execute("UPDATE task_attempts SET status=?,match_id=COALESCE(?,match_id),failure_reason=?,finished_at=? WHERE id=?", (status, match_id, reason, time.time(), attempt_id))
    repo.conn.commit()
