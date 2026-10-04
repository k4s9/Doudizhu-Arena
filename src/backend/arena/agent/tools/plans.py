"""Compare proposed plays by bounded, exact static hand partition search."""

from __future__ import annotations

from collections import Counter
from math import ceil
from typing import Any

from ...engine.card import Card, Rank
from ...engine.rules import InvalidPlayError, can_beat, recognize
from ...engine.trick import PatternType
from ..base import AgentContext
from .plays import (
    Counts, EMPTY_COUNTS, PATTERN_COVERAGE, Play, SearchBudget,
    SearchLimitExceeded, cards_from_counts, counts_from_cards, generate_plays,
)

DEFAULT_TIME_LIMIT_MS = 90.0
DEFAULT_MAX_NODES = 20_000


def _fits(play: Play, state: Counts) -> bool:
    return all(n <= state[i] for i, n in play.items)


def _subtract(state: Counts, play: Play) -> Counts:
    return tuple(n - used for n, used in zip(state, play.counts))


def _preserves(play: Play, original: Counts) -> bool:
    for i, n in enumerate(original[:13]):
        if n == 4 and play.counts[i]:
            if play.trick.pattern != PatternType.BOMB or play.counts[i] != 4:
                return False
    if original[13] and original[14] and (play.counts[13] or play.counts[14]):
        return play.trick.pattern == PatternType.ROCKET
    return True


def _rank_partition(state: Counts) -> tuple[Counts, ...]:
    """A legal upper-bound witness, available even when generation times out."""
    result = []
    for i, n in enumerate(state[:13]):
        if n:
            group = [0] * 15
            group[i] = n
            result.append(tuple(group))
    if state[13] or state[14]:
        result.append((0,) * 13 + state[13:])
    return tuple(result)


def _partition(
    state: Counts, plays: list[Play], generation_complete: bool,
    budget: SearchBudget,
) -> tuple[tuple[Counts, ...], int, bool]:
    """Iterative depth search with a fixed anchor and failed-state memoization.

    Every partition includes a group containing the lowest remaining rank.
    Restricting the next choice to that group removes permutations without
    excluding any partition. Completing depth k proves a lower bound k+1.
    """
    if not any(state):
        return (), 0, True
    witness = _rank_partition(state)
    usable = [play for play in plays if _fits(play, state)]
    usable.sort(key=lambda play: (-play.size, play.counts))
    greedy: list[Counts] = []
    rest = state
    for play in usable:
        while _fits(play, rest):
            greedy.append(play.counts)
            rest = _subtract(rest, play)
    greedy.extend(_rank_partition(rest))
    if len(greedy) < len(witness):
        witness = tuple(greedy)
    if len(witness) <= 1:
        return witness, len(witness), True
    if not generation_complete:
        # Missing moves may improve any proposed lower bound above one.
        return witness, 1, False

    by_rank = [[play for play in usable if play.counts[i]] for i in range(15)]
    complete_plays = {play.counts for play in usable}
    maximum = max(play.size for play in usable)
    lower = ceil(sum(state) / maximum)
    failed: set[tuple[Counts, int]] = set()

    def search(current: Counts, slots: int) -> tuple[Counts, ...] | None:
        budget.tick()
        if current == EMPTY_COUNTS:
            return ()
        if slots <= 0 or sum(current) > slots * maximum:
            return None
        if current in complete_plays:
            return (current,)
        if slots == 1:
            return None
        key = (current, slots)
        if key in failed:
            return None
        anchor = next(i for i, n in enumerate(current) if n)
        for play in by_rank[anchor]:
            budget.tick()
            if not _fits(play, current):
                continue
            remaining = _subtract(current, play)
            result = search(remaining, slots - 1)
            if result is not None:
                return (play.counts,) + result
        failed.add(key)
        return None

    try:
        while lower < len(witness):
            solution = search(state, lower)
            if solution is not None:
                return solution, lower, True
            lower += 1
    except SearchLimitExceeded:
        return witness, lower, False
    return witness, lower, True


def _parse_action(ctx: AgentContext, value: Any, preserve: bool) -> list[Card]:
    if not isinstance(value, dict) or set(value) != {"cards"}:
        raise ValueError("each action must contain only a cards array")
    strings = value["cards"]
    if not isinstance(strings, list) or len(strings) > 20 or any(
        not isinstance(card, str) for card in strings
    ):
        raise ValueError("cards must be an array of at most 20 full card strings")
    if len(set(strings)) != len(strings):
        raise ValueError("an action cannot use the same physical card twice")
    hand = {str(card): card for card in ctx.hand_cards}
    if any(card not in hand for card in strings):
        raise ValueError("action cards must be full card strings from your hand")
    cards = [hand[string] for string in strings]
    if not cards:
        if ctx.current_trick is None:
            raise ValueError("cannot pass when leading a new trick")
        return cards
    trick = recognize(cards)
    if Counter(trick.all_cards) != Counter(cards):
        raise ValueError("pattern does not account for every submitted card")
    if not can_beat(trick, ctx.current_trick):
        raise ValueError("action does not beat the current trick")
    if preserve:
        candidate = Play(counts_from_cards(cards), tuple(cards), trick, len(cards), ())
        if not _preserves(candidate, counts_from_cards(ctx.hand_cards)):
            raise ValueError("preserve_bombs requires bombs/rocket as standalone plays")
    return cards


