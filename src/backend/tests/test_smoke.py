"""Smoke tests: complete 1-2 hand flow with mock LLM provider.

Verifies that the full pipeline (TableRunner + Mock LLM Agent) works end-to-end.
Includes CI quick check, full pipeline, reflection flow, and memory flow tests.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from arena.agent.base import AgentContext
from arena.agent.llm_agent import LLMAgent
from arena.engine.card import SEATS
from arena.engine.deck import Deck
from arena.tournament.seating import get_dealer_idle
from arena.tournament.table import TableRunner

# Mock provider classes are in mock_utils so both conftest and test modules can import them.
from tests.mock_utils import CapturingMockProvider, SmokeMockProvider


# ── mock provider unit tests ────────────────────────────────────────────────────


class TestSmokeMockProvider:
    """Verify the mock provider itself works correctly."""

    def test_mock_bidding_response(self) -> None:
        mock = SmokeMockProvider()
        result = asyncio.run(mock.generate("", "叫分规则"))
        assert '"bid"' in result

    def test_mock_playing_response(self) -> None:
        mock = SmokeMockProvider()
        result = asyncio.run(mock.generate("", "出牌规则"))
        assert '"action"' in result


# ── single hand smoke tests ─────────────────────────────────────────────────────


class TestSmokeSingleHand:
    """Run a single hand with mock LLM agents."""

    @pytest.mark.smoke
    def test_one_hand_with_llm_agents(self) -> None:
        """Complete one hand with 4 mock LLM agents."""
        sm = SmokeMockProvider()
        agents = {}
        for seat, aid in [("S", "a1"), ("E", "a2"), ("N", "a3"), ("W", "a4")]:
            agent = LLMAgent(aid, sm)
            agent.seat = seat
            agents[seat] = agent

        teams = {"S": "red", "E": "blue", "N": "red", "W": "blue"}

        runner = TableRunner("A", agents, teams)
        deck = Deck("smoke-single-hand")
        deal = deck.deal()
        dealer, idle = get_dealer_idle(1)

        result = asyncio.run(runner.run_hand(1, dealer, idle, deal))

        assert result.table == "A"
        assert result.hand_num == 1
        if result.void:
            assert result.final_bid == 0
        else:
            assert result.landlord in ("S", "E", "N")
            assert result.final_bid in (1, 2, 3)
            assert len(result.play_history) > 0

    @pytest.mark.smoke
    def test_two_hands_with_llm_agents(self) -> None:
        """Complete two hands with mock LLM agents."""
        agents = {}
        for seat, aid in [("S", "a1"), ("E", "a2"), ("N", "a3"), ("W", "a4")]:
            mock = SmokeMockProvider()
            agent = LLMAgent(aid, mock)
            agent.seat = seat
            agents[seat] = agent

        teams = {"S": "red", "E": "blue", "N": "red", "W": "blue"}

        runner = TableRunner("A", agents, teams)

        for h in range(1, 3):
            deck = Deck(f"smoke-two-hands-{h}")
            deal = deck.deal()
            dealer, idle = get_dealer_idle(h)
            result = asyncio.run(runner.run_hand(h, dealer, idle, deal))

            assert result.hand_num == h
            if not result.void:
                assert result.landlord in ("S", "E", "N", "W")
                assert result.landlord != idle
                assert result.final_bid in (1, 2, 3)


# ── match-level smoke tests ─────────────────────────────────────────────────────


class TestSmokeMatchWithLLM:
    """Run a small match with mock LLM agents across both tables."""

    @pytest.mark.slow
    def test_2_hand_match_with_llm_agents(self) -> None:
        """2-hand match with mock LLM agents on both AB tables.

        With identical mock strategies on both tables, diff scores may be zero,
        which triggers tiebreakers. The match completes when the tiebreaker
        safety limit is reached (100 tiebreakers max). This test verifies
        the pipeline runs without crashing, not that scores are non-zero.
        """
        from arena.tournament.match import MatchConfig, MatchRunner
        from arena.tournament.seating import assign_seating

        red_ids = ["r1", "r2", "r3", "r4"]
        blue_ids = ["b1", "b2", "b3", "b4"]

        agents = {}
        for aid in red_ids + blue_ids:
            mock = SmokeMockProvider()
            agent = LLMAgent(aid, mock)
            agents[aid] = agent

        seating = assign_seating(red_ids, blue_ids, seed=42)

        config = MatchConfig(
            total_hands=2,
            ko_enabled=False,
            seed="smoke-match-llm",
        )
        runner = MatchRunner(config, seating, agents)

        result = asyncio.run(runner.run())

        # Match completes (may run tiebreakers if all scores are 0-0)
        assert result.total_hands_played >= 2
        assert result.red_score >= 0
        assert result.blue_score >= 0
        assert result.winner in ("red", "blue", "tie")
        assert len(result.score_history) >= 2


# ── CI quick check ──────────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_smoke_quick() -> None:
    """CI quick check: minimal 1-hand full pipeline in under 5 seconds.

    This is the fast path that CI runs on every commit.  Uses a single
    TableRunner hand with mock LLM agents.
    """
    sm = SmokeMockProvider()
    agents = {}
    for seat, aid in [("S", "q1"), ("E", "q2"), ("N", "q3"), ("W", "q4")]:
        agent = LLMAgent(aid, sm)
        agent.seat = seat
        agents[seat] = agent

    teams = {"S": "red", "E": "blue", "N": "red", "W": "blue"}

    runner = TableRunner("A", agents, teams)
    deck = Deck("smoke-quick")
    deal = deck.deal()
    dealer, idle = get_dealer_idle(1)

    start = time.monotonic()
    result = asyncio.run(runner.run_hand(1, dealer, idle, deal))
    elapsed = time.monotonic() - start

    assert result.table == "A"
    assert result.hand_num == 1
    assert elapsed < 5.0, f"Smoke quick check took {elapsed:.2f}s, expected < 5s"


# ── full pipeline with MatchRunner ──────────────────────────────────────────────


@pytest.mark.smoke
def test_full_pipeline() -> None:
    """MatchRunner with 2 hands and ko_enabled=False.

    Exercises the full duplicate-bridge match pipeline: 8 agents across
    AB tables, with diff scoring.  ko_enabled=False avoids premature
    termination so both scheduled hands are played.
    """
    from arena.tournament.match import MatchConfig, MatchRunner
    from arena.tournament.seating import assign_seating

    red_ids = ["r1", "r2", "r3", "r4"]
    blue_ids = ["b1", "b2", "b3", "b4"]

    agents = {}
    for aid in red_ids + blue_ids:
        mock = SmokeMockProvider()
        agent = LLMAgent(aid, mock)
        agents[aid] = agent

    seating = assign_seating(red_ids, blue_ids, seed=77)

    config = MatchConfig(
        total_hands=2,
        ko_enabled=False,
        seed="test-full-pipeline",
    )
    runner = MatchRunner(config, seating, agents)

    result = asyncio.run(runner.run())

    # Both hands should complete
    assert result.total_hands_played >= 2
    assert result.red_score >= 0
    assert result.blue_score >= 0
    assert result.winner in ("red", "blue", "tie")
    # MatchHandRecord should be available for each pair
    assert len(result.hand_results) >= 2
    assert len(result.score_history) >= 2


# ── reflection flow ─────────────────────────────────────────────────────────────


def _bidding_records_to_dicts(bidding_history: list) -> list[dict]:
    """Convert BiddingRecord objects to dicts for AgentContext."""
    result = []
    for r in bidding_history:
        result.append({"seat": r.seat, "bid": r.bid})
    return result


def _play_records_to_dicts(play_history: list) -> list[dict]:
    """Convert PlayRecord objects to dicts for AgentContext."""
    result = []
    for r in play_history:
        result.append({
            "round": r.round_num,
            "sub_round": r.sub_round,
            "seq": r.seq,
            "seat": r.seat,
            "action_type": r.action_type,
            "cards": list(r.cards) if r.cards else None,
            "trick_display": r.trick.display() if r.trick else None,
        })
    return result


@pytest.mark.smoke
def test_reflection_flow() -> None:
    """Verify all 4 agents (including idle) get reflect() called after a hand.

    Creates separate mock providers per agent so we can independently
    verify each was called for reflection.  Builds proper AgentContext
    from the completed HandResult, then invokes reflect() manually.
    """
    # Each agent gets its own provider for independent tracking
    providers: dict[str, SmokeMockProvider] = {}
    agents: dict[str, LLMAgent] = {}

    for seat, aid in [("S", "rfl-s"), ("E", "rfl-e"), ("N", "rfl-n"), ("W", "rfl-w")]:
        prov = SmokeMockProvider()
        providers[aid] = prov
        agent = LLMAgent(aid, prov)
        agent.seat = seat
        agents[seat] = agent

    teams = {"S": "red", "E": "blue", "N": "red", "W": "blue"}

    runner = TableRunner("A", agents, teams)
    deck = Deck("reflection-flow")
    deal = deck.deal()
    dealer, idle = get_dealer_idle(1)

    result = asyncio.run(runner.run_hand(1, dealer, idle, deal))

    # Void hands have no reflection — skip assertion in that case
    if result.void:
        pytest.skip("Hand was void; reflection flow not exercised")

    # Build reflection contexts for each seat and call reflect()
    for seat, agent in agents.items():
        actual_role = _compute_actual_role(seat, result)
        is_idle = seat == result.effective_idle and actual_role == "idle"
        remaining = result.remaining_hands

        ctx = AgentContext(
            seat=seat,
            role=actual_role,
            agent_role=actual_role,
            is_idle_observer=is_idle,
            hand_cards=list(remaining.get(seat, ())),
            hand_size=len(remaining.get(seat, ())),
            bidding_history=_bidding_records_to_dicts(result.bidding_history),
            play_history=_play_records_to_dicts(result.play_history),
            initial_hand=tuple(result.initial_hands.get(seat, ())),
            remaining_hands=remaining,
            hand_score=result.final_bid,
            winner_team=result.score.winner_team,
            winner_role=result.score.winner_role,
            hand_num=result.hand_num,
            landlord=result.landlord,
            active_players=[s for s in SEATS if s != result.effective_idle],
            player_hand_sizes={s: len(remaining.get(s, ())) for s in SEATS},
        )
        asyncio.run(agent.reflect(ctx))

    # Every agent's provider should have been called for reflection at least once
    for aid, prov in providers.items():
        assert prov._reflection_count >= 1, (
            f"Agent {aid} was not reflected (count={prov._reflection_count})"
        )

    total = sum(p._reflection_count for p in providers.values())
    assert total >= 4, f"Expected >= 4 reflections across all agents, got {total}"


def _compute_actual_role(seat: str, result) -> str:
    """Determine the *actual* role of a seat from the hand result."""
    if seat == result.landlord:
        return "landlord"
    if seat == result.effective_idle:
        return "idle"
    return "farmer"


# ── memory flow ─────────────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_memory_flow() -> None:
    """Verify short-term memory is properly passed between hands.

    After a hand's reflection updates short-term memory, the next hand's
    prompts (bidding/playing) should include that short-term memory text.
    """
    # Use a capturing provider so we can inspect the prompts
    prov = CapturingMockProvider()
    agent = LLMAgent("mem-test", prov)
    agent.seat = "S"

    # Initialise match-scoped memory
    agent.memory.start_match("memory-flow-match")

    # -- Step 1: simulate a post-hand reflection that sets short-term memory --
    ctx = AgentContext(
        seat="S",
        role="landlord",
        agent_role="landlord",
        is_idle_observer=False,
        hand_cards=[],
        hand_size=0,
        hand_num=1,
        landlord="S",
        winner_team="red",
        winner_role="landlord",
        hand_score=3,
        remaining_hands={"S": (), "E": (), "N": (), "W": ()},
        initial_hand=(),
    )
    reflection_result = asyncio.run(agent.reflect(ctx))
    assert "short_term_memory" in reflection_result
    assert len(reflection_result["short_term_memory"]) > 0

    # TableRunner._run_reflections() is responsible for this store step
    agent.memory.update_short_term(reflection_result["short_term_memory"])

    stored = agent.memory.get_short_term()
    assert len(stored) > 0, "Short-term memory should be stored after reflection"

    # -- Step 2: simulate the next hand's bidding --
    # Reset prompt capture for the new hand
    prov.prompts.clear()

    ctx2 = AgentContext(
        seat="S",
        role="farmer",
        hand_cards=[],
        hand_size=17,
        hand_num=2,
        landlord="N",
        current_high_bid=2,
        current_high_bidder="N",
        active_players=["S", "E", "N"],
        player_hand_sizes={"S": 17, "E": 17, "N": 20, "W": 0},
    )
    asyncio.run(agent.decide_bid(ctx2))

    # The short-term memory from step 1 should appear in the bidding prompt
    assert len(prov.prompts) > 0, "Expected at least one prompt to be captured"
    combined_prompt = prov.prompts[0]["system"] + prov.prompts[0]["user"]
    assert stored[:30] in combined_prompt, (
        "Short-term memory text should appear in the next hand's prompt"
    )

    # -- Step 3: also verify it appears in a playing prompt --
    prov.prompts.clear()

    ctx3 = AgentContext(
        seat="S",
        role="farmer",
        hand_cards=[],
        hand_size=17,
        hand_num=2,
        landlord="N",
        active_players=["S", "E", "N"],
        player_hand_sizes={"S": 17, "E": 17, "N": 20, "W": 0},
    )
    asyncio.run(agent.decide_play(ctx3))

    assert len(prov.prompts) > 0
    combined = prov.prompts[0]["system"] + prov.prompts[0]["user"]
    assert stored[:30] in combined, (
        "Short-term memory should also appear in playing prompts"
    )
