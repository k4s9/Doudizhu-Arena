"""Regression coverage for replay data used by the review UI."""

from arena.api.routes.replay import _build_table_detail
from arena.db.repository import DatabaseRepository


def test_replay_table_detail_uses_player_display_names(tmp_path):
    repo = DatabaseRepository(str(tmp_path / "arena.db"))
    repo.init()
    try:
        config_id = repo.create_player_config("test", "random", "random", "")
        south = repo.create_player(config_id, "Qwen-Balanced R1")
        east = repo.create_player(config_id, "Minimax-Flexible B4")

        match_id = repo.create_match("Replay names", {}, "seed")
        repo.add_participant(match_id, south, "red", seat_table_a="S")
        repo.add_participant(match_id, east, "blue", seat_table_a="E")
        hand_id = repo.create_hand(match_id, 1, "S", "W", "seed")
        table_hand_id = repo.create_table_hand(hand_id, "A")

        detail = _build_table_detail(
            repo,
            repo.get_table_hand(table_hand_id),
            match_id,
            1,
            "S",
            "W",
            "seed",
        )

        assert detail["players"]["S"]["agent_id"] == south
        assert detail["players"]["S"]["agent_name"] == "Qwen-Balanced R1"
        assert detail["players"]["E"]["agent_name"] == "Minimax-Flexible B4"
    finally:
        repo.close()
