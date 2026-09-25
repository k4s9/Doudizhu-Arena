"""Canonical business state, excluding clocks, connections and random IDs."""
import hashlib
import json

RULES_VERSION = "duplicate-four-seat-v2-leader-must-play"


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def project(state):
    return {
        "hand_num": state.hand_num, "phase": state.phase.value,
        "hands": {s: [str(c) for c in h.cards] for s, h in sorted(state.live_hands.items())},
        "landlord": state.landlord, "effective_idle": state.effective_idle,
        "current_player": state.current_player, "current_high_bid": state.current_high_bid,
        "current_high_bidder": state.current_high_bidder, "final_bid": state.final_bid,
        "round": state.round_number, "seq": state.global_seq,
        "consecutive_passes": state.consecutive_passes,
        "current_trick": state.current_trick.display() if state.current_trick else None,
        "trick_leader": state.trick_leader, "winner_seat": state.winner_seat,
        "winner_team": state.winner_team,
        "bids": [{"seat": r.seat, "bid": r.bid} for r in state.bidding_history],
    }
