"""Tests for pattern recognition (all 15 types) and comparison logic."""

import pytest

from arena.engine.card import Card, Rank, Suit
from arena.engine.rules import InvalidPlayError, can_beat, recognize
from arena.engine.trick import PatternType


def c(rank: Rank, suit: Suit) -> Card:
    return Card(rank=rank, suit=suit)


def j(rank: Rank) -> Card:
    return Card(rank=rank)


# ── helpers ──────────────────────────────────────────────────────────────────

S3 = c(Rank.THREE, Suit.SPADE)
H3 = c(Rank.THREE, Suit.HEART)
C3 = c(Rank.THREE, Suit.CLUB)
D3 = c(Rank.THREE, Suit.DIAMOND)
S4 = c(Rank.FOUR, Suit.SPADE)
H4 = c(Rank.FOUR, Suit.HEART)
C4 = c(Rank.FOUR, Suit.CLUB)
S5 = c(Rank.FIVE, Suit.SPADE)
H5 = c(Rank.FIVE, Suit.HEART)
C5 = c(Rank.FIVE, Suit.CLUB)
S6 = c(Rank.SIX, Suit.SPADE)
H6 = c(Rank.SIX, Suit.HEART)
S7 = c(Rank.SEVEN, Suit.SPADE)
H7 = c(Rank.SEVEN, Suit.HEART)
S8 = c(Rank.EIGHT, Suit.SPADE)
H8 = c(Rank.EIGHT, Suit.HEART)
S9 = c(Rank.NINE, Suit.SPADE)
H9 = c(Rank.NINE, Suit.HEART)
S10 = c(Rank.TEN, Suit.SPADE)
SJ = c(Rank.JACK, Suit.SPADE)
SQ = c(Rank.QUEEN, Suit.SPADE)
SK = c(Rank.KING, Suit.SPADE)
HK = c(Rank.KING, Suit.HEART)
CK = c(Rank.KING, Suit.CLUB)
DK = c(Rank.KING, Suit.DIAMOND)
SA = c(Rank.ACE, Suit.SPADE)
HA = c(Rank.ACE, Suit.HEART)
S2 = c(Rank.TWO, Suit.SPADE)
H2 = c(Rank.TWO, Suit.HEART)
C2 = c(Rank.TWO, Suit.CLUB)
D2 = c(Rank.TWO, Suit.DIAMOND)
D4 = c(Rank.FOUR, Suit.DIAMOND)
BJ = j(Rank.BIG_JOKER)
SJoker = j(Rank.SMALL_JOKER)


# ── single ───────────────────────────────────────────────────────────────────

class TestSingle:
    def test_single(self):
        t = recognize([S3])
        assert t.pattern == PatternType.SINGLE
        assert t.main_rank == Rank.THREE

    def test_single_joker(self):
        t = recognize([BJ])
        assert t.pattern == PatternType.SINGLE
        assert t.main_rank == Rank.BIG_JOKER


# ── pair ─────────────────────────────────────────────────────────────────────

class TestPair:
    def test_pair(self):
        t = recognize([S3, H3])
        assert t.pattern == PatternType.PAIR
        assert t.main_rank == Rank.THREE


# ── trio ─────────────────────────────────────────────────────────────────────

class TestTrio:
    def test_trio(self):
        t = recognize([S3, H3, C3])
        assert t.pattern == PatternType.TRIO
        assert t.main_rank == Rank.THREE


# ── trio + single ────────────────────────────────────────────────────────────

class TestTrioPlusOne:
    def test_trio_plus_one(self):
        t = recognize([S3, H3, C3, S4])
        assert t.pattern == PatternType.TRIO_PLUS_ONE
        assert t.main_rank == Rank.THREE
        assert len(t.attached) == 1


# ── trio + pair ──────────────────────────────────────────────────────────────

class TestTrioPlusTwo:
    def test_trio_plus_two(self):
        t = recognize([S3, H3, C3, S4, H4])
        assert t.pattern == PatternType.TRIO_PLUS_TWO
        assert t.main_rank == Rank.THREE
        assert len(t.attached) == 2


# ── straight ─────────────────────────────────────────────────────────────────

