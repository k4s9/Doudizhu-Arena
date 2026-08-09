"""SQLite DDL + CRUD functions for all tables.

Uses sqlite3 from stdlib — no ORM dependency.
All primary keys are UUID strings. Timestamps are ISO 8601 strings.
Composite data (cards, configs) stored as JSON TEXT.

v2 schema: player_configs + players replace the old agents table.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

# ── DDL ──────────────────────────────────────────────────────────────────────────

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS player_configs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    api_key         TEXT NOT NULL,
    base_url        TEXT,
    system_prompt   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    id                  TEXT PRIMARY KEY,
    config_id           TEXT NOT NULL REFERENCES player_configs(id),
    display_name        TEXT NOT NULL,
    long_term_memory    TEXT DEFAULT '',
    matches_played      INTEGER DEFAULT 0,
    matches_won         INTEGER DEFAULT 0,
    total_score         INTEGER DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_players_config ON players(config_id);

CREATE TABLE IF NOT EXISTS matches (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'created',
    config          TEXT NOT NULL,
    seed            TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    paused_at       TEXT,
    finished_at     TEXT,
    current_hand    INTEGER DEFAULT 0,
    score_red       INTEGER DEFAULT 0,
    score_blue      INTEGER DEFAULT 0,
    ko_result       TEXT
);

CREATE TABLE IF NOT EXISTS match_participants (
    id              TEXT PRIMARY KEY,
    match_id        TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    player_id       TEXT NOT NULL REFERENCES players(id),
    team            TEXT NOT NULL,
    seat_table_a    TEXT,
    seat_table_b    TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_participants_match ON match_participants(match_id);
CREATE INDEX IF NOT EXISTS idx_participants_player ON match_participants(player_id);

CREATE TABLE IF NOT EXISTS hands (
    id              TEXT PRIMARY KEY,
    match_id        TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    hand_num        INTEGER NOT NULL,
    dealer          TEXT NOT NULL,
    idle_seat       TEXT NOT NULL,
    seed            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    diff_score_red  INTEGER,
    diff_score_blue INTEGER,
    diff_capped     INTEGER,
    is_tiebreaker   INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL,
    finished_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_hands_match ON hands(match_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_hands_match_num ON hands(match_id, hand_num);

CREATE TABLE IF NOT EXISTS table_hands (
    id              TEXT PRIMARY KEY,
    hand_id         TEXT NOT NULL REFERENCES hands(id) ON DELETE CASCADE,
    "table"         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'dealing',
    landlord_seat   TEXT,
    final_bid       INTEGER,
    dizhu_cards     TEXT,
    idle_participation TEXT,
    void            INTEGER DEFAULT 0,
    winner_team     TEXT,
    winner_role     TEXT,
    base_score      INTEGER,
    multiplier      INTEGER,
    final_score     INTEGER,
    bombs_played    INTEGER DEFAULT 0,
    spring          INTEGER DEFAULT 0,
    anti_spring     INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL,
    finished_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_table_hands_hand ON table_hands(hand_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_table_hands_hand_table ON table_hands(hand_id, "table");

CREATE TABLE IF NOT EXISTS bidding_records (
    id              TEXT PRIMARY KEY,
    table_hand_id   TEXT NOT NULL REFERENCES table_hands(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    seat            TEXT NOT NULL,
    bid             INTEGER NOT NULL,
    timestamp_ms    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bidding_hand ON bidding_records(table_hand_id);

CREATE TABLE IF NOT EXISTS play_actions (
    id              TEXT PRIMARY KEY,
    table_hand_id   TEXT NOT NULL REFERENCES table_hands(id) ON DELETE CASCADE,
    round           INTEGER NOT NULL,
    sub_round       INTEGER NOT NULL,
    seq             INTEGER NOT NULL,
    seat            TEXT NOT NULL,
    action_type     TEXT NOT NULL,
    cards           TEXT,
    pattern         TEXT,
    display         TEXT,
    is_trusted      INTEGER DEFAULT 0,
    timestamp_ms    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_play_hand ON play_actions(table_hand_id);
CREATE INDEX IF NOT EXISTS idx_play_hand_seq ON play_actions(table_hand_id, seq);

CREATE TABLE IF NOT EXISTS agent_thoughts (
    id              TEXT PRIMARY KEY,
    table_hand_id   TEXT NOT NULL REFERENCES table_hands(id) ON DELETE CASCADE,
    player_id       TEXT NOT NULL,
    seat            TEXT NOT NULL,
    phase           TEXT NOT NULL,
    round           INTEGER,
    sub_round       INTEGER,
    reasoning       TEXT NOT NULL,
    decision        TEXT NOT NULL,
    llm_call_ms     INTEGER,
    retry_count     INTEGER DEFAULT 0,
    timestamp_ms    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_thoughts_hand ON agent_thoughts(table_hand_id);
CREATE INDEX IF NOT EXISTS idx_thoughts_player ON agent_thoughts(player_id);

CREATE TABLE IF NOT EXISTS reflections (
    id              TEXT PRIMARY KEY,
    table_hand_id   TEXT NOT NULL REFERENCES table_hands(id) ON DELETE CASCADE,
    player_id       TEXT NOT NULL,
    seat            TEXT NOT NULL,
    actual_role     TEXT NOT NULL,
    reflection      TEXT NOT NULL,
    short_term_memory TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reflections_hand ON reflections(table_hand_id);
CREATE INDEX IF NOT EXISTS idx_reflections_player ON reflections(player_id);

CREATE TABLE IF NOT EXISTS agent_memories (
    id              TEXT PRIMARY KEY,
    player_id       TEXT NOT NULL,
    match_id        TEXT,
    memory_type     TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_player ON agent_memories(player_id);
CREATE INDEX IF NOT EXISTS idx_memories_player_match ON agent_memories(player_id, match_id);

CREATE TABLE IF NOT EXISTS llm_call_logs (
    id              TEXT PRIMARY KEY,
    run_id          TEXT,
    variant_id      TEXT,
    match_id        TEXT,
    decision_id     TEXT,
    attempt         INTEGER,
    table_hand_id   TEXT REFERENCES table_hands(id) ON DELETE SET NULL,
    player_id       TEXT,
    phase           TEXT NOT NULL,
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    response_model  TEXT,
    system_fingerprint TEXT,
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    total_tokens    INTEGER,
    latency_ms      INTEGER,
    success         INTEGER NOT NULL,
    error_message   TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_logs_hand ON llm_call_logs(table_hand_id);
CREATE INDEX IF NOT EXISTS idx_llm_logs_player ON llm_call_logs(player_id);
CREATE INDEX IF NOT EXISTS idx_llm_logs_run_variant ON llm_call_logs(run_id, variant_id, phase);
CREATE INDEX IF NOT EXISTS idx_llm_logs_decision ON llm_call_logs(decision_id, attempt);

CREATE TABLE IF NOT EXISTS initial_hands (
    id              TEXT PRIMARY KEY,
    table_hand_id   TEXT NOT NULL REFERENCES table_hands(id) ON DELETE CASCADE,
    seat            TEXT NOT NULL,
    cards           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_initial_hands_hand ON initial_hands(table_hand_id);

CREATE TABLE IF NOT EXISTS remaining_hands (
    id              TEXT PRIMARY KEY,
    table_hand_id   TEXT NOT NULL REFERENCES table_hands(id) ON DELETE CASCADE,
    seat            TEXT NOT NULL,
    cards           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_remaining_hands_hand ON remaining_hands(table_hand_id);

CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL UNIQUE,
    spec_json TEXT NOT NULL,
    spec_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiment_variants (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    variant_id TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(experiment_id, variant_id)
);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'planned',
    manifest_json TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL UNIQUE,
    started_at TEXT,
    finished_at TEXT,
    failure_reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_tasks (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    variant_id TEXT NOT NULL,
    seed TEXT NOT NULL,
    seat_rotation INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'planned',
    match_id TEXT REFERENCES matches(id) ON DELETE SET NULL,
    started_at TEXT,
    finished_at TEXT,
    failure_reason TEXT,
    UNIQUE(run_id, variant_id, seed, seat_rotation)
);

CREATE INDEX IF NOT EXISTS idx_evaluation_tasks_run_status ON evaluation_tasks(run_id, status);

CREATE TABLE IF NOT EXISTS memory_training_checkpoints (
    run_id TEXT PRIMARY KEY REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    completed_task_id TEXT,
    memories_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_snapshots (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    config_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    base_url TEXT,
    parameters_json TEXT NOT NULL,
    prompt_sha256 TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    sdk_version TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decision_events (
    id TEXT PRIMARY KEY,
    decision_id TEXT,
    run_id TEXT REFERENCES evaluation_runs(id) ON DELETE SET NULL,
    variant_id TEXT,
    match_id TEXT REFERENCES matches(id) ON DELETE SET NULL,
    table_hand_id TEXT REFERENCES table_hands(id) ON DELETE SET NULL,
    player_id TEXT,
    seat TEXT,
    phase TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    output_sha256 TEXT,
    error_code TEXT,
    is_illegal INTEGER NOT NULL DEFAULT 0,
    is_retry INTEGER NOT NULL DEFAULT 0,
    is_fallback INTEGER NOT NULL DEFAULT 0,
    is_autoplay INTEGER NOT NULL DEFAULT 0,
    fallback_reason TEXT,
    latency_ms INTEGER,
    final_action TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decision_events_run_variant_phase ON decision_events(run_id, variant_id, phase);
CREATE INDEX IF NOT EXISTS idx_decision_events_match ON decision_events(match_id);
CREATE INDEX IF NOT EXISTS idx_decision_events_error ON decision_events(error_code);
CREATE INDEX IF NOT EXISTS idx_decision_events_fallback ON decision_events(is_fallback, is_autoplay);
CREATE INDEX IF NOT EXISTS idx_decision_events_decision ON decision_events(decision_id, attempt);
"""


