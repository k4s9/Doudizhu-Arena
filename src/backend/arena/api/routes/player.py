"""Player instance management routes.

A Player is an instance of a PlayerConfig with independent long-term memory and stats.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger("arena.api.players")

router = APIRouter(prefix="/players", tags=["players"])


def _repo(request: Request):
    return request.app.state.db_repo


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