def compare_hand_plans(ctx: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return legal remaining-card partitions and honest optimization bounds.

    No turn-by-turn or hidden-hand simulation is performed. Fewer static
    groups does not imply fewer actual turns or a higher probability of winning.
    """
    if not isinstance(arguments, dict) or set(arguments) - {"actions", "constraints"}:
        raise ValueError("expected actions and optional constraints")
    actions = arguments.get("actions")
    if not isinstance(actions, list) or not 1 <= len(actions) <= 3:
        raise ValueError("actions must contain one to three proposed plays")
    constraints = arguments.get("constraints", {})
    if not isinstance(constraints, dict) or set(constraints) - {"preserve_bombs"}:
        raise ValueError("only preserve_bombs is supported as a constraint")
    preserve = constraints.get("preserve_bombs", False)
    if type(preserve) is not bool:
        raise ValueError("preserve_bombs must be boolean")
    if not 0 <= len(ctx.hand_cards) <= 20 or len(set(ctx.hand_cards)) != len(ctx.hand_cards):
        raise ValueError("own hand must contain at most 20 distinct cards")

    budget = SearchBudget(DEFAULT_MAX_NODES, DEFAULT_TIME_LIMIT_MS)
    original = counts_from_cards(ctx.hand_cards)
    prepared: list[list[Card] | None] = []
    results: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        try:
            cards = _parse_action(ctx, action, preserve)
        except (ValueError, InvalidPlayError) as exc:
            prepared.append(None)
            results.append({"index": index, "valid": False, "error": str(exc)})
        else:
            prepared.append(cards)
            results.append({"index": index, "valid": True, "cards": [str(c) for c in cards]})

    plays: list[Play] = []
    generation_complete = True
    if any(cards is not None for cards in prepared):
        try:
            for play in generate_plays(original, max_size=len(ctx.hand_cards), budget=budget):
                if not preserve or _preserves(play, original):
                    plays.append(play)
        except SearchLimitExceeded:
            generation_complete = False

    # Candidates differing only in suits have the same partition problem.
    partitions: dict[Counts, tuple[tuple[Counts, ...], int, bool]] = {}
    for cards, result in zip(prepared, results):
        if cards is None:
            continue
        used = set(cards)
        remaining = [card for card in ctx.hand_cards if card not in used]
        state = counts_from_cards(remaining)
        if state not in partitions:
            partitions[state] = _partition(state, plays, generation_complete, budget)
        partition, lower, exact = partitions[state]
        pool = list(remaining)
        groups = []
        for group in partition:
            group_cards = cards_from_counts(group, pool)
            selected = set(group_cards)
            pool = [card for card in pool if card not in selected]
            trick = recognize(group_cards)
            groups.append({"cards": [str(c) for c in group_cards],
                           "pattern": trick.pattern.value})
        played_counts = counts_from_cards(cards)
        broken = [{"rank": Rank(i + 3).display, "before": n,
                   "played": played_counts[i], "remaining": state[i]}
                  for i, n in enumerate(original)
                  if n >= 2 and 0 < played_counts[i] < n]
        if original[13] and original[14] and played_counts[13] != played_counts[14]:
            broken.append({"rank": "小王+大王", "before": 2,
                           "played": 1, "remaining": 1})
        result.update({
            "remaining_cards": [str(c) for c in remaining], "groups": groups,
            "lower_bound": lower, "upper_bound": len(groups),
            "minimum_groups": len(groups) if exact else None,
            "exact": exact, "truncated": not exact,
            "singletons": [group["cards"][0] for group in groups
                           if group["pattern"] == PatternType.SINGLE.value],
            "broken_groups": broken,
        })

    return {
        "tool": "compare_hand_plans", "plans": results,
        "constraints": {"preserve_bombs": preserve},
        "semantics": "Static partition only; group count is not actual turns or win probability.",
        "coverage": PATTERN_COVERAGE,
        "stats": {"nodes": budget.nodes, "elapsed_ms": round(budget.elapsed_ms, 3),
                  "node_limit": budget.max_nodes, "time_limit_ms": budget.time_limit_ms,
                  "generation_complete": generation_complete,
                  "stop_reason": budget.reason},
    }
