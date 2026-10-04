"""Feasibility, privacy and bounded-work tests for the public threat tool."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import replace
from itertools import combinations

import pytest

from arena.agent.base import AgentContext
from arena.agent.tools import threats
from arena.agent.tools.plays import cards_from_counts, counts_from_cards
from arena.agent.tools.threats import analyze_threat
from arena.engine.card import ALL_CARDS, Card, Rank, Suit
from arena.engine.rules import InvalidPlayError, can_beat, recognize


def _context(
    own: list[str], east: list[str], north: list[str], *, bottom_current: bool = False,
) -> AgentContext:
    """Build a closed 54-card observation; hidden fixtures never enter context."""
    available = list(ALL_CARDS)
    hands = {}
    for seat, ranks in (("S", own), ("E", east), ("N", north)):
        hands[seat] = []
        for rank in ranks:
            card = next(card for card in available if card.rank.display == rank)
            available.remove(card)
            hands[seat].append(card)
    history = []
    played = {}
    for seat in ("S", "E", "N"):
        count = (20 if seat == "E" else 17) - len(hands[seat])
        played[seat] = available[:count]
        available = available[count:]
        history.extend({"seat": seat, "action_type": "play", "cards": [card]}
                       for card in played[seat])
    assert not available
    bottom_pool = (hands["E"] + played["E"]) if bottom_current else (played["E"] + hands["E"])
    return AgentContext(
        seat="S", role="farmer", hand_cards=hands["S"], hand_size=len(own),
        landlord="E", active_players=["S", "E", "N"],
        player_hand_sizes={seat: len(hand) for seat, hand in hands.items()} | {"W": 0},
        dizhu_cards=tuple(bottom_pool[:3]), play_history=history,
    )


def _run(ctx: AgentContext, event="can_finish", target_seat="E", **arguments):
    return analyze_threat(ctx, {"event": event, "target_seat": target_seat, **arguments})


def _without_timing(result):
    result = deepcopy(result)
    result["search"].pop("elapsed_ms")
    return result


def test_finish_produces_a_full_hypothetical_straight_witness():
    ctx = _context(["大王"], ["3", "4", "5", "6", "7"], ["8", "9"])
    result = _run(ctx)
    assert result["status"] == "possible"
    assert result["complete"] is True
    assert result["condition"] == "if_given_free_lead"
    assert result["witness"]["hypothetical"] is True
    assert result["witness"]["pattern"] == "顺子"
    assert len(result["witness"]["assumed_hand_ranks"]) == 5
    assert result["witness"]["play_ranks"] == result["witness"]["assumed_hand_ranks"]
    assert result["public_constraints"]["unseen_card_count"] == 7


def test_finish_ruled_out_only_after_all_legal_plays_are_excluded():
    result = _run(_context(["大王"], ["3", "5"], ["7"]))
    assert result["status"] == "ruled_out"
    assert result["complete"] is True
    assert result["search"]["exhaustive"] is True
    assert "witness" not in result


def test_finish_requires_beating_current_trick_when_following():
    ctx = _context(["2", "2"], ["3", "3"], ["4"])
    assert _run(ctx)["status"] == "possible"
    target = recognize(ctx.hand_cards)
    following = replace(ctx, current_trick=target)
    assert _run(following)["status"] == "ruled_out"
    assert _run(ctx, cards=[str(card) for card in ctx.hand_cards])["status"] == "ruled_out"


def test_can_beat_handles_bombs_and_a_complete_extended_hand():
    ctx = _context(["A", "A"], ["3", "3", "3", "3", "5"], ["7"])
    result = _run(ctx, "can_beat", cards=[str(card) for card in ctx.hand_cards])
    assert result["status"] == "possible"
    assert result["witness"]["pattern"] == "炸弹"
    assert result["witness"]["play_ranks"] == ["3"] * 4
    assert len(result["witness"]["assumed_hand_ranks"]) == 5


def test_rocket_response_excludes_large_unrelated_patterns_before_search():
    ctx = _context(
        ["小王", "大王"],
        ["3"] * 4 + ["4"] * 4 + ["5"] * 4 + ["6"] * 4 + ["7"] * 4,
        ["8"] * 4 + ["9"] * 4 + ["10"] * 4 + ["J"] * 4 + ["Q"],
    )
    result = _run(ctx, "can_beat", cards=[str(card) for card in ctx.hand_cards])
    assert result["status"] == "ruled_out"
    assert result["search"]["exhaustive"] is True
    assert result["search"]["nodes"] < 50


def test_bottom_cards_are_reserved_for_another_landlord_not_double_subtracted():
    ctx = _context(["Q"], ["大王"], ["3"], bottom_current=True)
    result = _run(ctx, "can_beat", "N", cards=[str(ctx.hand_cards[0])])
    assert result["status"] == "ruled_out"
    assert result["public_constraints"]["unseen_card_count"] == 2
    assert "大王" in result["public_constraints"]["landlord_minimum_ranks"]


def test_landlord_must_include_guaranteed_bottom_ranks_to_finish():
    ctx = _context(["Q"], ["大王", "3"], ["3"], bottom_current=True)
    # A pair of 3 exists in the pool, but assigning it all to the landlord
    # would violate the publicly guaranteed big joker in that two-card hand.
    result = _run(ctx)
    assert result["status"] == "ruled_out"
    assert "大王" in result["public_constraints"]["landlord_minimum_ranks"]


def test_pass_never_becomes_negative_ownership_evidence():
    ctx = _context(["3"], ["大王"], ["4"])
    arguments = {"event": "can_beat", "target_seat": "E", "cards": [str(ctx.hand_cards[0])]}
    original = analyze_threat(ctx, arguments)
    passed = replace(ctx, play_history=ctx.play_history + [
        {"seat": "E", "action_type": "pass", "cards": None},
    ])
    assert original["status"] == "possible"
    assert _without_timing(original) == _without_timing(analyze_threat(passed, arguments))


def test_opponent_suits_and_reflection_hidden_hands_cannot_change_the_answer():
    ctx = _context(["Q"], ["大王", "3"], ["3"], bottom_current=True)
    history = deepcopy(ctx.play_history)
    for record in history:
        if record["seat"] != ctx.seat:
            record["cards"] = [
                card if card.is_joker else Card(card.rank, Suit.SPADE)
                for card in record["cards"]
            ]
    changed = replace(
        ctx, play_history=history,
        remaining_hands={"E": tuple(ALL_CARDS), "N": ()},
        initial_hand=tuple(ALL_CARDS), match_id="secret-copy-table", hand_num=99,
    )
    original = _run(ctx)
    assert _without_timing(original) == _without_timing(_run(changed))
    public_result = str(original)
    assert all(suit not in public_result for suit in ("♠", "♥", "♣", "♦"))


def test_rank_only_history_is_equivalent_to_internal_cards():
    ctx = _context(["大王"], ["3", "4", "5", "6", "7"], ["8", "9"])
    history = [{**record, "cards": [card.rank_only for card in record["cards"]]}
               for record in ctx.play_history]
    assert _without_timing(_run(ctx)) == _without_timing(_run(replace(ctx, play_history=history)))


@pytest.mark.parametrize("change", [
    lambda ctx: replace(ctx, play_history=ctx.play_history[1:]),
    lambda ctx: replace(ctx, hand_size=ctx.hand_size + 1),
    lambda ctx: replace(ctx, dizhu_cards=None),
    lambda ctx: replace(ctx, active_players=["S", "E", "N", "W"]),
    lambda ctx: replace(ctx, active_players=["S", "E", "E"]),
    lambda ctx: replace(ctx, player_hand_sizes=ctx.player_hand_sizes | {"N": 0}),
    lambda ctx: replace(ctx, player_hand_sizes=ctx.player_hand_sizes | {"W": 1}),
    lambda ctx: replace(ctx, player_hand_sizes=ctx.player_hand_sizes | {"N": True}),
])
def test_incomplete_or_inconsistent_observation_never_rules_out(change):
    ctx = _context(["大王"], ["3", "5"], ["7"])
    result = _run(change(ctx))
    assert result["status"] == "unknown"
    assert result["complete"] is False
    assert result["reason"] == "inconsistent_public_information"


def test_impossible_rank_counts_are_unknown_not_a_false_safe_conclusion():
    ctx = _context(["大王"], ["3", "5"], ["7"])
    history = deepcopy(ctx.play_history)
    history[0]["cards"] = [Card(Rank.BIG_JOKER)]
    result = _run(replace(ctx, play_history=history))
    assert result["status"] == "unknown"
    assert result["reason"] == "inconsistent_public_information"


@pytest.mark.parametrize("arguments", [
    {}, {"event": "estimate_probability", "target_seat": "E"},
    {"event": "can_finish", "target_seat": "S"},
    {"event": "can_finish", "target_seat": "W"},
    {"event": "can_finish", "target_seat": "E", "seed": "secret"},
    {"event": "can_finish", "target_seat": "E", "cards": []},
    {"event": "can_finish", "target_seat": "E", "cards": ["Q"]},
    {"event": "can_finish", "target_seat": "E", "cards": ["♠Q", "♠Q"]},
    {"event": "can_finish", "target_seat": "E", "cards": ["大王"]},
    {"event": "can_beat", "target_seat": "E"},
])
def test_rejects_invalid_model_arguments(arguments):
    ctx = _context(["Q"], ["3", "5"], ["7"])
    with pytest.raises(ValueError):
        analyze_threat(ctx, arguments)


def test_rejects_proposed_play_that_cannot_beat_the_table():
    ctx = _context(["3"], ["5"], ["7"])
    current = recognize([Card(Rank.TWO, Suit.SPADE)])
    with pytest.raises(ValueError, match="压过当前牌型"):
        _run(replace(ctx, current_trick=current), "can_beat", cards=[str(ctx.hand_cards[0])])


def test_node_budget_exhaustion_is_unknown(monkeypatch):
    ctx = _context(["大王"], ["3", "3", "4", "4", "5"], ["6", "7", "8"])
    monkeypatch.setattr(threats, "MAX_SEARCH_NODES", 0)
    result = _run(ctx, "can_beat", cards=[str(ctx.hand_cards[0])])
    assert result["status"] == "unknown"
    assert result["complete"] is False
    assert result["reason"] == "node_limit"
    assert result["search"]["exhaustive"] is False


def _brute_feasible(ctx, target_seat, event, target):
    """Independent small-pool oracle: enumerate complete hidden hands first."""
    pool = Counter(card.rank for card in ALL_CARDS)
    pool.subtract(card.rank for card in ctx.hand_cards)
    for record in ctx.play_history:
        pool.subtract(card.rank for card in record["cards"])
    landlord_plays = Counter(
        card.rank for record in ctx.play_history if record["seat"] == ctx.landlord
        for card in record["cards"]
    )
    minimum = Counter(card.rank for card in ctx.dizhu_cards) - landlord_plays
    cards = cards_from_counts(tuple(pool[rank] for rank in Rank))
    size = ctx.player_hand_sizes[target_seat]
    for indexes in combinations(range(len(cards)), size):
        chosen = set(indexes)
        target_hand = [card for i, card in enumerate(cards) if i in chosen]
        other_hand = [card for i, card in enumerate(cards) if i not in chosen]
        landlord_hand = target_hand if target_seat == ctx.landlord else other_hand
        if minimum - Counter(card.rank for card in landlord_hand):
            continue
        subsets = [target_hand] if event == "can_finish" else (
            subset for length in range(1, size + 1)
            for subset in combinations(target_hand, length)
        )
        for subset in subsets:
            try:
                trick = recognize(subset)
            except InvalidPlayError:
                continue
            if len(trick.all_cards) == len(subset) and can_beat(trick, target):
                return True
    return False


@pytest.mark.parametrize("east,north,bottom_current", [
    (["3", "3"], ["4", "4"], False),
    (["3", "4", "5", "6", "7"], ["8"], False),
    (["3", "3", "3", "4"], ["4", "4"], False),
    (["大王", "3"], ["3"], True),
    (["小王", "大王"], ["3", "4"], True),
    (["3", "3", "3", "3"], ["4", "5"], False),
])
@pytest.mark.parametrize("target_seat", ["E", "N"])
def test_matches_complete_hidden_hand_enumeration_on_small_pools(east, north, bottom_current, target_seat):
    ctx = _context(["Q"], east, north, bottom_current=bottom_current)
    for event in ("can_finish", "can_beat"):
        target = None if event == "can_finish" else recognize(ctx.hand_cards)
        arguments = {} if target is None else {"cards": [str(ctx.hand_cards[0])]}
        result = _run(ctx, event, target_seat, **arguments)
        expected = _brute_feasible(ctx, target_seat, event, target)
        assert result["status"] == ("possible" if expected else "ruled_out")
