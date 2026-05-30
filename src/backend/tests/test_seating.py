"""Tests for seating rotation and random assignment."""

from arena.tournament.seating import (
    MatchSeating,
    assign_seating,
    get_bidding_order,
    get_dealer_idle,
)


class TestRotation:
    def test_hand_1(self):
        dealer, idle = get_dealer_idle(1)
        assert dealer == "S"
        assert idle == "W"

    def test_hand_2(self):
        dealer, idle = get_dealer_idle(2)
        assert dealer == "E"
        assert idle == "S"

    def test_hand_3(self):
        dealer, idle = get_dealer_idle(3)
        assert dealer == "N"
        assert idle == "E"

    def test_hand_4(self):
        dealer, idle = get_dealer_idle(4)
        assert dealer == "W"
        assert idle == "N"

    def test_hand_5_cycles(self):
        # After 4 hands, cycle repeats
        dealer, idle = get_dealer_idle(5)
        assert dealer == "S"
        assert idle == "W"

    def test_hand_6_cycles(self):
        dealer, idle = get_dealer_idle(6)
        assert dealer == "E"
        assert idle == "S"

    def test_hand_20(self):
        # hand 20: 20 % 4 = 0 → same as hand 4
        dealer, idle = get_dealer_idle(20)
        assert dealer == "W"
        assert idle == "N"

    def test_hand_21(self):
        # tiebreaker hand 21: 21 % 4 = 1 → same as hand 1
        dealer, idle = get_dealer_idle(21)
        assert dealer == "S"
        assert idle == "W"

    def test_all_hands_unique_idle(self):
        """Each hand in a full 4-hand cycle has a unique idle seat."""
        idles = set()
        for h in range(1, 5):
            _, idle = get_dealer_idle(h)
            idles.add(idle)
        assert idles == {"S", "E", "N", "W"}

    def test_all_hands_unique_dealer(self):
        """Each hand in a full 4-hand cycle has a unique dealer."""
        dealers = set()
        for h in range(1, 5):
            dealer, _ = get_dealer_idle(h)
            dealers.add(dealer)
        assert dealers == {"S", "E", "N", "W"}


class TestBiddingOrder:
    def test_dealer_s(self):
        order = get_bidding_order("S")
        assert order[0] == "S"  # dealer is first

    def test_dealer_e(self):
        order = get_bidding_order("E")
        assert order[0] == "E"

    def test_has_4_seats(self):
        order = get_bidding_order("S")
        assert len(order) == 4
        assert set(order) == {"S", "E", "N", "W"}


class TestRandomSeating:
    def test_assigns_all_agents(self):
        red = ["r1", "r2", "r3", "r4"]
        blue = ["b1", "b2", "b3", "b4"]
        seating = assign_seating(red, blue, seed=42)

        # All agents appear somewhere
        all_assigned = (
            set(seating.table_a.values()) | set(seating.table_b.values())
        )
        assert all_assigned == set(red + blue)

    def test_deterministic_with_seed(self):
        red = ["r1", "r2", "r3", "r4"]
        blue = ["b1", "b2", "b3", "b4"]
        s1 = assign_seating(red, blue, seed=42)
        s2 = assign_seating(red, blue, seed=42)
        assert s1.table_a == s2.table_a
        assert s1.table_b == s2.table_b

    def test_different_with_different_seed(self):
        red = ["r1", "r2", "r3", "r4"]
        blue = ["b1", "b2", "b3", "b4"]
        s1 = assign_seating(red, blue, seed=42)
        s2 = assign_seating(red, blue, seed=99)
        # Very unlikely to be the same
        assert s1.table_a != s2.table_a or s1.table_b != s2.table_b

    def test_a_table_north_south_red(self):
        red = ["r1", "r2", "r3", "r4"]
        blue = ["b1", "b2", "b3", "b4"]
        seating = assign_seating(red, blue, seed=42)
        assert seating.table_a_teams["S"] == "red"
        assert seating.table_a_teams["N"] == "red"
        assert seating.table_a_teams["E"] == "blue"
        assert seating.table_a_teams["W"] == "blue"

    def test_b_table_north_south_blue(self):
        red = ["r1", "r2", "r3", "r4"]
        blue = ["b1", "b2", "b3", "b4"]
        seating = assign_seating(red, blue, seed=42)
        assert seating.table_b_teams["S"] == "blue"
        assert seating.table_b_teams["N"] == "blue"
        assert seating.table_b_teams["E"] == "red"
        assert seating.table_b_teams["W"] == "red"

    def test_agent_seats_lookup(self):
        red = ["r1", "r2", "r3", "r4"]
        blue = ["b1", "b2", "b3", "b4"]
        seating = assign_seating(red, blue, seed=42)
        for aid in red + blue:
            table, seat = seating.agent_seats[aid]
            assert table in ("A", "B")
            assert seat in ("S", "E", "N", "W")

    def test_agent_teams_lookup(self):
        red = ["r1", "r2", "r3", "r4"]
        blue = ["b1", "b2", "b3", "b4"]
        seating = assign_seating(red, blue, seed=42)
        for aid in red:
            assert seating.agent_teams[aid] == "red"
        for aid in blue:
            assert seating.agent_teams[aid] == "blue"

    def test_wrong_count_raises(self):
        import pytest
        with pytest.raises(ValueError):
            assign_seating(["r1"], ["b1", "b2", "b3", "b4"])
