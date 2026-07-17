"""TableRunner — orchestrates a single table through one complete hand of Doudizhu.

Drives the GameEngine state machine by querying Agents at decision points.
Integrates TimeoutManager and PauseManager from the engine layer.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..agent.base import Agent, AgentContext, AgentError
from ..engine.card import SEATS, Card
from ..engine.deck import DealResult
from ..engine.pause import PauseManager, PauseState
from ..engine.rules import InvalidPlayError, recognize, validate_play
from ..engine.scoring import HandScore, calculate_hand_score
from ..engine.state import (
    BiddingRecord,
    GameEngine,
    PlayRecord,
    TablePhase,
    TableState,
)
from ..engine.timeout import TimeoutManager

if TYPE_CHECKING:
    from ..db.repository import DatabaseRepository
    from ..api.event_bus import MatchEventBus

MAX_PLAY_ITERATIONS = 1000  # safety limit to prevent infinite loops


@dataclass
class HandResult:
    """Complete result of one hand at one table."""

    table: str
    hand_num: int
    dealer: str
    original_idle: str

    # ── bidding outcome ──
    void: bool
    landlord: str
    final_bid: int
    bidding_history: list[BiddingRecord]

    # ── idle participation ──
    idle_participation_triggered: bool
    replaced_farmer: str
    effective_idle: str

    # ── play data ──
    initial_hands: dict[str, tuple[Card, ...]]
    play_history: list[PlayRecord]
    remaining_hands: dict[str, tuple[Card, ...]]

    # ── scoring ──
    score: HandScore
    bombs_played: int
    spring: bool
    anti_spring: bool


class TableRunner:
    """Runs hands on a single table by calling agents at decision points.

    Each hand goes through: dealing → bidding → (optional void) → playing → finished.
    """

    def __init__(
        self,
        table: str,
        agents: dict[str, Agent],
        teams: dict[str, str],
        pause_manager: PauseManager | None = None,
        timeout_manager: TimeoutManager | None = None,
        db_repo: DatabaseRepository | None = None,
        match_id: str = "",
    ) -> None:
        if table not in ("A", "B"):
            raise ValueError(f"Table must be 'A' or 'B', got {table!r}")
        self.table = table
        self.agents = agents
        self.teams = teams
        self.pause = pause_manager or PauseManager(table)
        self.timeout = timeout_manager or TimeoutManager()
        self.db_repo = db_repo
        self.match_id = match_id
        self.event_bus = None  # type: MatchEventBus | None — set by MatchRunner
        self._state = TableState(table=table)
        self._timeout_initialized = False
        self._current_hand_id = ""  # DB hand ID
        self._current_table_hand_id = ""  # DB table_hand ID

    # ── public API ────────────────────────────────────────────────────────

    async def run_hand(
        self,
        hand_num: int,
        dealer: str,
        idle_seat: str,
        deal_result: DealResult,
        hand_id: str = "",
    ) -> HandResult:
        """Run a complete hand from dealing through scoring.

        Args:
            hand_num: Hand number in the match.
            dealer: Dealer seat.
            idle_seat: Idle seat.
            deal_result: The dealt cards.
            hand_id: Optional DB hand ID for persistence.

        Returns HandResult with all play data and scores.
        """
        self._current_hand_id = hand_id

        # 1. Initialize
        GameEngine.init_hand(
            self._state,
            hand_num=hand_num,
            dealer=dealer,
            idle_seat=idle_seat,
            deal_result=deal_result,
        )
        self._state.seat_agents = {
            s: self.agents[s].agent_id for s in SEATS if s in self.agents
        }
        self._state.seat_teams = self.teams

        if not self._timeout_initialized:
            self.timeout.init_teams(self.teams)
            self._timeout_initialized = True

        # Create table_hand record in DB
        self._current_table_hand_id = ""
        if self.db_repo and hand_id:
            self._current_table_hand_id = self.db_repo.create_table_hand(hand_id, self.table)
            # Write initial hands
            for seat, cards in self._state.initial_hands.items():
                self.db_repo.add_initial_hand(
                    self._current_table_hand_id, seat,
                    list(cards) if cards else [],
                )

        # 2. Bidding phase
        await self._run_bidding()

        # 3. If void, return early
        if self._state.void:
            if self.db_repo and self._current_table_hand_id:
                self.db_repo.update_table_hand_result(
                    self._current_table_hand_id,
                    status="void",
                    void=True,
                )
            # Emit hand_ended for void tables too (so frontend knows this table is done)
            if self.event_bus:
                await self.event_bus.emit_hand_ended(
                    table=self.table,
                    hand_num=hand_num,
                    winner_team="",
                    winner_role="void",
                    score={"base": 0, "multiplier": 0, "total": 0, "breakdown": {}},
                    remaining_hands={s: [str(c) for c in self._state.initial_hands.get(s, ())] for s in SEATS},
                    timestamp_ms=self._now_ms(),
                )
            return HandResult(
                table=self.table,
                hand_num=hand_num,
                dealer=dealer,
                original_idle=idle_seat,
                void=True,
                landlord="",
                final_bid=0,
                bidding_history=list(self._state.bidding_history),
                idle_participation_triggered=False,
                replaced_farmer="",
                effective_idle=idle_seat,
                initial_hands=dict(self._state.initial_hands),
                play_history=[],
                remaining_hands={s: () for s in SEATS},
                score=calculate_hand_score(
                    final_bid=0, winner_seat="", winner_team="",
                    landlord_seat="", bombs_played=0,
                    spring=False, anti_spring=False, void=True,
                ),
                bombs_played=0,
                spring=False,
                anti_spring=False,
            )

        # 4. Finalize bidding (idle participation, dizhu cards, active order)
        GameEngine.finalize_bidding(self._state)

        # 5. Playing phase
        GameEngine.start_playing(self._state)
        await self._run_playing()

        # 6. Build result
        remaining = GameEngine.get_remaining_hands(self._state)
        winner_seat = self._state.winner_seat

        score = calculate_hand_score(
            final_bid=self._state.final_bid,
            winner_seat=winner_seat,
            winner_team=self._state.winner_team,
            landlord_seat=self._state.landlord,
            bombs_played=self._state.bombs_played,
            spring=(self._state.winner_role == "landlord" and self._state.farmer_has_not_played),
            anti_spring=(self._state.winner_role == "farmer" and self._state.landlord_play_count <= 1),
            void=False,
        )

        # Persist table_hand result
        if self.db_repo and self._current_table_hand_id:
            self.db_repo.update_table_hand_result(
                self._current_table_hand_id,
                status="finished",
                landlord_seat=self._state.landlord,
                final_bid=self._state.final_bid,
                dizhu_cards=",".join(str(c) for c in self._state.dizhu_cards) if self._state.dizhu_cards else "[]",
                idle_participation={
                    "triggered": self._state.idle_participation_triggered,
                    "original_farmer": self._state.replaced_farmer,
                    "replaced_by_idle": self._state.original_idle,
                    "reason": f"地主({self._state.landlord})非闲家({self._state.original_idle})本队"
                    if self._state.idle_participation_triggered else "同队地主无需参与",
                },
                void=False,
                winner_team=self._state.winner_team,
                winner_role=self._state.winner_role,
                base_score=score.base_score,
                multiplier=score.multiplier,
                final_score=score.final_score,
                bombs_played=self._state.bombs_played,
                spring=score.spring,
                anti_spring=score.anti_spring,
            )
            # Write remaining hands
            for seat, cards in remaining.items():
                self.db_repo.add_remaining_hand(
                    self._current_table_hand_id, seat, list(cards),
                )

            # Trigger reflection for all participating agents
            await self._run_reflections(hand_num, idle_seat, dealer, remaining)

        return HandResult(
            table=self.table,
            hand_num=hand_num,
            dealer=dealer,
            original_idle=idle_seat,
            void=False,
            landlord=self._state.landlord,
            final_bid=self._state.final_bid,
            bidding_history=list(self._state.bidding_history),
            idle_participation_triggered=self._state.idle_participation_triggered,
            replaced_farmer=self._state.replaced_farmer,
            effective_idle=self._state.effective_idle,
            initial_hands=dict(self._state.initial_hands),
            play_history=list(self._state.play_history),
            remaining_hands=remaining,
            score=score,
            bombs_played=self._state.bombs_played,
            spring=(score.spring),
            anti_spring=(score.anti_spring),
        )

    # ── bidding ───────────────────────────────────────────────────────────

    async def _run_bidding(self) -> None:
        """Drive the bidding phase, querying agents in turn."""
        GameEngine.start_bidding(self._state)

        while self._state.phase == TablePhase.BIDDING:
            await self._check_pause()
            if self._state.phase != TablePhase.BIDDING:
                break

            seat = self._state.bidding_order[self._state.current_bidder_idx]
            agent = self.agents.get(seat)
            if agent is None:
                raise AgentError(f"No agent assigned to seat {seat}")

            bid = 0
            reasoning = ""
            # Check if agent is in full auto-play mode
            if self.timeout.is_auto_play_enabled(seat):
                bid = 0  # auto-pass
                reasoning = "[托管] auto-pass"
            else:
                ctx = self._build_bidding_context(seat)
                team = self.teams.get(seat, "")
                timeout_seconds = self.timeout.bidding_limit(seat)
                self.timeout.start_turn(seat)
                try:
                    bid = await asyncio.wait_for(
                        agent.decide_bid(ctx),
                        timeout=timeout_seconds,
                    )
                    self.timeout.end_turn(seat, team, success=True)
                    # Capture reasoning from agent after successful decision
                    reasoning = agent.get_last_reasoning() if hasattr(agent, 'get_last_reasoning') else ""
                except asyncio.TimeoutError:
                    self.timeout.end_turn(seat, team, success=False)
                    bid = 0
                    reasoning = f"[超时] bidding timeout after {timeout_seconds}s"
                except Exception:
                    # Agent error → treat as pass
                    self.timeout.end_turn(seat, team, success=False)
                    bid = 0
                    reasoning = "[错误] agent error, fallback to pass"

                # Validate bid
                if bid not in (0, 1, 2, 3):
                    bid = 0
                if 0 < bid <= self._state.current_high_bid:
                    bid = 0  # invalid: must be > current_high_bid

            ts = self._now_ms()
            result = GameEngine.submit_bid(self._state, seat, bid, ts)

            # Persist bidding record + thought
            if self.db_repo and self._current_table_hand_id:
                bid_seq = len(self._state.bidding_history)
                self.db_repo.add_bidding_record(
                    self._current_table_hand_id, bid_seq, seat, bid, ts,
                )
                self.db_repo.add_agent_thought(
                    self._current_table_hand_id,
                    player_id=self.agents[seat].agent_id,
                    seat=seat,
                    phase="bidding",
                    reasoning=reasoning,
                    decision=f'{{"bid": {bid}}}',
                    timestamp_ms=ts,
                )

            # Emit WS events
            if self.event_bus:
                await self.event_bus.emit_bidding_update(
                    table=self.table,
                    hand_num=self._state.hand_num,
                    seat=seat,
                    bid=bid,
                    current_high_bid=self._state.current_high_bid,
                    current_high_seat=self._state.current_high_bidder,
                    bidding_history=[
                        {"seat": r.seat, "bid": r.bid}
                        for r in self._state.bidding_history
                    ],
                )
                await self.event_bus.emit_thought_update(
                    table=self.table,
                    hand_num=self._state.hand_num,
                    phase="bidding",
                    agent_id=self.agents[seat].agent_id,
                    seat=seat,
                    reasoning=reasoning,
                    decision={"bid": bid},
                    round_num=None,
                    sub_round=None,
                    timestamp_ms=ts,
                )

            # Handle result — emit bidding_complete
            if result.get("phase") == "bidding_done" or result.get("void"):
                if self.event_bus:
                    is_void = bool(result.get("void"))
                    # Compute idle_detail BEFORE finalize_bidding so we can send
                    # the correct effective_idle to the frontend.
                    # Replicating finalize_bidding logic inline for the event payload:
                    landlord_seat = self._state.current_high_bidder if not is_void else ""
                    if not is_void and landlord_seat:
                        landlord_team = self._state.seat_teams[landlord_seat]
                        idle_team = self._state.seat_teams[self._state.original_idle]
                        idle_triggered = (landlord_team != idle_team)
                        replaced = ""
                        effective = self._state.original_idle
                        if idle_triggered:
                            for seat in SEATS:
                                if (
                                    seat != landlord_seat
                                    and seat != self._state.original_idle
                                    and self._state.seat_teams[seat] == landlord_team
                                ):
                                    replaced = seat
                                    break
                            effective = replaced
                        idle_detail = {
                            "triggered": idle_triggered,
                            "idle_seat": self._state.original_idle,
                            "effective_idle": effective,
                            "replaced_farmer": replaced,
                            "reason": (
                                f"地主({landlord_seat})非闲家({self._state.original_idle})本队，"
                                f"闲家替换{replaced}"
                                if idle_triggered
                                else "同队地主无需参与"
                            ),
                        }
                    else:
                        idle_detail = {
                            "triggered": False,
                            "idle_seat": self._state.original_idle,
                            "effective_idle": self._state.original_idle,
                            "reason": "流局" if is_void else "同队地主无需参与",
                        }
                    dizhu_cards = [str(c) for c in self._state.dizhu_cards] if self._state.dizhu_cards else []
                    await self.event_bus.emit_bidding_complete(
                        table=self.table,
                        hand_num=self._state.hand_num,
                        landlord_seat=landlord_seat,
                        final_bid=self._state.current_high_bid if not is_void else 0,
                        dizhu_cards=dizhu_cards,
                        void=is_void,
                        idle_detail=idle_detail,
                    )
                break

    def _build_bidding_context(self, seat: str) -> AgentContext:
        hand = self._state.live_hands[seat]
        return AgentContext(
            seat=seat,
            role="bidding",  # role not yet assigned
            hand_cards=list(hand.cards),
            hand_size=hand.size,
            current_high_bid=self._state.current_high_bid,
            current_high_bidder=self._state.current_high_bidder,
            bidding_history=[
                {"seat": r.seat, "bid": r.bid} for r in self._state.bidding_history
            ],
        )

    # ── playing ───────────────────────────────────────────────────────────

    async def _run_playing(self) -> None:
        """Drive the playing phase loop."""
        iterations = 0

        while self._state.phase == TablePhase.PLAYING:
            iterations += 1
            if iterations > MAX_PLAY_ITERATIONS:
                raise AgentError(
                    f"Exceeded {MAX_PLAY_ITERATIONS} play iterations — "
                    f"likely infinite loop in hand {self._state.hand_num}"
                )

            await self._check_pause()
            if self._state.phase != TablePhase.PLAYING:
                break

            seat = self._state.current_player
            agent = self.agents.get(seat)
            if agent is None:
                raise AgentError(f"No agent assigned to seat {seat}")

            # Determine if agent should auto-play
            cards: list[Card] = []
            is_trusted = False
            reasoning = ""
            if self.timeout.is_auto_play_enabled(seat):
                is_trusted = True
                is_leader = self._state.current_trick is None
                hand = self._state.live_hands[seat]
                cards = self.timeout.auto_play(hand, is_leader)
                reasoning = "[托管] auto-play" if cards else "[托管] auto-pass"
            else:
                ctx = self._build_play_context(seat)
                team = self.teams.get(seat, "")
                is_leader = self._state.current_trick is None
                timeout_seconds = self.timeout.effective_individual_limit(seat, team)
                self.timeout.start_turn(seat)
                try:
                    # try to get both reasoning and cards from agent
                    play_result = await asyncio.wait_for(
                        agent.decide_play(ctx),
                        timeout=timeout_seconds,
                    )
                    self.timeout.end_turn(seat, team, success=True)
                    if isinstance(play_result, tuple) and len(play_result) == 2:
                        cards_list, reasoning = play_result
                        cards = list(cards_list)
                    else:
                        cards = list(play_result)
                        reasoning = agent.get_last_reasoning() if hasattr(agent, 'get_last_reasoning') else ""
                except asyncio.TimeoutError:
                    self.timeout.end_turn(seat, team, success=False)
                    cards = []  # timeout → pass
                    reasoning = f"[超时] play timeout after {timeout_seconds}s"
                except Exception:
                    self.timeout.end_turn(seat, team, success=False)
                    cards = []  # error → pass
                    reasoning = "[错误] agent error, fallback to pass"

                # Validate: cards must be in hand and form a legal play
                try:
                    hand = self._state.live_hands[seat]
                    for c in cards:
                        if c not in hand:
                            cards = []
                            break
                    if cards:
                        trick = recognize(cards)
                        if self._state.current_trick is not None:
                            from ..engine.rules import can_beat
                            if not can_beat(trick, self._state.current_trick):
                                cards = []  # illegal: cannot beat, fall back to pass
                except InvalidPlayError:
                    cards = []

            ts = self._now_ms()
            result = GameEngine.submit_play(self._state, seat, cards, ts)

            # Persist play action
            play_record = self._state.play_history[-1] if self._state.play_history else None
            if self.db_repo and self._current_table_hand_id and play_record:
                trick = play_record.trick
                self.db_repo.add_play_action(
                    self._current_table_hand_id,
                    play_record.round_num,
                    play_record.sub_round,
                    play_record.seq,
                    seat,
                    play_record.action_type,
                    cards=list(play_record.cards) if play_record.cards else None,
                    pattern=trick.pattern.value if trick else None,
                    display=trick.display() if trick else None,
                    is_trusted=is_trusted,
                    timestamp_ms=ts,
                )
                # Persist agent thought
                self.db_repo.add_agent_thought(
                    self._current_table_hand_id,
                    player_id=self.agents[seat].agent_id,
                    seat=seat,
                    phase="playing",
                    reasoning=reasoning if not is_trusted else "[托管] auto-play",
                    decision=json.dumps({"type": play_record.action_type, "cards": [str(c) for c in play_record.cards] if play_record.cards else []}),
                    round_num=play_record.round_num,
                    sub_round=play_record.sub_round,
                    timestamp_ms=ts,
                )

            # Emit WS events
            if self.event_bus and play_record:
                if play_record.action_type == "play":
                    trick = play_record.trick
                    current_pattern = None
                    if self._state.current_trick:
                        current_pattern = {
                            "pattern": self._state.current_trick.pattern.value,
                            "length": self._state.current_trick.length,
                            "max_rank": str(self._state.current_trick.main_rank) if self._state.current_trick.main_rank else None,
                        }
                    await self.event_bus.emit_card_played(
                        table=self.table,
                        hand_num=self._state.hand_num,
                        round_num=play_record.round_num,
                        sub_round=play_record.sub_round,
                        seat=seat,
                        cards=[str(c) for c in play_record.cards] if play_record.cards else [],
                        pattern=trick.pattern.value if trick else "",
                        display=trick.display() if trick else "",
                        current_pattern=current_pattern,
                        next_seat=self._state.current_player,
                        timestamp_ms=ts,
                    )
                else:
                    await self.event_bus.emit_pass(
                        table=self.table,
                        hand_num=self._state.hand_num,
                        round_num=play_record.round_num,
                        sub_round=play_record.sub_round,
                        seat=seat,
                        pass_count_in_round=1,  # simplified
                        next_seat=self._state.current_player,
                        timestamp_ms=ts,
                    )

                await self.event_bus.emit_thought_update(
                    table=self.table,
                    hand_num=self._state.hand_num,
                    phase="playing",
                    agent_id=self.agents[seat].agent_id,
                    seat=seat,
                    reasoning=reasoning if not is_trusted else "[托管] auto-play",
                    decision={"type": play_record.action_type, "cards": [str(c) for c in play_record.cards] if play_record.cards else []},
                    round_num=play_record.round_num,
                    sub_round=play_record.sub_round,
                    timestamp_ms=ts,
                )

                # Emit trick_won if round ended
                if result.get("status") == "round_end" and result.get("new_leader"):
                    await self.event_bus.emit_trick_won(
                        table=self.table,
                        hand_num=self._state.hand_num,
                        round_num=play_record.round_num,
                        winner_seat=result["new_leader"],
                        new_leader=result["new_leader"],
                        reason="连续pass",
                    )

            if result.get("status") == "hand_finished":
                # Emit hand_ended
                if self.event_bus:
                    remaining = GameEngine.get_remaining_hands(self._state)
                    score = calculate_hand_score(
                        final_bid=self._state.final_bid,
                        winner_seat=self._state.winner_seat,
                        winner_team=self._state.winner_team,
                        landlord_seat=self._state.landlord,
                        bombs_played=self._state.bombs_played,
                        spring=(self._state.winner_role == "landlord" and self._state.farmer_has_not_played),
                        anti_spring=(self._state.winner_role == "farmer" and self._state.landlord_play_count <= 1),
                        void=False,
                    )
                    await self.event_bus.emit_hand_ended(
                        table=self.table,
                        hand_num=self._state.hand_num,
                        winner_team=self._state.winner_team,
                        winner_role=self._state.winner_role,
                        score={
                            "base": score.base_score,
                            "multiplier": score.multiplier,
                            "total": score.final_score,
                            "breakdown": {
                                "bombs": self._state.bombs_played,
                                "spring": score.spring,
                                "anti_spring": score.anti_spring,
                            },
                        },
                        remaining_hands={
                            s: [str(c) for c in cards_list] for s, cards_list in remaining.items()
                        },
                        timestamp_ms=ts,
                    )
                break

    def _build_play_context(self, seat: str) -> AgentContext:
        hand = self._state.live_hands[seat]
        return AgentContext(
            seat=seat,
            role=self._state.role_of(seat),
            hand_cards=list(hand.cards),
            hand_size=hand.size,
            dizhu_cards=self._state.dizhu_cards if self._state.dizhu_cards else None,
            bidding_history=[
                {"seat": r.seat, "bid": r.bid} for r in self._state.bidding_history
            ],
            play_history=[
                {
                    "round": r.round_num,
                    "sub_round": r.sub_round,
                    "seq": r.seq,
                    "seat": r.seat,
                    "action_type": r.action_type,
                    "cards": list(r.cards) if r.cards else None,
                    "trick_display": r.trick.rank_only_display() if r.trick else None,
                }
                for r in self._state.play_history
            ],
            current_trick=self._state.current_trick,
            trick_leader=self._state.trick_leader,
            landlord=self._state.landlord,
            active_players=list(self._state.active_order),
            player_hand_sizes={
                s: self._state.live_hands[s].size for s in SEATS
            },
        )

    # ── reflection ────────────────────────────────────────────────────────

    async def _run_reflections(
        self,
        hand_num: int,
        dealer: str,
        idle_seat: str,
        remaining: dict[str, tuple[Card, ...]],
    ) -> None:
        """Run reflection for all agents that participated in this hand."""
        if not self.db_repo or not self._current_table_hand_id:
            return

        for seat in SEATS:
            agent = self.agents.get(seat)
            if agent is None:
                continue

            actual_role = self._state.role_of(seat)
            is_idle = seat == self._state.effective_idle and actual_role == "idle"

            # Build reflection context
            ctx = self._build_reflection_context(
                seat, actual_role, is_idle, hand_num, dealer, idle_seat, remaining,
            )

            try:
                reflection_result = await agent.reflect(ctx)
                reflection_text = reflection_result.get("reflection", "")
                short_term = reflection_result.get("short_term_memory", "")

                # Update in-memory short-term memory
                if short_term:
                    agent.memory.update_short_term(short_term)

                # Persist reflection
                self.db_repo.add_reflection(
                    self._current_table_hand_id,
                    player_id=agent.agent_id,
                    seat=seat,
                    actual_role=actual_role,
                    reflection=reflection_text,
                    short_term_memory=short_term,
                )
                if short_term:
                    self.db_repo.add_agent_memory(
                        agent.agent_id,
                        "short_term",
                        short_term,
                        match_id=self.match_id,
                    )
            except Exception:
                pass  # Reflection failure shouldn't block the match

    def _build_reflection_context(
        self,
        seat: str,
        actual_role: str,
        is_idle: bool,
        hand_num: int,
        dealer: str,
        idle_seat: str,
        remaining: dict[str, tuple[Card, ...]],
    ) -> AgentContext:
        """Build an AgentContext for post-hand reflection."""
        hand = self._state.live_hands[seat]
        return AgentContext(
            seat=seat,
            role=actual_role,
            agent_role=actual_role,
            is_idle_observer=is_idle,
            hand_cards=list(hand.cards),
            hand_size=hand.size,
            dizhu_cards=self._state.dizhu_cards if self._state.dizhu_cards else None,
            bidding_history=[
                {"seat": r.seat, "bid": r.bid} for r in self._state.bidding_history
            ],
            play_history=[
                {
                    "round": r.round_num,
                    "sub_round": r.sub_round,
                    "seq": r.seq,
                    "seat": r.seat,
                    "action_type": r.action_type,
                    "cards": list(r.cards) if r.cards else None,
                    "trick_display": r.trick.display() if r.trick else None,
                }
                for r in self._state.play_history
            ],
            initial_hand=tuple(self._state.initial_hands.get(seat, ())),
            remaining_hands=remaining,
            hand_score=self._state.final_bid,  # approximate
            winner_team=self._state.winner_team,
            winner_role=self._state.winner_role,
            hand_num=hand_num,
            landlord=self._state.landlord,
            active_players=list(self._state.active_order),
            player_hand_sizes={
                s: len(remaining.get(s, ())) for s in SEATS
            },
        )

    # ── helpers ───────────────────────────────────────────────────────────

    async def _check_pause(self) -> None:
        """Check pause state and block if paused."""
        if self.pause.check_pause():
            # State transitioned to PAUSED — block until resumed
            while self.pause.state == PauseState.PAUSED:
                import asyncio
                await asyncio.sleep(0.1)

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)
