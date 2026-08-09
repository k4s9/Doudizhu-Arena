"""High-level repository — wraps db/models functions with connection lifecycle.

Provides a single DatabaseRepository class that manages a sqlite3 connection
and exposes CRUD operations. Intended to be passed to MatchRunner/TableRunner
for persistence during match execution.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from . import models
from ..security.credentials import decrypt_secret, encrypt_secret, master_key_configured


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
        """Initialize the database — create tables and run migrations."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = models.init_db(self._db_path)
        # Upgrade legacy plaintext rows when the deployment has a master key.
        if master_key_configured():
            rows = self.conn.execute("SELECT id, api_key FROM player_configs").fetchall()
            for row in rows:
                value = row[1] or ""
                if value and not value.startswith("enc:v1:") and not value.startswith("${"):
                    self.conn.execute("UPDATE player_configs SET api_key = ?, updated_at = updated_at WHERE id = ?", (encrypt_secret(value), row[0]))
            self.conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("DatabaseRepository not initialized. Call init() first.")
        return self._conn

    # ── player_configs ──────────────────────────────────────────────────────────

    def create_player_config(
        self,
        name: str,
        provider: str,
        model: str,
        api_key: str,
        base_url: str | None = None,
        system_prompt: str | None = None,
        config_id: str = "",
    ) -> str:
        return models.insert_player_config(
            self.conn, name, provider, model, encrypt_secret(api_key),
            base_url=base_url, system_prompt=system_prompt, config_id=config_id,
        )

    def get_player_config(self, config_id: str) -> dict | None:
        return self._decrypt_config(models.get_player_config(self.conn, config_id))

    def get_player_config_by_name(self, name: str) -> dict | None:
        return self._decrypt_config(models.get_player_config_by_name(self.conn, name))

    def list_player_configs(self) -> list[dict]:
        return [self._decrypt_config(item) for item in models.list_player_configs(self.conn)]

    def update_player_config(self, config_id: str, **kwargs: Any) -> bool:
        if "api_key" in kwargs:
            kwargs["api_key"] = encrypt_secret(kwargs["api_key"])
        return models.update_player_config(self.conn, config_id, **kwargs)

    def delete_player_config(self, config_id: str) -> bool:
        return models.delete_player_config(self.conn, config_id)

    def count_players_for_config(self, config_id: str) -> int:
        return models.count_players_for_config(self.conn, config_id)

    # ── players ─────────────────────────────────────────────────────────────────

    def create_player(
        self,
        config_id: str,
        display_name: str,
        long_term_memory: str = "",
        player_id: str = "",
    ) -> str:
        return models.insert_player(
            self.conn, config_id, display_name,
            long_term_memory=long_term_memory, player_id=player_id,
        )

    def get_player(self, player_id: str) -> dict | None:
        return models.get_player(self.conn, player_id)

    def get_player_with_config(self, player_id: str) -> dict | None:
        return self._decrypt_config(models.get_player_with_config(self.conn, player_id))

    @staticmethod
    def _decrypt_config(config: dict | None) -> dict | None:
        if config and "api_key" in config:
            config = dict(config)
            config["api_key"] = decrypt_secret(config.get("api_key", ""))
        return config

    def list_players(self, config_id: str | None = None) -> list[dict]:
        return models.list_players(self.conn, config_id=config_id)

    def list_players_with_config(self, config_id: str | None = None) -> list[dict]:
        return models.list_players_with_config(self.conn, config_id=config_id)

    def update_player_long_term_memory(self, player_id: str, memory: str) -> None:
        models.update_player_long_term_memory(self.conn, player_id, memory)

    def update_player_stats(
        self,
        player_id: str,
        *,
        matches_played_delta: int = 0,
        matches_won_delta: int = 0,
        total_score_delta: int = 0,
    ) -> None:
        models.update_player_stats(
            self.conn, player_id,
            matches_played_delta=matches_played_delta,
            matches_won_delta=matches_won_delta,
            total_score_delta=total_score_delta,
        )

    def update_player_by_id(self, player_id: str, **kwargs: Any) -> bool:
        return models.update_player_by_id(self.conn, player_id, **kwargs)

    def delete_player(self, player_id: str) -> bool:
        return models.delete_player(self.conn, player_id)

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
        player_id: str,
        team: str,
        seat_table_a: str | None = None,
        seat_table_b: str | None = None,
    ) -> str:
        return models.insert_participant(
            self.conn, match_id, player_id, team, seat_table_a, seat_table_b,
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
        player_id: str,
        seat: str,
        phase: str,
        reasoning: str,
        decision: str,
        **kwargs: Any,
    ) -> str:
        return models.insert_agent_thought(
            self.conn, table_hand_id, player_id, seat, phase,
            reasoning, decision, **kwargs,
        )

    # ── reflections ─────────────────────────────────────────────────────────────

    def add_reflection(
        self,
        table_hand_id: str,
        player_id: str,
        seat: str,
        actual_role: str,
        reflection: str,
        short_term_memory: str,
    ) -> str:
        return models.insert_reflection(
            self.conn, table_hand_id, player_id, seat,
            actual_role, reflection, short_term_memory,
        )

    # ── agent_memories ──────────────────────────────────────────────────────────

    def add_agent_memory(
        self,
        player_id: str,
        memory_type: str,
        content: str,
        match_id: str | None = None,
    ) -> str:
        return models.insert_agent_memory(
            self.conn, player_id, memory_type, content, match_id,
        )

    def get_agent_memories(
        self,
        player_id: str,
        match_id: str | None = None,
        memory_type: str | None = None,
    ) -> list[dict]:
        return models.get_agent_memories(self.conn, player_id, match_id, memory_type)

    # ── llm_call_logs ───────────────────────────────────────────────────────────

    def add_llm_call_log(
        self,
        player_id: str,
        phase: str,
        provider: str,
        model: str,
        success: bool,
        **kwargs: Any,
    ) -> str:
        return models.insert_llm_call_log(
            self.conn, player_id, phase, provider, model, success, **kwargs,
        )

    # ── evaluation ──────────────────────────────────────────────────────────────

    def create_experiment(self, experiment_id: str, spec: dict, spec_sha256: str) -> str:
        return models.insert_experiment(self.conn, experiment_id, spec, spec_sha256)

    def create_evaluation_run(self, experiment_id: str, manifest: dict, manifest_sha256: str) -> str:
        return models.insert_evaluation_run(self.conn, experiment_id, manifest, manifest_sha256)

    def create_evaluation_task(self, run_id: str, variant_id: str, seed: str, seat_rotation: int) -> str:
        return models.insert_evaluation_task(self.conn, run_id, variant_id, seed, seat_rotation)

    def update_evaluation_task(self, task_id: str, status: str, *, match_id: str | None = None, failure_reason: str | None = None) -> None:
        models.update_evaluation_task(self.conn, task_id, status, match_id=match_id, failure_reason=failure_reason)

    def get_evaluation_run(self, run_id: str) -> dict | None:
        return models.get_evaluation_run(self.conn, run_id)

    def get_evaluation_tasks(self, run_id: str) -> list[dict]:
        return models.get_evaluation_tasks(self.conn, run_id)

    def save_memory_training_checkpoint(
        self, run_id: str, memories: dict[str, str], completed_task_id: str | None = None,
    ) -> None:
        models.upsert_memory_training_checkpoint(
            self.conn, run_id, memories, completed_task_id=completed_task_id,
        )

    def get_memory_training_checkpoint(self, run_id: str) -> dict | None:
        return models.get_memory_training_checkpoint(self.conn, run_id)

    def update_evaluation_run_status(self, run_id: str, status: str, failure_reason: str | None = None) -> None:
        models.update_evaluation_run_status(self.conn, run_id, status, failure_reason)

    def list_evaluation_runs(self) -> list[dict]:
        return models.list_evaluation_runs(self.conn)

    def get_experiment(self, experiment_db_id: str) -> dict | None:
        return models.get_experiment(self.conn, experiment_db_id)

    def add_model_snapshot(self, run_id: str, snapshot: dict) -> str:
        return models.insert_model_snapshot(self.conn, run_id, snapshot)

    def add_decision_event(self, *, phase: str, attempt: int, **kwargs: Any) -> str:
        return models.insert_decision_event(self.conn, phase=phase, attempt=attempt, **kwargs)

    def get_decision_events(self, run_id: str | None = None, match_id: str | None = None) -> list[dict]:
        return models.get_decision_events(self.conn, run_id=run_id, match_id=match_id)

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

    def get_player_hand_records(self, player_id: str | None = None) -> list[dict]:
        return models.get_player_hand_records(self.conn, player_id)

    def get_table_hand(self, table_hand_id: str) -> dict | None:
        return models.get_table_hand(self.conn, table_hand_id)

    def delete_match_from_db(self, match_id: str) -> bool:
        return models.delete_match_from_db(self.conn, match_id)