class TestStraight:
    def test_min_straight(self):
        t = recognize([S3, S4, S5, S6, S7])
        assert t.pattern == PatternType.STRAIGHT
        assert t.length == 5
        assert t.main_rank == Rank.SEVEN

    def test_long_straight(self):
        cards = [Card(r, Suit.SPADE) for r in [
            Rank.THREE, Rank.FOUR, Rank.FIVE, Rank.SIX, Rank.SEVEN,
            Rank.EIGHT, Rank.NINE, Rank.TEN, Rank.JACK, Rank.QUEEN,
            Rank.KING, Rank.ACE,
        ]]
        t = recognize(cards)
        assert t.pattern == PatternType.STRAIGHT
        assert t.length == 12

    def test_straight_includes_2_fails(self):
        """2 is not allowed in straights."""
        with pytest.raises(InvalidPlayError):
            recognize([SJ, SQ, SK, SA, S2])

    def test_non_consecutive_fails(self):
        with pytest.raises(InvalidPlayError):
            recognize([S3, S4, S5, S6, S8])


# ── consecutive pairs ────────────────────────────────────────────────────────

class TestConsecutivePairs:
    def test_min_consecutive_pairs(self):
        t = recognize([S3, H3, S4, H4, S5, H5])
        assert t.pattern == PatternType.CONSECUTIVE_PAIRS
        assert t.length == 3
        assert t.main_rank == Rank.FIVE

    def test_long_consecutive_pairs(self):
        cards = []
        for r in [Rank.THREE, Rank.FOUR, Rank.FIVE, Rank.SIX, Rank.SEVEN]:
            cards.append(Card(r, Suit.SPADE))
            cards.append(Card(r, Suit.HEART))
        t = recognize(cards)
        assert t.pattern == PatternType.CONSECUTIVE_PAIRS
        assert t.length == 5


# ── airplane (不带) ──────────────────────────────────────────────────────────

class TestAirplane:
    def test_min_airplane(self):
        t = recognize([S3, H3, C3, S4, H4, C4])
        assert t.pattern == PatternType.AIRPLANE
        assert t.length == 2
        assert t.main_rank == Rank.FOUR

    def test_triple_airplane(self):
        cards = []
        for r in [Rank.THREE, Rank.FOUR, Rank.FIVE]:
            cards.extend([Card(r, s) for s in [Suit.SPADE, Suit.HEART, Suit.CLUB]])
        t = recognize(cards)
        assert t.pattern == PatternType.AIRPLANE
        assert t.length == 3


# ── airplane + singles ───────────────────────────────────────────────────────

class TestAirplanePlusOnes:
    def test_airplane_plus_ones(self):
        t = recognize([S3, H3, C3, S4, H4, C4, S5, H7])
        assert t.pattern == PatternType.AIRPLANE_PLUS_ONES
        assert t.length == 2
        assert len(t.attached) == 2  # two singles

    def test_airplane_plus_ones_rank_conflict(self):
        """Attached singles cannot conflict with airplane ranks."""
        with pytest.raises(InvalidPlayError):
            recognize([S3, H3, C3, S4, H4, C4, S3, H5])  # S3 conflicts


# ── airplane + pairs ─────────────────────────────────────────────────────────

class TestAirplanePlusPairs:
    def test_airplane_plus_pairs(self):
        t = recognize([
            S3, H3, C3,  # trio 3
            S4, H4, C4,  # trio 4
            S5, H5,      # pair 5
            S6, H6,      # pair 6
        ])
        assert t.pattern == PatternType.AIRPLANE_PLUS_PAIRS
        assert t.length == 2
        assert len(t.attached) == 4  # two pairs

    def test_airplane_plus_pairs_rank_conflict(self):
        with pytest.raises(InvalidPlayError):
            recognize([
                S3, H3, C3,
                S4, H4, C4,
                S4, H4,  # pair 4 conflicts with airplane rank 4
                S5, H5,
            ])


# ── bomb ─────────────────────────────────────────────────────────────────────

class TestBomb:
    def test_bomb(self):
        t = recognize([S3, H3, C3, D3])
        assert t.pattern == PatternType.BOMB
        assert t.main_rank == Rank.THREE

    def test_bomb_high(self):
        t = recognize([S2, H2, C2, D2])
        assert t.pattern == PatternType.BOMB
        assert t.main_rank == Rank.TWO


# ── rocket ───────────────────────────────────────────────────────────────────

