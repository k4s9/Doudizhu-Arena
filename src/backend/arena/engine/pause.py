"""Pause/resume manager with DB-backed state persistence.

Pause granularity: pauses take effect after the current play/bid completes.
State is serialized to a DB temporary table on pause and cleared on resume.
Hard crashes lose in-memory state (accepted limitation for Phase 1).
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class PauseState(StrEnum):
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"


# ── persistence interface ────────────────────────────────────────────────────


class PauseStore(Protocol):
    """Protocol for persisting pause state. Implementations: InMemoryStore, SqliteStore."""

    def save(self, table: str, state_json: str) -> None: ...
    def load(self, table: str) -> str | None: ...
    def delete(self, table: str) -> None: ...


class InMemoryPauseStore:
    """In-memory store for testing / no-DB scenarios."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def save(self, table: str, state_json: str) -> None:
        self._store[table] = state_json

    def load(self, table: str) -> str | None:
        return self._store.get(table)

    def delete(self, table: str) -> None:
        self._store.pop(table, None)


# ── manager ──────────────────────────────────────────────────────────────────


class PauseManager:
    """Per-table pause manager.

    Thread-safe for use by the game engine thread and the API thread.
    """

    def __init__(self, table: str, store: PauseStore | None = None) -> None:
        self.table = table
        self._store = store or InMemoryPauseStore()
        self._state = PauseState.RUNNING
        self._lock = threading.Lock()
        self._paused_at_hand: int = 0
        self._reason: str = ""

    # ── API ─────────────────────────────────────────────────────────────

    @property
    def state(self) -> PauseState:
        with self._lock:
            return self._state

    @property
    def is_paused(self) -> bool:
        return self.state == PauseState.PAUSED

    def request_pause(self, reason: str = "user_requested") -> None:
        """Signal that a pause is desired. Takes effect after current play/bid."""
        with self._lock:
            if self._state == PauseState.RUNNING:
                self._state = PauseState.PAUSE_REQUESTED
                self._reason = reason

    def check_pause(self) -> bool:
        """Called by game engine after each play/bid completes.
        Returns True if the engine should now pause.
        """
        with self._lock:
            if self._state == PauseState.PAUSE_REQUESTED:
                self._state = PauseState.PAUSED
                return True
            return False

    def resume(self) -> None:
        """Resume from pause. Clears persisted state."""
        with self._lock:
            self._state = PauseState.RUNNING
            self._reason = ""
            self._store.delete(self.table)

    # ── persistence ─────────────────────────────────────────────────────

    def persist_state(self, state_dict: dict[str, Any]) -> None:
        """Serialize and persist the current game state."""
        state_dict["_pause_reason"] = self._reason
        state_dict["_paused_at_hand"] = state_dict.get("hand_num", 0)
        self._store.save(self.table, json.dumps(state_dict, default=str))

    def load_state(self) -> dict[str, Any] | None:
        """Load persisted state. Returns None if no state exists."""
        raw = self._store.load(self.table)
        if raw is None:
            return None
        return json.loads(raw)

    @property
    def paused_at_hand(self) -> int:
        return self._paused_at_hand

    @property
    def reason(self) -> str:
        return self._reason
