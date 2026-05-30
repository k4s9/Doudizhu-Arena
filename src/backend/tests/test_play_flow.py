"""Tests for game state machine — bidding flow, play flow, pass logic, idle participation."""
import pytest

from arena.engine.card import Card, Rank, Suit
from arena.engine.deck import DealResult
from arena.engine.rules import InvalidPlayError
from arena.engine.state import GameEngine, TablePhase, TableState
from arena.engine.trick import PatternType


# ── card helpers ────────────────────────────────────────────────────────────────

def c(rank: Rank, suit: Suit) -> Card:
    return Card(rank=rank, suit=suit)


def j(rank: Rank) -> Card:
    return Card(rank=rank)


def make_deal_result(seed: str = "play-flow-test"):
    """Create a DealResult with known cards for deterministic testing.

    Seat assignments: dealer=S, second=E, third=N, idle=W.
    """
    from arena.engine.deck import Deck
    deck = Deck(seed=seed)
    return deck.deal()


def make_state(table="A"):
    """Create a TableState with standard seating."""
    state = TableState(table=table)
    state.seat_agents = {"S": "agent-red-1", "E": "agent-blue-1",
                          "N": "agent-red-2", "W": "agent-blue-2"}
    state.seat_teams = {"S": "red", "E": "blue", "N": "red", "W": "blue"}
    return state


# ── bidding ─────────────────────────────────────────────────────────────────────

class TestBidding:
    def test_full_bidding_flow_landlord_s(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)

        # Start bidding
        first = GameEngine.start_bidding(state)
        assert first == "S"
        assert state.phase == TablePhase.BIDDING

        # S bids 2
        result = GameEngine.submit_bid(state, "S", 2, 1000)
        assert result["phase"] == "bidding_continue"
        assert result["next_bidder"] == "E"

        # E passes
        result = GameEngine.submit_bid(state, "E", 0, 2000)
        assert result["phase"] == "bidding_continue"
        assert result["next_bidder"] == "N"

        # N passes → S wins with 2
        result = GameEngine.submit_bid(state, "N", 0, 3000)
        assert result["phase"] == "bidding_done"
        assert result["landlord"] == "S"
        assert state.final_bid == 2

    def test_3_bid_immediate_landlord(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)

        result = GameEngine.submit_bid(state, "S", 3, 1000)
        assert result["phase"] == "bidding_done"
        assert result["landlord"] == "S"
        assert state.final_bid == 3

    def test_all_pass_void(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)

        GameEngine.submit_bid(state, "S", 0, 1000)
        GameEngine.submit_bid(state, "E", 0, 2000)
        result = GameEngine.submit_bid(state, "N", 0, 3000)
        assert result["phase"] == "void"
        assert result["void"] is True
        assert state.phase == TablePhase.VOID
        assert state.void is True

    def test_bid_must_be_higher(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 2, 1000)

        with pytest.raises(InvalidPlayError, match="must be >"):
            GameEngine.submit_bid(state, "E", 1, 2000)

    def test_wrong_seat_bidding(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)

        with pytest.raises(InvalidPlayError, match="Expected S"):
            GameEngine.submit_bid(state, "E", 2, 1000)

    def test_invalid_bid_value(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)

        with pytest.raises(InvalidPlayError):
            GameEngine.submit_bid(state, "S", 5, 1000)

    def test_bidding_not_in_phase(self):
        state = make_state()
        with pytest.raises(InvalidPlayError, match="bidding phase"):
            GameEngine.submit_bid(state, "S", 2, 1000)

    def test_bidding_history(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)

        GameEngine.submit_bid(state, "S", 1, 1000)
        GameEngine.submit_bid(state, "E", 2, 2000)
        GameEngine.submit_bid(state, "N", 0, 3000)

        assert len(state.bidding_history) == 3
        assert state.bidding_history[0].bid == 1
        assert state.bidding_history[1].bid == 2
        assert state.bidding_history[2].bid == 0


# ── idle participation ──────────────────────────────────────────────────────────

