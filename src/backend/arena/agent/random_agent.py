"""Random agent — makes random (but legal) decisions for baseline testing."""

from __future__ import annotations

import random

from ..engine.card import Card
from ..engine.rules import can_beat, recognize
from ..engine.trick import Trick
from .base import Agent, AgentContext
from .memory import MemoryManager


class RandomAgent:
    """An agent that makes random but rule-compliant decisions.

    Used as a baseline opponent and for tournament engine testing.
    """

    def __init__(self, agent_id: str, seat: str = "", seed: int | None = None) -> None:
        self._agent_id = agent_id
        self._seat = seat
        self._rng = random.Random(seed)
        self._memory = MemoryManager(agent_id=agent_id)

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def seat(self) -> str:
        return self._seat

    @seat.setter
    def seat(self, value: str) -> None:
        self._seat = value

    @property
    def memory(self) -> MemoryManager:
        return self._memory

    async def decide_bid(self, ctx: AgentContext) -> int:
        """Random bid: 50% pass, otherwise random from valid options."""
        options = [b for b in (0, 1, 2, 3) if b == 0 or b > ctx.current_high_bid]
        return self._rng.choice(options)

    async def decide_play(self, ctx: AgentContext) -> list[Card]:
        """Pick a random legal play.

        If must follow a pattern: find all legal plays that beat current_trick,
        pick one randomly. If none exist, pass.
        If leader (no current_trick): pick a random play from all possible plays.
        """
        if ctx.current_trick is None:
            return self._random_leader_play(ctx)
        else:
            return self._random_follow_play(ctx)

    def _random_leader_play(self, ctx: AgentContext) -> list[Card]:
        """As round leader, pick any valid play from hand."""
        hand = list(ctx.hand_cards)
        if not hand:
            return []
        # Try various play sizes — prefer simpler plays
        # Build a list of all possible single plays, pairs, trios, etc.
        candidates: list[list[Card]] = []

        # Single cards
        for c in hand:
            candidates.append([c])

        # Pairs and trios
        rank_counts: dict[int, list[Card]] = {}
        for c in hand:
            rank_counts.setdefault(c.rank.value, []).append(c)
        for cards in rank_counts.values():
            if len(cards) >= 2:
                candidates.append(cards[:2])
            if len(cards) >= 3:
                candidates.append(cards[:3])
            if len(cards) >= 4:
                candidates.append(cards[:4])

        if not candidates:
            return [hand[-1]]  # fallback

        choice = self._rng.choice(candidates)
        # Verify
        try:
            recognize(choice)
            return choice
        except Exception:
            return [hand[-1]]

    def _random_follow_play(self, ctx: AgentContext) -> list[Card]:
        """Must follow current pattern. Find legal plays or pass."""
        hand = list(ctx.hand_cards)
        target = ctx.current_trick
        if target is None:
            return []

        # Generate candidate plays from hand that could beat target
        candidates = self._find_beating_plays(hand, target)

        if not candidates:
            return []  # pass

        # Prefer passing sometimes (~30%)
        if self._rng.random() < 0.3:
            return []

        choice = self._rng.choice(candidates)
        try:
            recognize(choice)
            return choice
        except Exception:
            return []

    def _find_beating_plays(self, hand: list[Card], target: Trick) -> list[list[Card]]:
        """Find all legal plays from hand that beat the target trick."""
        candidates: list[list[Card]] = []

        # Group by rank
        rank_groups: dict[int, list[Card]] = {}
        for c in hand:
            rank_groups.setdefault(c.rank.value, []).append(c)

        # Only generates singles for SINGLE target (straight/airplane/other complex
        # pattern candidates are not generated). When the target is a complex pattern,
        # the agent can only respond with bombs, rockets, or pass. Acceptable for baseline.
        if target.pattern.value in ("单张",):
            for c in hand:
                trial = [c]
                try:
                    trick = recognize(trial)
                    if can_beat(trick, target):
                        candidates.append(trial)
                except Exception:
                    pass

        # Try pairs that beat target
        for cards in rank_groups.values():
            if len(cards) >= 2:
                trial = cards[:2]
                try:
                    trick = recognize(trial)
                    if can_beat(trick, target):
                        candidates.append(trial)
                except Exception:
                    pass

        # Try trios
        for cards in rank_groups.values():
            if len(cards) >= 3:
                trial = cards[:3]
                try:
                    trick = recognize(trial)
                    if can_beat(trick, target):
                        candidates.append(trial)
                except Exception:
                    pass

        # Try bombs
        for cards in rank_groups.values():
            if len(cards) >= 4:
                trial = cards[:4]
                try:
                    trick = recognize(trial)
                    if can_beat(trick, target):
                        candidates.append(trial)
                except Exception:
                    pass

        # Try rocket
        has_big = any(c.rank.display == "大王" for c in hand)
        has_small = any(c.rank.display == "小王" for c in hand)
        if has_big and has_small:
            from ..engine.card import Card as C, Rank, Suit
            rocket = [c for c in hand if c.rank.display in ("大王", "小王")]
            if len(rocket) >= 2:
                rocket = rocket[:2]
                try:
                    trick = recognize(rocket)
                    if can_beat(trick, target):
                        candidates.append(rocket)
                except Exception:
                    pass

        return candidates

    async def reflect(self, ctx: AgentContext) -> dict[str, str]:
        """No-op reflection for random agent."""
        return {"reflection": "", "short_term_memory": ""}

    async def summarize(self, ctx: AgentContext) -> dict[str, str]:
        """No-op summary for random agent."""
        return {"summary": "", "long_term_memory": ""}
