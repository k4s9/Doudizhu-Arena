"""Card, Rank, Suit — foundational types for the Doudizhu game engine.

Rank values double as comparison order: 3 < 4 < ... < A < 2 < 小王 < 大王.
Suit values define display order (descending): ♠ > ♥ > ♣ > ♦.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from functools import total_ordering

# ── Rank ────────────────────────────────────────────────────────────────────

_RANK_DISPLAY: dict[int, str] = {
    3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "10",
    11: "J", 12: "Q", 13: "K", 14: "A", 15: "2",
    16: "小王", 17: "大王",
}

_RANK_FROM_DISPLAY: dict[str, int] = {v: k for k, v in _RANK_DISPLAY.items()}


class Rank(IntEnum):
    THREE = 3
    FOUR = 4
    FIVE = 5
    SIX = 6
    SEVEN = 7
    EIGHT = 8
    NINE = 9
    TEN = 10
    JACK = 11
    QUEEN = 12
    KING = 13
    ACE = 14
    TWO = 15
    SMALL_JOKER = 16
    BIG_JOKER = 17

    @property
    def display(self) -> str:
        return _RANK_DISPLAY[self.value]

    @classmethod
    def from_display(cls, s: str) -> Rank:
        """Parse a display string like '3', 'K', '小王' into a Rank."""
        v = _RANK_FROM_DISPLAY.get(s)
        if v is None:
            raise ValueError(f"Invalid rank display: {s!r}")
        return cls(v)

    @classmethod
    def from_char(cls, c: str) -> Rank:
        """Parse a single character like '3', 'J', '2' into a Rank (jokers not supported)."""
        return cls.from_display(c)

    def __repr__(self) -> str:
        return f"Rank.{self.name}"


# ── Suit ────────────────────────────────────────────────────────────────────

_SUIT_DISPLAY: dict[int, str] = {4: "♠", 3: "♥", 2: "♣", 1: "♦"}
_SUIT_FROM_DISPLAY: dict[str, int] = {v: k for k, v in _SUIT_DISPLAY.items()}


class Suit(IntEnum):
    SPADE = 4    # ♠
    HEART = 3    # ♥
    CLUB = 2     # ♣
    DIAMOND = 1  # ♦

    @property
    def display(self) -> str:
        return _SUIT_DISPLAY[self.value]

    @classmethod
    def from_display(cls, s: str) -> Suit:
        v = _SUIT_FROM_DISPLAY.get(s)
        if v is None:
            raise ValueError(f"Invalid suit display: {s!r}")
        return cls(v)

    def __repr__(self) -> str:
        return f"Suit.{self.name}"


# ── Card ────────────────────────────────────────────────────────────────────

_JOKER_RANKS = frozenset({Rank.SMALL_JOKER, Rank.BIG_JOKER})

# Pre-compute rank display length to avoid repeated len() calls
_RANK_DISPLAY_LEN: dict[int, int] = {k: len(v) for k, v in _RANK_DISPLAY.items()}


@dataclass(frozen=True, slots=True)
class Card:
    """An immutable playing card.

    Regular cards have rank + suit. Jokers have rank only (suit is None).
    """
    rank: Rank
    suit: Suit | None = None

    def __post_init__(self) -> None:
        if self.rank in _JOKER_RANKS:
            if self.suit is not None:
                raise ValueError(f"Joker {self.rank.display} cannot have a suit")
        elif self.suit is None:
            raise ValueError(f"Regular card {self.rank.display} must have a suit")

    @property
    def is_joker(self) -> bool:
        return self.rank in _JOKER_RANKS

    # -- display ---------------------------------------------------------------

    def __str__(self) -> str:
        """Full display: '♠A', '♥K', '大王', '小王'."""
        if self.is_joker:
            return self.rank.display
        return f"{self.suit.display}{self.rank.display}"

    @property
    def rank_only(self) -> str:
        """Rank-only display used for other players' cards in play history."""
        return self.rank.display

    # -- parsing ---------------------------------------------------------------

    @classmethod
    def from_string(cls, s: str) -> Card:
        """Parse from full display format: '♠A', '♥10', '大王', '小王'."""
        if s in ("大王", "小王"):
            return cls(rank=Rank.from_display(s))
        if len(s) < 2:
            raise ValueError(f"Invalid card string: {s!r}")
        suit_str = s[0]
        rank_str = s[1:]
        return cls(suit=Suit.from_display(suit_str), rank=Rank.from_display(rank_str))

    # -- ordering --------------------------------------------------------------

    def _sort_key(self) -> tuple[int, int]:
        """Sort key for hand display: rank desc, suit desc (♠>♥>♣>♦)."""
        suit_val = self.suit.value if self.suit is not None else 0
        return (-self.rank.value, -suit_val)

    def __lt__(self, other: Card) -> bool:
        if not isinstance(other, Card):
            return NotImplemented
        return self._sort_key() < other._sort_key()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Card):
            return NotImplemented
        return self.rank == other.rank and self.suit == other.suit

    def __hash__(self) -> int:
        return hash((self.rank, self.suit))

    def __repr__(self) -> str:
        return f"Card({self})"


# ── Helpers ──────────────────────────────────────────────────────────────────

# All 54 cards pre-computed for deck building
ALL_CARDS: list[Card] = []
for _rank in Rank:
    if _rank in _JOKER_RANKS:
        ALL_CARDS.append(Card(rank=_rank))
    else:
        for _suit in Suit:
            ALL_CARDS.append(Card(rank=_rank, suit=_suit))

# Fixed seat cycle (S→E→N→W), used across engine and tournament modules
SEATS = ("S", "E", "N", "W")
TURN_CYCLE = ("S", "E", "N", "W")  # same cycle; idle seat is skipped during play

# Convenience lookups
RANK_COMPARE_ORDER: dict[Rank, int] = {r: r.value for r in Rank}
SUIT_DISPLAY_ORDER: dict[Suit, int] = {s: s.value for s in Suit}

# Valid ranks for straights / consecutive patterns (3 through A, no 2 or jokers)
STRAIGHT_RANKS: list[Rank] = [
    Rank.THREE, Rank.FOUR, Rank.FIVE, Rank.SIX, Rank.SEVEN,
    Rank.EIGHT, Rank.NINE, Rank.TEN, Rank.JACK, Rank.QUEEN, Rank.KING, Rank.ACE,
]
STRAIGHT_RANK_VALUES: frozenset[int] = frozenset(r.value for r in STRAIGHT_RANKS)
