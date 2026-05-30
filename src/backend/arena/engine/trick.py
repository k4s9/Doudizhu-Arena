"""Trick — pattern type definitions and structured representation of a played hand.

Display format (section 3.6):
  - Default: rank desc, suit ♠→♥→♣→♦ (same as hand)
  - 顺子/连对: rank ascending, e.g. "34567", "334455"
  - Structured patterns use special formats, e.g. "333+J", "7777+5+9"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .card import Card, Rank


class PatternType(StrEnum):
    SINGLE = "单张"
    PAIR = "对子"
    TRIO = "三条"
    TRIO_PLUS_ONE = "三带一"
    TRIO_PLUS_TWO = "三带二"
    STRAIGHT = "顺子"
    CONSECUTIVE_PAIRS = "连对"
    AIRPLANE = "飞机"
    AIRPLANE_PLUS_ONES = "飞机带单"
    AIRPLANE_PLUS_PAIRS = "飞机带对"
    FOUR_PLUS_TWO_SINGLES = "四带二单"
    FOUR_PLUS_ONE_PAIR = "四带二同单"
    FOUR_PLUS_TWO_PAIRS = "四带二对"
    BOMB = "炸弹"
    ROCKET = "火箭"


@dataclass(frozen=True, slots=True)
class Trick:
    """A validated, structured representation of a played set of cards.

    Attributes:
        pattern: The pattern type.
        main_cards: Core cards defining pattern strength.
        attached: Attached/kicker cards.
        main_rank: The comparison rank (highest rank in sequence / trio rank / quad rank).
        length: For straights/airplanes — number of ranks involved.
    """

    pattern: PatternType
    main_cards: tuple[Card, ...]
    attached: tuple[Card, ...] = field(default_factory=tuple)
    main_rank: Rank | None = None
    length: int = 1

    @property
    def all_cards(self) -> tuple[Card, ...]:
        return self.main_cards + self.attached

    @property
    def is_bomb(self) -> bool:
        return self.pattern in (PatternType.BOMB, PatternType.ROCKET)

    def display(self) -> str:
        """Format for play history display per section 3.6 rules."""
        return _format_display(self)

    def rank_only_display(self) -> str:
        """Rank-only display for other players' cards in agent context."""
        return _format_rank_only(self)

    def __repr__(self) -> str:
        return f"Trick({self.pattern.value} {self.display()})"


# ── display formatting ───────────────────────────────────────────────────────

def _cards_by_rank_asc(cards: tuple[Card, ...]) -> list[Card]:
    """Sort cards by rank ascending, suit ♠→♥→♣→♦ (for straights/consecutive pairs display)."""
    return sorted(cards, key=lambda c: (c.rank.value, -(c.suit.value if c.suit else 0)))


def _cards_by_rank_desc(cards: tuple[Card, ...]) -> list[Card]:
    """Sort cards by rank descending, suit ♠→♥→♣→♦ (default display)."""
    return sorted(cards, key=lambda c: (-c.rank.value, -(c.suit.value if c.suit else 0)))


def _rank_repr(rank: Rank) -> str:
    return rank.display


def _format_display(trick: Trick) -> str:
    p = trick.pattern

    if p == PatternType.ROCKET:
        return "小王大王"

    if p == PatternType.STRAIGHT:
        # rank ascending: "34567"
        sorted_cards = _cards_by_rank_asc(trick.main_cards)
        return "".join(c.rank_only for c in sorted_cards)

    if p == PatternType.CONSECUTIVE_PAIRS:
        sorted_cards = _cards_by_rank_asc(trick.main_cards)
        return "".join(c.rank_only for c in sorted_cards)

    if p == PatternType.BOMB:
        return "".join(c.rank_only for c in _cards_by_rank_desc(trick.main_cards))

    if p == PatternType.TRIO_PLUS_ONE:
        trio_rank_str = _rank_repr(trick.main_rank) * 3
        attached_str = "".join(c.rank_only for c in trick.attached)
        return f"{trio_rank_str}+{attached_str}"

    if p == PatternType.TRIO_PLUS_TWO:
        trio_rank_str = _rank_repr(trick.main_rank) * 3
        attached_str = "".join(c.rank_only for c in trick.attached)
        return f"{trio_rank_str}+{attached_str}"

    if p == PatternType.AIRPLANE:
        sorted_cards = _cards_by_rank_asc(trick.main_cards)
        return "".join(c.rank_only for c in sorted_cards)

    if p == PatternType.AIRPLANE_PLUS_ONES:
        main_str = "".join(c.rank_only for c in _cards_by_rank_asc(trick.main_cards))
        attached_str = "".join(c.rank_only for c in _cards_by_rank_asc(trick.attached))
        return f"{main_str}+{attached_str}"

    if p == PatternType.AIRPLANE_PLUS_PAIRS:
        main_str = "".join(c.rank_only for c in _cards_by_rank_asc(trick.main_cards))
        attached_str = "".join(c.rank_only for c in _cards_by_rank_asc(trick.attached))
        return f"{main_str}+{attached_str}"

    if p == PatternType.FOUR_PLUS_TWO_SINGLES:
        quad_str = _rank_repr(trick.main_rank) * 4
        singles = _cards_by_rank_asc(trick.attached)
        return f"{quad_str}+{singles[0].rank_only}+{singles[1].rank_only}"

    if p == PatternType.FOUR_PLUS_ONE_PAIR:
        quad_str = _rank_repr(trick.main_rank) * 4
        attached_str = "".join(c.rank_only for c in trick.attached)
        return f"{quad_str}+{attached_str}"

    if p == PatternType.FOUR_PLUS_TWO_PAIRS:
        quad_str = _rank_repr(trick.main_rank) * 4
        pairs = _cards_by_rank_asc(trick.attached)
        # attached has 4 cards (2 pairs) sorted by rank ascending;
        # pairs[0:2] = first pair, pairs[2:4] = second pair
        r0 = pairs[0].rank_only
        r1 = pairs[2].rank_only
        return f"{quad_str}+{r0}{r0}+{r1}{r1}"

    # Default: rank descending, suit ♠→♥→♣→♦
    sorted_cards = _cards_by_rank_desc(trick.main_cards + trick.attached)
    return "".join(str(c) for c in sorted_cards)


def _format_rank_only(trick: Trick) -> str:
    """Rank-only display — used for other players' cards in agent play history."""
    sorted_cards = sorted(
        trick.all_cards, key=lambda c: (-c.rank.value, -(c.suit.value if c.suit else 0))
    )
    return "".join(c.rank_only for c in sorted_cards)
