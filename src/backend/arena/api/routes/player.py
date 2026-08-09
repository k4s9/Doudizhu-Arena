"""Player instance management routes.

A Player is an instance of a PlayerConfig with independent long-term memory and stats.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger("arena.api.players")

router = APIRouter(prefix="/players", tags=["players"])


def _repo(request: Request):
    return request.app.state.db_repo


def _empty_role_stats() -> dict[str, int | float | None]:
    return {"hands": 0, "wins": 0, "losses": 0, "win_rate": None}


def _rate(wins: int, hands: int) -> float | None:
    if not hands:
        return None
    return round(wins / hands * 100, 1)


def _parse_idle_participation(value: Any) -> dict[str, Any]:
    """Parse the historical JSON formats used for idle-seat participation."""
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _role_for_record(record: dict[str, Any]) -> str:
    if record.get("void"):
        return "void"

    seat = record.get("seat", "")
    idle = _parse_idle_participation(record.get("idle_participation"))
    if idle.get("triggered"):
        if seat == idle.get("original_farmer"):
            return "idle"
        if seat == idle.get("replaced_by_idle"):
            return "farmer"

    if seat == record.get("landlord_seat"):
        return "landlord"
    if seat == record.get("idle_seat"):
        return "idle"
    return "farmer"


def build_player_statistics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate hand-level player metrics from completed table records."""
    stats: dict[str, Any] = {
        "total_hands": 0,
        "hands_played": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": None,
        "landlord": _empty_role_stats(),
        "farmer": _empty_role_stats(),
        "idle_hands": 0,
        "void_hands": 0,
        "score_total": 0,
        "score_history": [],
    }

    for record in records:
        role = _role_for_record(record)
        score_delta = (
            (record.get("diff_score_red", 0) or 0)
            if record.get("team") == "red"
            else (record.get("diff_score_blue", 0) or 0)
        )
        stats["total_hands"] += 1
        stats["score_total"] += score_delta

        outcome = "not_counted"
        if role == "void":
            stats["void_hands"] += 1
            outcome = "void"
        elif role == "idle":
            stats["idle_hands"] += 1
            outcome = "idle"
        else:
            won = record.get("winner_team") == record.get("team")
            role_stats = stats[role]
            stats["hands_played"] += 1
            role_stats["hands"] += 1
            if won:
                stats["wins"] += 1
                role_stats["wins"] += 1
                outcome = "win"
            else:
                stats["losses"] += 1
                role_stats["losses"] += 1
                outcome = "loss"

        stats["score_history"].append({
            "match_id": record["match_id"],
            "match_name": record["match_name"],
            "hand_num": record["hand_num"],
            "table": record["table_id"],
            "seat": record["seat"],
            "role": role,
            "outcome": outcome,
            "score_delta": score_delta,
            "cumulative_score": stats["score_total"],
            "table_score": record.get("final_score", 0) or 0,
            "is_tiebreaker": bool(record.get("is_tiebreaker")),
            "finished_at": record.get("hand_finished_at"),
        })

    stats["win_rate"] = _rate(stats["wins"], stats["hands_played"])
    for role in ("landlord", "farmer"):
        role_stats = stats[role]
        role_stats["win_rate"] = _rate(role_stats["wins"], role_stats["hands"])
    return stats


def _player_summary(player: dict[str, Any], statistics: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": player["id"],
        "display_name": player["display_name"],
        "config_name": player.get("config_name", ""),
        "provider": player.get("provider", ""),
        "model": player.get("model", ""),
        "statistics": statistics,
    }


@router.get("")
async def list_players(request: Request, config_id: str = ""):
    """List all players, optionally filtered by config_id. Includes config info."""
    repo = _repo(request)
    players = repo.list_players_with_config(config_id=config_id if config_id else None)
    return {
        "players": [
            {
                "id": p["id"],
                "display_name": p["display_name"],
                "config_id": p["config_id"],
                "config_name": p.get("config_name", ""),
                "provider": p.get("provider", ""),
                "model": p.get("model", ""),
                "matches_played": p.get("matches_played", 0),
                "matches_won": p.get("matches_won", 0),
                "total_score": p.get("total_score", 0),
                "created_at": p["created_at"],
                "updated_at": p["updated_at"],
            }
            for p in players
        ]
    }


