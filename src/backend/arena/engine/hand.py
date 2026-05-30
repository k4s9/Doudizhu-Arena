"""Hand — card collection with display ordering, grouping, and dizhu tracking."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .card import STRAIGHT_RANK_VALUES, Card, Rank


@dataclass
class Hand:
    """A player's hand of cards.

    Maintains cards in display order (rank desc, suit ♠>♥>♣>♦).
    Tracks which cards are dizhu cards (底牌) and their played status.
    """

    cards: list[Card] = field(default_factory=list)
    _dizhu_card_ids: set[int] = field(default_factory=set)
    _played_dizhu: set[int] = field(default_factory=set)

    # ── construction ──────────────────────────────────────────────────────

    @classmethod
    def from_cards(cls, cards: tuple[Card, ...] | list[Card]) -> Hand:
        """Create a hand sorted in display order."""
        h = cls()
        h.cards = sorted(cards, key=lambda c: c._sort_key())
        return h

    # ── dizhu tracking ────────────────────────────────────────────────────

    def mark_dizhu_cards(self, dizhu_cards: tuple[Card, ...]) -> None:
        """Mark which cards came from the dizhu (底牌). Call after landlord takes them."""
        self._dizhu_card_ids = {id(c) for c in dizhu_cards}

    def is_dizhu_card(self, card: Card) -> bool:
        return id(card) in self._dizhu_card_ids

    def dizhu_played_status(self) -> dict[str, bool]:
        """Returns {card_str: was_played} for each dizhu card."""
        return {
            str(c): id(c) in self._played_dizhu
            for c in self.cards
            if id(c) in self._dizhu_card_ids
        }

    @property
    def unplayed_dizhu(self) -> list[Card]:
        """Dizhu cards that have not yet been played."""
        return [
            c for c in self.cards
            if id(c) in self._dizhu_card_ids and id(c) not in self._played_dizhu
        ]

    # ── mutations ─────────────────────────────────────────────────────────

    def add_cards(self, cards: list[Card] | tuple[Card, ...]) -> None:
        """Add cards and re-sort."""
        self.cards.extend(cards)
        self.cards.sort(key=lambda c: c._sort_key())

    def remove_cards(self, cards: list[Card]) -> None:
        """Remove specific cards. Raises ValueError if any card not found."""
        remaining = list(self.cards)
        for c in cards:
            try:
                remaining.remove(c)
            except ValueError:
                raise ValueError(f"Card {c} not in hand")
        # Track played dizhu cards
        for c in cards:
            if id(c) in self._dizhu_card_ids:
                self._played_dizhu.add(id(c))
        self.cards = remaining

    def remove_by_ids(self, card_ids: list[int]) -> list[Card]:
        """Remove cards by their Python id(). Returns the removed cards."""
        removed = []
        for c in self.cards:
            if id(c) in card_ids:
                removed.append(c)
                if id(c) in self._dizhu_card_ids:
                    self._played_dizhu.add(id(c))
        if len(removed) != len(card_ids):
            raise ValueError("Some card ids not found in hand")
        self.cards = [c for c in self.cards if id(c) not in card_ids]
        return removed

    # ── queries ───────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self.cards)

    def __contains__(self, card: Card) -> bool:
        return card in self.cards

    def __iter__(self):
        return iter(self.cards)

    @property
    def size(self) -> int:
        return len(self.cards)

    def rank_counts(self) -> dict[Rank, int]:
        """Count cards by rank."""
        counts: Counter[Rank] = Counter()
        for c in self.cards:
            counts[c.rank] += 1
        return dict(counts)

    def cards_of_rank(self, rank: Rank) -> list[Card]:
        return [c for c in self.cards if c.rank == rank]

    def has_rank(self, rank: Rank) -> bool:
        return any(c.rank == rank for c in self.cards)

    def display_str(self) -> str:
        """Space-separated display string (rank desc, ♠→♥→♣→♦)."""
        return " ".join(str(c) for c in self.cards)

    def rank_only_str(self) -> str:
        """Space-separated rank-only string."""
        return " ".join(c.rank_only for c in self.cards)

    def __repr__(self) -> str:
        return f"Hand({self.display_str()})"