# ── helpers ──────────────────────────────────────────────────────────────────────

def _now() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _uid() -> str:
    return uuid.uuid4().hex


def _cards_json(cards) -> str:
    """Serialize cards to JSON string array."""
    if cards is None:
        return "[]"
    return json.dumps([str(c) for c in cards], ensure_ascii=False)


# ── helpers: row → dict ──────────────────────────────────────────────────────────

def _config_row_to_dict(row: tuple) -> dict:
    cols = [
        "id", "name", "provider", "model", "api_key", "base_url",
        "system_prompt", "created_at", "updated_at",
    ]
    return dict(zip(cols, row))


def _player_row_to_dict(row: tuple) -> dict:
    cols = [
        "id", "config_id", "display_name", "long_term_memory",
        "matches_played", "matches_won", "total_score",
        "created_at", "updated_at",
    ]
    return dict(zip(cols, row))


# ── initialization + migration ───────────────────────────────────────────────────

def init_db(path: str) -> sqlite3.Connection:
    """Create all tables and return a connection with WAL mode + foreign keys."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")

    # Run migration first: renames agent_id → player_id on old tables,
    # creates player_configs + players, drops agents.
    _migrate_v1_to_v2(conn)
    _migrate_v2_to_v3(conn)
    _migrate_v3_to_v4(conn)

    # Now create/update tables and indexes — safe because old columns are migrated.
    # The SCHEMA_DDL handles both fresh DBs (all tables new) and migrated DBs
    # (tables exist, CREATE IF NOT EXISTS is a no-op, but new indexes are created).
    conn.executescript(SCHEMA_DDL)

    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    return conn


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Advance legacy databases without rewriting existing match data."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 3:
        conn.execute("PRAGMA user_version = 3")
        conn.commit()


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """Add evaluation correlation fields without rewriting existing observations."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= 4:
        return
    additions = {
        "llm_call_logs": {
            "run_id": "TEXT", "variant_id": "TEXT", "match_id": "TEXT",
            "decision_id": "TEXT", "attempt": "INTEGER",
            "response_model": "TEXT", "system_fingerprint": "TEXT",
        },
        "decision_events": {
            "decision_id": "TEXT", "seat": "TEXT",
        },
        "model_snapshots": {
            "sdk_version": "TEXT",
        },
    }
    for table, columns in additions.items():
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, sql_type in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
    conn.execute("PRAGMA user_version = 4")
    conn.commit()


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Migrate from v1 (agents table) to v2 (player_configs + players).

    Idempotent — checks PRAGMA user_version and skips if already migrated.
    Preserves existing data: each agent row becomes one player_config + one player.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= 2:
        return

    # Check if old agents table exists
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='agents'"
    ).fetchone()
    if row is None:
        # No old agents table — mark as v2 and return
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
        return

    # Create v2 tables first (they won't exist yet on a v1 DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS player_configs (
        id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, provider TEXT NOT NULL,
        model TEXT NOT NULL, api_key TEXT NOT NULL, base_url TEXT,
        system_prompt TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS players (
        id TEXT PRIMARY KEY, config_id TEXT NOT NULL REFERENCES player_configs(id),
        display_name TEXT NOT NULL, long_term_memory TEXT DEFAULT '',
        matches_played INTEGER DEFAULT 0, matches_won INTEGER DEFAULT 0,
        total_score INTEGER DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_players_config ON players(config_id)")

    # Read all existing agents
    agents = conn.execute("SELECT * FROM agents").fetchall()

    # Build column index map for the agents row
    cols = [c[1] for c in conn.execute("PRAGMA table_info(agents)")]
    col_idx = {name: i for i, name in enumerate(cols)}

    agent_to_player: dict[str, str] = {}  # old agent_id → new player_id

    for agent in agents:
        old_id = agent[col_idx["id"]]
        name = agent[col_idx["name"]]
        provider = agent[col_idx["provider"]]
        model = agent[col_idx["model"]]
        api_key = agent[col_idx["api_key"]]
        base_url = agent[col_idx["base_url"]] if "base_url" in col_idx else None
        system_prompt = (
            agent[col_idx["system_prompt_override"]]
            if "system_prompt_override" in col_idx
            else None
        )
        long_term_memory = (
            agent[col_idx["long_term_memory"]]
            if "long_term_memory" in col_idx
            else ""
        )
        created_at = agent[col_idx["created_at"]]
        updated_at = agent[col_idx["updated_at"]]

        # Insert config (the template part)
        config_id = _uid()
        now = _now()
        existing = conn.execute(
            "SELECT id FROM player_configs WHERE name = ?", (name,)
        ).fetchone()
        if existing:
            config_id = existing[0]
        else:
            conn.execute(
                """INSERT INTO player_configs (id, name, provider, model, api_key,
                   base_url, system_prompt, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (config_id, name, provider, model, api_key,
                 base_url, system_prompt, created_at, updated_at),
            )

        # Create one player instance per old agent (preserving its long_term_memory)
        player_id = _uid()
        existing_player = conn.execute(
            "SELECT id FROM players WHERE display_name = ? AND config_id = ?",
            (name, config_id),
        ).fetchone()
        if existing_player:
            player_id = existing_player[0]
        else:
            conn.execute(
                """INSERT INTO players (id, config_id, display_name, long_term_memory,
                   matches_played, matches_won, total_score, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 0, 0, 0, ?, ?)""",
                (player_id, config_id, name, long_term_memory,
                 created_at, now),
            )
        agent_to_player[old_id] = player_id

    # Remap FK references in child tables
    _remap_fk(conn, "match_participants", "agent_id", "player_id", agent_to_player)
    _remap_fk(conn, "agent_thoughts", "agent_id", "player_id", agent_to_player)
    _remap_fk(conn, "reflections", "agent_id", "player_id", agent_to_player)
    _remap_fk(conn, "agent_memories", "agent_id", "player_id", agent_to_player)
    _remap_fk(conn, "llm_call_logs", "agent_id", "player_id", agent_to_player)

    # Drop old index on agent_id columns
    for idx in [
        "idx_participants_agent", "idx_thoughts_agent",
        "idx_reflections_agent", "idx_memories_agent",
        "idx_memories_agent_match", "idx_llm_logs_agent",
    ]:
        try:
            conn.execute(f"DROP INDEX IF EXISTS {idx}")
        except sqlite3.OperationalError:
            pass

    # Drop old agents table
    conn.execute("DROP TABLE agents")

    # Mark migration complete
    conn.execute("PRAGMA user_version = 2")
    conn.commit()


def _remap_fk(
    conn: sqlite3.Connection,
    table: str,
    old_col: str,
    new_col: str,
    mapping: dict[str, str],
) -> None:
    """Rename an FK column and update its values atomically."""
    # Check if table exists and old_col exists
    tbl_check = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if tbl_check is None:
        return

    existing_cols = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
    if old_col not in existing_cols:
        return  # already migrated or column doesn't exist

    # Rename the column
    conn.execute(f"ALTER TABLE {table} RENAME COLUMN {old_col} TO {new_col}")

    # Update values
    for old_id, new_id in mapping.items():
        conn.execute(
            f"UPDATE {table} SET {new_col} = ? WHERE {new_col} = ?",
            (new_id, old_id),
        )


# ── player_configs ───────────────────────────────────────────────────────────────

def insert_player_config(
    conn: sqlite3.Connection,
    name: str,
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None = None,
    system_prompt: str | None = None,
    config_id: str = "",
) -> str:
    config_id = config_id or _uid()
    now = _now()
    conn.execute(
        """INSERT OR IGNORE INTO player_configs (id, name, provider, model, api_key,
           base_url, system_prompt, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (config_id, name, provider, model, api_key,
         base_url, system_prompt, now, now),
    )
    conn.commit()
    return config_id


def get_player_config(conn: sqlite3.Connection, config_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM player_configs WHERE id = ?", (config_id,)
    ).fetchone()
    if row is None:
        return None
    return _config_row_to_dict(row)


def get_player_config_by_name(conn: sqlite3.Connection, name: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM player_configs WHERE name = ?", (name,)
    ).fetchone()
    if row is None:
        return None
    return _config_row_to_dict(row)


def list_player_configs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM player_configs ORDER BY created_at DESC"
    ).fetchall()
    return [_config_row_to_dict(r) for r in rows]


def update_player_config(
    conn: sqlite3.Connection,
    config_id: str,
    *,
    name: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    system_prompt: str | None = None,
    provider: str | None = None,
) -> bool:
    """Update player_config fields. Returns True if found and updated."""
    updates = []
    params: list[Any] = []
    for field, value in [
        ("name", name), ("model", model), ("api_key", api_key),
        ("base_url", base_url), ("system_prompt", system_prompt),
        ("provider", provider),
    ]:
        if value is not None:
            updates.append(f"{field} = ?")
            params.append(value)
    if not updates:
        return False
    updates.append("updated_at = ?")
    params.append(_now())
    params.append(config_id)
    conn.execute(
        f"UPDATE player_configs SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    conn.commit()
    return conn.total_changes > 0


def delete_player_config(conn: sqlite3.Connection, config_id: str) -> bool:
    conn.execute("DELETE FROM player_configs WHERE id = ?", (config_id,))
    conn.commit()
    return conn.total_changes > 0


def count_players_for_config(conn: sqlite3.Connection, config_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM players WHERE config_id = ?", (config_id,)
    ).fetchone()[0]


# ── players ──────────────────────────────────────────────────────────────────────

def insert_player(
    conn: sqlite3.Connection,
    config_id: str,
    display_name: str,
    long_term_memory: str = "",
    player_id: str = "",
) -> str:
    player_id = player_id or _uid()
    now = _now()
    conn.execute(
        """INSERT INTO players (id, config_id, display_name, long_term_memory,
           matches_played, matches_won, total_score, created_at, updated_at)
           VALUES (?, ?, ?, ?, 0, 0, 0, ?, ?)""",
        (player_id, config_id, display_name, long_term_memory, now, now),
    )
    conn.commit()
    return player_id


def get_player(conn: sqlite3.Connection, player_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM players WHERE id = ?", (player_id,)
    ).fetchone()
    if row is None:
        return None
    return _player_row_to_dict(row)


def get_player_with_config(conn: sqlite3.Connection, player_id: str) -> dict | None:
    """Return player merged with its config fields (provider, model, api_key, etc.)."""
    row = conn.execute(
        """SELECT p.*, c.name AS config_name, c.provider, c.model, c.api_key,
                  c.base_url, c.system_prompt
           FROM players p
           JOIN player_configs c ON p.config_id = c.id
           WHERE p.id = ?""",
        (player_id,),
    ).fetchone()
    if row is None:
        return None
    cols = [
        "id", "config_id", "display_name", "long_term_memory",
        "matches_played", "matches_won", "total_score",
        "created_at", "updated_at",
        "config_name", "provider", "model", "api_key", "base_url", "system_prompt",
    ]
    return dict(zip(cols, row))


def list_players(
    conn: sqlite3.Connection,
    config_id: str | None = None,
) -> list[dict]:
    """List players, optionally filtered by config_id."""
    if config_id:
        rows = conn.execute(
            "SELECT * FROM players WHERE config_id = ? ORDER BY created_at DESC",
            (config_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM players ORDER BY created_at DESC"
        ).fetchall()
    return [_player_row_to_dict(r) for r in rows]


def list_players_with_config(
    conn: sqlite3.Connection,
    config_id: str | None = None,
) -> list[dict]:
    """List players with their config fields joined in."""
    base = """SELECT p.*, c.name AS config_name, c.provider, c.model
              FROM players p
              JOIN player_configs c ON p.config_id = c.id"""
    if config_id:
        rows = conn.execute(
            f"{base} WHERE p.config_id = ? ORDER BY p.created_at DESC",
            (config_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"{base} ORDER BY p.created_at DESC"
        ).fetchall()
    results = []
    for r in rows:
        d = _player_row_to_dict(r)
        d["config_name"] = r[9] if len(r) > 9 else ""
        d["provider"] = r[10] if len(r) > 10 else ""
        d["model"] = r[11] if len(r) > 11 else ""
        results.append(d)
    return results


def update_player_long_term_memory(
    conn: sqlite3.Connection, player_id: str, memory: str
) -> None:
    conn.execute(
        "UPDATE players SET long_term_memory = ?, updated_at = ? WHERE id = ?",
        (memory, _now(), player_id),
    )
    conn.commit()


def update_player_stats(
    conn: sqlite3.Connection,
    player_id: str,
    *,
    matches_played_delta: int = 0,
    matches_won_delta: int = 0,
    total_score_delta: int = 0,
) -> None:
    conn.execute(
        """UPDATE players SET
           matches_played = matches_played + ?,
           matches_won = matches_won + ?,
           total_score = total_score + ?,
           updated_at = ?
           WHERE id = ?""",
        (matches_played_delta, matches_won_delta, total_score_delta,
         _now(), player_id),
    )
    conn.commit()


def update_player_by_id(
    conn: sqlite3.Connection,
    player_id: str,
    *,
    display_name: str | None = None,
    long_term_memory: str | None = None,
) -> bool:
    """Update player fields. Returns True if found and updated."""
    updates = []
    params: list[Any] = []
    for field, value in [
        ("display_name", display_name),
        ("long_term_memory", long_term_memory),
    ]:
        if value is not None:
            updates.append(f"{field} = ?")
            params.append(value)
    if not updates:
        return False
    updates.append("updated_at = ?")
    params.append(_now())
    params.append(player_id)
    conn.execute(
        f"UPDATE players SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    conn.commit()
    return conn.total_changes > 0


def delete_player(conn: sqlite3.Connection, player_id: str) -> bool:
    conn.execute("DELETE FROM players WHERE id = ?", (player_id,))
    conn.commit()
    return conn.total_changes > 0


# ── matches ──────────────────────────────────────────────────────────────────────

def insert_match(
    conn: sqlite3.Connection,
    name: str,
    config: dict,
    seed: str,
) -> str:
    match_id = _uid()
    now = _now()
    conn.execute(
        """INSERT INTO matches (id, name, status, config, seed, created_at)
           VALUES (?, ?, 'created', ?, ?, ?)""",
        (match_id, name, json.dumps(config, ensure_ascii=False), seed, now),
    )
    conn.commit()
    return match_id


def get_match(conn: sqlite3.Connection, match_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    if row is None:
        return None
    return _match_row_to_dict(row)


def list_matches(
    conn: sqlite3.Connection,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    if status:
        where = "WHERE status = ?"
        params = (status,)
    else:
        where = ""
        params = ()

    total = conn.execute(f"SELECT COUNT(*) FROM matches {where}", params).fetchone()[0]
    offset = (page - 1) * page_size
    rows = conn.execute(
        f"SELECT * FROM matches {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (*params, page_size, offset),
    ).fetchall()
    return [_match_row_to_dict(r) for r in rows], total


def update_match_status(
    conn: sqlite3.Connection,
    match_id: str,
    status: str,
    *,
    started_at: str | None = None,
    paused_at: str | None = None,
    finished_at: str | None = None,
    current_hand: int | None = None,
    score_red: int | None = None,
    score_blue: int | None = None,
    ko_result: str | None = None,
) -> None:
    conn.execute(
        """UPDATE matches SET status = ?, started_at = COALESCE(?, started_at),
           paused_at = COALESCE(?, paused_at),
           finished_at = COALESCE(?, finished_at),
           current_hand = COALESCE(?, current_hand),
           score_red = COALESCE(?, score_red),
           score_blue = COALESCE(?, score_blue),
           ko_result = COALESCE(?, ko_result)
           WHERE id = ?""",
        (status, started_at, paused_at, finished_at,
         current_hand, score_red, score_blue, ko_result, match_id),
    )
    conn.commit()


def _match_row_to_dict(row: tuple) -> dict:
    cols = [
        "id", "name", "status", "config", "seed",
        "created_at", "started_at", "paused_at", "finished_at",
        "current_hand", "score_red", "score_blue", "ko_result",
    ]
    d = dict(zip(cols, row))
    if d["config"] and isinstance(d["config"], str):
        d["config"] = json.loads(d["config"])
    if d["ko_result"] and isinstance(d["ko_result"], str):
        d["ko_result"] = json.loads(d["ko_result"])
    return d


# ── match_participants ──────────────────────────────────────────────────────────

def insert_participant(
    conn: sqlite3.Connection,
    match_id: str,
    player_id: str,
    team: str,
    seat_table_a: str | None = None,
    seat_table_b: str | None = None,
) -> str:
    pid = _uid()
    conn.execute(
        """INSERT INTO match_participants (id, match_id, player_id, team,
           seat_table_a, seat_table_b, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (pid, match_id, player_id, team, seat_table_a, seat_table_b, _now()),
    )
    conn.commit()
    return pid


def get_participants(conn: sqlite3.Connection, match_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM match_participants WHERE match_id = ?", (match_id,)
    ).fetchall()
    cols = ["id", "match_id", "player_id", "team",
            "seat_table_a", "seat_table_b", "created_at"]
    return [dict(zip(cols, r)) for r in rows]


# ── hands ────────────────────────────────────────────────────────────────────────

def insert_hand(
    conn: sqlite3.Connection,
    match_id: str,
    hand_num: int,
    dealer: str,
    idle_seat: str,
    seed: str,
    is_tiebreaker: bool = False,
) -> str:
    hand_id = _uid()
    conn.execute(
        """INSERT INTO hands (id, match_id, hand_num, dealer, idle_seat, seed,
           status, is_tiebreaker, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (hand_id, match_id, hand_num, dealer, idle_seat, seed,
         int(is_tiebreaker), _now()),
    )
    conn.commit()
    return hand_id


def finish_hand(
    conn: sqlite3.Connection,
    hand_id: str,
    diff_score_red: int,
    diff_score_blue: int,
    diff_capped: bool,
) -> None:
    conn.execute(
        """UPDATE hands SET status = 'finished',
           diff_score_red = ?, diff_score_blue = ?, diff_capped = ?, finished_at = ?
           WHERE id = ?""",
        (diff_score_red, diff_score_blue, int(diff_capped), _now(), hand_id),
    )
    conn.commit()


def get_hands_for_match(conn: sqlite3.Connection, match_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM hands WHERE match_id = ? ORDER BY hand_num", (match_id,)
    ).fetchall()
    cols = [
        "id", "match_id", "hand_num", "dealer", "idle_seat", "seed",
        "status", "diff_score_red", "diff_score_blue", "diff_capped",
        "is_tiebreaker", "created_at", "finished_at",
    ]
    return [dict(zip(cols, r)) for r in rows]


# ── table_hands ─────────────────────────────────────────────────────────────────

def insert_table_hand(
    conn: sqlite3.Connection,
    hand_id: str,
    table: str,
) -> str:
    th_id = _uid()
    conn.execute(
        """INSERT INTO table_hands (id, hand_id, "table", status, created_at)
           VALUES (?, ?, ?, 'dealing', ?)""",
        (th_id, hand_id, table, _now()),
    )
    conn.commit()
    return th_id


def update_table_hand_result(
    conn: sqlite3.Connection,
    table_hand_id: str,
    *,
    status: str = "finished",
    landlord_seat: str = "",
    final_bid: int = 0,
    dizhu_cards: str = "[]",
    idle_participation: str = "{}",
    void: bool = False,
    winner_team: str = "",
    winner_role: str = "",
    base_score: int = 0,
    multiplier: int = 0,
    final_score: int = 0,
    bombs_played: int = 0,
    spring: bool = False,
    anti_spring: bool = False,
) -> None:
    conn.execute(
        """UPDATE table_hands SET status = ?, landlord_seat = ?, final_bid = ?,
           dizhu_cards = ?, idle_participation = ?, void = ?,
           winner_team = ?, winner_role = ?,
           base_score = ?, multiplier = ?, final_score = ?,
           bombs_played = ?, spring = ?, anti_spring = ?,
           finished_at = ?
           WHERE id = ?""",
        (status, landlord_seat, final_bid, dizhu_cards,
         json.dumps(idle_participation, ensure_ascii=False), int(void),
         winner_team, winner_role,
         base_score, multiplier, final_score,
         bombs_played, int(spring), int(anti_spring),
         _now(), table_hand_id),
    )
    conn.commit()


def get_table_hand(conn: sqlite3.Connection, table_hand_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM table_hands WHERE id = ?", (table_hand_id,)
    ).fetchone()
    if row is None:
        return None
    cols = [
        "id", "hand_id", "table", "status", "landlord_seat", "final_bid",
        "dizhu_cards", "idle_participation", "void", "winner_team", "winner_role",
        "base_score", "multiplier", "final_score", "bombs_played",
        "spring", "anti_spring", "created_at", "finished_at",
    ]
    return dict(zip(cols, row))


def get_table_hands_for_hand(conn: sqlite3.Connection, hand_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM table_hands WHERE hand_id = ? ORDER BY \"table\"", (hand_id,)
    ).fetchall()
    cols = [
        "id", "hand_id", "table", "status", "landlord_seat", "final_bid",
        "dizhu_cards", "idle_participation", "void", "winner_team", "winner_role",
        "base_score", "multiplier", "final_score", "bombs_played",
        "spring", "anti_spring", "created_at", "finished_at",
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_player_hand_records(
    conn: sqlite3.Connection, player_id: str | None = None,
) -> list[dict]:
    """Return completed per-hand records for one or all player instances.

    Every player occupies exactly one table in a duplicate-deal hand. The
    result includes both that table's role/outcome data and the hand-pair's
    team differential score, which is used by the match scoreboard.
    """
    where = ""
    params: tuple[str, ...] = ()
    if player_id:
        where = "AND p.id = ?"
        params = (player_id,)

    rows = conn.execute(
        f"""SELECT p.id AS player_id, p.display_name,
                   h.id AS hand_id, h.match_id, h.hand_num, h.idle_seat,
                   h.is_tiebreaker, h.diff_score_red, h.diff_score_blue,
                   h.finished_at AS hand_finished_at,
                   m.name AS match_name,
                   mp.team,
                   CASE WHEN th."table" = 'A' THEN mp.seat_table_a
                        ELSE mp.seat_table_b END AS seat,
                   th."table" AS table_id, th.landlord_seat,
                   th.idle_participation, th.void, th.winner_team,
                   th.winner_role, th.final_score
            FROM match_participants mp
            JOIN players p ON p.id = mp.player_id
            JOIN matches m ON m.id = mp.match_id
            JOIN hands h ON h.match_id = mp.match_id
            JOIN table_hands th ON th.hand_id = h.id
            WHERE h.status = 'finished'
              AND th.status = 'finished'
              AND ((th."table" = 'A' AND mp.seat_table_a IS NOT NULL)
                   OR (th."table" = 'B' AND mp.seat_table_b IS NOT NULL))
              {where}
            ORDER BY h.finished_at, h.created_at, h.hand_num, th.id""",
        params,
    ).fetchall()
    cols = [
        "player_id", "display_name", "hand_id", "match_id", "hand_num",
        "idle_seat", "is_tiebreaker", "diff_score_red", "diff_score_blue",
        "hand_finished_at", "match_name", "team", "seat", "table_id",
        "landlord_seat", "idle_participation", "void", "winner_team",
        "winner_role", "final_score",
    ]
    return [dict(zip(cols, row)) for row in rows]


# ── bidding_records ─────────────────────────────────────────────────────────────

def insert_bidding_record(
    conn: sqlite3.Connection,
    table_hand_id: str,
    seq: int,
    seat: str,
    bid: int,
    timestamp_ms: int,
) -> str:
    rid = _uid()
    conn.execute(
        """INSERT INTO bidding_records (id, table_hand_id, seq, seat, bid, timestamp_ms)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (rid, table_hand_id, seq, seat, bid, timestamp_ms),
    )
    conn.commit()
    return rid


# ── play_actions ────────────────────────────────────────────────────────────────

def insert_play_action(
    conn: sqlite3.Connection,
    table_hand_id: str,
    round_num: int,
    sub_round: int,
    seq: int,
    seat: str,
    action_type: str,
    cards: list | None = None,
    pattern: str | None = None,
    display: str | None = None,
    is_trusted: bool = False,
    timestamp_ms: int = 0,
) -> str:
    aid = _uid()
    cards_json = _cards_json(cards) if cards else None
    conn.execute(
        """INSERT INTO play_actions (id, table_hand_id, round, sub_round, seq,
           seat, action_type, cards, pattern, display, is_trusted, timestamp_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (aid, table_hand_id, round_num, sub_round, seq,
         seat, action_type, cards_json, pattern, display,
         int(is_trusted), timestamp_ms),
    )
    conn.commit()
    return aid


# ── agent_thoughts ──────────────────────────────────────────────────────────────

def insert_agent_thought(
    conn: sqlite3.Connection,
    table_hand_id: str,
    player_id: str,
    seat: str,
    phase: str,
    reasoning: str,
    decision: str,
    *,
    round_num: int | None = None,
    sub_round: int | None = None,
    llm_call_ms: int | None = None,
    retry_count: int = 0,
    timestamp_ms: int = 0,
) -> str:
    tid = _uid()
    conn.execute(
        """INSERT INTO agent_thoughts (id, table_hand_id, player_id, seat, phase,
           round, sub_round, reasoning, decision, llm_call_ms, retry_count, timestamp_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (tid, table_hand_id, player_id, seat, phase,
         round_num, sub_round, reasoning, decision,
         llm_call_ms, retry_count, timestamp_ms),
    )
    conn.commit()
    return tid


# ── reflections ─────────────────────────────────────────────────────────────────

def insert_reflection(
    conn: sqlite3.Connection,
    table_hand_id: str,
    player_id: str,
    seat: str,
    actual_role: str,
    reflection: str,
    short_term_memory: str,
) -> str:
    rid = _uid()
    conn.execute(
        """INSERT INTO reflections (id, table_hand_id, player_id, seat,
           actual_role, reflection, short_term_memory, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (rid, table_hand_id, player_id, seat, actual_role,
         reflection, short_term_memory, _now()),
    )
    conn.commit()
    return rid


# ── agent_memories ──────────────────────────────────────────────────────────────

def insert_agent_memory(
    conn: sqlite3.Connection,
    player_id: str,
    memory_type: str,
    content: str,
    match_id: str | None = None,
) -> str:
    mid = _uid()
    conn.execute(
        """INSERT INTO agent_memories (id, player_id, match_id, memory_type, content, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (mid, player_id, match_id, memory_type, content, _now()),
    )
    conn.commit()
    return mid


def get_agent_memories(
    conn: sqlite3.Connection,
    player_id: str,
    match_id: str | None = None,
    memory_type: str | None = None,
) -> list[dict]:
    conditions = ["player_id = ?"]
    params: list[Any] = [player_id]
    if match_id is not None:
        conditions.append("match_id = ?")
        params.append(match_id)
    if memory_type is not None:
        conditions.append("memory_type = ?")
        params.append(memory_type)
    where = " AND ".join(conditions)
    rows = conn.execute(
        f"SELECT * FROM agent_memories WHERE {where} ORDER BY created_at DESC",
        params,
    ).fetchall()
    cols = ["id", "player_id", "match_id", "memory_type", "content", "created_at"]
    return [dict(zip(cols, r)) for r in rows]


# ── llm_call_logs ───────────────────────────────────────────────────────────────

def insert_llm_call_log(
    conn: sqlite3.Connection,
    player_id: str,
    phase: str,
    provider: str,
    model: str,
    success: bool,
    *,
    run_id: str | None = None,
    variant_id: str | None = None,
    match_id: str | None = None,
    decision_id: str | None = None,
    attempt: int | None = None,
    table_hand_id: str | None = None,
    response_model: str | None = None,
    system_fingerprint: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    latency_ms: int | None = None,
    error_message: str | None = None,
) -> str:
    lid = _uid()
    conn.execute(
        """INSERT INTO llm_call_logs
           (id, run_id, variant_id, match_id, decision_id, attempt,
            table_hand_id, player_id, phase, provider, model, response_model,
            system_fingerprint, prompt_tokens, completion_tokens, total_tokens,
            latency_ms, success, error_message, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (lid, run_id, variant_id, match_id, decision_id, attempt,
         table_hand_id, player_id, phase, provider, model, response_model,
         system_fingerprint, prompt_tokens, completion_tokens, total_tokens,
         latency_ms, int(success), error_message, _now()),
    )
    conn.commit()
    return lid


# ── evaluation entities ─────────────────────────────────────────────────────────

def insert_experiment(conn: sqlite3.Connection, experiment_id: str, spec: dict, spec_sha256: str) -> str:
    eid = _uid()
    conn.execute(
        "INSERT OR IGNORE INTO experiments (id, experiment_id, spec_json, spec_sha256, created_at) VALUES (?, ?, ?, ?, ?)",
        (eid, experiment_id, json.dumps(spec, ensure_ascii=False, sort_keys=True), spec_sha256, _now()),
    )
    conn.commit()
    return conn.execute("SELECT id FROM experiments WHERE experiment_id = ?", (experiment_id,)).fetchone()[0]


def insert_evaluation_run(conn: sqlite3.Connection, experiment_id: str, manifest: dict, manifest_sha256: str) -> str:
    rid = _uid()
    conn.execute(
        "INSERT OR IGNORE INTO evaluation_runs (id, experiment_id, manifest_json, manifest_sha256, created_at) VALUES (?, ?, ?, ?, ?)",
        (rid, experiment_id, json.dumps(manifest, ensure_ascii=False, sort_keys=True), manifest_sha256, _now()),
    )
    conn.commit()
    return conn.execute("SELECT id FROM evaluation_runs WHERE manifest_sha256 = ?", (manifest_sha256,)).fetchone()[0]


def insert_evaluation_task(conn: sqlite3.Connection, run_id: str, variant_id: str, seed: str, seat_rotation: int) -> str:
    task_id = _uid()
    conn.execute(
        """INSERT OR IGNORE INTO evaluation_tasks
           (id, run_id, variant_id, seed, seat_rotation)
           VALUES (?, ?, ?, ?, ?)""",
        (task_id, run_id, variant_id, seed, seat_rotation),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM evaluation_tasks WHERE run_id = ? AND variant_id = ? AND seed = ? AND seat_rotation = ?",
        (run_id, variant_id, seed, seat_rotation),
    ).fetchone()[0]


def update_evaluation_task(conn: sqlite3.Connection, task_id: str, status: str, *, match_id: str | None = None, failure_reason: str | None = None) -> None:
    fields = ["status = ?"]
    values: list[Any] = [status]
    if status == "running":
        fields.append("started_at = ?"); values.append(_now())
    if status in ("finished", "failed", "cancelled"):
        fields.append("finished_at = ?"); values.append(_now())
    if match_id is not None:
        fields.append("match_id = ?"); values.append(match_id)
    if failure_reason is not None:
        fields.append("failure_reason = ?"); values.append(failure_reason)
    values.append(task_id)
    conn.execute(f"UPDATE evaluation_tasks SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()


def get_evaluation_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM evaluation_runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def get_evaluation_tasks(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT * FROM evaluation_tasks WHERE run_id = ? ORDER BY variant_id, seed, seat_rotation",
        (run_id,),
    ).fetchall()]


def upsert_memory_training_checkpoint(
    conn: sqlite3.Connection, run_id: str, memories: dict[str, str],
    completed_task_id: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO memory_training_checkpoints
           (run_id, completed_task_id, memories_json, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(run_id) DO UPDATE SET
             completed_task_id=excluded.completed_task_id,
             memories_json=excluded.memories_json,
             updated_at=excluded.updated_at""",
        (run_id, completed_task_id, json.dumps(memories, ensure_ascii=False, sort_keys=True), _now()),
    )
    conn.commit()


def get_memory_training_checkpoint(conn: sqlite3.Connection, run_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM memory_training_checkpoints WHERE run_id = ?", (run_id,)
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["memories"] = json.loads(result.pop("memories_json"))
    return result


def update_evaluation_run_status(conn: sqlite3.Connection, run_id: str, status: str, failure_reason: str | None = None) -> None:
    fields = ["status = ?"]
    values: list[Any] = [status]
    if status == "running":
        fields.append("started_at = COALESCE(started_at, ?)"); values.append(_now())
    if status in ("finished", "failed", "cancelled"):
        fields.append("finished_at = ?"); values.append(_now())
    if failure_reason is not None:
        fields.append("failure_reason = ?"); values.append(failure_reason)
    values.append(run_id)
    conn.execute(f"UPDATE evaluation_runs SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()


def list_evaluation_runs(conn: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in conn.execute(
        """SELECT r.*, e.experiment_id AS experiment_name,
                  COUNT(t.id) AS task_count,
                  SUM(CASE WHEN t.status = 'finished' THEN 1 ELSE 0 END) AS finished_count,
                  SUM(CASE WHEN t.status = 'failed' THEN 1 ELSE 0 END) AS failed_count
           FROM evaluation_runs r JOIN experiments e ON e.id = r.experiment_id
           LEFT JOIN evaluation_tasks t ON t.run_id = r.id
           GROUP BY r.id ORDER BY r.created_at DESC"""
    ).fetchall()]


def get_experiment(conn: sqlite3.Connection, experiment_db_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM experiments WHERE id = ?", (experiment_db_id,)).fetchone()
    return dict(row) if row else None


def insert_model_snapshot(conn: sqlite3.Connection, run_id: str, snapshot: dict) -> str:
    sid = _uid()
    conn.execute(
        """INSERT INTO model_snapshots (id, run_id, config_name, provider, model, base_url, parameters_json, prompt_sha256, source_revision, sdk_version, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (sid, run_id, snapshot["config_name"], snapshot["provider"], snapshot["model"], snapshot.get("base_url"),
         json.dumps(snapshot["parameters"], ensure_ascii=False, sort_keys=True), snapshot["prompt_sha256"], snapshot["revision"], snapshot.get("sdk_version"), _now()),
    )
    conn.commit()
    return sid


def insert_decision_event(conn: sqlite3.Connection, *, phase: str, attempt: int, **kwargs: Any) -> str:
    eid = _uid()
    columns = ["id", "decision_id", "run_id", "variant_id", "match_id", "table_hand_id", "player_id", "seat", "phase", "attempt", "output_sha256", "error_code", "is_illegal", "is_retry", "is_fallback", "is_autoplay", "fallback_reason", "latency_ms", "final_action", "created_at"]
    values = [eid, kwargs.get("decision_id"), kwargs.get("run_id"), kwargs.get("variant_id"), kwargs.get("match_id"), kwargs.get("table_hand_id"), kwargs.get("player_id"), kwargs.get("seat"), phase, attempt, kwargs.get("output_sha256"), kwargs.get("error_code"), int(kwargs.get("is_illegal", False)), int(kwargs.get("is_retry", False)), int(kwargs.get("is_fallback", False)), int(kwargs.get("is_autoplay", False)), kwargs.get("fallback_reason"), kwargs.get("latency_ms"), kwargs.get("final_action"), _now()]
    conn.execute(f"INSERT INTO decision_events ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", values)
    conn.commit()
    return eid


def get_decision_events(conn: sqlite3.Connection, run_id: str | None = None, match_id: str | None = None) -> list[dict]:
    conditions, params = [], []
    if run_id is not None:
        conditions.append("run_id = ?"); params.append(run_id)
    if match_id is not None:
        conditions.append("match_id = ?"); params.append(match_id)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return [dict(row) for row in conn.execute(f"SELECT * FROM decision_events{where} ORDER BY created_at, attempt", params).fetchall()]


# ── initial_hands ───────────────────────────────────────────────────────────────

def insert_initial_hand(
    conn: sqlite3.Connection,
    table_hand_id: str,
    seat: str,
    cards: list,
) -> str:
    hid = _uid()
    conn.execute(
        "INSERT INTO initial_hands (id, table_hand_id, seat, cards) VALUES (?, ?, ?, ?)",
        (hid, table_hand_id, seat, _cards_json(cards)),
    )
    conn.commit()
    return hid


# ── remaining_hands ─────────────────────────────────────────────────────────────

def insert_remaining_hand(
    conn: sqlite3.Connection,
    table_hand_id: str,
    seat: str,
    cards: list,
) -> str:
    hid = _uid()
    conn.execute(
        "INSERT INTO remaining_hands (id, table_hand_id, seat, cards) VALUES (?, ?, ?, ?)",
        (hid, table_hand_id, seat, _cards_json(cards)),
    )
    conn.commit()
    return hid


# ── query helpers for API routes ─────────────────────────────────────────────


def get_bidding_records(conn: sqlite3.Connection, table_hand_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM bidding_records WHERE table_hand_id = ? ORDER BY seq",
        (table_hand_id,),
    ).fetchall()
    cols = ["id", "table_hand_id", "seq", "seat", "bid", "timestamp_ms"]
    return [dict(zip(cols, r)) for r in rows]


def get_play_actions_for_table(
    conn: sqlite3.Connection, table_hand_id: str
) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM play_actions WHERE table_hand_id = ? ORDER BY seq",
        (table_hand_id,),
    ).fetchall()
    cols = [
        "id", "table_hand_id", "round", "sub_round", "seq",
        "seat", "action_type", "cards", "pattern", "display",
        "is_trusted", "timestamp_ms",
    ]
    results = []
    for r in rows:
        d = dict(zip(cols, r))
        if d["cards"] and isinstance(d["cards"], str):
            d["cards"] = json.loads(d["cards"])
        results.append(d)
    return results


def get_agent_thoughts_for_table(
    conn: sqlite3.Connection, table_hand_id: str
) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM agent_thoughts WHERE table_hand_id = ? ORDER BY timestamp_ms",
        (table_hand_id,),
    ).fetchall()
    cols = [
        "id", "table_hand_id", "player_id", "seat", "phase",
        "round", "sub_round", "reasoning", "decision",
        "llm_call_ms", "retry_count", "timestamp_ms",
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_reflections_for_table(
    conn: sqlite3.Connection, table_hand_id: str
) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM reflections WHERE table_hand_id = ? ORDER BY created_at",
        (table_hand_id,),
    ).fetchall()
    cols = [
        "id", "table_hand_id", "player_id", "seat",
        "actual_role", "reflection", "short_term_memory", "created_at",
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_initial_hands_for_table(
    conn: sqlite3.Connection, table_hand_id: str
) -> dict[str, list]:
    rows = conn.execute(
        "SELECT seat, cards FROM initial_hands WHERE table_hand_id = ?",
        (table_hand_id,),
    ).fetchall()
    result: dict[str, list] = {}
    for seat, cards_json in rows:
        result[seat] = json.loads(cards_json) if cards_json else []
    return result


def get_remaining_hands_for_table(
    conn: sqlite3.Connection, table_hand_id: str
) -> dict[str, list]:
    rows = conn.execute(
        "SELECT seat, cards FROM remaining_hands WHERE table_hand_id = ?",
        (table_hand_id,),
    ).fetchall()
    result: dict[str, list] = {}
    for seat, cards_json in rows:
        result[seat] = json.loads(cards_json) if cards_json else []
    return result


def get_hand(conn: sqlite3.Connection, hand_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM hands WHERE id = ?", (hand_id,)).fetchone()
    if row is None:
        return None
    cols = [
        "id", "match_id", "hand_num", "dealer", "idle_seat", "seed",
        "status", "diff_score_red", "diff_score_blue", "diff_capped",
        "is_tiebreaker", "created_at", "finished_at",
    ]
    return dict(zip(cols, row))


def get_hand_by_num(
    conn: sqlite3.Connection, match_id: str, hand_num: int
) -> dict | None:
    row = conn.execute(
        "SELECT * FROM hands WHERE match_id = ? AND hand_num = ?",
        (match_id, hand_num),
    ).fetchone()
    if row is None:
        return None
    cols = [
        "id", "match_id", "hand_num", "dealer", "idle_seat", "seed",
        "status", "diff_score_red", "diff_score_blue", "diff_capped",
        "is_tiebreaker", "created_at", "finished_at",
    ]
    return dict(zip(cols, row))


def delete_match_from_db(conn: sqlite3.Connection, match_id: str) -> bool:
    conn.execute("DELETE FROM matches WHERE id = ?", (match_id,))
    conn.commit()
    return conn.total_changes > 0