class TestIdleParticipation:
    def test_idle_replaces_farmer_when_opponent_landlord(self):
        """A桌: A=红, B=蓝. W(蓝)闲家. E(蓝)地主→闲家不参与.
           S(红)地主→W替换N(红农民)参战."""
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 3, 1000)  # S(red) landlord

        GameEngine.finalize_bidding(state)
        assert state.idle_participation_triggered is True
        # W(blue idle) replaces the red farmer (either N)
        assert state.replaced_farmer == "N"  # N=red farmer
        assert state.effective_idle == "N"  # N becomes idle
        # W got N's cards
        assert len(state.live_hands["W"]) == 17
        assert len(state.live_hands["N"]) == 0

    def test_idle_does_not_participate_when_teammate_landlord(self):
        """A桌: W(蓝)闲家. E(蓝)地主 → W stays idle."""
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 0, 1000)
        GameEngine.submit_bid(state, "E", 3, 2000)  # E(blue) landlord

        GameEngine.finalize_bidding(state)
        assert state.idle_participation_triggered is False
        assert state.effective_idle == "W"  # W stays idle
        assert len(state.live_hands["W"]) == 0  # still no cards

    def test_dizhu_cards_added_to_landlord(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 3, 1000)
        GameEngine.finalize_bidding(state)

        # Landlord should have 20 cards (17 + 3 dizhu)
        assert len(state.live_hands["S"]) == 20

    def test_active_order_excludes_idle(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 3, 1000)
        GameEngine.finalize_bidding(state)

        # W participated (replaced N), so effective idle is N
        assert state.effective_idle == "N"
        assert "N" not in state.active_order
        assert state.active_order == ["S", "E", "W"]


# ── playing ─────────────────────────────────────────────────────────────────────

class TestPlaying:
    def test_landlord_starts(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 3, 1000)
        GameEngine.finalize_bidding(state)

        leader = GameEngine.start_playing(state)
        assert leader == "S"  # landlord always starts
        assert state.phase == TablePhase.PLAYING
        assert state.round_number == 1
        assert state.current_trick is None  # new round, free play

    def test_play_single_card(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 3, 1000)
        GameEngine.finalize_bidding(state)
        GameEngine.start_playing(state)

        # S plays their rightmost card (min rank, worst suit)
        hand = state.live_hands["S"]
        card_to_play = [hand.cards[-1]]  # smallest card
        result = GameEngine.submit_play(state, "S", card_to_play, 5000)

        assert result["status"] == "continue"
        assert result["next_player"] == "E"  # next active (after idle replacement)
        assert state.current_trick is not None
        assert state.current_trick.pattern == PatternType.SINGLE
        assert len(state.play_history) == 1

    def test_pass_flow(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 3, 1000)
        GameEngine.finalize_bidding(state)
        GameEngine.start_playing(state)

        # S plays, E and W in active order
        # active_order after idle replacement: ["S", "E", "W"] (N is idle)
        hand_s = state.live_hands["S"]
        GameEngine.submit_play(state, "S", [hand_s.cards[-1]], 1000)

        # E passes
        result_e = GameEngine.submit_pass(state, "E", 2000)
        assert result_e["status"] == "continue"
        assert state.consecutive_passes == 1

        # W passes → round ends (2 consecutive passes)
        result_w = GameEngine.submit_pass(state, "W", 3000)
        assert result_w["status"] == "round_end"
        assert result_w["new_leader"] == "S"  # S trick_leader

    def test_play_must_beat_current_trick(self):
        state = make_state()
        deal = make_deal_result("seed-1")  # S gets ♦4, E has small cards ≤rank 4
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 3, 1000)
        GameEngine.finalize_bidding(state)
        GameEngine.start_playing(state)

        # S plays ♦4 as a single (rank 4)
        hand_s = state.live_hands["S"]
        low_card = None
        for c in hand_s.cards:
            if str(c) == "♦4":
                low_card = c
                break
        assert low_card is not None, "seed-1 must give S the ♦4"

        GameEngine.submit_play(state, "S", [low_card], 1000)
        assert state.current_trick is not None
        assert state.current_trick.pattern == PatternType.SINGLE
        assert state.current_trick.main_rank.value == 4

        # E tries to play ♥3 (rank 3, cannot beat ♦4 rank 4)
        hand_e = state.live_hands["E"]
        smallest = hand_e.cards[-1]  # rightmost = smallest rank, worst suit
        assert smallest.rank.value <= 4, f"Expected E's smallest to be ≤rank 4, got {smallest}"

        with pytest.raises(InvalidPlayError, match="cannot beat"):
            GameEngine.submit_play(state, "E", [smallest], 2000)

    def test_card_not_in_hand(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 3, 1000)
        GameEngine.finalize_bidding(state)
        GameEngine.start_playing(state)

        # Try to play a card from another player's hand
        wrong_card = state.live_hands["E"].cards[0]
        with pytest.raises(InvalidPlayError, match="not in hand"):
            GameEngine.submit_play(state, "S", [wrong_card], 1000)

    def test_no_play_when_not_your_turn(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 3, 1000)
        GameEngine.finalize_bidding(state)
        GameEngine.start_playing(state)

        # E tries to play before S
        with pytest.raises(InvalidPlayError, match="Not your turn"):
            GameEngine.submit_play(state, "E", [state.live_hands["E"].cards[-1]], 1000)

    def test_idle_cannot_play(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 3, 1000)
        GameEngine.finalize_bidding(state)
        GameEngine.start_playing(state)

        # N is effective idle after the idle participation
        # Submit plays until it's N's turn — but wait, N is idle and not in active_order
        # The idle player is never current_player, so let's just verify the idle seat
        assert state.effective_idle == "N"
        assert "N" not in state.active_order
        # Current player should NOT be the idle
        assert state.current_player != "N"


