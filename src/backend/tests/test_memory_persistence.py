"""Offline regression tests for settlement, transactions and frozen memory."""

from __future__ import annotations

import json
import sqlite3

import pytest

from arena.db import models
from arena.db.repository import DatabaseRepository, MemoryConflictError


@pytest.fixture
def repo(tmp_path):
    repository = DatabaseRepository(str(tmp_path / "memory.db"))
    repository.init()
    yield repository
    repository.close()


def make_player(repo, name="Player", memory="old lesson"):
    config = repo.create_player_config(name, "random", "random", "unused")
    return repo.create_player(config, name, long_term_memory=memory)


def make_table(repo):
    match = repo.create_match("Memory match", {}, "seed")
    hand = repo.create_hand(match, 1, "S", "W", "seed/1")
    return match, repo.create_table_hand(hand, "A")


def test_additive_migration_preserves_finished_matches_and_existing_memories(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(models.SCHEMA_DDL)
    config = models.insert_player_config(conn, "Legacy", "random", "random", "unused")
    player = models.insert_player(conn, config, "Legacy player", long_term_memory="legacy lesson")
    match = models.insert_match(conn, "Already played", {}, "seed")
    models.update_match_status(conn, match, "finished", score_red=6, score_blue=-6)
    models.update_player_stats(conn, player, matches_played_delta=1,
                               matches_won_delta=1, total_score_delta=6)
    models.insert_agent_memory(conn, player, "long_term", "legacy lesson", match)
    conn.execute("PRAGMA user_version=5")
    conn.commit()
    conn.close()

    repository = DatabaseRepository(str(path))
    repository.init()
    try:
        assert repository.get_player(player)["long_term_memory"] == "legacy lesson"
        assert repository.get_agent_memories(player)[0]["content"] == "legacy lesson"
        assert not repository.settle_match(match, "red", {player: "red"}, score_red=999)
        assert repository.get_player(player)["matches_played"] == 1
        assert repository.get_match(match)["score_red"] == 6
        assert repository.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        repository.close()
        repository.init()
        assert repository.conn.execute("SELECT COUNT(*) FROM memory_schema_migrations").fetchone()[0] == 1
        assert repository.conn.execute("SELECT COUNT(*) FROM match_settlements").fetchone()[0] == 1
    finally:
        repository.close()


def test_settlement_is_atomic_and_idempotent_and_skips_ephemeral_players(repo):
    red, blue = make_player(repo, "Red"), make_player(repo, "Blue")
    match = repo.create_match("Settlement", {}, "seed")
    teams = {red: "red", blue: "blue", "ephemeral": "red"}
    assert repo.settle_match(match, "red", teams, score_red=8, score_blue=-8, current_hand=2)
    assert not repo.settle_match(match, "blue", teams, score_red=-100, score_blue=100)
    assert repo.get_match(match)["status"] == "finished"
    assert repo.get_match(match)["score_red"] == 8
    assert repo.get_player(red)["matches_played"] == 1
    assert repo.get_player(red)["matches_won"] == 1
    assert repo.get_player(blue)["matches_played"] == 1
    assert repo.get_player(blue)["matches_won"] == 0
    assert repo.get_player(blue)["total_score"] == -8


def test_settlement_failure_rolls_back_finished_state_and_all_statistics(repo, monkeypatch):
    red, blue = make_player(repo, "Red"), make_player(repo, "Blue")
    match = repo.create_match("Settlement failure", {}, "seed")
    original = repo.update_player_stats

    def fail_second(player_id, **fields):
        if player_id == blue:
            raise sqlite3.OperationalError("simulated write failure")
        return original(player_id, **fields)

    monkeypatch.setattr(repo, "update_player_stats", fail_second)
    with pytest.raises(sqlite3.OperationalError):
        repo.settle_match(match, "red", {red: "red", blue: "blue"}, score_red=4, score_blue=-4)
    assert repo.get_match(match)["status"] == "created"
    assert repo.get_player(red)["matches_played"] == 0
    assert repo.conn.execute("SELECT COUNT(*) FROM match_settlements").fetchone()[0] == 0
    monkeypatch.setattr(repo, "update_player_stats", original)
    assert repo.settle_match(match, "red", {red: "red", blue: "blue"}, score_red=4, score_blue=-4)


def test_reflection_and_short_term_memory_roll_back_together(repo, monkeypatch):
    match, table = make_table(repo)
    player = make_player(repo)

    def fail_memory(*args, **kwargs):
        raise sqlite3.OperationalError("simulated second insert failure")

    monkeypatch.setattr(repo, "add_agent_memory", fail_memory)
    with pytest.raises(sqlite3.OperationalError):
        repo.save_reflection_memory(table, player, "S", "landlord", "review", "lesson", match)
    assert repo.conn.execute("SELECT COUNT(*) FROM reflections").fetchone()[0] == 0
    assert repo.get_agent_memories(player) == []


def test_long_term_update_and_version_and_legacy_history_roll_back_together(repo, monkeypatch):
    player = make_player(repo)
    frozen = repo.freeze_match_memory("match-a", player)

    def fail_history(*args, **kwargs):
        raise sqlite3.OperationalError("simulated history insert failure")

    monkeypatch.setattr(repo, "add_agent_memory", fail_history)
    with pytest.raises(sqlite3.OperationalError):
        repo.save_long_term_memory(player, "new lesson", "match-a", expected_content="old lesson")
    assert repo.get_player(player)["long_term_memory"] == "old lesson"
    assert [v["id"] for v in repo.list_memory_versions(player)] == [frozen["id"]]
    assert repo.get_agent_memories(player) == []


def test_versions_are_immutable_idempotent_and_link_to_consumed_snapshot(repo):
    player = make_player(repo)
    frozen = repo.freeze_match_memory("match-a", player)
    source = {"kind": "match_summary", "model": "offline-model", "api_key": "never-store-this"}
    saved = repo.save_long_term_memory(player, "new lesson", "match-a",
                                      expected_content="old lesson", source=source)
    assert saved["revision"] == frozen["revision"] + 1
    assert saved["previous_id"] == frozen["id"]
    assert saved["content_hash"] != frozen["content_hash"]
    assert saved["source"] == {"kind": "match_summary", "model": "offline-model"}
    retried = repo.save_long_term_memory(player, "new lesson", "match-a", expected_content="old lesson")
    assert retried == saved
    assert len(repo.get_agent_memories(player)) == 1
    usage = repo.list_match_memory_usage("match-a", player)[0]
    assert usage["id"] == frozen["id"]
    assert usage["content"] == "old lesson"
    assert repo.get_memory_version(frozen["id"])["content"] == "old lesson"
    with pytest.raises(MemoryConflictError):
        repo.save_long_term_memory(player, "different retry", "match-a")


def test_stale_match_snapshot_cannot_overwrite_newer_player_memory(repo):
    player = make_player(repo)
    first = repo.freeze_match_memory("match-a", player)
    second = repo.freeze_match_memory("match-b", player)
    assert first["id"] == second["id"]
    assert first["usage_id"] != second["usage_id"]
    repo.save_long_term_memory(player, "lesson from a", "match-a", expected_content=first["content"])
    with pytest.raises(MemoryConflictError):
        repo.save_long_term_memory(player, "lesson from b", "match-b", expected_content=second["content"])
    assert repo.get_player(player)["long_term_memory"] == "lesson from a"
    assert len(repo.list_memory_versions(player)) == 2


@pytest.mark.parametrize("newer_contents", [["old lesson"], ["intermediate lesson", "old lesson"]])
def test_frozen_version_detects_updates_even_when_content_returns_to_same_value(repo, newer_contents):
    player = make_player(repo)
    frozen = repo.freeze_match_memory("stale-match", player)
    for index, content in enumerate(newer_contents):
        repo.save_long_term_memory(player, content, f"newer-{index}")
    with pytest.raises(MemoryConflictError, match="version changed"):
        repo.save_long_term_memory(player, "stale summary", "stale-match",
                                   expected_content=frozen["content"])
    assert repo.get_player(player)["long_term_memory"] == "old lesson"
    assert not any(version["match_id"] == "stale-match" for version in repo.list_memory_versions(player))


def test_manual_repair_preserves_oversized_legacy_memory_verbatim(repo):
    original = "  " + "old lesson " * 1000 + "  "
    player = make_player(repo, memory=original)
    saved = repo.save_long_term_memory(player, "  repaired lesson  ", None,
                                      expected_content=original)
    versions = repo.list_memory_versions(player)
    assert len(versions) == 2
    assert versions[1]["content"] == original
    assert saved["previous_id"] == versions[1]["id"]
    assert saved["source"] == {"kind": "manual_update"}
    assert saved["content"] == repo.get_player(player)["long_term_memory"] == "repaired lesson"


def test_freezing_same_match_is_idempotent_but_cannot_change_content(repo):
    player = make_player(repo)
    first = repo.freeze_match_memory("match-a", player)
    assert repo.freeze_match_memory("match-a", player) == first
    with pytest.raises(MemoryConflictError):
        repo.freeze_match_memory("match-a", player, content="changed")
    assert len(repo.list_memory_versions(player)) == 1
    assert len(repo.list_match_memory_usage("match-a")) == 1


def test_retry_of_older_success_does_not_overwrite_a_later_memory_version(repo):
    player = make_player(repo)
    older = repo.save_long_term_memory(player, "first", "match-a", expected_content="old lesson")
    newer = repo.save_long_term_memory(player, "second", "match-b", expected_content="first")
    assert repo.save_long_term_memory(player, "first", "match-a", expected_content="old lesson") == older
    assert repo.get_player(player)["long_term_memory"] == "second"
    assert repo.list_memory_versions(player)[0]["id"] == newer["id"]


def test_failure_records_omit_secret_messages_and_success_records_recovery(repo):
    player = make_player(repo)
    repo.record_memory_failure(player, "match-a", "summary", "persistence",
                               RuntimeError("Authorization: secret-credential"))
    failed = repo.list_memory_failures(player_id=player, unresolved_only=True)
    assert len(failed) == 1
    assert failed[0]["error_type"] == "RuntimeError"
    assert "secret-credential" not in json.dumps(failed)
    saved = repo.save_long_term_memory(player, "recovered lesson", "match-a", expected_content="old lesson")
    assert repo.list_memory_failures(match_id="match-a", unresolved_only=True) == []
    recovered = repo.list_memory_failures(match_id="match-a")[0]
    assert recovered["recovered"]
    assert recovered["resolution_version_id"] == saved["id"]


def test_reflection_recovery_only_resolves_the_same_hand(repo):
    player = make_player(repo)
    match, table = make_table(repo)
    repo.record_memory_failure(player, match, "reflection", "generation", "empty_response", table)
    repo.record_memory_failure(player, match, "reflection", "persistence", "invalid_response", "other-table")
    repo.save_reflection_memory(table, player, "S", "landlord", "review", "lesson", match)
    unresolved = repo.list_memory_failures(player_id=player, unresolved_only=True)
    assert len(unresolved) == 1
    assert unresolved[0]["table_hand_id"] == "other-table"
    assert repo.get_agent_memories(player)[0]["content"] == "lesson"


def test_programmatic_agent_without_player_row_can_preserve_history(repo):
    saved = repo.save_long_term_memory("ephemeral", "lesson", "match-a", expected_content="")
    assert saved["content"] == "lesson"
    assert repo.get_player("ephemeral") is None
    assert repo.get_agent_memories("ephemeral")[0]["content"] == "lesson"


@pytest.mark.parametrize("invalid", ["", "   ", "x" * 8001, None, {"memory": "text"}])
def test_invalid_long_term_update_preserves_existing_content(repo, invalid):
    player = make_player(repo)
    with pytest.raises(ValueError):
        repo.save_long_term_memory(player, invalid, "match-a")
    assert repo.get_player(player)["long_term_memory"] == "old lesson"
    assert repo.list_memory_versions(player) == []


def test_oversized_short_term_update_does_not_write_reflection(repo):
    player = make_player(repo)
    match, table = make_table(repo)
    with pytest.raises(ValueError):
        repo.save_reflection_memory(table, player, "S", "landlord", "review", "x" * 4001, match)
    assert repo.conn.execute("SELECT COUNT(*) FROM reflections").fetchone()[0] == 0
