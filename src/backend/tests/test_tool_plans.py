"""Independent enumeration/partition oracles for deterministic analysis tools."""

from collections import Counter
from functools import lru_cache
from itertools import product
import random

import pytest

from arena.agent.base import AgentContext
from arena.agent.tools import plans
from arena.agent.tools.plans import compare_hand_plans
from arena.agent.tools.plays import (
    SearchBudget, SearchLimitExceeded, cards_from_counts,
    counts_from_cards, generate_plays,
)
from arena.engine.card import ALL_CARDS, Card
from arena.engine.rules import InvalidPlayError, recognize
from arena.engine.trick import PatternType


def counts(*values):
    return tuple(values) + (0,) * (15 - len(values))


def context(rank_counts, *, target=None):
    cards = cards_from_counts(rank_counts)
    return AgentContext(seat="S", role="farmer", hand_cards=cards,
                        hand_size=len(cards), current_trick=target)


def complete_pattern(candidate):
    if not any(candidate):
        return False
    cards = cards_from_counts(candidate)
    try:
        trick = recognize(cards)
    except InvalidPlayError:
        return False
    return Counter(trick.all_cards) == Counter(cards)


def brute_patterns(pool):
    return {candidate for candidate in product(*(range(n + 1) for n in pool))
            if complete_pattern(candidate)}


@pytest.mark.parametrize("pool", [
    counts(3, 3, 2, 1, 1), counts(4, 2, 2, 1), counts(2, 2, 2, 2, 2, 2),
    counts(3, 3, 3, 1, 1, 1), counts(4, 4, 0, 1, 1),
    (0,) * 10 + (1, 1, 1, 1, 1),
])
def test_generator_matches_exhaustive_complete_card_oracle(pool):
    result = list(generate_plays(pool, budget=SearchBudget(100_000, 2000)))
    actual = {play.counts for play in result}
    assert len(actual) == len(result), "rank patterns must be suit-deduplicated"
    assert actual == brute_patterns(pool)
    for play in result:
        assert Counter(play.cards) == Counter(play.trick.all_cards)


@pytest.mark.parametrize("pool,pattern", [
    (counts(1), PatternType.SINGLE), (counts(2), PatternType.PAIR),
    (counts(3), PatternType.TRIO), (counts(3, 1), PatternType.TRIO_PLUS_ONE),
    (counts(3, 2), PatternType.TRIO_PLUS_TWO),
    (counts(1, 1, 1, 1, 1), PatternType.STRAIGHT),
    (counts(2, 2, 2), PatternType.CONSECUTIVE_PAIRS),
    (counts(3, 3), PatternType.AIRPLANE),
    (counts(3, 3, 1, 1), PatternType.AIRPLANE_PLUS_ONES),
    (counts(3, 3, 2, 2), PatternType.AIRPLANE_PLUS_PAIRS),
    (counts(4, 1, 1), PatternType.FOUR_PLUS_TWO_SINGLES),
    (counts(4, 2), PatternType.FOUR_PLUS_ONE_PAIR),
    (counts(4, 2, 2), PatternType.FOUR_PLUS_TWO_PAIRS),
    (counts(4), PatternType.BOMB),
    ((0,) * 13 + (1, 1), PatternType.ROCKET),
])
def test_all_fifteen_pattern_families(pool, pattern):
    result = list(generate_plays(pool, exact_size=sum(pool)))
    assert len(result) == 1
    assert result[0].counts == pool
    assert result[0].trick.pattern == pattern


def test_airplane_wings_match_repository_rules_and_never_swallow_cards():
    # A pair is not two different single wings. Unused quads/pairs cannot be
    # silently lost even if an older recognizer accepts a partial airplane.
    for pool in (counts(3, 3, 2), counts(3, 3, 1, 1, 2), counts(3, 3, 1, 1, 4)):
        assert not list(generate_plays(pool, exact_size=sum(pool)))


@pytest.mark.parametrize("pool", [counts(3, 3, 1, 1, 2), counts(3, 3, 2, 2, 4)])
def test_recognizer_rejects_airplanes_with_extraneous_cards(pool):
    with pytest.raises(InvalidPlayError):
        recognize(cards_from_counts(pool))


def test_exact_size_filters_unknown_pool_without_enumerating_physical_suits():
    pool = counts_from_cards(ALL_CARDS)
    result = list(generate_plays(pool, exact_size=2))
    assert len(result) == 14  # thirteen rank pairs plus rocket
    assert all(play.size == 2 for play in result)


def test_generator_reports_node_and_clock_limits():
    budget = SearchBudget(2, 1000)
    with pytest.raises(SearchLimitExceeded):
        list(generate_plays(counts(3, 3), budget=budget))
    assert budget.nodes == 2
    assert budget.reason == "node_limit"
    clock_budget = SearchBudget(1000, 0)
    with pytest.raises(SearchLimitExceeded):
        list(generate_plays(counts(1), budget=clock_budget))
    assert clock_budget.reason == "time_limit"


def assert_partition(plan):
    flattened = [card for group in plan["groups"] for card in group["cards"]]
    assert Counter(flattened) == Counter(plan["remaining_cards"])
    for group in plan["groups"]:
        cards = [Card.from_string(card) for card in group["cards"]]
        assert Counter(recognize(cards).all_cards) == Counter(cards)
    assert plan["lower_bound"] <= plan["upper_bound"] == len(plan["groups"])


