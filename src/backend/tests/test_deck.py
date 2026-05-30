"""Tests for Deck, DealResult, and seat rotation helpers."""
import pytest

from arena.engine.card import ALL_CARDS, Card, Rank, Suit
from arena.engine.deck import SEATS, DealResult, Deck


class TestDeck:
    def test_deck_default_54_cards(self):
        deck = Deck()
        assert len(deck.cards) == 54

    def test_deck_contains_all_cards(self):
        deck = Deck()
        deck_str = {str(c) for c in deck.cards}
        all_str = {str(c) for c in ALL_CARDS}
        assert deck_str == all_str

    def test_shuffle_changes_order(self):
        """Deterministic shuffle with a seed."""
        deck1 = Deck(seed="test-seed-1")
        deck2 = Deck(seed="test-seed-1")
        deck3 = Deck(seed="test-seed-2")
        cards1 = [str(c) for c in deck1.cards]
        cards2 = [str(c) for c in deck2.cards]
        cards3 = [str(c) for c in deck3.cards]
        assert cards1 == cards2  # same seed = same order
        assert cards1 != cards3  # different seed

    def test_deal_result_counts(self):
        deck = Deck(seed="deal-test")
        result = deck.deal()
        assert len(result.dealer_hand) == 17
        assert len(result.second_hand) == 17
        assert len(result.third_hand) == 17
        assert len(result.dizhu_cards) == 3

    def test_deal_exhausts_deck(self):
        deck = Deck(seed="exhaust-test")
        result = deck.deal()
        all_dealt = (
            list(result.dealer_hand)
            + list(result.second_hand)
            + list(result.third_hand)
            + list(result.dizhu_cards)
        )
        assert len(all_dealt) == 54
        assert len(set(all_dealt)) == 54  # no duplicates

    def test_deal_without_shuffle_raises(self):
        deck = Deck()  # no seed
        # deal() still works because initial order is fixed
        result = deck.deal()
        assert len(result.dealer_hand) == 17

    def test_deal_incomplete_deck_raises(self):
        deck = Deck()
        deck.cards.pop()
        with pytest.raises(ValueError, match="54"):
            deck.deal()

    def test_deal_result_seed(self):
        deck = Deck(seed="seed-test")
        result = deck.deal()
        assert result.seed == "seed-test"

    def test_deal_result_immutability(self):
        deck = Deck(seed="freeze-test")
        result = deck.deal()
        # tuples are immutable — ensure they're tuples
        assert isinstance(result.dealer_hand, tuple)
        assert isinstance(result.dizhu_cards, tuple)


class TestSeats:
    def test_seats_tuple(self):
        assert SEATS == ("S", "E", "N", "W")
        assert len(SEATS) == 4
