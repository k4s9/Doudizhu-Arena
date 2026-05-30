"""Tests for score calculation — additive mode and differential scoring."""
import pytest

from arena.engine.scoring import DIFF_CAP, HandScore, calculate_diff_score, calculate_hand_score


class TestHandScore:
    def test_void_hand(self):
        score = calculate_hand_score(
            final_bid=0, winner_seat="", winner_team="",
            landlord_seat="", bombs_played=0, spring=False,
            anti_spring=False, void=True,
        )
        assert score.void is True
        assert score.final_score == 0
        assert score.red_score == 0
        assert score.blue_score == 0

    def test_basic_landlord_win(self):
        score = calculate_hand_score(
            final_bid=2, winner_seat="S", winner_team="red",
            landlord_seat="S", bombs_played=0, spring=False,
            anti_spring=False, void=False,
        )
        assert score.winner_team == "red"
        assert score.winner_role == "landlord"
        assert score.base_score == 2
        assert score.multiplier == 1
        assert score.final_score == 2

    def test_farmer_win_with_bombs(self):
        score = calculate_hand_score(
            final_bid=3, winner_seat="E", winner_team="blue",
            landlord_seat="S", bombs_played=3, spring=False,
            anti_spring=False, void=False,
        )
        assert score.winner_team == "blue"
        assert score.winner_role == "farmer"
        assert score.multiplier == 4  # 1 + 3 bombs
        assert score.final_score == 12

    def test_spring_landlord(self):
        score = calculate_hand_score(
            final_bid=3, winner_seat="S", winner_team="red",
            landlord_seat="S", bombs_played=1, spring=True,
            anti_spring=False, void=False,
        )
        assert score.multiplier == 3  # 1 + 1 bomb + 1 spring
        assert score.final_score == 9
        assert score.spring is True

    def test_anti_spring(self):
        score = calculate_hand_score(
            final_bid=2, winner_seat="E", winner_team="blue",
            landlord_seat="S", bombs_played=0, spring=False,
            anti_spring=True, void=False,
        )
        assert score.winner_role == "farmer"
        assert score.multiplier == 2  # 1 + 1 anti-spring
        assert score.final_score == 4

    def test_landlord_spring_with_bombs(self):
        score = calculate_hand_score(
            final_bid=3, winner_seat="S", winner_team="red",
            landlord_seat="S", bombs_played=2, spring=True,
            anti_spring=False, void=False,
        )
        assert score.multiplier == 4  # 1 + 2 bombs + 1 spring
        assert score.final_score == 12

    def test_team_score_properties(self):
        red_win = calculate_hand_score(
            final_bid=1, winner_seat="S", winner_team="red",
            landlord_seat="S", bombs_played=0, spring=False,
            anti_spring=False, void=False,
        )
        assert red_win.red_score == 1
        assert red_win.blue_score == 0

        blue_win = calculate_hand_score(
            final_bid=2, winner_seat="E", winner_team="blue",
            landlord_seat="E", bombs_played=1, spring=False,
            anti_spring=False, void=False,
        )
        assert blue_win.red_score == 0
        assert blue_win.blue_score == 4  # 2 * (1+1)


class TestDiffScoring:
    def test_both_void(self):
        void_a = calculate_hand_score(
            final_bid=0, winner_seat="", winner_team="",
            landlord_seat="", bombs_played=0, spring=False,
            anti_spring=False, void=True,
        )
        void_b = calculate_hand_score(
            final_bid=0, winner_seat="", winner_team="",
            landlord_seat="", bombs_played=0, spring=False,
            anti_spring=False, void=True,
        )
        red_diff, blue_diff = calculate_diff_score(void_a, void_b)
        assert red_diff == 0
        assert blue_diff == 0

    def test_single_void(self):
        void_a = calculate_hand_score(
            final_bid=0, winner_seat="", winner_team="",
            landlord_seat="", bombs_played=0, spring=False,
            anti_spring=False, void=True,
        )
        red_win = calculate_hand_score(
            final_bid=3, winner_seat="S", winner_team="red",
            landlord_seat="S", bombs_played=0, spring=False,
            anti_spring=False, void=False,
        )
        red_diff, blue_diff = calculate_diff_score(void_a, red_win)
        assert red_diff == 3  # 3 - 0 = 3
        assert blue_diff == 0

    def test_equal_diff(self):
        a_red_win = calculate_hand_score(
            final_bid=3, winner_seat="S", winner_team="red",
            landlord_seat="S", bombs_played=0, spring=False,
            anti_spring=False, void=False,
        )
        b_blue_win = calculate_hand_score(
            final_bid=3, winner_seat="S", winner_team="blue",
            landlord_seat="S", bombs_played=0, spring=False,
            anti_spring=False, void=False,
        )
        red_diff, blue_diff = calculate_diff_score(a_red_win, b_blue_win)
        # 3-3 tie
        assert red_diff == 0
        assert blue_diff == 0

    def test_red_net_positive(self):
        a_red = calculate_hand_score(
            final_bid=3, winner_seat="S", winner_team="red",
            landlord_seat="S", bombs_played=2, spring=False,
            anti_spring=False, void=False,
        )
        b_red = calculate_hand_score(
            final_bid=2, winner_seat="S", winner_team="red",
            landlord_seat="S", bombs_played=0, spring=False,
            anti_spring=False, void=False,
        )
        red_diff, blue_diff = calculate_diff_score(a_red, b_red)
        # red: 9+2=11, blue: 0 → diff=11
        assert red_diff == 11
        assert blue_diff == 0

    def test_diff_cap_12(self):
        a_red = calculate_hand_score(
            final_bid=3, winner_seat="S", winner_team="red",
            landlord_seat="S", bombs_played=5, spring=True,
            anti_spring=False, void=False,
        )
        # 3*(1+5+1)=21, no cap on single table
        assert a_red.final_score == 21
        b_red = calculate_hand_score(
            final_bid=0, winner_seat="", winner_team="",
            landlord_seat="", bombs_played=0, spring=False,
            anti_spring=False, void=True,
        )
        # red_net=21, blue_net=0 → diff capped at 12
        red_diff, blue_diff = calculate_diff_score(a_red, b_red)
        assert red_diff == DIFF_CAP  # 12
        assert blue_diff == 0

    def test_single_table_no_cap(self):
        """Single table score has no cap, only diff scoring is capped."""
        score = calculate_hand_score(
            final_bid=3, winner_seat="S", winner_team="red",
            landlord_seat="S", bombs_played=10, spring=True,
            anti_spring=False, void=False,
        )
        # 3 * (1+10+1) = 36 — no cap on single table
        assert score.final_score == 36