def test_compares_legal_actions_and_accounts_for_all_remaining_cards():
    ctx = context(counts(3, 3, 1, 1, 1))
    single = [str(ctx.hand_cards[0])]
    airplane = [str(card) for card in ctx.hand_cards if card.rank.value <= 4]
    result = compare_hand_plans(ctx, {"actions": [{"cards": single}, {"cards": airplane}]})
    assert len(result["plans"]) == 2
    for plan in result["plans"]:
        assert plan["valid"]
        assert_partition(plan)
        assert plan["exact"]
        assert plan["minimum_groups"] == plan["lower_bound"] == plan["upper_bound"]
    assert result["plans"][0]["broken_groups"] == [
        {"rank": "3", "before": 3, "played": 1, "remaining": 2},
    ]


def test_suit_equivalent_candidates_reuse_search_but_keep_their_own_cards():
    ctx = context(counts(3, 3, 1, 1))
    result = compare_hand_plans(ctx, {"actions": [{"cards": ["♠3"]},
                                                  {"cards": ["♥3"]}]})
    first, second = result["plans"]
    assert first["minimum_groups"] == second["minimum_groups"]
    assert "♠3" not in first["remaining_cards"]
    assert "♥3" not in second["remaining_cards"]
    assert_partition(first)
    assert_partition(second)


def test_partition_matches_independent_small_hand_bruteforce():
    rng = random.Random(417)
    pools = [counts(3, 3, 1, 1), counts(4, 2, 2), counts(1, 1, 1, 1, 1, 1)]
    pools.extend(counts(*(rng.randrange(3) for _ in range(6))) for _ in range(15))
    target = recognize([Card.from_string("♠2")])
    for pool in pools:
        patterns = brute_patterns(pool)

        @lru_cache(None)
        def minimum(state):
            if not any(state):
                return 0
            return 1 + min(minimum(tuple(n - p for n, p in zip(state, play)))
                           for play in patterns
                           if all(p <= n for p, n in zip(play, state)))

        result = compare_hand_plans(context(pool, target=target), {"actions": [{"cards": []}]})
        plan = result["plans"][0]
        assert plan["exact"]
        assert plan["minimum_groups"] == minimum(pool)
        assert_partition(plan)


def test_preserve_bombs_requires_standalone_groups():
    ctx = context(counts(4, 1, 1))
    single = [str(card) for card in ctx.hand_cards if card.rank.value == 4]
    unconstrained = compare_hand_plans(ctx, {"actions": [{"cards": single}]})["plans"][0]
    constrained = compare_hand_plans(ctx, {"actions": [{"cards": single}],
                                         "constraints": {"preserve_bombs": True}})["plans"][0]
    assert unconstrained["minimum_groups"] == 2
    assert constrained["minimum_groups"] == 2
    assert "炸弹" in [group["pattern"] for group in constrained["groups"]]
    bomb = [str(card) for card in ctx.hand_cards if card.rank.value == 3]
    result = compare_hand_plans(ctx, {"actions": [{"cards": bomb}, {"cards": bomb[:1]},
                                                  {"cards": [str(c) for c in ctx.hand_cards]}],
                                     "constraints": {"preserve_bombs": True}})
    assert [plan["valid"] for plan in result["plans"]] == [True, False, False]


def test_preserve_rocket_and_constraint_changes_optimum():
    ctx = context(counts(4, 1, 1), target=recognize([Card.from_string("♠2")]))
    arguments = {"actions": [{"cards": []}]}
    assert compare_hand_plans(ctx, arguments)["plans"][0]["minimum_groups"] == 1
    arguments["constraints"] = {"preserve_bombs": True}
    assert compare_hand_plans(ctx, arguments)["plans"][0]["minimum_groups"] == 3
    joker_ctx = context((1,) + (0,) * 12 + (1, 1))
    result = compare_hand_plans(joker_ctx, {"actions": [{"cards": ["小王"]},
                                                        {"cards": ["小王", "大王"]}],
                                          "constraints": {"preserve_bombs": True}})
    assert [plan["valid"] for plan in result["plans"]] == [False, True]


def test_incomplete_search_returns_witness_and_honest_bounds(monkeypatch):
    monkeypatch.setattr(plans, "DEFAULT_MAX_NODES", 0)
    ctx = context(counts(3, 3, 1, 1), target=recognize([Card.from_string("♠2")]))
    result = compare_hand_plans(ctx, {"actions": [{"cards": []}]})
    plan = result["plans"][0]
    assert plan["valid"] and plan["truncated"] and not plan["exact"]
    assert plan["minimum_groups"] is None
    assert plan["lower_bound"] == 1
    assert result["stats"]["stop_reason"] == "node_limit"
    assert_partition(plan)


def test_invalid_actions_are_isolated_and_actual_turn_legality_checked():
    ctx = context(counts(2, 1), target=recognize([Card.from_string("♠A")]))
    result = compare_hand_plans(ctx, {"actions": [
        {"cards": ["♠3", "♠3"]}, {"cards": ["♠3"]}, {"cards": []},
    ]})
    assert [plan["valid"] for plan in result["plans"]] == [False, False, True]
    ctx.current_trick = None
    assert not compare_hand_plans(ctx, {"actions": [{"cards": []}]})["plans"][0]["valid"]
    assert not compare_hand_plans(ctx, {"actions": [{"cards": ["3"]}]})["plans"][0]["valid"]


@pytest.mark.parametrize("arguments", [
    {}, {"actions": []}, {"actions": [{"cards": []}] * 4},
    {"actions": [{"cards": []}], "constraints": {"preserve_bombs": "true"}},
    {"actions": [{"cards": []}], "constraints": {"anything": True}},
])
def test_defensive_argument_validation(arguments):
    with pytest.raises(ValueError):
        compare_hand_plans(context(counts(1)), arguments)
