"""M4 end-to-end integration tests: full match with DB persistence.

Tests that the full pipeline (MatchRunner + LLM Agent + SQLite) works
and all expected data flows to the database.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest

from arena.agent.llm_agent import LLMAgent
from arena.db.models import init_db
from arena.db.repository import DatabaseRepository
from arena.engine.deck import Deck
from arena.llm.base import AbstractLLMProvider
from arena.tournament.match import MatchConfig, MatchRunner
from arena.tournament.seating import assign_seating, get_dealer_idle
from arena.tournament.table import TableRunner


# ── mock provider ────────────────────────────────────────────────────────────────


class M4MockProvider(AbstractLLMProvider):
    """Mock provider for M4 integration testing."""

    def __init__(self, seed: int = 42) -> None:
        import random
        self._rng = random.Random(seed)
        self._bid_count = 0
        self._play_count = 0
        self._reflection_count = 0
        self._summary_count = 0
        self.last_usage = None

    @property
    def provider_name(self) -> str:
        return "m4-mock"

    @property
    def model(self) -> str:
        return "m4-mock-model"

    async def generate(self, user_prompt: str, system_prompt: str = "") -> str:
        combined = system_prompt + user_prompt

        if "叫分规则" in system_prompt:
            return self._bidding(user_prompt)
        elif "出牌规则" in system_prompt:
            return self._playing(user_prompt)
        elif "复盘" in system_prompt:
            return self._reflection()
        elif "赛后总结" in system_prompt:
            return self._summary()
        else:
            return '{"reasoning": "ok", "bid": 0}'

    def _bidding(self, prompt: str) -> str:
        self._bid_count += 1
        import re
        m = re.search(r'当前最高叫分：(\d)分', prompt)
        if m:
            high_bid = int(m.group(1))
            if high_bid >= 3:
                return '{"reasoning": "max", "bid": 0}'
            return f'{{"reasoning": "raise", "bid": {high_bid + 1}}}'
        if "（尚无叫分记录）" in prompt:
            return '{"reasoning": "open", "bid": 1}'
        if "当前尚无叫分" in prompt:
            return '{"reasoning": "decent", "bid": 1}'
        return '{"reasoning": "pass", "bid": 0}'

    def _playing(self, prompt: str) -> str:
        self._play_count += 1
        if "新一轮" in prompt:
            cards = self._parse_hand(prompt)
            if cards:
                return (
                    '{"reasoning": "lead", "action": {"type": "play",'
                    f' "cards": ["{cards[0]}"]}}}}'
                )
            return '{"reasoning": "no cards", "action": {"type": "pass"}}'
        return '{"reasoning": "pass", "action": {"type": "pass"}}'

    def _parse_hand(self, prompt: str) -> list[str]:
        import re
        m = re.search(r'你的手牌（\d+张）：\n(.+?)(?:\n\n|\n[A-Z])', prompt, re.DOTALL)
        if m:
            return m.group(1).strip().split()
        return []

    def _reflection(self) -> str:
        self._reflection_count += 1
        return (
            '{"reflection": "Good hand. Made reasonable decisions.",'
            ' "short_term_memory": "Opponents passive. Continue aggressive bidding."}'
        )

    def _summary(self) -> str:
        self._summary_count += 1
        return (
            '{"summary": "Match completed successfully.",'
            ' "long_term_memory": "Prefer aggressive bidding on strong hands."}'
        )


# ── fixtures ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def db_repo():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="arena_test_")
    os.close(fd)
    repo = DatabaseRepository(path)
    repo.init()
    yield repo
    repo.close()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def db_path():
    """Create a temporary database path and clean up after."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="arena_test_")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


def make_agents(agent_ids: list[str]) -> dict[str, LLMAgent]:
    """Create LLM agents with mock providers."""
    agents = {}
    for i, aid in enumerate(agent_ids):
        mock = M4MockProvider(seed=100 + i)
        agent = LLMAgent(aid, mock)
        agents[aid] = agent
    return agents


# ── DB model tests ──────────────────────────────────────────────────────────────