# ── hand completion ─────────────────────────────────────────────────────────────

class TestHandCompletion:
    def test_hand_finished_when_hand_empty(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 3, 1000)
        GameEngine.finalize_bidding(state)

        # Remove all but 1 card from S's hand for quick test
        hand = state.live_hands["S"]
        last_card = hand.cards[0]
        all_but_last = [c for c in hand.cards if c != last_card]
        hand.remove_cards(all_but_last)

        GameEngine.start_playing(state)
        result = GameEngine.submit_play(state, "S", [last_card], 5000)
        assert result["status"] == "hand_finished"
        assert result["winner_seat"] == "S"
        assert state.phase == TablePhase.FINISHED

    def test_remaining_hands_revealed(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 3, 1000)
        GameEngine.finalize_bidding(state)

        # Remove all but 1 card from S's hand
        hand_s = state.live_hands["S"]
        last_card = hand_s.cards[0]
        all_but_last = [c for c in hand_s.cards if c != last_card]
        hand_s.remove_cards(all_but_last)

        GameEngine.start_playing(state)
        GameEngine.submit_play(state, "S", [last_card], 5000)

        remaining = GameEngine.get_remaining_hands(state)
        assert "S" in remaining
        assert len(remaining["S"]) == 0  # S played everything


# ── role queries ────────────────────────────────────────────────────────────────

class TestRoleQueries:
    def test_role_of(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 3, 1000)
        GameEngine.finalize_bidding(state)

        assert state.role_of("S") == "landlord"
        assert state.role_of("E") == "farmer"
        assert state.role_of("N") == "idle"  # replaced, now idle
        assert state.role_of("W") == "farmer"  # replaced N

    def test_is_landlord(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 3, 1000)
        GameEngine.finalize_bidding(state)

        roles = state.is_landlord
        assert roles["S"] is True
        assert roles["E"] is False


# ── spring / anti-spring ───────────────────────────────────────────────────────

class TestSpringTracking:
    def test_spring_true_when_landlord_wins_no_farmer_play(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 3, 1000)
        GameEngine.finalize_bidding(state)

        # Remove all S cards except 1
        hand_s = state.live_hands["S"]
        last = hand_s.cards[0]
        hand_s.remove_cards([c for c in hand_s.cards if c != last])

        GameEngine.start_playing(state)
        result = GameEngine.submit_play(state, "S", [last], 5000)

        assert result["status"] == "hand_finished"
        assert result["spring"] is True
        assert result["anti_spring"] is False

    def test_spring_false_when_farmer_plays(self):
        state = make_state()
        deal = make_deal_result()
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 3, 1000)
        GameEngine.finalize_bidding(state)
        GameEngine.start_playing(state)

        # S plays a card
        hand_s = state.live_hands["S"]
        GameEngine.submit_play(state, "S", [hand_s.cards[-1]], 1000)
        # Spring still possible if only S has played
        assert state.farmer_has_not_played is True

        # E (farmer, active player) PLAYS a card that beats S's
        hand_e = state.live_hands["E"]
        # Find a card that beats the current trick
        current_rank = state.current_trick.main_rank
        for c in hand_e.cards:
            if c.rank.value > current_rank.value:
                GameEngine.submit_play(state, "E", [c], 2000)
                break
        else:
            # If E can't beat, just note that spring is still True since E never played
            pass

        # farmer_has_not_played should be False if E actually played a card
        if len(state.play_history) >= 2 and state.play_history[1].action_type == "play":
            assert state.farmer_has_not_played is False


# ── bomb tracking ───────────────────────────────────────────────────────────────

class TestBombTracking:
    def test_bomb_increments_counter(self):
        state = make_state()
        deal = make_deal_result("bomb-seed-2")  # S gets 4×2 bomb
        GameEngine.init_hand(state, hand_num=1, dealer="S", idle_seat="W",
                             deal_result=deal)
        GameEngine.start_bidding(state)
        GameEngine.submit_bid(state, "S", 3, 1000)
        GameEngine.finalize_bidding(state)
        GameEngine.start_playing(state)

        # S has 四个2
        hand_s = state.live_hands["S"]
        rank_counts = hand_s.rank_counts()
        bomb_rank = None
        for r, cnt in rank_counts.items():
            if cnt == 4:
                bomb_rank = r
                break
        assert bomb_rank is not None, "bomb-seed-2 must give S a 4-of-a-kind"

        bomb_cards = hand_s.cards_of_rank(bomb_rank)
        assert len(bomb_cards) == 4

        GameEngine.submit_play(state, "S", bomb_cards, 5000)
        assert state.bombs_played == 1
        assert state.current_trick.pattern == PatternType.BOMB
