"""Replay routes — hand history queries for post-match review."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["replay"])


def _repo(request: Request):
    return request.app.state.db_repo


def _build_table_detail(
    repo,
    table_hand: dict,
    match_id: str,
    hand_num: int,
    dealer: str,
    idle_seat: str,
    seed: str,
) -> dict[str, Any]:
    """Build the full table detail response for a single table_hand."""
    th_id = table_hand["id"]
    table = table_hand["table"]

    # Get participants for this match
    participants = repo.get_participants(match_id)
    agent_map: dict[str, dict] = {}
    for p in participants:
        player = repo.get_player(p["player_id"])
        if player:
            agent_map[p["player_id"]] = player
        # Determine seat on this table
    seat_agent: dict[str, str] = {}
    seat_team: dict[str, str] = {}
    for p in participants:
        seat_key = f"seat_table_{table.lower()}"
        seat = p.get(seat_key)
        if seat:
            seat_agent[seat] = p["player_id"]
            seat_team[seat] = p["team"]

    # Determine roles
    landlord_seat = table_hand.get("landlord_seat")
    idle_participation = table_hand.get("idle_participation")
    if isinstance(idle_participation, str) and idle_participation:
        try:
            idle_participation = json.loads(idle_participation)
        except json.JSONDecodeError:
            idle_participation = {}
    # Double-encoded JSON edge case (e.g. '"{}"' becomes the string '{}')
    if isinstance(idle_participation, str):
        try:
            idle_participation = json.loads(idle_participation)
        except json.JSONDecodeError:
            idle_participation = {}
    if not isinstance(idle_participation, dict):
        idle_participation = {}

    # Build players dict
    players: dict[str, dict[str, str]] = {}
    for seat in ["S", "E", "N", "W"]:
        aid = seat_agent.get(seat, "")
        team = seat_team.get(seat, "")
        role = "idle" if seat == idle_seat else "farmer"
        if seat == landlord_seat:
            role = "landlord"
        if idle_participation and idle_participation.get("triggered"):
            if seat == idle_participation.get("replaced_by_idle"):
                role = "farmer"
            if seat == idle_participation.get("original_farmer"):
                role = "idle"
        players[seat] = {
            "agent_id": aid,
            "team": team,
            "role": role,
        }

    # Bidding
    bidding_records = repo.get_bidding_records(th_id)
    bidding = [
        {"seat": r["seat"], "bid": r["bid"], "timestamp_ms": r["timestamp_ms"]}
        for r in bidding_records
    ]

    # Play history
    play_actions = repo.get_play_actions_for_table(th_id)
    play_history = []
    for a in play_actions:
        cards = a.get("cards")
        if isinstance(cards, str):
            cards = json.loads(cards)
        play_history.append({
            "round": a["round"],
            "sub_round": a["sub_round"],
            "seat": a["seat"],
            "action": {
                "type": a["action_type"],
                "cards": cards if a["action_type"] == "play" else None,
                "pattern": a.get("pattern") if a["action_type"] == "play" else None,
            },
            "timestamp_ms": a["timestamp_ms"],
        })

    # Initial hands
    initial_hands = repo.get_initial_hands_for_table(th_id)

    # Remaining hands
    remaining_hands = repo.get_remaining_hands_for_table(th_id)

    # Result
    result = None
    if table_hand.get("status") in ("finished",):
        result = {
            "winner_team": table_hand.get("winner_team"),
            "winner_role": table_hand.get("winner_role"),
            "base_score": table_hand.get("base_score"),
            "multiplier": table_hand.get("multiplier"),
            "final_score": table_hand.get("final_score"),
            "bombs_played": table_hand.get("bombs_played", 0),
            "spring": bool(table_hand.get("spring")),
            "anti_spring": bool(table_hand.get("anti_spring")),
        }

    # Agent thoughts
    thoughts = repo.get_agent_thoughts_for_table(th_id)
    agent_thoughts = []
    for t in thoughts:
        decision = t.get("decision", "{}")
        if isinstance(decision, str):
            try:
                decision = json.loads(decision)
            except json.JSONDecodeError:
                decision = {"raw": decision}
        agent_thoughts.append({
            "seat": t["seat"],
            "phase": t["phase"],
            "round": t.get("round"),
            "sub_round": t.get("sub_round"),
            "timestamp_ms": t["timestamp_ms"],
            "reasoning": t["reasoning"],
            "decision": decision,
        })

    # Reflections
    reflection_rows = repo.get_reflections_for_table(th_id)
    reflections = []
    for r in reflection_rows:
        reflections.append({
            "player_id": r["player_id"],
            "seat": r["seat"],
            "actual_role": r["actual_role"],
            "reflection": r["reflection"],
            "short_term_memory": r["short_term_memory"],
        })

    # Idle participation detail
    idle_detail = {
        "triggered": False,
        "idle_seat": idle_seat,
        "reason": "No idle participation triggered",
    }
    if idle_participation and isinstance(idle_participation, dict):
        idle_detail = {
            "triggered": idle_participation.get("triggered", False),
            "original_farmer": idle_participation.get("original_farmer", idle_seat),
            "replaced_by_idle": idle_participation.get("replaced_by_idle", ""),
            "reason": idle_participation.get("reason", ""),
        }

    return {
        "table_id": table,
        "players": players,
        "bidding": bidding,
        "landlord": landlord_seat,
        "final_bid": table_hand.get("final_bid", 0),
        "dizhu_cards": _parse_cards_str(table_hand.get("dizhu_cards", "[]")),
        "initial_hands": initial_hands,
        "play_history": play_history,
        "result": result,
        "remaining_hands": remaining_hands,
        "agent_thoughts": agent_thoughts,
        "reflections": reflections,
        "idle_participation": idle_detail,
    }


def _parse_cards_str(cards_str: str) -> list[str]:
    """Parse cards from DB storage format (comma-separated or JSON array)."""
    if not cards_str or cards_str == "[]":
        return []
    if cards_str.startswith("["):
        return json.loads(cards_str)
    # comma-separated format
    return [c.strip() for c in cards_str.split(",") if c.strip()]


@router.get("/matches/{match_id}/hands")
async def list_hand_summaries(match_id: str, request: Request):
    """Get summaries of all hands in a match, with AB table diff results."""
    repo = _repo(request)
    match = repo.get_match(match_id)
    if not match:
        raise HTTPException(404, detail={"error": {"code": "MATCH_NOT_FOUND", "message": f"Match not found: {match_id}"}})

    hands = repo.get_hands_for_match(match_id)
    participants = repo.get_participants(match_id)

    # Build player_id → name map
    agent_names: dict[str, str] = {}
    for p in participants:
        player = repo.get_player(p["player_id"])
        agent_names[p["player_id"]] = player["display_name"] if player else p["player_id"]

    total_hands = len(hands)
    hand_summaries = []
    running_red = 0
    running_blue = 0

    for h in hands:
        hand_num = h["hand_num"]
        table_hands = repo.get_table_hands_for_hand(h["id"])

        table_a_data = None
        table_b_data = None

        for th in table_hands:
            entry = _build_table_summary(th, participants, agent_names)
            if th["table"] == "A":
                table_a_data = entry
            else:
                table_b_data = entry

        # Diff result
        hand_diff = {"red_diff": 0, "running_total": {"red": running_red, "blue": running_blue}}
        if h.get("status") == "finished":
            red_diff = h.get("diff_score_red", 0) or 0
            blue_diff = h.get("diff_score_blue", 0) or 0
            running_red += red_diff
            running_blue += blue_diff
            hand_diff = {
                "red_diff": red_diff,
                "blue_diff": blue_diff,
                "running_total": {"red": running_red, "blue": running_blue},
            }

        hand_summaries.append({
            "hand_num": hand_num,
            "dealer": h["dealer"],
            "table_a": table_a_data,
            "table_b": table_b_data,
            "diff_result": hand_diff,
        })

    return {
        "match_id": match_id,
        "total_hands": total_hands,
        "hands": hand_summaries,
    }


def _build_table_summary(
    table_hand: dict,
    participants: list[dict],
    agent_names: dict[str, str],
) -> dict[str, Any]:
    """Build a compact table summary for the hands list."""
    th_id = table_hand["id"]
    table = table_hand["table"]

    # Find landlord player
    landlord_seat = table_hand.get("landlord_seat", "")
    landlord_agent = ""
    for p in participants:
        if p.get(f"seat_table_{table.lower()}") == landlord_seat:
            landlord_agent = p["player_id"]
            break

    return {
        "landlord_seat": landlord_seat,
        "landlord_agent": landlord_agent,
        "bid": table_hand.get("final_bid", 0),
        "score": {
            "red": table_hand.get("final_score", 0) if table_hand.get("winner_team") == "red" else 0,
            "blue": table_hand.get("final_score", 0) if table_hand.get("winner_team") == "blue" else 0,
        },
        "bombs_played": table_hand.get("bombs_played", 0),
        "spring": bool(table_hand.get("spring")),
        "void": bool(table_hand.get("void")),
    }


@router.get("/matches/{match_id}/hands/{hand_num}")
async def get_hand_detail(match_id: str, hand_num: int, request: Request):
    """Get full detail for a single hand, including both tables."""
    repo = _repo(request)
    match = repo.get_match(match_id)
    if not match:
        raise HTTPException(404, detail={"error": {"code": "MATCH_NOT_FOUND", "message": f"Match not found: {match_id}"}})

    hand = repo.get_hand_by_num(match_id, hand_num)
    if not hand:
        raise HTTPException(404, detail={"error": {"code": "HAND_NOT_FOUND", "message": f"Hand {hand_num} not found"}})

    table_hands = repo.get_table_hands_for_hand(hand["id"])
    if not table_hands:
        raise HTTPException(404, detail={"error": {"code": "HAND_NOT_FOUND", "message": f"No table hands found for hand {hand_num}"}})

    table_a_detail = None
    table_b_detail = None

    for th in table_hands:
        detail = _build_table_detail(
            repo, th, match_id, hand_num,
            hand["dealer"], hand["idle_seat"], hand["seed"],
        )
        if th["table"] == "A":
            table_a_detail = detail
        else:
            table_b_detail = detail

    # Diff score
    red_diff = hand.get("diff_score_red", 0) or 0
    blue_diff = hand.get("diff_score_blue", 0) or 0

    return {
        "hand_num": hand_num,
        "dealer": hand["dealer"],
        "seed": hand["seed"],
        "idle_seat": hand["idle_seat"],
        "table_a": table_a_detail,
        "table_b": table_b_detail,
        "diff_score": {
            "red_net": red_diff,
            "blue_net": blue_diff,
            "diff": max(red_diff, blue_diff),
            "capped": bool(hand.get("diff_capped")),
        },
    }


@router.get("/matches/{match_id}/hands/{hand_num}/table/{table_id}")
async def get_single_table_hand(
    match_id: str, hand_num: int, table_id: str, request: Request,
):
    """Get detailed replay data for a single table in a single hand."""
    if table_id not in ("a", "b", "A", "B"):
        raise HTTPException(400, detail={"error": {"code": "INVALID_REQUEST", "message": "table must be 'a' or 'b'"}})
    table_id = table_id.upper()

    repo = _repo(request)
    match = repo.get_match(match_id)
    if not match:
        raise HTTPException(404, detail={"error": {"code": "MATCH_NOT_FOUND", "message": f"Match not found: {match_id}"}})

    hand = repo.get_hand_by_num(match_id, hand_num)
    if not hand:
        raise HTTPException(404, detail={"error": {"code": "HAND_NOT_FOUND", "message": f"Hand {hand_num} not found"}})

    table_hands = repo.get_table_hands_for_hand(hand["id"])
    target_th = None
    for th in table_hands:
        if th["table"] == table_id:
            target_th = th
            break

    if target_th is None:
        raise HTTPException(404, detail={"error": {"code": "HAND_NOT_FOUND", "message": f"Table {table_id} data not found for hand {hand_num}"}})

    return _build_table_detail(
        repo, target_th, match_id, hand_num,
        hand["dealer"], hand["idle_seat"], hand["seed"],
    )