@router.get("/leaderboard")
async def get_player_leaderboard(request: Request, sort: str = "win_rate"):
    """Return every player with fresh per-hand statistics for ranking."""
    if sort not in {"win_rate", "score"}:
        raise HTTPException(400, detail={"error": {"code": "INVALID_REQUEST", "message": "sort must be 'win_rate' or 'score'"}})

    repo = _repo(request)
    records_by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in repo.get_player_hand_records():
        records_by_player[record["player_id"]].append(record)

    players = [
        _player_summary(player, build_player_statistics(records_by_player[player["id"]]))
        for player in repo.list_players_with_config()
    ]
    if sort == "score":
        players.sort(key=lambda player: (-player["statistics"]["score_total"], player["display_name"]))
    else:
        players.sort(key=lambda player: (
            -(player["statistics"]["win_rate"] if player["statistics"]["win_rate"] is not None else -1),
            -player["statistics"]["hands_played"],
            player["display_name"],
        ))
    return {"players": players}


@router.post("", status_code=201)
async def create_player(request: Request):
    """Create a new player from an existing config."""
    body = await request.json()
    repo = _repo(request)

    # Accept config_id or config_name
    config_id = body.get("config_id", "")
    config_name = body.get("config_name", "")

    config = None
    if config_id:
        config = repo.get_player_config(config_id)
    elif config_name:
        config = repo.get_player_config_by_name(config_name)

    if config is None:
        raise HTTPException(400, detail={"error": {"code": "INVALID_REQUEST", "message": "config_id or config_name referencing an existing config is required"}})

    display_name = body.get("display_name", "")
    if not display_name:
        # Auto-generate: config name + incrementing number
        existing = repo.list_players(config_id=config["id"])
        display_name = f"{config['name']} #{len(existing) + 1}"

    player_id = repo.create_player(
        config_id=config["id"],
        display_name=display_name,
    )

    player = repo.get_player_with_config(player_id)
    return {
        "id": player["id"],
        "display_name": player["display_name"],
        "config_id": player["config_id"],
        "config_name": player.get("config_name", ""),
        "provider": player.get("provider", ""),
        "model": player.get("model", ""),
        "matches_played": player.get("matches_played", 0),
        "created_at": player["created_at"],
    }


@router.get("/{player_id}")
async def get_player(player_id: str, request: Request):
    """Get a single player with full config details."""
    repo = _repo(request)
    player = repo.get_player_with_config(player_id)
    if not player:
        raise HTTPException(404, detail={"error": {"code": "PLAYER_NOT_FOUND", "message": f"Player not found: {player_id}"}})

    statistics = build_player_statistics(repo.get_player_hand_records(player_id))
    memories = repo.get_agent_memories(player_id, memory_type="long_term")
    return {
        "id": player["id"],
        "display_name": player["display_name"],
        "config_id": player["config_id"],
        "config_name": player.get("config_name", ""),
        "provider": player.get("provider", ""),
        "model": player.get("model", ""),
        "base_url": player.get("base_url"),
        "system_prompt": player.get("system_prompt"),
        "long_term_memory": player.get("long_term_memory", ""),
        "matches_played": player.get("matches_played", 0),
        "matches_won": player.get("matches_won", 0),
        "total_score": player.get("total_score", 0),
        "statistics": statistics,
        "long_term_memory_history": [
            {
                "content": memory["content"],
                "match_id": memory.get("match_id"),
                "created_at": memory["created_at"],
            }
            for memory in memories
        ],
        "created_at": player["created_at"],
        "updated_at": player["updated_at"],
    }


@router.put("/{player_id}")
async def update_player(player_id: str, request: Request):
    """Update a player's display_name."""
    body = await request.json()
    repo = _repo(request)

    player = repo.get_player(player_id)
    if not player:
        raise HTTPException(404, detail={"error": {"code": "PLAYER_NOT_FOUND", "message": f"Player not found: {player_id}"}})

    updates = {}
    if "display_name" in body:
        updates["display_name"] = body["display_name"]

    if updates:
        repo.update_player_by_id(player_id, **updates)

    updated = repo.get_player_with_config(player_id)
    return {
        "id": updated["id"],
        "display_name": updated["display_name"],
        "config_id": updated["config_id"],
        "config_name": updated.get("config_name", ""),
        "matches_played": updated.get("matches_played", 0),
        "matches_won": updated.get("matches_won", 0),
        "total_score": updated.get("total_score", 0),
        "created_at": updated["created_at"],
        "updated_at": updated["updated_at"],
    }


@router.delete("/{player_id}", status_code=204)
async def delete_player(player_id: str, request: Request):
    """Delete a player."""
    repo = _repo(request)
    player = repo.get_player(player_id)
    if not player:
        raise HTTPException(404, detail={"error": {"code": "PLAYER_NOT_FOUND", "message": f"Player not found: {player_id}"}})

    if not repo.delete_player(player_id):
        raise HTTPException(500, detail={"error": {"code": "INTERNAL_ERROR", "message": "Failed to delete player"}})