class TestDBInit:
    """Tests for schema initialization and basic CRUD."""

    def test_schema_creation(self, db_path):
        conn = init_db(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = {t[0] for t in tables}
        expected = {
            "player_configs", "players", "matches", "match_participants", "hands",
            "table_hands", "bidding_records", "play_actions",
            "agent_thoughts", "reflections", "agent_memories",
            "llm_call_logs", "initial_hands", "remaining_hands",
        }
        assert expected.issubset(table_names), f"Missing tables: {expected - table_names}"
        # Old agents table must NOT exist
        assert "agents" not in table_names, "agents table should have been removed"
        conn.close()

    def test_foreign_keys_enabled(self, db_path):
        conn = init_db(db_path)
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
        conn.close()


class TestPlayerConfigCRUD:
    """Tests for player config persistence."""

    def test_create_and_get_config(self, db_repo):
        cid = db_repo.create_player_config("Test-Config", "claude", "claude-opus-4-7", "sk-test")
        assert cid
        config = db_repo.get_player_config(cid)
        assert config is not None
        assert config["name"] == "Test-Config"
        assert config["provider"] == "claude"

    def test_get_config_by_name(self, db_repo):
        db_repo.create_player_config("ByName", "openai", "gpt-4o", "sk-test")
        config = db_repo.get_player_config_by_name("ByName")
        assert config is not None
        assert config["model"] == "gpt-4o"

    def test_list_configs(self, db_repo):
        db_repo.create_player_config("C1", "claude", "m1", "k1")
        db_repo.create_player_config("C2", "openai", "m2", "k2")
        configs = db_repo.list_player_configs()
        assert len(configs) == 2


class TestPlayerCRUD:
    """Tests for player persistence."""

    def test_create_and_get_player(self, db_repo):
        cid = db_repo.create_player_config("PC", "openai", "gpt-4o", "sk-test")
        pid = db_repo.create_player(cid, "Player One")
        player = db_repo.get_player(pid)
        assert player is not None
        assert player["display_name"] == "Player One"
        assert player["config_id"] == cid

    def test_get_player_with_config(self, db_repo):
        cid = db_repo.create_player_config("PC2", "openai", "gpt-4o", "sk-test",
                                           base_url="https://test.api.com")
        pid = db_repo.create_player(cid, "Player Two")
        player = db_repo.get_player_with_config(pid)
        assert player is not None
        assert player["display_name"] == "Player Two"
        assert player["provider"] == "openai"
        assert player["model"] == "gpt-4o"
        assert player["base_url"] == "https://test.api.com"

    def test_update_long_term_memory(self, db_repo):
        cid = db_repo.create_player_config("PC3", "claude", "m1", "k1")
        pid = db_repo.create_player(cid, "Memory Player")
        db_repo.update_player_long_term_memory(pid, "Updated memory")
        player = db_repo.get_player(pid)
        assert player["long_term_memory"] == "Updated memory"

    def test_list_players(self, db_repo):
        cid = db_repo.create_player_config("PC4", "openai", "m1", "k1")
        db_repo.create_player(cid, "P1")
        db_repo.create_player(cid, "P2")
        players = db_repo.list_players()
        assert len(players) == 2

    def test_player_stats_update(self, db_repo):
        cid = db_repo.create_player_config("PC5", "openai", "m", "k")
        pid = db_repo.create_player(cid, "Stats Player")
        db_repo.update_player_stats(pid, matches_played_delta=1, matches_won_delta=1, total_score_delta=5)
        player = db_repo.get_player(pid)
        assert player["matches_played"] == 1
        assert player["matches_won"] == 1
        assert player["total_score"] == 5


class TestMatchCRUD:
    """Tests for match persistence."""

    def test_create_and_get_match(self, db_repo):
        mid = db_repo.create_match("Test Match", {"total_hands": 20}, "seed-123")
        match = db_repo.get_match(mid)
        assert match["name"] == "Test Match"
        assert match["status"] == "created"
        config = match["config"]
        if isinstance(config, str):
            config = json.loads(config)
        assert config["total_hands"] == 20

    def test_list_matches_by_status(self, db_repo):
        db_repo.create_match("M1", {}, "s1")
        mid2 = db_repo.create_match("M2", {}, "s2")
        db_repo.update_match_status(mid2, "running", started_at="now")
        matches, total = db_repo.list_matches(status="running")
        assert total == 1
        assert matches[0]["name"] == "M2"

    def test_update_match_status(self, db_repo):
        mid = db_repo.create_match("UpdateTest", {}, "s")
        db_repo.update_match_status(mid, "running", started_at="2024-01-01T00:00:00Z")
        m = db_repo.get_match(mid)
        assert m["status"] == "running"
        assert m["started_at"] == "2024-01-01T00:00:00Z"

    def test_participants(self, db_repo):
        mid = db_repo.create_match("PTest", {}, "s")
        # Create config + players for FK constraint
        cid = db_repo.create_player_config("conf", "claude", "m", "k")
        pid1 = db_repo.create_player(cid, "player-1")
        pid2 = db_repo.create_player(cid, "player-2")
        db_repo.add_participant(mid, pid1, "red", seat_table_a="S")
        db_repo.add_participant(mid, pid2, "blue", seat_table_a="E")
        participants = db_repo.get_participants(mid)
        assert len(participants) == 2


class TestHandCRUD:
    """Tests for hand persistence."""

    def test_hand_lifecycle(self, db_repo):
        mid = db_repo.create_match("HTest", {}, "s")
        hid = db_repo.create_hand(mid, 1, "S", "W", "seed-h1")
        hands = db_repo.get_hands_for_match(mid)
        assert len(hands) == 1
        assert hands[0]["hand_num"] == 1

        db_repo.finish_hand(hid, 3, 0, False)
        hands = db_repo.get_hands_for_match(mid)
        assert hands[0]["status"] == "finished"
        assert hands[0]["diff_score_red"] == 3

    def test_table_hand_lifecycle(self, db_repo):
        mid = db_repo.create_match("THTest", {}, "s")
        hid = db_repo.create_hand(mid, 1, "S", "W", "s1")
        thid = db_repo.create_table_hand(hid, "A")
        th = db_repo.get_table_hand(thid)
        assert th["status"] == "dealing"

        db_repo.update_table_hand_result(
            thid, status="finished", landlord_seat="S", final_bid=2,
            winner_team="red", winner_role="landlord",
            base_score=2, multiplier=2, final_score=4, bombs_played=1,
        )
        th = db_repo.get_table_hand(thid)
        assert th["status"] == "finished"
        assert th["final_score"] == 4


class TestPlayRecords:
    """Tests for play action and bidding record persistence."""

    def test_bidding_records(self, db_repo):
        mid = db_repo.create_match("BTest", {}, "s")
        hid = db_repo.create_hand(mid, 1, "S", "W", "s1")
        thid = db_repo.create_table_hand(hid, "A")
        db_repo.add_bidding_record(thid, 1, "S", 0, 1000)
        db_repo.add_bidding_record(thid, 2, "E", 2, 2000)
        db_repo.add_bidding_record(thid, 3, "N", 0, 3000)

    def test_play_actions(self, db_repo):
        mid = db_repo.create_match("PATest", {}, "s")
        hid = db_repo.create_hand(mid, 1, "S", "W", "s1")
        thid = db_repo.create_table_hand(hid, "A")
        db_repo.add_play_action(thid, 1, 1, 1, "S", "play",
                                cards=["♠A"], pattern="单张", display="♠A")
        db_repo.add_play_action(thid, 1, 2, 2, "E", "pass")


class TestMemoriesAndReflections:
    """Tests for reflection and memory persistence."""

    def test_reflection(self, db_repo):
        mid = db_repo.create_match("RTest", {}, "s")
        hid = db_repo.create_hand(mid, 1, "S", "W", "s1")
        thid = db_repo.create_table_hand(hid, "A")
        cid = db_repo.create_player_config("conf", "claude", "m", "k")
        pid = db_repo.create_player(cid, "player-1")
        db_repo.add_reflection(
            thid, pid, "S", "landlord",
            "Should have played differently", "Opponent is aggressive",
        )

    def test_agent_memories(self, db_repo):
        cid = db_repo.create_player_config("conf", "claude", "m", "k")
        pid = db_repo.create_player(cid, "player-1")
        db_repo.add_agent_memory(pid, "short_term", "Game 1 memory", match_id="m1")
        db_repo.add_agent_memory(pid, "short_term", "Game 2 memory", match_id="m1")
        db_repo.add_agent_memory(pid, "long_term", "Global strategy")

        short = db_repo.get_agent_memories(pid, match_id="m1")
        assert len(short) == 2

        long = db_repo.get_agent_memories(pid, memory_type="long_term")
        assert len(long) == 1
        assert long[0]["content"] == "Global strategy"


class TestInitialAndRemainingHands:
    """Tests for initial/remaining hand persistence."""

    def test_initial_and_remaining(self, db_repo):
        mid = db_repo.create_match("IRTest", {}, "s")
        hid = db_repo.create_hand(mid, 1, "S", "W", "s1")
        thid = db_repo.create_table_hand(hid, "A")
        db_repo.add_initial_hand(thid, "S", ["♠A", "♥K"])
        db_repo.add_remaining_hand(thid, "S", ["♠A"])


# ── End-to-end match with DB tests ────────────────────────────────────────────


class TestFullMatchWithDB:
    """Run a complete match (5 hands) with DB persistence."""

    def test_5_hand_match_with_db(self, db_repo):
        """Run a 5-hand match with mock LLM agents and verify DB is populated."""
        red_ids = [f"red-{i}" for i in range(4)]
        blue_ids = [f"blue-{i}" for i in range(4)]

        agents = make_agents(red_ids + blue_ids)
        seating = assign_seating(red_ids, blue_ids, seed=42)

        config = MatchConfig(
            total_hands=5,
            ko_enabled=False,
            seed="test-match-db",
        )
        runner = MatchRunner(config, seating, agents, db_repo=db_repo, match_name="M4 Test Match")

        result = asyncio.run(runner.run())

        assert result.total_hands_played >= 5
        assert result.red_score >= 0
        assert result.blue_score >= 0

        match = db_repo.get_match(runner.match_id)
        assert match is not None
        assert match["name"] == "M4 Test Match"
        assert match["status"] == "finished"

        # Participants are not auto-created for direct MatchRunner use;
        # they're only persisted via API routes which validate player IDs.
        participants = db_repo.get_participants(runner.match_id)
        assert len(participants) == 0

        hands = db_repo.get_hands_for_match(runner.match_id)
        assert len(hands) >= 5
        for h in hands:
            assert h["status"] == "finished"

            from arena.db.models import get_table_hands_for_hand
            ths = get_table_hands_for_hand(db_repo.conn, h["id"])
            assert len(ths) == 2, f"Hand {h['hand_num']} should have 2 table_hands"


class TestReflectionFlow:
    """Verify that reflection updates short-term memory."""

    def test_reflection_updates_memory(self, db_repo):
        red_ids = [f"r-{i}" for i in range(4)]
        blue_ids = [f"b-{i}" for i in range(4)]

        agents = make_agents(red_ids + blue_ids)
        seating = assign_seating(red_ids, blue_ids, seed=99)

        config = MatchConfig(total_hands=2, ko_enabled=False, seed="reflection-test")
        runner = MatchRunner(config, seating, agents, db_repo=db_repo, match_name="Reflection Test")

        result = asyncio.run(runner.run())
        assert result.total_hands_played >= 2

        for aid in red_ids + blue_ids:
            memories = db_repo.get_agent_memories(aid, memory_type="short_term")
            assert len(memories) > 0, f"Agent {aid} should have short-term memories"


class TestSummaryFlow:
    """Verify that match summary updates long-term memory."""

    def test_summary_updates_long_term(self, db_repo):
        red_ids = [f"rs-{i}" for i in range(4)]
        blue_ids = [f"bs-{i}" for i in range(4)]

        agents = make_agents(red_ids + blue_ids)
        seating = assign_seating(red_ids, blue_ids, seed=77)

        config = MatchConfig(total_hands=2, ko_enabled=False, seed="summary-test")
        runner = MatchRunner(config, seating, agents, db_repo=db_repo, match_name="Summary Test")

        result = asyncio.run(runner.run())

        for aid in red_ids[:1] + blue_ids[:1]:
            memories = db_repo.get_agent_memories(aid, memory_type="long_term")
            assert len(memories) > 0, f"Agent {aid} should have long-term memory"
            assert "aggressive" in memories[0]["content"].lower()


class TestDBModelsModule:
    """Verify the models.py module directly integrates with repository."""

    def test_init_db_via_repository(self, db_repo):
        """Verify repository.init() created all tables."""
        tables = db_repo.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = {t[0] for t in tables}
        assert "matches" in table_names
        assert "player_configs" in table_names
        assert "players" in table_names
        assert "play_actions" in table_names
        assert "agent_thoughts" in table_names
        assert "reflections" in table_names
        assert "agent_memories" in table_names
        assert "llm_call_logs" in table_names
        # Old agents table should not exist
        assert "agents" not in table_names
