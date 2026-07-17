"""MatchRunner — orchestrates a full Duplicate Bridge match.

Runs 20+ hands across AB tables in parallel, with diff scoring, KO, and tiebreaker logic.
Each table runs independently via asyncio.gather.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..agent.base import Agent
from ..engine.deck import Deck, DealResult
from ..engine.pause import PauseManager
from ..engine.timeout import TimeoutConfig, TimeoutManager
from .diff_scoring import KOStatus, MatchScoreboard
from .seating import MatchSeating, assign_seating, get_dealer_idle
from .table import HandResult, TableRunner

if TYPE_CHECKING:
    from ..db.repository import DatabaseRepository
    from ..api.event_bus import MatchEventBus


@dataclass
class MatchConfig:
    """Configuration for a match."""
    total_hands: int = 20
    ko_enabled: bool = True
    seed: str = ""  # match-level seed; hand seeds derived from this
    timeout_config: TimeoutConfig | None = None


@dataclass
class MatchResult:
    """Complete result of a full match."""
    match_name: str
    config: MatchConfig
    red_score: int
    blue_score: int
    winner: str  # 'red' | 'blue' | 'tie'
    hand_results: list[MatchHandRecord] = field(default_factory=list)
    score_history: list[dict[str, Any]] = field(default_factory=list)
    ko_result: KOStatus | None = None
    tiebreaker_hands: int = 0
    started_at: str = ""
    finished_at: str = ""
    total_hands_played: int = 0


@dataclass
class MatchHandRecord:
    """Record of one hand across both tables."""
    hand_num: int
    dealer: str
    idle_seat: str
    seed: str
    table_a: HandResult
    table_b: HandResult
    red_diff: int
    blue_diff: int
    running_total: dict[str, int]
    is_tiebreaker: bool = False


class MatchRunner:
    """Orchestrates a full Duplicate Bridge match.

    Usage:
        seating = assign_seating(red_ids, blue_ids)
        match = MatchRunner(config, seating, agents, db_repo=repo)
        result = await match.run()
    """

    def __init__(
        self,
        config: MatchConfig,
        seating: MatchSeating,
        agents: dict[str, Agent],
        pause_manager: PauseManager | None = None,
        db_repo: DatabaseRepository | None = None,
        match_name: str = "",
    ) -> None:
        self.config = config
        self.seating = seating
        self.agents = agents
        self.db_repo = db_repo
        self.match_name = match_name
        self.match_id = ""  # Set when match is created in DB
        self.pause = pause_manager or PauseManager("match")

        # Event bus for WebSocket broadcasting (lazy init — needs match_id)
        self.event_bus = None  # type: MatchEventBus | None

        # Build per-table agents dict
        table_a_agents = {
            seat: agents[aid] for seat, aid in seating.table_a.items()
        }
        table_b_agents = {
            seat: agents[aid] for seat, aid in seating.table_b.items()
        }

        # Set agent seats
        for seat, agent in table_a_agents.items():
            agent.seat = seat
        for seat, agent in table_b_agents.items():
            agent.seat = seat

        # Create table runners
        timeout_a = TimeoutManager(config.timeout_config)
        timeout_b = TimeoutManager(config.timeout_config)
        pause_a = PauseManager("A")
        pause_b = PauseManager("B")

        self.table_a = TableRunner(
            "A", table_a_agents, seating.table_a_teams,
            pause_a, timeout_a, db_repo=db_repo, match_id=self.match_id,
        )
        self.table_b = TableRunner(
            "B", table_b_agents, seating.table_b_teams,
            pause_b, timeout_b, db_repo=db_repo, match_id=self.match_id,
        )

        self.scoreboard = MatchScoreboard()
        self._hand_records: list[MatchHandRecord] = []

    async def run(self) -> MatchResult:
        """Run the full match: 20 hands (plus tiebreakers) across AB tables."""
        started_at = self._now_iso()

        # If no match seed, generate one
        match_seed = self.config.seed or f"match-{int(time.time())}"

        # Create match in DB (if not already created via API)
        if self.db_repo and not self.match_id:
            self.match_id = self.db_repo.create_match(
                name=self.match_name or f"Match-{match_seed[:8]}",
                config={
                    "total_hands": self.config.total_hands,
                    "ko_enabled": self.config.ko_enabled,
                    "seed": match_seed,
                },
                seed=match_seed,
            )
            self.table_a.match_id = self.match_id
            self.table_b.match_id = self.match_id

        # Initialize event bus for WebSocket broadcasting
        from ..api.event_bus import MatchEventBus
        self.event_bus = MatchEventBus(self.match_id)
        self.table_a.event_bus = self.event_bus
        self.table_b.event_bus = self.event_bus

        if self.db_repo and self.match_id:
            # Update status to running (covers both newly created and pre-existing matches)
            self.db_repo.update_match_status(
                self.match_id, "running", started_at=started_at,
            )
            # If newly created (not via API), skip participant persistence
            # since we don't have corresponding player rows in the DB.
            # (API-created matches set _db_initialized=True and already have participants.)
            # Mark as initialized so summary still runs.
            if not getattr(self, '_db_initialized', False):
                self._db_initialized = True

        # Initialize per-match short-term memory
        if self.match_id:
            for agent in self.agents.values():
                agent.memory.start_match(self.match_id)

        for hand_num in range(1, self.config.total_hands + 1):
            # Check pause before each hand
            await self._check_pause()

            record = await self._run_hand_pair(hand_num, match_seed, is_tiebreaker=False)
            self._hand_records.append(record)

            # Update scoreboard
            self.scoreboard.record_hand(
                hand_num,
                record.table_a.score,
                record.table_b.score,
            )

            # Persist hand result in DB
            if self.db_repo and self.match_id:
                self.db_repo.update_match_status(
                    self.match_id, "running",
                    current_hand=hand_num,
                    score_red=self.scoreboard.red_total,
                    score_blue=self.scoreboard.blue_total,
                )

            # Check KO
            remaining = self.config.total_hands - hand_num
            ko = self.scoreboard.check_ko(remaining, self.config.ko_enabled)
            if ko and ko.triggered:
                break

        # Tiebreaker loop
        tiebreaker_num = 0
        while self.scoreboard.is_tie:
            # Need at least one tiebreaker
            tiebreaker_num += 1
            hand_num = self.config.total_hands + tiebreaker_num

            # Wait for both tables to be ready (quick pause check)
            await self._check_pause()

            record = await self._run_hand_pair(hand_num, match_seed, is_tiebreaker=True)
            self._hand_records.append(record)

            # Update scoreboard
            self.scoreboard.record_hand(
                hand_num,
                record.table_a.score,
                record.table_b.score,
            )
            self.scoreboard.tiebreaker_count += 1

            # Safety limit
            if tiebreaker_num > 100:
                break

        # Determine winner for the match result
        if self.scoreboard.ko_status and self.scoreboard.ko_status.triggered:
            winner_team = self.scoreboard.ko_status.winner_team
        elif self.scoreboard.red_total > self.scoreboard.blue_total:
            winner_team = "red"
        elif self.scoreboard.blue_total > self.scoreboard.red_total:
            winner_team = "blue"
        else:
            winner_team = "tie"

        finished_at = self._now_iso()

        # Emit match_ended FIRST (before potentially slow summary/DB operations)
        ko_triggered = self.scoreboard.ko_status is not None and self.scoreboard.ko_status.triggered
        if self.event_bus:
            await self.event_bus.emit_match_ended(
                winner_team=winner_team,
                final_score={"red": self.scoreboard.red_total, "blue": self.scoreboard.blue_total},
                total_hands_played=self.scoreboard.total_hands_played,
                ko_triggered=ko_triggered,
                tiebreaker_hands=tiebreaker_num,
                finished_at=finished_at,
            )

        # Finalize match in DB
        if self.db_repo and self.match_id:
            ko_json = None
            if self.scoreboard.ko_status and self.scoreboard.ko_status.triggered:
                import json
                ko_json = json.dumps({
                    "winner_team": self.scoreboard.ko_status.winner_team,
                    "lead": self.scoreboard.ko_status.lead,
                    "threshold": self.scoreboard.ko_status.threshold,
                })
            self.db_repo.update_match_status(
                self.match_id, "finished",
                finished_at=finished_at,
                current_hand=hand_num,
                score_red=self.scoreboard.red_total,
                score_blue=self.scoreboard.blue_total,
                ko_result=ko_json,
            )

        # Run match summary for all agents (updates long-term memory)
        # This can be slow (LLM calls) — does NOT block match_ended emission above
        try:
            await self._run_match_summary(winner_team, finished_at)
        except Exception:
            pass  # Summary failure should not affect match completion

        # Close event bus after everything is done
        if self.event_bus:
            self.event_bus.close()

        return MatchResult(
            match_name=self.match_name,
            config=self.config,
            red_score=self.scoreboard.red_total,
            blue_score=self.scoreboard.blue_total,
            winner=winner_team,
            hand_results=list(self._hand_records),
            score_history=[
                {
                    "hand": r.hand_num,
                    "red_diff": r.red_diff,
                    "blue_diff": r.blue_diff,
                    "running_total": r.running_total,
                }
                for r in self._hand_records
            ],
            ko_result=self.scoreboard.ko_status,
            tiebreaker_hands=tiebreaker_num,
            started_at=started_at,
            finished_at=finished_at,
            total_hands_played=self.scoreboard.total_hands_played,
        )

    async def _run_hand_pair(
        self, hand_num: int, match_seed: str, is_tiebreaker: bool
    ) -> MatchHandRecord:
        """Run one hand on both tables in parallel using the same deal."""
        # Derive hand seed from match seed + hand number
        hand_seed = f"{match_seed}/hand-{hand_num}"

        # Generate deal — AB share the same cards
        deck = Deck(hand_seed)
        deal = deck.deal()

        # Get rotation
        dealer, idle_seat = get_dealer_idle(hand_num)

        # Create hand record in DB
        db_hand_id = ""
        if self.db_repo and self.match_id:
            db_hand_id = self.db_repo.create_hand(
                self.match_id, hand_num, dealer, idle_seat,
                hand_seed, is_tiebreaker=is_tiebreaker,
            )

        # Run both tables in parallel
        result_a: HandResult | None = None
        result_b: HandResult | None = None

        # Emit hand_started
        if self.event_bus:
            tables_payload = self._build_hand_started_tables(hand_num, dealer, idle_seat, deal)
            await self.event_bus.emit_hand_started(
                hand_num, dealer, hand_seed, tables_payload, idle_seat=idle_seat,
            )

        async def run_a():
            nonlocal result_a
            result_a = await self.table_a.run_hand(
                hand_num, dealer, idle_seat, deal,
                hand_id=db_hand_id,
            )

        async def run_b():
            nonlocal result_b
            result_b = await self.table_b.run_hand(
                hand_num, dealer, idle_seat, deal,
                hand_id=db_hand_id,
            )

        # AB tables run in parallel via asyncio.gather
        await asyncio.gather(run_a(), run_b())

        assert result_a is not None and result_b is not None

        # Calculate diff
        from ..engine.scoring import calculate_diff_score
        red_diff, blue_diff = calculate_diff_score(result_a.score, result_b.score)

        # Finalize hand in DB
        if self.db_repo and db_hand_id:
            from ..engine.scoring import DIFF_CAP
            self.db_repo.finish_hand(
                db_hand_id, red_diff, blue_diff,
                diff_capped=(abs(red_diff) >= DIFF_CAP or abs(blue_diff) >= DIFF_CAP),
            )

        running_total = {
            "red": self.scoreboard.red_total + red_diff,
            "blue": self.scoreboard.blue_total + blue_diff,
        }

        # Emit score_update
        if self.event_bus:
            from ..engine.scoring import DIFF_CAP
            remaining = self.config.total_hands - hand_num if not is_tiebreaker else 0
            ko = self.scoreboard.check_ko(max(remaining, 0), self.config.ko_enabled)
            await self.event_bus.emit_score_update(
                hand_num=hand_num,
                hand_diff={
                    "red_diff": red_diff,
                    "details": {
                        "table_a_score": {"red": result_a.score.red_score, "blue": result_a.score.blue_score},
                        "table_b_score": {"red": result_b.score.red_score, "blue": result_b.score.blue_score},
                        "red_net": red_diff,
                        "capped": abs(red_diff) >= DIFF_CAP or abs(blue_diff) >= DIFF_CAP,
                    },
                },
                running_total=running_total,
                remaining_hands=max(remaining, 0),
                ko_status={
                    "possible": self.config.ko_enabled,
                    "lead": ko.lead if ko else 0,
                    "max_remaining_diff": max(remaining, 0) * 12,
                    "ko_threshold": max(remaining, 0) * 12,
                } if ko else {},
            )

        return MatchHandRecord(
            hand_num=hand_num,
            dealer=dealer,
            idle_seat=idle_seat,
            seed=hand_seed,
            table_a=result_a,
            table_b=result_b,
            red_diff=red_diff,
            blue_diff=blue_diff,
            running_total=running_total,
            is_tiebreaker=is_tiebreaker,
        )

    def _build_hand_started_tables(
        self, hand_num: int, dealer: str, idle_seat: str, deal: DealResult,
    ) -> dict[str, Any]:
        """Build the per-table payload for hand_started WS event."""
        tables: dict[str, Any] = {}
        # Build seat → cards mapping same way GameEngine.init_hand does
        TURN_CYCLE = ["S", "E", "N", "W"]
        active_seats = [s for s in TURN_CYCLE if s != idle_seat]
        dealer_idx = active_seats.index(dealer)
        # Sort cards in display order: rank desc (大王→3), suit ♠→♥→♣→♦
        cards_by_seat: dict[str, tuple] = {}
        cards_by_seat[active_seats[dealer_idx]] = tuple(sorted(deal.dealer_hand, key=lambda c: c._sort_key()))
        cards_by_seat[active_seats[(dealer_idx + 1) % 3]] = tuple(sorted(deal.second_hand, key=lambda c: c._sort_key()))
        cards_by_seat[active_seats[(dealer_idx + 2) % 3]] = tuple(sorted(deal.third_hand, key=lambda c: c._sort_key()))
        cards_by_seat[idle_seat] = ()

        # Bidding order: only the 3 active players (match state.bidding_order)
        bidding_order = list(active_seats)

        # Format dizhu cards as strings for the frontend
        dizhu_cards = [str(c) for c in sorted(deal.dizhu_cards, key=lambda c: c._sort_key())]

        for table_key in ("A", "B"):
            table_runner = self.table_a if table_key == "A" else self.table_b
            players: dict[str, dict[str, Any]] = {}
            for seat in ["S", "E", "N", "W"]:
                player_id = table_runner._state.seat_agents.get(seat, "")
                team = table_runner.teams.get(seat, "")
                cards = cards_by_seat.get(seat, ())
                # Look up display_name from DB repo (same as _build_match_state does)
                display_name = ""
                if self.db_repo and player_id:
                    player = self.db_repo.get_player(player_id)
                    if player:
                        display_name = player.get("display_name", "")
                if not display_name:
                    display_name = player_id
                # Include hand cards for spectator view (except idle seat)
                hand_cards = [str(c) for c in cards] if seat != idle_seat else []
                players[seat] = {
                    "agent_name": display_name,
                    "player_id": player_id,
                    "team": team,
                    "role": "idle" if seat == idle_seat else "",
                    "hand_size": len(cards) if seat != idle_seat else 0,
                    "hand_cards": hand_cards,
                }
            tables[table_key] = {
                "phase": "bidding",
                "hand_num": hand_num,
                "current_seat": dealer,
                "dealer": dealer,
                "landlord": "",
                "dizhu_cards": dizhu_cards,
                "players": players,
                "play_history": [],
                "current_pattern": None,
                "bidding_order": bidding_order,
                "idle_seat": idle_seat,
                "effective_idle": idle_seat,
                "bidding_history": [],
                "current_high_bid": 0,
                "current_high_bidder": "",
            }
        return tables

    # ── match summary ─────────────────────────────────────────────────────

    async def _run_match_summary(self, winner_team: str, finished_at: str) -> None:
        """Run post-match summary for all agents, updating long-term memory and stats."""
        if not self.db_repo or not self.match_id:
            return

        from ..agent.base import AgentContext
        from ..engine.card import SEATS

        # ── Update player stats ──────────────────────────────────────────────
        # Increment matches_played for every participant, and matches_won for
        # players on the winning team.  Also update total_score from the
        # cumulative match score (red vs blue).
        for agent_id, team in self.seating.agent_teams.items():
            won = (team == winner_team)
            self.db_repo.update_player_stats(
                agent_id,
                matches_played_delta=1,
                matches_won_delta=1 if won else 0,
                # Per-player total_score: signed contribution to their team's score
                # Each team has 4 players — divide team total evenly.
                total_score_delta=(
                    self.scoreboard.red_total if team == "red"
                    else self.scoreboard.blue_total
                ),
            )

        # ── Build match summary context ──────────────────────────────────────
        match_summary: dict[int, dict[str, str]] = {}
        for record in self._hand_records:
            for seat in SEATS:
                aid = self.seating.table_a.get(seat) or self.seating.table_b.get(seat)
                if aid:
                    match_summary.setdefault(record.hand_num, {})[
                        seat
                    ] = record.table_a.remaining_hands.get(seat, ())

        # ── Summarize for each unique agent ──────────────────────────────────
        seen_agents: set[str] = set()
        for agent in self.agents.values():
            if agent.agent_id in seen_agents:
                continue
            seen_agents.add(agent.agent_id)

            ctx = AgentContext(
                seat=agent.seat,
                role="",
                hand_cards=[],
                hand_size=0,
                match_id=self.match_id,
                match_summary={
                    h: {"role": "participant"}
                    for h in range(1, self.config.total_hands + 1)
                },
            )
            try:
                summary_result = await agent.summarize(ctx)
                summary_text = summary_result.get("summary", "")
                long_term = summary_result.get("long_term_memory", "")

                if long_term:
                    agent.memory.update_long_term(long_term)
                    self.db_repo.update_player_long_term_memory(
                        agent.agent_id, long_term,
                    )
                    self.db_repo.add_agent_memory(
                        agent.agent_id,
                        "long_term",
                        long_term,
                        match_id=self.match_id or None,
                    )
            except Exception:
                pass  # Summary failure shouldn't crash

    def request_pause(self, reason: str = "user_requested") -> None:
        """Request pause at match level AND both tables."""
        self.pause.request_pause(reason)
        if hasattr(self, 'table_a'):
            self.table_a.pause.request_pause(reason)
        if hasattr(self, 'table_b'):
            self.table_b.pause.request_pause(reason)

    def resume_tables(self) -> None:
        """Resume both tables."""
        if hasattr(self, 'table_a'):
            self.table_a.pause.resume()
        if hasattr(self, 'table_b'):
            self.table_b.pause.resume()

    async def _check_pause(self) -> None:
        """Check match-level pause and propagate to table runners."""
        if self.pause.check_pause():
            # Also pause table runners
            if hasattr(self, 'table_a'):
                self.table_a.pause.request_pause("match_paused")
            if hasattr(self, 'table_b'):
                self.table_b.pause.request_pause("match_paused")
            from ..engine.pause import PauseState
            while self.pause.state == PauseState.PAUSED:
                await asyncio.sleep(0.1)
            # Resume tables when match resumes
            self.resume_tables()

    @staticmethod
    def _now_iso() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
