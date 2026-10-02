"""Single-server match ownership, restart recovery and bounded shutdown."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid

from ..db import models

logger = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS match_start_claims (
    match_id TEXT PRIMARY KEY REFERENCES matches(id) ON DELETE CASCADE,
    acquired_at TEXT NOT NULL, released_at TEXT, release_reason TEXT
);
CREATE TABLE IF NOT EXISTS player_match_claims (
    id TEXT PRIMARY KEY, player_id TEXT NOT NULL,
    match_id TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    acquired_at TEXT NOT NULL, released_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_player_match_claim_active
    ON player_match_claims(player_id) WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_player_match_claim_match
    ON player_match_claims(match_id, released_at);
"""


class MatchStartConflict(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class MatchExecutionInterrupted(RuntimeError):
    """A server stopped before the match's learning phase completed."""


def init_match_lifecycle(repo) -> None:
    count = repo.conn.execute(
        """SELECT COUNT(*) FROM sqlite_master WHERE name IN
           ('match_start_claims', 'player_match_claims',
            'idx_player_match_claim_active', 'idx_player_match_claim_match')"""
    ).fetchone()[0]
    if count != 4:
        with repo.atomic():
            for statement in DDL.split(";"):
                if statement.strip():
                    repo.conn.execute(statement)


def claim_match_start(repo, match_id: str, player_ids, *, writes_memory: bool) -> str:
    """Claim and mark running in one transaction before scheduling a runner."""
    init_match_lifecycle(repo)
    now = models._now()
    try:
        with repo.atomic():
            changed = repo.conn.execute(
                "UPDATE matches SET status='running', started_at=? WHERE id=? AND status='created'",
                (now, match_id),
            )
            if changed.rowcount != 1:
                raise MatchStartConflict("MATCH_ALREADY_STARTED", "Match already started")
            repo.conn.execute(
                "INSERT INTO match_start_claims(match_id, acquired_at) VALUES (?, ?)",
                (match_id, now),
            )
            if writes_memory:
                for player_id in sorted(set(player_ids)):
                    busy = repo.conn.execute(
                        "SELECT match_id FROM player_match_claims WHERE player_id=? AND released_at IS NULL",
                        (player_id,),
                    ).fetchone()
                    if busy:
                        raise MatchStartConflict(
                            "PLAYER_BUSY", "A player is still playing or saving memory in another match",
                        )
                    repo.conn.execute(
                        "INSERT INTO player_match_claims(id, player_id, match_id, acquired_at) VALUES (?, ?, ?, ?)",
                        (uuid.uuid4().hex, player_id, match_id, now),
                    )
    except sqlite3.IntegrityError as exc:
        raise MatchStartConflict("MATCH_ALREADY_CLAIMED", "Match or player already claimed") from exc
    return now


def release_match_claims(repo, match_id: str, reason: str = "completed") -> None:
    with repo.atomic():
        now = models._now()
        repo.conn.execute(
            "UPDATE player_match_claims SET released_at=? WHERE match_id=? AND released_at IS NULL",
            (now, match_id),
        )
        repo.conn.execute(
            """UPDATE match_start_claims SET released_at=?, release_reason=?
               WHERE match_id=? AND released_at IS NULL""",
            (now, reason, match_id),
        )


def match_is_claimed(repo, match_id: str) -> bool:
    init_match_lifecycle(repo)
    return repo.conn.execute(
        "SELECT 1 FROM match_start_claims WHERE match_id=? AND released_at IS NULL", (match_id,),
    ).fetchone() is not None


def player_memory_is_claimed(repo, player_id: str) -> bool:
    init_match_lifecycle(repo)
    return repo.conn.execute(
        "SELECT 1 FROM player_match_claims WHERE player_id=? AND released_at IS NULL",
        (player_id,),
    ).fetchone() is not None


def mark_match_interrupted(repo, match_id: str) -> None:
    """Keep settled scores and report unfinished summary work separately."""
    match = repo.get_match(match_id)
    if match is None:
        return
    if match["status"] in {"created", "running", "paused"}:
        repo.update_match_status(match_id, "interrupted")
    elif match["status"] == "finished":
        players = repo.conn.execute(
            "SELECT player_id FROM player_match_claims WHERE match_id=? AND released_at IS NULL",
            (match_id,),
        ).fetchall()
        for row in players:
            # Recovery only needs the provider kind. It must still work if the
            # credential master key changed or is unavailable after a restart.
            player = repo.conn.execute(
                """SELECT c.provider FROM players p
                   JOIN player_configs c ON c.id=p.config_id WHERE p.id=?""",
                (row["player_id"],),
            ).fetchone()
            if player and player["provider"] == "random":
                continue  # Random agents intentionally produce no summary memory.
            saved = repo.conn.execute(
                "SELECT 1 FROM memory_versions WHERE player_id=? AND match_id=?",
                (row["player_id"], match_id),
            ).fetchone()
            failed = repo.list_memory_failures(player_id=row["player_id"], match_id=match_id,
                                               unresolved_only=True)
            if not saved and not any(item["phase"] == "summary" for item in failed):
                repo.record_memory_failure(row["player_id"], match_id, "summary", "generation",
                                           MatchExecutionInterrupted())


def recover_interrupted_matches(repo, page_size: int = 200) -> int:
    """Drain changing result sets from page one, so no orphan is skipped."""
    init_match_lifecycle(repo)
    recovered = 0
    while True:
        claims = repo.conn.execute(
            "SELECT match_id FROM match_start_claims WHERE released_at IS NULL ORDER BY match_id LIMIT ?",
            (page_size,),
        ).fetchall()
        if not claims:
            break
        for row in claims:
            with repo.atomic():
                mark_match_interrupted(repo, row["match_id"])
                release_match_claims(repo, row["match_id"], "server_restart")
            recovered += 1
    for status in ("running", "paused"):
        while True:
            matches, _ = repo.list_matches(status=status, page=1, page_size=page_size)
            if not matches:
                break
            for match in matches:
                repo.update_match_status(match["id"], "interrupted")
                recovered += 1
    return recovered


def ensure_task_registry(app) -> None:
    for name in ("active_matches", "match_tasks", "match_scopes", "evaluation_tasks",
                 "match_cleanup_pending"):
        if not hasattr(app.state, name):
            setattr(app.state, name, {})
    if not hasattr(app.state, "detached_match_tasks"):
        app.state.detached_match_tasks = set()


def consume_task_result(task) -> None:
    if not task.cancelled():
        task.exception()


def _finish_claim_cleanup(app, match_id: str, cleanup: dict) -> None:
    if cleanup["scope"].active:
        raise RuntimeError("cannot release claims before execution is revoked")
    with app.state.db_repo.atomic():
        if cleanup["reason"] != "completed":
            mark_match_interrupted(app.state.db_repo, match_id)
        release_match_claims(app.state.db_repo, match_id, cleanup["reason"])
    app.state.match_cleanup_pending.pop(match_id, None)


async def cleanup_match_claims(app, match_id: str, scope, reason: str,
                               attempts: int = 3, retry_delay: float = .05) -> bool:
    """Fence this process's owned task before retrying its durable cleanup."""
    ensure_task_registry(app)
    if app.state.match_scopes.get(match_id) is not scope:
        return False  # Shutdown or another cleanup already detached this task.
    try:
        scope.revoke()
    except (Exception, asyncio.CancelledError) as exc:
        logger.warning("Could not finalize cancelled call evidence: %s", type(exc).__name__)
    if scope.active:
        return False
    cleanup = {"scope": scope, "reason": reason,
               "task": app.state.match_tasks.get(match_id)}
    # Register before any await: cancellation or another storage failure must
    # not erase the only in-process record capable of releasing these claims.
    app.state.match_cleanup_pending[match_id] = cleanup
    for attempt in range(attempts):
        try:
            _finish_claim_cleanup(app, match_id, cleanup)
            return True
        except sqlite3.OperationalError as exc:
            cleanup["error_type"] = type(exc).__name__
            if attempt + 1 < attempts:
                await asyncio.sleep(retry_delay)
        except Exception as exc:
            cleanup["error_type"] = type(exc).__name__
            break
    logger.error("Match %s cleanup pending after storage failure; retrying a start request "
                 "will retry cleanup (%s)", match_id, cleanup.get("error_type", "unknown"))
    return False


def recover_pending_match_cleanups(app) -> int:
    """Retry only locally owned, revoked tasks that have actually finished."""
    ensure_task_registry(app)
    recovered = 0
    for match_id, cleanup in list(app.state.match_cleanup_pending.items()):
        task = cleanup.get("task") or app.state.match_tasks.get(match_id)
        if cleanup["scope"].active or (task is not None and not task.done()):
            continue
        try:
            _finish_claim_cleanup(app, match_id, cleanup)
            recovered += 1
        except Exception as exc:
            cleanup["error_type"] = type(exc).__name__
            logger.warning("Match %s cleanup still pending: %s", match_id, type(exc).__name__)
    return recovered


async def shutdown_matches(app, timeout_seconds: float = 5.0) -> set:
    """Cancel managed work; fence a cancellation-resistant task before DB close."""
    ensure_task_registry(app)
    app.state.shutting_down = True
    repo = app.state.db_repo
    for runner in list(getattr(app.state, "active_evaluations", {}).values()):
        runner.cancel()
    tasks = set(app.state.match_tasks.values()) | set(app.state.evaluation_tasks.values())
    for scope in list(app.state.match_scopes.values()):
        scope.cancel_requested = True
    for task in tasks:
        task.cancel()
    pending = set()
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
    for match_id, scope in list(app.state.match_scopes.items()):
        await cleanup_match_claims(app, match_id, scope, "shutdown")
        app.state.active_matches.pop(match_id, None)
        app.state.match_tasks.pop(match_id, None)
        app.state.match_scopes.pop(match_id, None)
    recover_pending_match_cleanups(app)
    for task in pending:
        app.state.detached_match_tasks.add(task)
        task.add_done_callback(app.state.detached_match_tasks.discard)
        task.add_done_callback(consume_task_result)
    # Evaluation workers already have revocable scopes. Its API task registry
    # lets shutdown wait for the outer status/report cleanup before DB close.
    for runner in list(getattr(app.state, "active_evaluations", {}).values()):
        scope = getattr(runner, "_scope", None)
        if scope:
            try:
                scope.revoke()
            except (Exception, asyncio.CancelledError) as exc:
                logger.warning("Could not finalize cancelled evaluation: %s", type(exc).__name__)
    remaining_evaluations = {task for task in app.state.evaluation_tasks.values() if not task.done()}
    for task in remaining_evaluations:
        task.cancel()
    if remaining_evaluations:
        _, remaining_evaluations = await asyncio.wait(remaining_evaluations, timeout=1.0)
    return remaining_evaluations


def close_database_after_cleanup(repo, pending: set) -> None:
    """An evaluation's outer cleanup must finish before its raw repo closes."""
    remaining = {task for task in pending if not task.done()}
    if not remaining:
        repo.close()
        return

    def finished(task):
        remaining.discard(task)
        if not remaining:
            repo.close()

    for task in remaining:
        task.add_done_callback(finished)
