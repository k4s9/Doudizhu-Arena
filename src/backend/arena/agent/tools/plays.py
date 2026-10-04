"""Bounded enumeration of complete plays, with suits factored out.

The fifteen coordinates are ranks 3 through big joker. A coordinate tuple
represents one play regardless of which equivalent suits are used. Templates
follow this repository's rules (in particular, airplane single wings have
distinct ranks), and every result is checked by the authoritative recognizer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from time import perf_counter
from typing import Iterable, Iterator

from ...engine.card import ALL_CARDS, Card
from ...engine.rules import InvalidPlayError, recognize
from ...engine.trick import Trick

Counts = tuple[int, ...]
EMPTY_COUNTS: Counts = (0,) * 15
PATTERN_COVERAGE = "all 15 engine patterns with complete card consumption"
_RANK_CARDS = tuple(
    tuple(sorted(c for c in ALL_CARDS if c.rank.value == rank))
    for rank in range(3, 18)
)


class SearchLimitExceeded(RuntimeError):
    """A bounded search stopped; its partial results are still usable."""


@dataclass
class SearchBudget:
    max_nodes: int = 20_000
    time_limit_ms: float = 90.0
    nodes: int = 0
    exhausted: bool = False
    reason: str | None = None
    _started: float = field(default_factory=perf_counter, repr=False)

    @property
    def elapsed_ms(self) -> float:
        return (perf_counter() - self._started) * 1000

    def tick(self) -> None:
        # Check the clock at each expansion, including template enumeration.
        # This is a cooperative limit, not a real-time scheduling guarantee.
        if self.exhausted or self.nodes >= self.max_nodes:
            self.exhausted = True
            self.reason = self.reason or "node_limit"
            raise SearchLimitExceeded(self.reason)
        if self.elapsed_ms >= self.time_limit_ms:
            self.exhausted = True
            self.reason = "time_limit"
            raise SearchLimitExceeded(self.reason)
        self.nodes += 1


@dataclass(frozen=True, slots=True)
class Play:
    counts: Counts
    cards: tuple[Card, ...]
    trick: Trick
    size: int
    items: tuple[tuple[int, int], ...]


def counts_from_cards(cards: Iterable[Card]) -> Counts:
    counts = [0] * 15
    for card in cards:
        counts[card.rank.value - 3] += 1
    return tuple(counts)


def _validate_counts(counts: Counts) -> None:
    if len(counts) != 15 or any(
        type(n) is not int or n < 0 or n > (4 if i < 13 else 1)
        for i, n in enumerate(counts)
    ):
        raise ValueError("counts must contain 15 physical deck rank counts")


def cards_from_counts(counts: Counts, pool: Iterable[Card] | None = None) -> list[Card]:
    """Materialize counts, optionally selecting actual cards from one's hand.

    With no pool the suits are synthetic and must never be reported as an
    opponent's real cards. Hidden-information callers should return ranks only.
    """
    _validate_counts(counts)
    if pool is None:
        buckets = _RANK_CARDS
    else:
        mutable: list[list[Card]] = [[] for _ in range(15)]
        for card in sorted(pool):
            mutable[card.rank.value - 3].append(card)
        buckets = mutable
    if any(len(buckets[i]) < n for i, n in enumerate(counts)):
        raise ValueError("card pool does not contain the requested ranks")
    return [card for i, n in enumerate(counts) for card in buckets[i][:n]]


def _templates(counts: Counts, minimum: int, maximum: int) -> Iterator[Counts]:
    def make(core: Iterable[int], multiplicity: int,
             wings: Iterable[int] = (), wing_size: int = 1) -> Counts:
        result = [0] * 15
        for rank in core:
            result[rank] = multiplicity
        for rank in wings:
            result[rank] = wing_size
        return tuple(result)

    # Same-rank plays, including single jokers.
    for rank, available in enumerate(counts):
        for n in range(max(1, minimum), min(available, maximum) + 1):
            yield make((rank,), n)
    if minimum <= 2 <= maximum and counts[13] and counts[14]:
        yield make((13, 14), 1)

    for rank, available in enumerate(counts[:13]):
        if available >= 3:
            for wing_size in (1, 2):
                if minimum <= 3 + wing_size <= maximum:
                    for other, other_count in enumerate(counts):
                        if other != rank and other_count >= wing_size:
                            yield make((rank,), 3, (other,), wing_size)
        if available == 4:
            for wing_size, wing_count in ((1, 2), (2, 1), (2, 2)):
                if minimum <= 4 + wing_size * wing_count <= maximum:
                    choices = [i for i, n in enumerate(counts)
                               if i != rank and n >= wing_size]
                    for wings in combinations(choices, wing_count):
                        yield make((rank,), 4, wings, wing_size)

    # Sequences never include 2 or jokers. Each prefix is a separate play.
    for multiplicity, shortest in ((1, 5), (2, 3), (3, 2)):
        for start in range(12):
            for end in range(start, 12):
                if counts[end] < multiplicity:
                    break
                length = end - start + 1
                if multiplicity * length > maximum:
                    break
                if length < shortest:
                    continue
                core = range(start, end + 1)
                if minimum <= multiplicity * length <= maximum:
                    yield make(core, multiplicity)
                if multiplicity != 3:
                    continue
                for wing_size in (1, 2):
                    if not minimum <= (3 + wing_size) * length <= maximum:
                        continue
                    choices = [i for i, n in enumerate(counts)
                               if not start <= i <= end and n >= wing_size]
                    for wings in combinations(choices, length):
                        yield make(core, 3, wings, wing_size)


def generate_plays(
    counts: Counts, *, max_size: int | None = None,
    exact_size: int | None = None, budget: SearchBudget | None = None,
) -> Iterator[Play]:
    """Yield all complete legal rank patterns, or raise at the search limit.

    Completeness concerns patterns for which recognize consumes every input
    card. The additional coverage check guards against recognizer regressions
    that might accept a partial airplane and omit extra cards from its Trick.
    """
    _validate_counts(counts)
    for size in (max_size, exact_size):
        if size is not None and (type(size) is not int or size < 0):
            raise ValueError("size limits must be nonnegative integers")
    maximum = min(sum(counts), 20 if max_size is None else max_size)
    minimum = 1
    if exact_size is not None:
        minimum = exact_size
        maximum = min(maximum, exact_size)
    if maximum < max(1, minimum):
        return
    active_budget = budget if budget is not None else SearchBudget()
    for candidate in _templates(counts, minimum, maximum):
        active_budget.tick()
        cards = tuple(cards_from_counts(candidate))
        try:
            trick = recognize(cards)
        except InvalidPlayError:
            continue
        if len(trick.all_cards) != len(cards) or set(trick.all_cards) != set(cards):
            continue
        yield Play(candidate, cards, trick, len(cards),
                   tuple((i, n) for i, n in enumerate(candidate) if n))
