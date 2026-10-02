"""Atomic match settlement and traceable, immutable memory snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from . import models


class MemoryConflictError(ValueError):
    """A memory snapshot changed or an idempotency key was reused."""


DDL = """
CREATE TABLE IF NOT EXISTS memory_schema_migrations (
    name TEXT PRIMARY KEY, applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS match_settlements (
    match_id TEXT PRIMARY KEY REFERENCES matches(id) ON DELETE CASCADE,
    winner_team TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_versions (
    id TEXT PRIMARY KEY, player_id TEXT NOT NULL, match_id TEXT,
    content TEXT NOT NULL, content_hash TEXT NOT NULL, revision INTEGER NOT NULL,
    previous_id TEXT REFERENCES memory_versions(id), source_json TEXT NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(player_id, revision)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_version_match
    ON memory_versions(player_id, match_id) WHERE match_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_memory_version_player
    ON memory_versions(player_id, revision DESC);
CREATE TABLE IF NOT EXISTS match_memory_usage (
    id TEXT PRIMARY KEY, match_id TEXT NOT NULL, player_id TEXT NOT NULL,
    version_id TEXT NOT NULL REFERENCES memory_versions(id), created_at TEXT NOT NULL,
    UNIQUE(match_id, player_id)
);
CREATE TABLE IF NOT EXISTS memory_failures (
    id TEXT PRIMARY KEY, player_id TEXT NOT NULL, match_id TEXT NOT NULL,
    table_hand_id TEXT, phase TEXT NOT NULL, stage TEXT NOT NULL,
    error_type TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT,
    resolution_version_id TEXT REFERENCES memory_versions(id)
);
CREATE INDEX IF NOT EXISTS idx_memory_failure_match
    ON memory_failures(match_id, player_id, phase);
"""


def init(conn) -> None:
    """Add tables without changing old rows or re-counting completed matches."""
    conn.executescript(DDL)
    marker = "memory_persistence_v1"
    if conn.execute(
        "SELECT 1 FROM memory_schema_migrations WHERE name = ?", (marker,)
    ).fetchone():
        return
    # Old finished matches may already have incremented player statistics. Their
    # historical counters are retained, rather than guessed or counted again.
    conn.execute(
        """INSERT OR IGNORE INTO match_settlements
           (match_id, winner_team, source, created_at)
           SELECT id, CASE WHEN score_red > score_blue THEN 'red'
                           WHEN score_blue > score_red THEN 'blue' ELSE 'tie' END,
                  'legacy', COALESCE(finished_at, created_at)
           FROM matches WHERE status = 'finished'"""
    )
    conn.execute(
        "INSERT INTO memory_schema_migrations(name, applied_at) VALUES (?, ?)",
        (marker, models._now()),
    )
    conn.commit()


def settle_match(repo, match_id: str, winner_team: str,
                 player_teams: dict[str, str], **finish_fields: Any) -> bool:
    if winner_team not in {"red", "blue", "tie"}:
        raise ValueError("invalid winner team")
    if any(team not in {"red", "blue"} for team in player_teams.values()):
        raise ValueError("invalid player team")
    with repo.atomic():
        if repo.conn.execute(
            "SELECT 1 FROM match_settlements WHERE match_id = ?", (match_id,)
        ).fetchone():
            return False
        if repo.get_match(match_id) is None:
            raise ValueError("match does not exist")
        repo.conn.execute(
            "INSERT INTO match_settlements VALUES (?, ?, 'match', ?)",
            (match_id, winner_team, models._now()),
        )
        repo.update_match_status(match_id, "finished", **finish_fields)
        settled = repo.get_match(match_id)
        for player_id, team in player_teams.items():
            # UPDATE deliberately affects zero rows for programmatic/evaluation
            # agents that have no persistent player identity.
            repo.update_player_stats(
                player_id, matches_played_delta=1,
                matches_won_delta=int(team == winner_team),
                total_score_delta=settled[f"score_{team}"] or 0,
            )
    return True


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _source(source: dict | str | None, default: str) -> dict:
    """Provenance is structured metadata, never credentials or request bodies."""
    if isinstance(source, str):
        source = {"kind": source}
    source = source or {"kind": default}
    allowed = {"kind", "provider", "model", "prompt_sha256", "source_revision",
               "artifact_sha256", "source_match_id", "schema_version"}
    return {
        key: value for key, value in source.items()
        if key in allowed and isinstance(value, (str, int, float, bool))
    }


def _version(row) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    result["source"] = json.loads(result.pop("source_json"))
    return result


def get_memory_version(repo, version_id: str) -> dict | None:
    return _version(repo.conn.execute(
        "SELECT * FROM memory_versions WHERE id = ?", (version_id,)
    ).fetchone())


def list_memory_versions(repo, player_id: str, limit: int = 100) -> list[dict]:
    rows = repo.conn.execute(
        "SELECT * FROM memory_versions WHERE player_id = ? ORDER BY revision DESC LIMIT ?",
        (player_id, max(1, min(limit, 1000))),
    ).fetchall()
    return [_version(row) for row in rows]


def _latest(repo, player_id: str) -> dict | None:
    return _version(repo.conn.execute(
        "SELECT * FROM memory_versions WHERE player_id = ? ORDER BY revision DESC LIMIT 1",
        (player_id,),
    ).fetchone())


def _insert_version(repo, player_id: str, content: str, match_id: str | None,
                    previous: dict | None, source: dict) -> dict:
    version_id = models._uid()
    revision = (previous["revision"] if previous else 0) + 1
    repo.conn.execute(
        """INSERT INTO memory_versions
           (id, player_id, match_id, content, content_hash, revision,
            previous_id, source_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (version_id, player_id, match_id, content, _hash(content), revision,
         previous["id"] if previous else None,
         json.dumps(source, ensure_ascii=False, sort_keys=True), models._now()),
    )
    return get_memory_version(repo, version_id)


def freeze_match_memory(repo, match_id: str, player_id: str,
                        content: str | None = None,
                        source: dict | str | None = None) -> dict:
    """Record the exact initial prompt memory once per player and match."""
    from ..agent.memory import MAX_LONG_TERM_CHARS, validate_memory_text

    if not match_id:
        raise ValueError("match id is required")
    if content is not None:
        content = validate_memory_text(content, max_chars=MAX_LONG_TERM_CHARS, allow_empty=True)
    with repo.atomic():
        existing = repo.conn.execute(
            "SELECT * FROM match_memory_usage WHERE match_id = ? AND player_id = ?",
            (match_id, player_id),
        ).fetchone()
        if existing:
            version = get_memory_version(repo, existing["version_id"])
            if content is not None and version["content"] != content:
                raise MemoryConflictError("match memory is already frozen")
            return {**version, "usage_id": existing["id"], "used_in_match_id": match_id}
        previous = _latest(repo, player_id)
        if content is None:
            player = repo.get_player(player_id)
            content = (player.get("long_term_memory") or "") if player else (
                previous["content"] if previous else ""
            )
            content = validate_memory_text(content, max_chars=MAX_LONG_TERM_CHARS, allow_empty=True)
        version = previous
        if version is None or version["content"] != content:
            version = _insert_version(
                repo, player_id, content, None, previous, _source(source, "initial_snapshot"),
            )
        usage_id = models._uid()
        repo.conn.execute(
            "INSERT INTO match_memory_usage VALUES (?, ?, ?, ?, ?)",
            (usage_id, match_id, player_id, version["id"], models._now()),
        )
        return {**version, "usage_id": usage_id, "used_in_match_id": match_id}


def list_match_memory_usage(repo, match_id: str,
                            player_id: str | None = None) -> list[dict]:
    clause = " AND u.player_id = ?" if player_id is not None else ""
    params = (match_id, player_id) if player_id is not None else (match_id,)
    rows = repo.conn.execute(
        """SELECT v.*, u.id AS usage_id, u.match_id AS used_in_match_id,
                  u.created_at AS used_at FROM match_memory_usage u
           JOIN memory_versions v ON v.id = u.version_id WHERE u.match_id = ?"""
        + clause + " ORDER BY u.player_id", params,
    ).fetchall()
    return [_version(row) for row in rows]


def save_long_term_memory(repo, player_id: str, content: str, match_id: str | None,
                          expected_content: str | None = None,
                          source: dict | str | None = None) -> dict:
    from ..agent.memory import MAX_LONG_TERM_CHARS, validate_memory_text

    content = validate_memory_text(content, max_chars=MAX_LONG_TERM_CHARS)
    if expected_content is not None:
        # A manual repair may replace an oversized legacy value. The old text
        # is a comparison token and archival input, never new prompt memory.
        if not isinstance(expected_content, str):
            raise ValueError("expected memory must be text")
        expected_content = expected_content.strip()
    with repo.atomic():
        if match_id:
            existing = _version(repo.conn.execute(
                "SELECT * FROM memory_versions WHERE player_id = ? AND match_id = ?",
                (player_id, match_id),
            ).fetchone())
            if existing:
                if existing["content"] != content:
                    raise MemoryConflictError("match memory was already saved with different content")
                resolve_memory_failures(repo, player_id, match_id, "summary",
                                        version_id=existing["id"])
                return existing
        player = repo.get_player(player_id)
        previous = _latest(repo, player_id)
        original = (player.get("long_term_memory") or "") if player else (
            previous["content"] if previous else (expected_content or "")
        )
        current = original.strip()
        if match_id:
            current = validate_memory_text(current, max_chars=MAX_LONG_TERM_CHARS, allow_empty=True)
            usage = repo.conn.execute(
                "SELECT version_id FROM match_memory_usage WHERE match_id = ? AND player_id = ?",
                (match_id, player_id),
            ).fetchone()
            # Content alone misses A -> B -> A changes, including a newer
            # successful summary that deliberately retained the same lesson.
            if usage and (previous is None or previous["id"] != usage["version_id"]):
                raise MemoryConflictError("player memory version changed since the match started")
            if usage and previous["content"] != current:
                raise MemoryConflictError("player memory changed since the match started")
        if expected_content is not None and current != expected_content:
            raise MemoryConflictError("player memory changed since the match started")
        # Preserve an imported/legacy value before replacing it, even when the
        # caller did not explicitly freeze a starting snapshot.
        if previous is None or previous["content"] != current:
            previous = _insert_version(
                repo, player_id, original, None, previous, {"kind": "initial_snapshot"},
            )
        repo.update_player_long_term_memory(player_id, content)
        version = _insert_version(
            repo, player_id, content, match_id or None, previous,
            _source(source, "match_summary" if match_id else "manual_update"),
        )
        repo.add_agent_memory(player_id, "long_term", content, match_id=match_id)
        if match_id:
            resolve_memory_failures(repo, player_id, match_id, "summary", version_id=version["id"])
        return version


def save_reflection_memory(repo, table_hand_id: str, player_id: str, seat: str,
                           actual_role: str, reflection: str,
                           short_term_memory: str, match_id: str) -> str:
    from ..agent.memory import MAX_SHORT_TERM_CHARS, validate_memory_text

    short_term_memory = validate_memory_text(
        short_term_memory, max_chars=MAX_SHORT_TERM_CHARS, allow_empty=True,
    )
    with repo.atomic():
        reflection_id = repo.add_reflection(
            table_hand_id, player_id=player_id, seat=seat, actual_role=actual_role,
            reflection=reflection, short_term_memory=short_term_memory,
        )
        if short_term_memory:
            repo.add_agent_memory(player_id, "short_term", short_term_memory, match_id=match_id)
        resolve_memory_failures(repo, player_id, match_id, "reflection", table_hand_id=table_hand_id)
        return reflection_id


def record_memory_failure(repo, player_id: str, match_id: str, phase: str,
                          stage: str, error: BaseException | str,
                          table_hand_id: str | None = None) -> str:
    if phase not in {"reflection", "summary"} or stage not in {"generation", "persistence"}:
        raise ValueError("invalid memory failure phase or stage")
    # Exception messages may contain provider request headers or credentials.
    # Store only a type name or one of the intentionally safe application codes.
    safe_codes = {"empty_response", "memory_conflict", "cancelled", "invalid_response"}
    error_type = type(error).__name__ if isinstance(error, BaseException) else (
        error if error in safe_codes else "OperationFailed"
    )
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", error_type):
        error_type = "OperationFailed"
    failure_id = models._uid()
    repo.conn.execute(
        """INSERT INTO memory_failures
           (id, player_id, match_id, table_hand_id, phase, stage, error_type, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (failure_id, player_id, match_id, table_hand_id, phase, stage, error_type, models._now()),
    )
    repo.conn.commit()
    return failure_id


def resolve_memory_failures(repo, player_id: str, match_id: str, phase: str,
                            table_hand_id: str | None = None,
                            version_id: str | None = None) -> None:
    clause = " AND table_hand_id = ?" if table_hand_id is not None else ""
    params = [models._now(), version_id, player_id, match_id, phase]
    if table_hand_id is not None:
        params.append(table_hand_id)
    repo.conn.execute(
        """UPDATE memory_failures SET resolved_at = ?, resolution_version_id = ?
           WHERE player_id = ? AND match_id = ? AND phase = ? AND resolved_at IS NULL"""
        + clause, params,
    )
    repo.conn.commit()


def list_memory_failures(repo, player_id: str | None = None,
                         match_id: str | None = None,
                         unresolved_only: bool = False) -> list[dict]:
    clauses, params = [], []
    for column, value in (("player_id", player_id), ("match_id", match_id)):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    if unresolved_only:
        clauses.append("resolved_at IS NULL")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = repo.conn.execute(
        "SELECT * FROM memory_failures" + where + " ORDER BY created_at, id", params,
    ).fetchall()
    return [{**dict(row), "recovered": row["resolved_at"] is not None} for row in rows]
