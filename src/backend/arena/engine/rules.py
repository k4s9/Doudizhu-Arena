"""Pattern recognition and play validation for all 15 Doudizhu card patterns.

Recognition is deterministic: the set of cards uniquely determines the pattern type
because each pattern has a distinct rank-grouping signature (see module docstring).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from .card import STRAIGHT_RANK_VALUES, STRAIGHT_RANKS, Card, Rank
from .trick import PatternType, Trick

# ── helpers ──────────────────────────────────────────────────────────────────


def _group_by_rank(cards: list[Card] | tuple[Card, ...]) -> dict[Rank, list[Card]]:
    groups: dict[Rank, list[Card]] = defaultdict(list)
    for c in cards:
        groups[c.rank].append(c)
    return groups


def _count_signature(groups: dict[Rank, list[Card]]) -> dict[int, int]:
    """Return {count: how_many_ranks_have_this_count}, e.g. {3:1, 1:1} for 三带一."""
    sig: Counter[int] = Counter()
    for cards in groups.values():
        sig[len(cards)] += 1
    return dict(sig)


def _is_consecutive(ranks: list[Rank]) -> bool:
    """Check if ranks are consecutive AND within the straight-valid range (3-A)."""
    if not ranks:
        return False
    vals = sorted(r.value for r in ranks)
    if any(v not in STRAIGHT_RANK_VALUES for v in vals):
        return False
    for i in range(1, len(vals)):
        if vals[i] - vals[i - 1] != 1:
            return False
    return True


def _get_consecutive_count(ranks: list[Rank]) -> int:
    """Return the number of ranks in a consecutive sequence (must be validated first)."""
    return len(ranks)


def _ranks_by_count(groups: dict[Rank, list[Card]], count: int) -> list[Rank]:
    """Return ranks that appear exactly `count` times, sorted by rank value ascending."""
    return sorted(
        [r for r, cards in groups.items() if len(cards) == count],
        key=lambda r: r.value,
    )


def _max_rank(groups: dict[Rank, list[Card]]) -> Rank:
    """Return the highest rank in the groups (by comparison value)."""
    return max(groups.keys(), key=lambda r: r.value)


# ── recognition ──────────────────────────────────────────────────────────────


class InvalidPlayError(ValueError):
    """Raised when the cards do not form a valid Doudizhu pattern."""
    pass


def recognize(cards: list[Card] | tuple[Card, ...]) -> Trick:
    """Identify the Doudizhu pattern formed by a set of cards.

    Returns a Trick describing the pattern, main cards, and attached cards.
    Raises InvalidPlayError if the cards don't form any valid pattern.
    """
    n = len(cards)
    if n == 0:
        raise InvalidPlayError("Cannot play zero cards")

    groups = _group_by_rank(list(cards))
    sig = _count_signature(groups)
    sorted_main = tuple(sorted(cards, key=lambda c: c._sort_key()))

    # ── Rocket (2 jokers) ──────────────────────────────────────────────
    if n == 2 and set(groups.keys()) == {Rank.BIG_JOKER, Rank.SMALL_JOKER}:
        return Trick(
            pattern=PatternType.ROCKET,
            main_cards=sorted_main,
            main_rank=Rank.BIG_JOKER,
        )

    # ── Single ─────────────────────────────────────────────────────────
    if n == 1:
        return Trick(
            pattern=PatternType.SINGLE,
            main_cards=sorted_main,
            main_rank=list(groups.keys())[0],
        )

    # ── Pair ───────────────────────────────────────────────────────────
    if n == 2 and sig == {2: 1}:
        r = list(groups.keys())[0]
        return Trick(
            pattern=PatternType.PAIR,
            main_cards=sorted_main,
            main_rank=r,
        )

    # ── Trio ───────────────────────────────────────────────────────────
    if n == 3 and sig == {3: 1}:
        r = list(groups.keys())[0]
        return Trick(
            pattern=PatternType.TRIO,
            main_cards=sorted_main,
            main_rank=r,
        )

    # ── Bomb ───────────────────────────────────────────────────────────
    if n == 4 and sig == {4: 1}:
        r = list(groups.keys())[0]
        return Trick(
            pattern=PatternType.BOMB,
            main_cards=sorted_main,
            main_rank=r,
        )

    # ── Trio + Single ──────────────────────────────────────────────────
    if n == 4 and sig == {3: 1, 1: 1}:
        trio_rank = _ranks_by_count(groups, 3)[0]
        single_rank = _ranks_by_count(groups, 1)[0]
        if trio_rank == single_rank:
            raise InvalidPlayError("Trio and attached single cannot be same rank")
        trio_cards = tuple(sorted(groups[trio_rank], key=lambda c: c._sort_key()))
        single_cards = tuple(sorted(groups[single_rank], key=lambda c: c._sort_key()))
        return Trick(
            pattern=PatternType.TRIO_PLUS_ONE,
            main_cards=trio_cards,
            attached=single_cards,
            main_rank=trio_rank,
        )

    # ── Trio + Pair ────────────────────────────────────────────────────
    if n == 5 and sig == {3: 1, 2: 1}:
        trio_rank = _ranks_by_count(groups, 3)[0]
        pair_rank = _ranks_by_count(groups, 2)[0]
        if trio_rank == pair_rank:
            raise InvalidPlayError("Trio and attached pair cannot be same rank")
        trio_cards = tuple(sorted(groups[trio_rank], key=lambda c: c._sort_key()))
        pair_cards = tuple(sorted(groups[pair_rank], key=lambda c: c._sort_key()))
        return Trick(
            pattern=PatternType.TRIO_PLUS_TWO,
            main_cards=trio_cards,
            attached=pair_cards,
            main_rank=trio_rank,
        )

    # ── Straight (>=5 consecutive singles, 3-A) ────────────────────────
    if n >= 5 and sig.get(1, 0) == n:
        ranks = _ranks_by_count(groups, 1)
        if len(ranks) == n and _is_consecutive(ranks):
            return Trick(
                pattern=PatternType.STRAIGHT,
                main_cards=sorted_main,
                main_rank=ranks[-1],  # highest rank
                length=len(ranks),
            )

    # ── Consecutive Pairs (>=3 consecutive pairs, 3-A) ─────────────────
    if n >= 6 and n % 2 == 0 and sig.get(2, 0) == n // 2:
        ranks = _ranks_by_count(groups, 2)
        if len(ranks) == n // 2 and _is_consecutive(ranks):
            return Trick(
                pattern=PatternType.CONSECUTIVE_PAIRS,
                main_cards=sorted_main,
                main_rank=ranks[-1],
                length=len(ranks),
            )

    # ── Airplane (>=2 consecutive trios, no attachments) ───────────────
    if n >= 6 and n % 3 == 0 and sig.get(3, 0) == n // 3:
        ranks = _ranks_by_count(groups, 3)
        if len(ranks) >= 2 and _is_consecutive(ranks):
            return Trick(
                pattern=PatternType.AIRPLANE,
                main_cards=sorted_main,
                main_rank=ranks[-1],
                length=len(ranks),
            )

    # ── Airplane + Singles ─────────────────────────────────────────────
    trio_count = sig.get(3, 0)
    if trio_count >= 2 and sig.get(1, 0) == trio_count:
        trio_ranks = _ranks_by_count(groups, 3)
        if _is_consecutive(trio_ranks):
            single_ranks = _ranks_by_count(groups, 1)
            # Attached singles must not conflict with airplane ranks
            if any(r in trio_ranks for r in single_ranks):
                raise InvalidPlayError("Airplane attached singles conflict with airplane ranks")
            trio_cards = tuple(
                sorted(
                    [c for r in trio_ranks for c in groups[r]],
                    key=lambda c: c._sort_key(),
                )
            )
            single_cards = tuple(
                sorted(
                    [c for r in single_ranks for c in groups[r]],
                    key=lambda c: c._sort_key(),
                )
            )
            return Trick(
                pattern=PatternType.AIRPLANE_PLUS_ONES,
                main_cards=trio_cards,
                attached=single_cards,
                main_rank=trio_ranks[-1],
                length=len(trio_ranks),
            )

    # ── Airplane + Pairs ───────────────────────────────────────────────
    if trio_count >= 2 and sig.get(2, 0) == trio_count:
        trio_ranks = _ranks_by_count(groups, 3)
        pair_ranks = _ranks_by_count(groups, 2)
        if _is_consecutive(trio_ranks):
            if any(r in trio_ranks for r in pair_ranks):
                raise InvalidPlayError("Airplane attached pairs conflict with airplane ranks")
            trio_cards = tuple(
                sorted(
                    [c for r in trio_ranks for c in groups[r]],
                    key=lambda c: c._sort_key(),
                )
            )
            pair_cards = tuple(
                sorted(
                    [c for r in pair_ranks for c in groups[r]],
                    key=lambda c: c._sort_key(),
                )
            )
            return Trick(
                pattern=PatternType.AIRPLANE_PLUS_PAIRS,
                main_cards=trio_cards,
                attached=pair_cards,
                main_rank=trio_ranks[-1],
                length=len(trio_ranks),
            )

    # ── Four + Two Singles ─────────────────────────────────────────────
    if n == 6 and sig == {4: 1, 1: 2}:
        quad_rank = _ranks_by_count(groups, 4)[0]
        single_ranks = _ranks_by_count(groups, 1)
        if any(r == quad_rank for r in single_ranks):
            raise InvalidPlayError("Four+2singles: attached singles conflict with quad rank")
        quad_cards = tuple(sorted(groups[quad_rank], key=lambda c: c._sort_key()))
        single_cards = tuple(
            sorted(
                [c for r in single_ranks for c in groups[r]],
                key=lambda c: c._sort_key(),
            )
        )
        return Trick(
            pattern=PatternType.FOUR_PLUS_TWO_SINGLES,
            main_cards=quad_cards,
            attached=single_cards,
            main_rank=quad_rank,
        )

    # ── Four + One Pair (四带二同单) ───────────────────────────────────
    if n == 6 and sig == {4: 1, 2: 1}:
        quad_rank = _ranks_by_count(groups, 4)[0]
        pair_rank = _ranks_by_count(groups, 2)[0]
        if quad_rank == pair_rank:
            raise InvalidPlayError("Four+pair: attached pair cannot be same rank as quad")
        quad_cards = tuple(sorted(groups[quad_rank], key=lambda c: c._sort_key()))
        pair_cards = tuple(sorted(groups[pair_rank], key=lambda c: c._sort_key()))
        return Trick(
            pattern=PatternType.FOUR_PLUS_ONE_PAIR,
            main_cards=quad_cards,
            attached=pair_cards,
            main_rank=quad_rank,
        )

    # ── Four + Two Pairs ───────────────────────────────────────────────
    if n == 8 and sig == {4: 1, 2: 2}:
        quad_rank = _ranks_by_count(groups, 4)[0]
        pair_ranks = _ranks_by_count(groups, 2)
        if quad_rank in pair_ranks:
            raise InvalidPlayError("Four+2pairs: attached pairs conflict with quad rank")
        quad_cards = tuple(sorted(groups[quad_rank], key=lambda c: c._sort_key()))
        pair_cards = tuple(
            sorted(
                [c for r in pair_ranks for c in groups[r]],
                key=lambda c: c._sort_key(),
            )
        )
        return Trick(
            pattern=PatternType.FOUR_PLUS_TWO_PAIRS,
            main_cards=quad_cards,
            attached=pair_cards,
            main_rank=quad_rank,
        )

    raise InvalidPlayError(
        f"Cards do not form a valid Doudizhu pattern: "
        f"{' '.join(str(c) for c in sorted_main)}"
    )


# ── play validation ──────────────────────────────────────────────────────────


def validate_play(trick: Trick, hand_cards: list[Card]) -> None:
    """Verify all cards in trick exist in hand. Raises InvalidPlayError."""
    hand_set = {id(c): c for c in hand_cards}
    for c in trick.all_cards:
        if id(c) not in hand_set:
            raise InvalidPlayError(f"Card {c} not in hand")


def can_beat(trick: Trick, target: Trick | None) -> bool:
    """Check if `trick` can beat `target`.

    - If target is None, any valid trick is allowed (new round leader).
    - Same pattern type + same length (for sequences): higher main_rank wins.
    - Bomb beats any non-bomb. Rocket beats everything.
    - Different non-bomb patterns cannot be compared (returns False).
    """
    if target is None:
        return True  # leader of new round, any play is valid

    # Rocket beats everything
    if trick.pattern == PatternType.ROCKET:
        return True

    # Bomb beats non-bomb
    if trick.pattern == PatternType.BOMB:
        if target.pattern == PatternType.ROCKET:
            return False
        if target.pattern != PatternType.BOMB:
            return True
        # Bomb vs bomb: higher rank wins
        return trick.main_rank.value > target.main_rank.value

    # Non-bomb cannot beat bomb/rocket
    if target.is_bomb:
        return False

    # Same pattern family — must match type AND length
    if trick.pattern != target.pattern:
        return False

    # For sequence-based patterns, length must match
    if trick.pattern in (
        PatternType.STRAIGHT,
        PatternType.CONSECUTIVE_PAIRS,
        PatternType.AIRPLANE,
        PatternType.AIRPLANE_PLUS_ONES,
        PatternType.AIRPLANE_PLUS_PAIRS,
    ):
        if trick.length != target.length:
            return False

    # Compare main_rank
    if trick.main_rank is None or target.main_rank is None:
        return False
    return trick.main_rank.value > target.main_rank.value


def is_pattern_match(trick: Trick, target: Trick) -> bool:
    """Check if `trick` matches the pattern type and length of `target`.

    True when trick could be played to follow target (ignoring rank comparison).
    """
    if trick.pattern == PatternType.ROCKET:
        return True
    if trick.pattern == PatternType.BOMB:
        return target.pattern != PatternType.ROCKET
    if target.is_bomb:
        return trick.is_bomb and trick.main_rank.value > target.main_rank.value
    if trick.pattern != target.pattern:
        return False
    if trick.pattern in (
        PatternType.STRAIGHT,
        PatternType.CONSECUTIVE_PAIRS,
        PatternType.AIRPLANE,
        PatternType.AIRPLANE_PLUS_ONES,
        PatternType.AIRPLANE_PLUS_PAIRS,
    ):
        return trick.length == target.length
    return True
