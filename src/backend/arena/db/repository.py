"""High-level repository — wraps db/models functions with connection lifecycle.

Provides a single DatabaseRepository class that manages a sqlite3 connection
and exposes CRUD operations. Intended to be passed to MatchRunner/TableRunner
for persistence during match execution.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import models


class DatabaseRepository:
    """Repository wrapping a sqlite3 connection for arena data persistence.

    Usage:
        repo = DatabaseRepository("data/arena.db")
        repo.init()
        match_id = repo.create_match("Test Match", config, "seed123")
        # ... pass repo to MatchRunner ...
        repo.close()
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    # ── lifecycle ───────────────────────────────────────────────────────────────

    def init(self) -> None:
        """Initialize the database — create tables if not exist."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = models.init_db(self._db_path)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("DatabaseRepository not initialized. Call init() first.")
        return self._conn

    # ── agents ───────────────────────────────────────────────────────────────────

    def create_agent(
        self,
        name: str,
        provider: str,
        model: str,
        api_key: str,
        system_prompt_override: str | None = None,
        long_term_memory: str = "",
        agent_id: str = "",
    ) -> str:
        return models.insert_agent(
            self.conn, name, provider, model, api_key,
            system_prompt_override, long_term_memory, agent_id=agent_id,
        )

    def get_agent(self, agent_id: str) -> dict | None:
        return models.get_agent(self.conn, agent_id)

    def get_agent_by_name(self, name: str) -> dict | None:
        return models.get_agent_by_name(self.conn, name)

    def list_agents(self) -> list[dict]:
        return models.list_agents(self.conn)

    def update_agent_long_term_memory(self, agent_id: str, memory: str) -> None:
        models.update_agent_long_term_memory(self.conn, agent_id, memory)

    # ── matches ──────────────────────────────────────────────────────────────────

    def create_match(self, name: str, config: dict, seed: str) -> str:
        return models.insert_match(self.conn, name, config, seed)

    def get_match(self, match_id: str) -> dict | None:
        return models.get_match(self.conn, match_id)

    def list_matches(
        self, status: str | None = None, page: int = 1, page_size: int = 20
    ) -> tuple[list[dict], int]:
        return models.list_matches(self.conn, status, page, page_size)

    def update_match_status(
        self,
        match_id: str,
        status: str,
        **kwargs: Any,
    ) -> None:
        models.update_match_status(self.conn, match_id, status, **kwargs)

    # ── participants ────────────────────────────────────────────────────────────

    def add_participant(
        self,
        match_id: str,
        agent_id: str,
        team: str,
        seat_table_a: str | None = None,
        seat_table_b: str | None = None,
    ) -> str:
        return models.insert_participant(
            self.conn, match_id, agent_id, team, seat_table_a, seat_table_b,
        )

    def get_participants(self, match_id: str) -> list[dict]:
        return models.get_participants(self.conn, match_id)

    # ── hands ────────────────────────────────────────────────────────────────────

    def create_hand(
        self,
        match_id: str,
        hand_num: int,
        dealer: str,
        idle_seat: str,
        seed: str,
        is_tiebreaker: bool = False,
    ) -> str:
        return models.insert_hand(
            self.conn, match_id, hand_num, dealer, idle_seat, seed, is_tiebreaker,
        )

    def finish_hand(
        self,
        hand_id: str,
        diff_score_red: int,
        diff_score_blue: int,
        diff_capped: bool,
    ) -> None:
        models.finish_hand(self.conn, hand_id, diff_score_red, diff_score_blue, diff_capped)

    def get_hands_for_match(self, match_id: str) -> list[dict]:
        return models.get_hands_for_match(self.conn, match_id)

    # ── table_hands ─────────────────────────────────────────────────────────────

    def create_table_hand(self, hand_id: str, table: str) -> str:
        return models.insert_table_hand(self.conn, hand_id, table)

    def update_table_hand_result(
        self, table_hand_id: str, **kwargs: Any
    ) -> None:
        models.update_table_hand_result(self.conn, table_hand_id, **kwargs)

    def get_table_hand(self, table_hand_id: str) -> dict | None:
        return models.get_table_hand(self.conn, table_hand_id)

    # ── bidding_records ─────────────────────────────────────────────────────────

    def add_bidding_record(
        self,
        table_hand_id: str,
        seq: int,
        seat: str,
        bid: int,
        timestamp_ms: int,
    ) -> str:
        return models.insert_bidding_record(
            self.conn, table_hand_id, seq, seat, bid, timestamp_ms,
        )

    # ── play_actions ────────────────────────────────────────────────────────────

    def add_play_action(
        self,
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
        return models.insert_play_action(
            self.conn, table_hand_id, round_num, sub_round, seq,
            seat, action_type, cards, pattern, display, is_trusted, timestamp_ms,
        )

    # ── agent_thoughts ──────────────────────────────────────────────────────────

    def add_agent_thought(
        self,
        table_hand_id: str,
        agent_id: str,
        seat: str,
        phase: str,
        reasoning: str,
        decision: str,
        **kwargs: Any,
    ) -> str:
        return models.insert_agent_thought(
            self.conn, table_hand_id, agent_id, seat, phase,
            reasoning, decision, **kwargs,
        )

    # ── reflections ─────────────────────────────────────────────────────────────

    def add_reflection(
        self,
        table_hand_id: str,
        agent_id: str,
        seat: str,
        actual_role: str,
        reflection: str,
        short_term_memory: str,
    ) -> str:
        return models.insert_reflection(
            self.conn, table_hand_id, agent_id, seat,
            actual_role, reflection, short_term_memory,
        )

    # ── agent_memories ──────────────────────────────────────────────────────────

    def add_agent_memory(
        self,
        agent_id: str,
        memory_type: str,
        content: str,
        match_id: str | None = None,
    ) -> str:
        return models.insert_agent_memory(
            self.conn, agent_id, memory_type, content, match_id,
        )

    def get_agent_memories(
        self,
        agent_id: str,
        match_id: str | None = None,
        memory_type: str | None = None,
    ) -> list[dict]:
        return models.get_agent_memories(self.conn, agent_id, match_id, memory_type)

    # ── llm_call_logs ───────────────────────────────────────────────────────────

    def add_llm_call_log(
        self,
        agent_id: str,
        phase: str,
        provider: str,
        model: str,
        success: bool,
        **kwargs: Any,
    ) -> str:
        return models.insert_llm_call_log(
            self.conn, agent_id, phase, provider, model, success, **kwargs,
        )

    # ── initial_hands / remaining_hands ──────────────────────────────────────────

    def add_initial_hand(
        self, table_hand_id: str, seat: str, cards: list
    ) -> str:
        return models.insert_initial_hand(self.conn, table_hand_id, seat, cards)

    def add_remaining_hand(
        self, table_hand_id: str, seat: str, cards: list
    ) -> str:
        return models.insert_remaining_hand(self.conn, table_hand_id, seat, cards)

    # ── query helpers for API routes ────────────────────────────────────────────

    def get_bidding_records(self, table_hand_id: str) -> list[dict]:
        return models.get_bidding_records(self.conn, table_hand_id)

    def get_play_actions_for_table(self, table_hand_id: str) -> list[dict]:
        return models.get_play_actions_for_table(self.conn, table_hand_id)

    def get_agent_thoughts_for_table(self, table_hand_id: str) -> list[dict]:
        return models.get_agent_thoughts_for_table(self.conn, table_hand_id)

    def get_reflections_for_table(self, table_hand_id: str) -> list[dict]:
        return models.get_reflections_for_table(self.conn, table_hand_id)

    def get_initial_hands_for_table(self, table_hand_id: str) -> dict[str, list]:
        return models.get_initial_hands_for_table(self.conn, table_hand_id)

    def get_remaining_hands_for_table(self, table_hand_id: str) -> dict[str, list]:
        return models.get_remaining_hands_for_table(self.conn, table_hand_id)

    def get_hand(self, hand_id: str) -> dict | None:
        return models.get_hand(self.conn, hand_id)

    def get_hand_by_num(self, match_id: str, hand_num: int) -> dict | None:
        return models.get_hand_by_num(self.conn, match_id, hand_num)

    def get_table_hands_for_hand(self, hand_id: str) -> list[dict]:
        return models.get_table_hands_for_hand(self.conn, hand_id)

    def get_table_hand(self, table_hand_id: str) -> dict | None:
        return models.get_table_hand(self.conn, table_hand_id)

    def update_agent_by_id(self, agent_id: str, **kwargs: str) -> bool:
        return models.update_agent_by_id(self.conn, agent_id, **kwargs)

    def delete_agent_from_db(self, agent_id: str) -> bool:
        return models.delete_agent_from_db(self.conn, agent_id)

    def delete_match_from_db(self, match_id: str) -> bool:
        return models.delete_match_from_db(self.conn, match_id)
