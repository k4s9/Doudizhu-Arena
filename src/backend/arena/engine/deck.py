"""Deck — a 54-card deck with seeded shuffle and deal."""

from __future__ import annotations

import random
from dataclasses import dataclass

from .card import ALL_CARDS, SEATS, Card, Rank, Suit


class Deck:
    """A complete 54-card deck (52 regular + 2 jokers)."""

    def __init__(self, seed: str | None = None) -> None:
        self.cards: list[Card] = list(ALL_CARDS)
        self._seed = seed
        if seed is not None:
            rng = random.Random(seed)
            rng.shuffle(self.cards)

    def shuffle(self, seed: str) -> None:
        """Shuffle deterministically with a string seed."""
        self._seed = seed
        rng = random.Random(seed)
        rng.shuffle(self.cards)

    def deal(self) -> DealResult:
        """Deal 17 cards each to dealer/二家/尾家, 3 dizhu cards. 闲家 gets 0.

        Caller is responsible for mapping dealer_seat to the correct recipient.
        """
        if len(self.cards) != 54:
            raise ValueError("Deck must have exactly 54 cards to deal")
        return DealResult(
            dealer_hand=tuple(self.cards[0:17]),
            second_hand=tuple(self.cards[17:34]),
            third_hand=tuple(self.cards[34:51]),
            dizhu_cards=tuple(self.cards[51:54]),
            seed=self._seed or "",
        )



@dataclass(frozen=True, slots=True)
class DealResult:
    dealer_hand: tuple[Card, ...]   # 17 cards
    second_hand: tuple[Card, ...]   # 17 cards
    third_hand: tuple[Card, ...]    # 17 cards
    dizhu_cards: tuple[Card, ...]   # 3 cards
    seed: str
