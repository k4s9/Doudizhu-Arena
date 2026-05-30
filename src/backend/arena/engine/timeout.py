"""Three-tier timeout manager.

Tier 0: Bidding timeout (default 60s per individual bid).
Tier 1: Individual play timeout (default 360s per play).
Tier 2: Team accumulated timeout (default 60min per team).

After team pool exhausted → individual timeout reduced to 60s.
Consecutive 3 LLM failures → auto-play for the rest of the match.
Auto-play: pass if must follow pattern, else play rightmost card (min rank, worst suit).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum

from .card import Card
from .hand import Hand

# ── configuration ────────────────────────────────────────────────────────────


@dataclass
class TimeoutConfig:
    bidding_seconds: float = 60.0
    individual_play_seconds: float = 360.0
    team_pool_seconds: float = 3600.0  # 60 min
    exhausted_individual_seconds: float = 60.0
    max_consecutive_llm_failures: int = 3


# ── timeout manager ──────────────────────────────────────────────────────────


class TimeoutStatus(StrEnum):
    NORMAL = "normal"
    WARNING = "warning"
    EXHAUSTED = "exhausted"


@dataclass
class TeamTimePool:
    remaining_ms: float  # remaining time in milliseconds
    status: TimeoutStatus = TimeoutStatus.NORMAL

    def consume(self, ms: float) -> None:
        self.remaining_ms -= ms
        if self.remaining_ms <= 0:
            self.remaining_ms = 0
            self.status = TimeoutStatus.EXHAUSTED


@dataclass
class PlayerTimeoutState:
    consecutive_failures: int = 0
    auto_play_enabled: bool = False  # True → all future plays auto-resolved (agent timed out)


class TimeoutManager:
    """Manages timeouts for a single table."""

    def __init__(self, config: TimeoutConfig | None = None) -> None:
        self.config = config or TimeoutConfig()
        self._team_pools: dict[str, TeamTimePool] = {}  # team → pool
        self._player_states: dict[str, PlayerTimeoutState] = {}  # seat → state
        self._play_start_time: dict[str, float] = {}  # seat → monotonic start

    def init_teams(self, seat_teams: dict[str, str]) -> None:
        """Initialize team pools and player states for all seats."""
        teams = set(seat_teams.values())
        team_ms = self.config.team_pool_seconds * 1000
        for team in teams:
            self._team_pools[team] = TeamTimePool(remaining_ms=team_ms)
        for seat in seat_teams:
            self._player_states[seat] = PlayerTimeoutState()

    # ── individual timeout ──────────────────────────────────────────────

    def individual_limit(self, seat: str) -> float:
        """Seconds allowed for the current individual play."""
        ps = self._player_states.get(seat)
        if ps and ps.auto_play_enabled:
            return 0  # immediate auto-play
        return self.config.individual_play_seconds

    def bidding_limit(self, seat: str) -> float:
        """Seconds allowed for a bidding decision."""
        ps = self._player_states.get(seat)
        if ps and ps.auto_play_enabled:
            return 0
        return self.config.bidding_seconds

    def start_turn(self, seat: str) -> None:
        self._play_start_time[seat] = time.monotonic()

    def end_turn(self, seat: str, team: str, success: bool) -> None:
        """Record turn completion. Updates team pool and failure tracking."""
        start = self._play_start_time.pop(seat, None)
        elapsed_ms = 0.0
        if start is not None:
            elapsed_ms = (time.monotonic() - start) * 1000

        # Consume team time
        pool = self._team_pools.get(team)
        if pool and pool.status != TimeoutStatus.EXHAUSTED:
            pool.consume(elapsed_ms)

        # Track LLM failures
        ps = self._player_states.get(seat)
        if ps and not ps.auto_play_enabled:
            if success:
                ps.consecutive_failures = 0
            else:
                ps.consecutive_failures += 1
                if ps.consecutive_failures >= self.config.max_consecutive_llm_failures:
                    ps.auto_play_enabled = True

    # ── team pool queries ───────────────────────────────────────────────

    def team_remaining_ms(self, team: str) -> float:
        pool = self._team_pools.get(team)
        return pool.remaining_ms if pool else 0

    def team_status(self, team: str) -> TimeoutStatus:
        pool = self._team_pools.get(team)
        return pool.status if pool else TimeoutStatus.NORMAL

    def is_exhausted(self, team: str) -> bool:
        return self.team_status(team) == TimeoutStatus.EXHAUSTED

    def effective_individual_limit(self, seat: str, team: str) -> float:
        """Get the effective individual time limit considering team pool status."""
        ps = self._player_states.get(seat)
        if ps and ps.auto_play_enabled:
            return 0
        if self.is_exhausted(team):
            return self.config.exhausted_individual_seconds
        return self.config.individual_play_seconds

    def is_auto_play_enabled(self, seat: str) -> bool:
        ps = self._player_states.get(seat)
        return ps.auto_play_enabled if ps else False

    # ── auto-play ───────────────────────────────────────────────────────

    @staticmethod
    def auto_play(hand: Hand, is_leader: bool) -> list[Card]:
        """Generate an auto-play decision.

        If not the leader (must follow): always pass.
        If leader (free play): play the rightmost card (min rank, worst suit).
        """
        if not is_leader:
            return []  # pass

        if len(hand) == 0:
            return []

        # Rightmost card = smallest rank, worst suit (♦ < ♣ < ♥ < ♠)
        # Hand is sorted: rank desc, suit ♠→♥→♣→♦
        # So rightmost = last element
        return [hand.cards[-1]]
