"""Tests for Card, Rank, Suit."""

import pytest

from arena.engine.card import (
    ALL_CARDS,
    STRAIGHT_RANKS,
    STRAIGHT_RANK_VALUES,
    Card,
    Rank,
    Suit,
)


class TestRank:
    def test_display(self):
        assert Rank.THREE.display == "3"
        assert Rank.TEN.display == "10"
        assert Rank.JACK.display == "J"
        assert Rank.ACE.display == "A"
        assert Rank.TWO.display == "2"
        assert Rank.SMALL_JOKER.display == "小王"
        assert Rank.BIG_JOKER.display == "大王"

    def test_from_display(self):
        assert Rank.from_display("3") == Rank.THREE
        assert Rank.from_display("K") == Rank.KING
        assert Rank.from_display("小王") == Rank.SMALL_JOKER
        assert Rank.from_display("大王") == Rank.BIG_JOKER

    def test_from_display_invalid(self):
        with pytest.raises(ValueError):
            Rank.from_display("X")

    def test_comparison(self):
        assert Rank.THREE < Rank.FOUR
        assert Rank.ACE < Rank.TWO
        assert Rank.TWO < Rank.SMALL_JOKER
        assert Rank.SMALL_JOKER < Rank.BIG_JOKER


class TestSuit:
    def test_display(self):
        assert Suit.SPADE.display == "♠"
        assert Suit.HEART.display == "♥"
        assert Suit.CLUB.display == "♣"
        assert Suit.DIAMOND.display == "♦"

    def test_from_display(self):
        assert Suit.from_display("♠") == Suit.SPADE
        assert Suit.from_display("♦") == Suit.DIAMOND

    def test_order(self):
        assert Suit.SPADE > Suit.HEART > Suit.CLUB > Suit.DIAMOND


class TestCard:
    def test_regular_card(self):
        c = Card(rank=Rank.ACE, suit=Suit.SPADE)
        assert str(c) == "♠A"
        assert c.rank_only == "A"
        assert not c.is_joker

    def test_joker(self):
        big = Card(rank=Rank.BIG_JOKER)
        small = Card(rank=Rank.SMALL_JOKER)
        assert str(big) == "大王"
        assert str(small) == "小王"
        assert big.is_joker
        assert small.is_joker

    def test_joker_with_suit_raises(self):
        with pytest.raises(ValueError):
            Card(rank=Rank.BIG_JOKER, suit=Suit.SPADE)

    def test_regular_without_suit_raises(self):
        with pytest.raises(ValueError):
            Card(rank=Rank.ACE)

    def test_from_string_regular(self):
        c = Card.from_string("♠A")
        assert c.rank == Rank.ACE
        assert c.suit == Suit.SPADE

    def test_from_string_ten(self):
        c = Card.from_string("♥10")
        assert c.rank == Rank.TEN
        assert c.suit == Suit.HEART

    def test_from_string_joker(self):
        assert Card.from_string("大王") == Card(rank=Rank.BIG_JOKER)
        assert Card.from_string("小王") == Card(rank=Rank.SMALL_JOKER)

    def test_from_string_invalid(self):
        with pytest.raises(ValueError):
            Card.from_string("X5")

    def test_sort_order_rank_desc(self):
        """Hand display: rank desc, suit ♠→♥→♣→♦."""
        cards = [
            Card(Rank.THREE, Suit.DIAMOND),
            Card(Rank.ACE, Suit.SPADE),
            Card(Rank.THREE, Suit.SPADE),
        ]
        sorted_cards = sorted(cards, key=lambda c: c._sort_key())
        assert str(sorted_cards[0]) == "♠A"
        assert str(sorted_cards[1]) == "♠3"
        assert str(sorted_cards[2]) == "♦3"

    def test_sort_order_same_rank(self):
        cards = [
            Card(Rank.KING, Suit.DIAMOND),
            Card(Rank.KING, Suit.CLUB),
            Card(Rank.KING, Suit.HEART),
            Card(Rank.KING, Suit.SPADE),
        ]
        sorted_cards = sorted(cards, key=lambda c: c._sort_key())
        assert [c.suit for c in sorted_cards] == [
            Suit.SPADE, Suit.HEART, Suit.CLUB, Suit.DIAMOND,
        ]

    def test_equality(self):
        c1 = Card(Rank.ACE, Suit.SPADE)
        c2 = Card(Rank.ACE, Suit.SPADE)
        c3 = Card(Rank.ACE, Suit.HEART)
        assert c1 == c2
        assert c1 != c3
        assert hash(c1) == hash(c2)

    def test_all_cards_count(self):
        assert len(ALL_CARDS) == 54

    def test_straight_ranks(self):
        assert Rank.TWO not in STRAIGHT_RANKS
        assert Rank.SMALL_JOKER not in STRAIGHT_RANKS
        assert Rank.BIG_JOKER not in STRAIGHT_RANKS
        assert Rank.THREE in STRAIGHT_RANKS
        assert Rank.ACE in STRAIGHT_RANKS
        assert 2 not in STRAIGHT_RANK_VALUES