class TestRocket:
    def test_rocket(self):
        t = recognize([BJ, SJoker])
        assert t.pattern == PatternType.ROCKET


# ── four + two singles ───────────────────────────────────────────────────────

class TestFourPlusTwoSingles:
    def test_four_plus_two_singles(self):
        t = recognize([SK, HK, CK, DK, S3, H4])
        assert t.pattern == PatternType.FOUR_PLUS_TWO_SINGLES
        assert t.main_rank == Rank.KING
        assert len(t.attached) == 2


# ── four + one pair ──────────────────────────────────────────────────────────

class TestFourPlusOnePair:
    def test_four_plus_one_pair(self):
        t = recognize([SK, HK, CK, DK, S3, H3])
        assert t.pattern == PatternType.FOUR_PLUS_ONE_PAIR
        assert t.main_rank == Rank.KING


# ── four + two pairs ─────────────────────────────────────────────────────────

class TestFourPlusTwoPairs:
    def test_four_plus_two_pairs(self):
        t = recognize([SK, HK, CK, DK, S3, H3, S4, H4])
        assert t.pattern == PatternType.FOUR_PLUS_TWO_PAIRS
        assert t.main_rank == Rank.KING


# ── comparison ───────────────────────────────────────────────────────────────

class TestComparison:
    def test_same_pattern_higher_wins(self):
        t1 = recognize([S3, H3])  # pair 3
        t2 = recognize([S4, H4])  # pair 4
        assert can_beat(t2, t1)
        assert not can_beat(t1, t2)

    def test_bomb_beats_non_bomb(self):
        pair = recognize([S3, H3])
        bomb = recognize([SK, HK, CK, DK])
        assert can_beat(bomb, pair)
        assert not can_beat(pair, bomb)

    def test_rocket_beats_bomb(self):
        bomb = recognize([SK, HK, CK, DK])
        rocket = recognize([BJ, SJoker])
        assert can_beat(rocket, bomb)
        assert not can_beat(bomb, rocket)

    def test_different_patterns_cannot_compare(self):
        pair = recognize([S3, H3])
        single = recognize([S3])
        assert not can_beat(single, pair)

    def test_straight_must_match_length(self):
        s5 = recognize([S3, S4, S5, S6, S7])
        s6 = recognize([S3, S4, S5, S6, S7, S8])
        assert not can_beat(s6, s5)

    def test_airplane_must_match_length(self):
        a2 = recognize([S3, H3, C3, S4, H4, C4])
        a3 = recognize([
            S3, H3, C3, S4, H4, C4, S5, H5, C5,
        ])
        assert not can_beat(a3, a2)

    def test_bomb_vs_bomb_higher_wins(self):
        b3 = recognize([S3, H3, C3, D3])
        b4 = recognize([S4, H4, C4, D4])
        assert can_beat(b4, b3)
        assert not can_beat(b3, b4)

    def test_leader_can_play_anything(self):
        t = recognize([S3])
        assert can_beat(t, None)


# ── error cases ──────────────────────────────────────────────────────────────

class TestErrors:
    def test_empty_play(self):
        with pytest.raises(InvalidPlayError):
            recognize([])

    def test_five_random_cards(self):
        with pytest.raises(InvalidPlayError):
            recognize([S3, S5, S7, S9, SJ])

    def test_four_different(self):
        with pytest.raises(InvalidPlayError):
            recognize([S3, S4, S5, S6])  # not a straight (need >=5)


# ── display ──────────────────────────────────────────────────────────────────

class TestDisplay:
    def test_single_display(self):
        t = recognize([SA])
        assert t.display() == "♠A"

    def test_pair_display(self):
        t = recognize([SK, HK])
        assert t.display() == "♠K♥K"

    def test_bomb_display(self):
        t = recognize([SK, HK, CK, DK])
        assert t.display() == "KKKK"

    def test_rocket_display(self):
        t = recognize([BJ, SJoker])
        assert t.display() == "小王大王"

    def test_straight_display(self):
        t = recognize([S3, S4, S5, S6, S7])
        assert t.display() == "34567"

    def test_trio_plus_one_display(self):
        t = recognize([S3, H3, C3, S5])
        assert t.display() == "333+5"

    def test_four_plus_two_singles_display(self):
        t = recognize([SK, HK, CK, DK, S3, H4])
        assert t.display() == "KKKK+3+4"
