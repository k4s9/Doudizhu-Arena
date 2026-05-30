"""Game state machine — turn flow, pass logic, idle participation, phase transitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .card import TURN_CYCLE, Card
from .deck import SEATS, DealResult
from .hand import Hand
from .rules import InvalidPlayError, can_beat, recognize
from .trick import PatternType, Trick


class TablePhase(StrEnum):
    DEALING = "dealing"
    BIDDING = "bidding"
    PLAYING = "playing"
    FINISHED = "finished"
    VOID = "void"  # 流局 — all three passed on bidding


@dataclass
class BiddingRecord:
    seat: str
    bid: int  # 0 = pass, 1/2/3 = bid score
    timestamp_ms: int


@dataclass
class PlayRecord:
    round_num: int
    sub_round: int
    seq: int
    seat: str
    action_type: str  # 'play' | 'pass'
    cards: tuple[Card, ...] | None  # None for pass
    trick: Trick | None
    timestamp_ms: int



@dataclass
class TableState:
    """Complete state for a single table's current hand."""

    table: str  # 'A' | 'B'
    phase: TablePhase = TablePhase.DEALING

    # ── seating ────────────────────────────────────────────────────────
    seat_agents: dict[str, str] = field(default_factory=dict)  # seat → agent_id
    seat_teams: dict[str, str] = field(default_factory=dict)   # seat → 'red'|'blue'

    # ── hand info ──────────────────────────────────────────────────────
    hand_num: int = 0
    dealer: str = ""         # dealer seat for this hand
    original_idle: str = ""  # idle seat from rotation
    effective_idle: str = "" # actual idle after participation check

    # ── dealing ────────────────────────────────────────────────────────
    initial_hands: dict[str, tuple[Card, ...]] = field(default_factory=dict)
    dizhu_cards: tuple[Card, ...] = ()
    live_hands: dict[str, Hand] = field(default_factory=dict)

    # ── bidding ────────────────────────────────────────────────────────
    bidding_order: list[str] = field(default_factory=list)  # [dealer, second, third]
    bidding_history: list[BiddingRecord] = field(default_factory=list)
    current_bidder_idx: int = 0
    current_high_bid: int = 0
    current_high_bidder: str = ""
    landlord: str = ""
    final_bid: int = 0

    # ── idle participation ─────────────────────────────────────────────
    idle_participation_triggered: bool = False
    replaced_farmer: str = ""  # original farmer who gave up cards

    # ── playing ────────────────────────────────────────────────────────
    active_order: list[str] = field(default_factory=list)  # turn order excluding idle
    current_player: str = ""
    current_trick: Trick | None = None
    trick_leader: str = ""
    consecutive_passes: int = 0
    play_history: list[PlayRecord] = field(default_factory=list)
    round_number: int = 0
    sub_round: int = 0
    global_seq: int = 0

    # ── scoring state ──────────────────────────────────────────────────
    bombs_played: int = 0
    farmer_has_not_played: bool = True  # True until a farmer plays a card (spring tracking)
    landlord_play_count: int = 0   # for anti-spring: >1 means no anti-spring

    # ── result ─────────────────────────────────────────────────────────
    winner_team: str = ""
    winner_role: str = ""  # 'landlord' | 'farmer'
    winner_seat: str = ""
    void: bool = False

    # ── computed properties ────────────────────────────────────────────

    @property
    def is_landlord(self) -> dict[str, bool]:
        return {s: s == self.landlord for s in SEATS}

    def role_of(self, seat: str) -> str:
        if seat == self.landlord:
            return "landlord"
        if seat == self.effective_idle:
            return "idle"
        return "farmer"

    def team_of(self, seat: str) -> str:
        return self.seat_teams.get(seat, "")


