import asyncio
from types import SimpleNamespace

import pytest

from arena.api.routes.player import build_player_statistics, get_player, get_player_leaderboard
from arena.db.repository import DatabaseRepository


@pytest.fixture
def db_repo(tmp_path):
    repo = DatabaseRepository(str(tmp_path / "player_statistics.db"))
    repo.init()
    yield repo
    repo.close()


def _record(hand_num, *, role_seat, landlord_seat, team, winner_team, score, void=False):
    return {
        "match_id": f"match-{hand_num}",
        "match_name": f"比赛 {hand_num}",
        "hand_num": hand_num,
        "table_id": "A",
        "seat": role_seat,
        "idle_seat": "W",
        "landlord_seat": landlord_seat,
        "idle_participation": "{}",
        "team": team,
        "winner_team": winner_team,
        "void": void,
        "diff_score_red": score if team == "red" else -score,
        "diff_score_blue": score if team == "blue" else -score,
        "final_score": abs(score),
        "is_tiebreaker": 0,
        "hand_finished_at": f"2026-07-{hand_num:02d}T00:00:00Z",
    }


def test_player_statistics_uses_hand_results_and_team_score():
    records = [
        _record(1, role_seat="S", landlord_seat="S", team="red", winner_team="red", score=5),
        _record(2, role_seat="E", landlord_seat="S", team="blue", winner_team="red", score=-4),
        _record(3, role_seat="E", landlord_seat="S", team="blue", winner_team="blue", score=2),
        _record(4, role_seat="W", landlord_seat="S", team="red", winner_team="red", score=1),
        _record(5, role_seat="S", landlord_seat="", team="red", winner_team="", score=0, void=True),
    ]

    stats = build_player_statistics(records)

    assert stats["total_hands"] == 5
    assert stats["hands_played"] == 3
    assert stats["wins"] == 2
    assert stats["losses"] == 1
    assert stats["win_rate"] == 66.7
    assert stats["landlord"] == {"hands": 1, "wins": 1, "losses": 0, "win_rate": 100.0}
    assert stats["farmer"] == {"hands": 2, "wins": 1, "losses": 1, "win_rate": 50.0}
    assert stats["idle_hands"] == 1
    assert stats["void_hands"] == 1
    assert stats["score_total"] == 4
    assert [entry["cumulative_score"] for entry in stats["score_history"]] == [5, 1, 3, 4, 4]


def test_repository_returns_completed_records_per_player(db_repo):
    config_id = db_repo.create_player_config("Stats", "random", "random", "unused")
    player_id = db_repo.create_player(config_id, "统计选手")
    match_id = db_repo.create_match("统计比赛", {"total_hands": 1}, "seed")
    db_repo.add_participant(match_id, player_id, "red", seat_table_a="S")

    hand_id = db_repo.create_hand(match_id, 1, "S", "W", "seed/hand-1")
    table_hand_id = db_repo.create_table_hand(hand_id, "A")
    db_repo.update_table_hand_result(
        table_hand_id,
        landlord_seat="S",
        winner_team="red",
        winner_role="landlord",
        final_score=3,
    )
    db_repo.finish_hand(hand_id, 3, -3, False)

    records = db_repo.get_player_hand_records(player_id)

    assert len(records) == 1
    assert records[0]["seat"] == "S"
    assert records[0]["table_id"] == "A"
    assert records[0]["diff_score_red"] == 3


def test_player_detail_and_leaderboard_return_fresh_hand_statistics(db_repo):
    config_id = db_repo.create_player_config("Stats API", "random", "random", "unused")
    player_id = db_repo.create_player(config_id, "接口选手")
    match_id = db_repo.create_match("接口比赛", {"total_hands": 1}, "seed")
    db_repo.add_participant(match_id, player_id, "red", seat_table_a="S")

    hand_id = db_repo.create_hand(match_id, 1, "S", "W", "seed/hand-1")
    table_hand_id = db_repo.create_table_hand(hand_id, "A")
    db_repo.update_table_hand_result(
        table_hand_id,
        landlord_seat="S",
        winner_team="red",
        winner_role="landlord",
        final_score=6,
    )
    db_repo.finish_hand(hand_id, 6, -6, False)

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_repo=db_repo)))
    detail = asyncio.run(get_player(player_id, request))
    leaderboard = asyncio.run(get_player_leaderboard(request))

    assert detail["statistics"]["landlord"]["win_rate"] == 100.0
    assert detail["statistics"]["score_total"] == 6
    assert leaderboard["players"][0]["id"] == player_id
    assert leaderboard["players"][0]["statistics"]["hands_played"] == 1
