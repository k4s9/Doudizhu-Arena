"""Agent abstract interface — the contract between Tournament Manager and agent implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..engine.card import Card
from ..engine.rules import InvalidPlayError
from ..engine.trick import Trick
from .memory import MemoryManager


@dataclass
class AgentContext:
    """Information passed to an agent for a bidding or play decision.

    Contains only what the agent is permitted to see per information isolation rules.
    """

    # ── seat info ──
    seat: str                          # this agent's seat
    role: str                          # 'landlord' | 'farmer' | 'idle'

    # ── hand ──
    hand_cards: list[Card]             # full suit+rank, sorted display order
    hand_size: int

    # ── public info ──
    dizhu_cards: tuple[Card, ...] | None = None    # revealed after bidding
    bidding_history: list[dict[str, Any]] = field(default_factory=list)
    play_history: list[dict[str, Any]] = field(default_factory=list)

    # ── game state ──
    current_trick: Trick | None = None              # the pattern to beat (None = new round)
    trick_leader: str = ""
    landlord: str = ""
    active_players: list[str] = field(default_factory=list)
    player_hand_sizes: dict[str, int] = field(default_factory=dict)

    # ── bidding context ──
    current_high_bid: int = 0
    current_high_bidder: str = ""

    # ── hand result (post-hand, for reflection) ──
    hand_score: int | None = None
    winner_team: str = ""
    winner_role: str = ""

    # ── reflection / summary (post-hand / post-match) ──
    hand_num: int = 0
    match_id: str = ""
    initial_hand: tuple[Card, ...] | None = None    # original dealt hand
    remaining_hands: dict[str, tuple[Card, ...]] | None = None  # all players' remaining cards
    agent_role: str = ""              # actual role played (may differ from `role` for idle)
    is_idle_observer: bool = False    # True if agent was idle and only observing
    match_summary: dict[str, Any] | None = None     # per-hand role summary for match summary


@runtime_checkable
class Agent(Protocol):
    """Protocol that all agent implementations must satisfy.

    The Tournament Manager calls these methods at decision points.
    Each method receives an AgentContext and returns the agent's decision.
    """

    @property
    def agent_id(self) -> str: ...

    @property
    def seat(self) -> str: ...

    @seat.setter
    def seat(self, value: str) -> None: ...

    async def decide_bid(self, ctx: AgentContext) -> int:
        """Decide a bid: 0=pass, 1/2/3=bid score.

        Must return a valid bid (honor the 'must be > current_high_bid' rule).
        """
        ...

    async def decide_play(self, ctx: AgentContext) -> list[Card]:
        """Decide which cards to play. Return empty list for pass.

        The returned cards must exist in ctx.hand_cards and form a valid Trick.
        If ctx.current_trick is not None, the play must beat it.
        Raises InvalidPlayError if decision is illegal (tournament handles retry/fallback).
        """
        ...

    async def reflect(self, ctx: AgentContext) -> dict[str, str]:
        """Post-hand reflection. Returns {'reflection': ..., 'short_term_memory': ...}.

        Called after each hand where the agent participated (including idle observers).
        """
        ...

    async def summarize(self, ctx: AgentContext) -> dict[str, str]:
        """Post-match summary. Returns {'summary': ..., 'long_term_memory': ...}.

        Called once after the match ends for all participating agents.
        """
        ...

    @property
    def memory(self) -> MemoryManager: ...


class AgentError(Exception):
    """Raised when an agent fails to produce a valid decision."""
    pass