class GameEngine:
    """State machine that drives a single table's game logic.

    Methods take a TableState (mutated in place) and an action,
    validate the action, and advance the state.
    """

    # ── initialisation ──────────────────────────────────────────────────

    @staticmethod
    def init_hand(
        state: TableState,
        *,
        hand_num: int,
        dealer: str,
        idle_seat: str,
        deal_result: DealResult,
    ) -> None:
        """Set up a new hand from a deal result.

        Maps dealer/second/third from deal_result to the correct seats
        based on dealer position and turn cycle.
        """
        state.phase = TablePhase.DEALING
        state.hand_num = hand_num
        state.dealer = dealer
        state.original_idle = idle_seat
        state.effective_idle = idle_seat

        # Build seat → cards mapping
        # The deal_result gives: dealer_hand, second_hand, third_hand, dizhu_cards
        # Map to actual seats based on turn cycle skipping idle
        active_seats = [s for s in TURN_CYCLE if s != idle_seat]
        dealer_idx = active_seats.index(dealer)
        cards_by_seat: dict[str, tuple[Card, ...]] = {}
        cards_by_seat[active_seats[dealer_idx]] = deal_result.dealer_hand
        cards_by_seat[active_seats[(dealer_idx + 1) % 3]] = deal_result.second_hand
        cards_by_seat[active_seats[(dealer_idx + 2) % 3]] = deal_result.third_hand
        cards_by_seat[idle_seat] = ()

        state.initial_hands = cards_by_seat
        state.dizhu_cards = deal_result.dizhu_cards
        state.live_hands = {
            s: Hand.from_cards(cards) for s, cards in cards_by_seat.items()
        }

        # Reset per-hand fields
        state.bidding_order = active_seats  # [dealer, second, third]
        state.bidding_history = []
        state.current_bidder_idx = 0
        state.current_high_bid = 0
        state.current_high_bidder = ""
        state.landlord = ""
        state.final_bid = 0
        state.idle_participation_triggered = False
        state.replaced_farmer = ""
        state.current_player = ""
        state.current_trick = None
        state.trick_leader = ""
        state.consecutive_passes = 0
        state.play_history = []
        state.round_number = 0
        state.sub_round = 0
        state.global_seq = 0
        state.bombs_played = 0
        state.farmer_has_not_played = True
        state.landlord_play_count = 0
        state.winner_team = ""
        state.winner_role = ""
        state.winner_seat = ""
        state.void = False

    # ── bidding ─────────────────────────────────────────────────────────

    @staticmethod
    def start_bidding(state: TableState) -> str:
        """Transition to bidding phase. Returns the first bidder's seat."""
        state.phase = TablePhase.BIDDING
        state.current_bidder_idx = 0
        return state.bidding_order[0]

    @staticmethod
    def submit_bid(state: TableState, seat: str, bid: int, timestamp_ms: int) -> dict:
        """Record a bid. Returns {'phase': ..., 'landlord': ..., 'finished': bool, ...}.

        Raises InvalidPlayError if seat/order is wrong or bid is invalid.
        """
        if state.phase != TablePhase.BIDDING:
            raise InvalidPlayError("Not in bidding phase")
        expected = state.bidding_order[state.current_bidder_idx]
        if seat != expected:
            raise InvalidPlayError(f"Expected {expected} to bid, got {seat}")
        if bid not in (0, 1, 2, 3):
            raise InvalidPlayError(f"Invalid bid: {bid}")
        if 0 < bid <= state.current_high_bid:
            raise InvalidPlayError(
                f"Bid {bid} must be > current high bid {state.current_high_bid}"
            )

        state.bidding_history.append(BiddingRecord(seat, bid, timestamp_ms))

        if bid > 0:
            state.current_high_bid = bid
            state.current_high_bidder = seat

        # 3分 → immediate landlord
        if bid == 3:
            state.landlord = seat
            state.final_bid = 3
            return {"phase": "bidding_done", "landlord": seat, "void": False}

        state.current_bidder_idx += 1

        # All three have bid
        if state.current_bidder_idx >= 3:
            if state.current_high_bid == 0:
                # 流局 — all passed
                state.void = True
                state.phase = TablePhase.VOID
                return {"phase": "void", "landlord": "", "void": True}
            state.landlord = state.current_high_bidder
            state.final_bid = state.current_high_bid
            return {"phase": "bidding_done", "landlord": state.landlord, "void": False}

        return {
            "phase": "bidding_continue",
            "next_bidder": state.bidding_order[state.current_bidder_idx],
            "void": False,
        }

    # ── post-bidding setup ──────────────────────────────────────────────

    @staticmethod
    def finalize_bidding(state: TableState) -> None:
        """After bidding: reveal dizhu, check idle participation, landlord takes dizhu."""
        if state.void:
            return

        landlord_team = state.seat_teams[state.landlord]
        idle_team = state.seat_teams[state.original_idle]

        # Check idle participation: if landlord is NOT from idle's team,
        # idle takes over a farmer's hand
        if landlord_team != idle_team:
            state.idle_participation_triggered = True
            # Find the farmer who is NOT the landlord and NOT from idle's team
            # (the other farmer from landlord's team)
            for seat in SEATS:
                if (
                    seat != state.landlord
                    and seat != state.original_idle
                    and state.seat_teams[seat] == landlord_team
                ):
                    state.replaced_farmer = seat
                    break

            # Transfer hand: idle (original) gets farmer's cards
            farmer_hand = state.live_hands[state.replaced_farmer]
            state.live_hands[state.original_idle] = farmer_hand
            state.live_hands[state.replaced_farmer] = Hand.from_cards(())
            # Update initial_hands for display
            state.initial_hands = {
                **state.initial_hands,
                state.original_idle: state.initial_hands[state.replaced_farmer],
                state.replaced_farmer: (),
            }
            state.effective_idle = state.replaced_farmer
        else:
            state.effective_idle = state.original_idle

        # Landlord takes dizhu cards
        state.live_hands[state.landlord].add_cards(list(state.dizhu_cards))
        state.live_hands[state.landlord].mark_dizhu_cards(state.dizhu_cards)

        # Build active turn order (skip effective idle)
        state.active_order = [s for s in TURN_CYCLE if s != state.effective_idle]

    # ── playing ─────────────────────────────────────────────────────────

    @staticmethod
    def start_playing(state: TableState) -> str:
        """Transition to playing phase. Landlord leads first trick. Returns leader seat."""
        state.phase = TablePhase.PLAYING
        state.current_player = state.landlord
        state.trick_leader = state.landlord
        state.current_trick = None  # new round, no pattern to beat
        state.round_number = 1
        state.sub_round = 0
        state.consecutive_passes = 0
        return state.landlord

    @staticmethod
    def submit_play(
        state: TableState,
        seat: str,
        cards: list[Card],
        timestamp_ms: int,
    ) -> dict:
        """Submit a play or pass. Returns result dict with game status.

        Raises InvalidPlayError on invalid play.
        """
        if state.phase != TablePhase.PLAYING:
            raise InvalidPlayError("Not in playing phase")
        if seat != state.current_player:
            raise InvalidPlayError(f"Not your turn: expected {state.current_player}")
        if seat == state.effective_idle:
            raise InvalidPlayError("Idle player cannot play")

        is_pass = len(cards) == 0
        trick = None

        if not is_pass:
            # Validate cards are in hand
            hand = state.live_hands[seat]
            for c in cards:
                if c not in hand:
                    raise InvalidPlayError(f"Card {c} not in hand")

            trick = recognize(cards)

            # If there's a current trick to beat, validate
            if state.current_trick is not None:
                if not can_beat(trick, state.current_trick):
                    raise InvalidPlayError(
                        f"{trick.display()} cannot beat {state.current_trick.display()}"
                    )

            # Track bombs (pure bombs only, not 四带二)
            if trick.pattern == PatternType.BOMB:
                state.bombs_played += 1
            elif trick.pattern == PatternType.ROCKET:
                state.bombs_played += 1

            # Remove cards from hand
            hand.remove_cards(cards)

            # Spring tracking
            if seat != state.landlord:
                state.farmer_has_not_played = False
            if seat == state.landlord:
                state.landlord_play_count += 1

        # Build play record
        state.global_seq += 1
        if is_pass:
            state.sub_round += 1
            state.consecutive_passes += 1
        else:
            state.sub_round += 1
            state.consecutive_passes = 0

        record = PlayRecord(
            round_num=state.round_number,
            sub_round=state.sub_round,
            seq=state.global_seq,
            seat=seat,
            action_type="pass" if is_pass else "play",
            cards=tuple(cards) if not is_pass else None,
            trick=trick,
            timestamp_ms=timestamp_ms,
        )
        state.play_history.append(record)

        if not is_pass:
            state.current_trick = trick
            state.trick_leader = seat

        # Check win condition
        if not is_pass and len(state.live_hands[seat]) == 0:
            return GameEngine._finish_hand(state, seat)

        # Check if round ends (2 consecutive passes)
        if state.consecutive_passes >= 2:
            state.round_number += 1
            state.sub_round = 0
            state.current_trick = None
            state.current_player = state.trick_leader
            state.consecutive_passes = 0
            return {
                "status": "round_end",
                "new_leader": state.trick_leader,
                "round": state.round_number,
                "next_player": state.current_player,
            }

        # Advance to next player in active order
        state.current_player = GameEngine._next_active(state)
        return {
            "status": "continue",
            "next_player": state.current_player,
            "trick_to_beat": state.current_trick.display() if state.current_trick else None,
        }

    @staticmethod
    def submit_pass(state: TableState, seat: str, timestamp_ms: int) -> dict:
        """Convenience wrapper for passing (playing zero cards)."""
        return GameEngine.submit_play(state, seat, [], timestamp_ms)

    # ── helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _next_active(state: TableState) -> str:
        """Get the next seat in active order after current_player."""
        idx = state.active_order.index(state.current_player)
        return state.active_order[(idx + 1) % len(state.active_order)]

    @staticmethod
    def _finish_hand(state: TableState, winner_seat: str) -> dict:
        """Handle hand completion."""
        state.phase = TablePhase.FINISHED
        winner_team = state.seat_teams[winner_seat]
        winner_role = "landlord" if winner_seat == state.landlord else "farmer"

        # Determine spring / anti-spring
        spring = False
        anti_spring = False
        if winner_role == "landlord" and state.farmer_has_not_played:
            spring = True
        if winner_role == "farmer" and state.landlord_play_count <= 1:
            anti_spring = True

        state.winner_team = winner_team
        state.winner_role = winner_role
        state.winner_seat = winner_seat

        return {
            "status": "hand_finished",
            "winner_seat": winner_seat,
            "winner_team": winner_team,
            "winner_role": winner_role,
            "spring": spring,
            "anti_spring": anti_spring,
            "bombs_played": state.bombs_played,
        }

    @staticmethod
    def get_remaining_hands(state: TableState) -> dict[str, tuple[Card, ...]]:
        """Return remaining cards for each seat (for post-hand reveal)."""
        return {s: tuple(state.live_hands[s].cards) for s in SEATS}
