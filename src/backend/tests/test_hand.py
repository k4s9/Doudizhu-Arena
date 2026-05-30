"""Tests for Hand — card collection, sorting, dizhu tracking."""
import pytest

from arena.engine.card import Card, Rank, Suit
from arena.engine.hand import Hand


S3 = Card(Rank.THREE, Suit.SPADE)
H3 = Card(Rank.THREE, Suit.HEART)
C3 = Card(Rank.THREE, Suit.CLUB)
D3 = Card(Rank.THREE, Suit.DIAMOND)
SA = Card(Rank.ACE, Suit.SPADE)
HA = Card(Rank.ACE, Suit.HEART)
S2 = Card(Rank.TWO, Suit.SPADE)
H2 = Card(Rank.TWO, Suit.HEART)
SK = Card(Rank.KING, Suit.SPADE)
HK = Card(Rank.KING, Suit.HEART)
CK = Card(Rank.KING, Suit.CLUB)
DK = Card(Rank.KING, Suit.DIAMOND)
BJ = Card(Rank.BIG_JOKER)
SJ = Card(Rank.SMALL_JOKER)


class TestHandConstruction:
    def test_from_cards_sorts(self):
        h = Hand.from_cards((D3, SA, S2))
        assert [str(c) for c in h.cards] == ["♠2", "♠A", "♦3"]

    def test_empty_hand(self):
        h = Hand()
        assert len(h) == 0
        assert h.size == 0
        assert h.display_str() == ""

    def test_hand_preserves_joker_order(self):
        h = Hand.from_cards((SJ, BJ, SA, S2))
        assert str(h.cards[0]) == "大王"
        assert str(h.cards[1]) == "小王"
        assert str(h.cards[2]) == "♠2"


class TestHandQueries:
    def test_contains(self):
        h = Hand.from_cards((SA, D3))
        assert SA in h
        assert S2 not in h

    def test_iter(self):
        h = Hand.from_cards((D3, SA, S2))
        assert list(h) == [S2, SA, D3]

    def test_size(self):
        h = Hand.from_cards((SA, S2, D3))
        assert h.size == 3

    def test_rank_counts(self):
        h = Hand.from_cards((SK, HK, S2, D3, H2))
        counts = h.rank_counts()
        assert counts[Rank.KING] == 2
        assert counts[Rank.TWO] == 2
        assert counts[Rank.THREE] == 1
        assert Rank.ACE not in counts

    def test_cards_of_rank(self):
        h = Hand.from_cards((SK, HK, CK, SA))
        kings = h.cards_of_rank(Rank.KING)
        assert len(kings) == 3
        aces = h.cards_of_rank(Rank.ACE)
        assert len(aces) == 1

    def test_has_rank(self):
        h = Hand.from_cards((SK, D3))
        assert h.has_rank(Rank.KING)
        assert not h.has_rank(Rank.ACE)

    def test_display_str(self):
        h = Hand.from_cards((D3, SA, S2))
        assert h.display_str() == "♠2 ♠A ♦3"

    def test_rank_only_str(self):
        h = Hand.from_cards((D3, SA, S2))
        assert h.rank_only_str() == "2 A 3"


class TestHandMutations:
    def test_add_cards(self):
        h = Hand.from_cards((SA,))
        h.add_cards([D3, S2])
        assert h.size == 3
        assert str(h.cards[0]) == "♠2"
        assert str(h.cards[-1]) == "♦3"

    def test_remove_cards(self):
        h = Hand.from_cards((SA, S2, D3))
        h.remove_cards([D3])
        assert h.size == 2
        assert D3 not in h

    def test_remove_card_not_in_hand_raises(self):
        h = Hand.from_cards((SA,))
        with pytest.raises(ValueError, match="not in hand"):
            h.remove_cards([S2])

    def test_remove_by_ids(self):
        h = Hand.from_cards((D3, SA, S2))
        target_id = id(D3)
        removed = h.remove_by_ids([target_id])
        assert len(removed) == 1
        assert str(removed[0]) == "♦3"
        assert D3 not in h

    def test_remove_by_ids_not_found_raises(self):
        h = Hand.from_cards((SA,))
        with pytest.raises(ValueError):
            h.remove_by_ids([12345])


class TestDizhuTracking:
    def test_mark_dizhu_cards(self):
        h = Hand.from_cards((SA, S2, D3))
        # mark the D3 that's actually in the hand
        h.mark_dizhu_cards((h.cards_of_rank(Rank.THREE)[0],))
        assert h.is_dizhu_card(h.cards_of_rank(Rank.THREE)[0])
        assert not h.is_dizhu_card(SA)

    def test_unplayed_dizhu(self):
        h = Hand.from_cards((SA, S2, D3))
        dizhu = (h.cards_of_rank(Rank.THREE)[0], h.cards_of_rank(Rank.ACE)[0])
        h.mark_dizhu_cards(dizhu)
        assert len(h.unplayed_dizhu) == 2

    def test_remove_tracks_played_dizhu(self):
        h = Hand.from_cards((SA, S2, D3))
        d3 = h.cards_of_rank(Rank.THREE)[0]
        h.mark_dizhu_cards((d3,))
        assert len(h.unplayed_dizhu) == 1
        h.remove_cards([d3])
        # After removal, the dizhu card is no longer in hand
        assert len(h.unplayed_dizhu) == 0

    def test_remove_by_ids_tracks_played_dizhu(self):
        h = Hand.from_cards((SA, S2, D3))
        d3 = h.cards_of_rank(Rank.THREE)[0]
        h.mark_dizhu_cards((d3,))
        h.remove_by_ids([id(d3)])
        assert len(h.unplayed_dizhu) == 0

    def test_dizhu_played_status(self):
        h = Hand.from_cards((SA, D3, S2))
        d3 = h.cards_of_rank(Rank.THREE)[0]
        h.mark_dizhu_cards((d3,))
        # Before removal: card in hand, not yet played
        status = h.dizhu_played_status()
        assert "♦3" in status
        assert status["♦3"] is False
        h.remove_cards([d3])
        # After removal: card is gone from hand, dizhu_played_status only shows
        # cards still in hand, so entry for ♦3 is gone
        assert "♦3" not in h.dizhu_played_status()
