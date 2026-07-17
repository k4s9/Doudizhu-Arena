"""Agent/Config/Player management routes.

Endpoints:
  GET    /agents          — list all players (backward-compatible alias)
  POST   /agents          — create a player from a config
  POST   /agents/reload   — reload configs + default_players from agents.yaml

Player configs:
  GET    /configs         — list all player configs
  POST   /configs         — create a player config
  POST   /configs/reload  — reload configs from agents.yaml

Players:
  GET    /players         — list all players (with stats)
  POST   /players         — create a new player from a config
  GET    /players/{id}    — get a player with config details
  PUT    /players/{id}    — update a player
  DELETE /players/{id}    — delete a player
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ...agent.loader import load_agents_from_yaml

logger = logging.getLogger("arena.api.agent")

router = APIRouter(prefix="/agents", tags=["agents"])


def _repo(request: Request):
    return request.app.state.db_repo


# ── backward-compatible /agents endpoints ─────────────────────────────────────

@router.get("")
async def list_agents(request: Request):
    """List all players (backward compatible — returns players, not configs)."""
    repo = _repo(request)
    players = repo.list_players_with_config()
    return {
        "agents": [
            {
                "id": p["id"],
                "name": p["display_name"],
                "provider": p.get("provider", ""),
                "model": p.get("model", ""),
                "config_name": p.get("config_name", ""),
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
async def create_agent(request: Request):
    """Create a new player from a config. Accepts config_name to look up config."""
    body = await request.json()
    repo = _repo(request)

    display_name = body.get("name") or body.get("display_name", "")
    if not display_name:
        raise HTTPException(400, detail={"error": {"code": "INVALID_REQUEST", "message": "name (display_name) is required"}})

    # Allow config lookup by name or id
    config_name = body.get("config_name", "")
    config_id = body.get("config_id", "")
    config = None
    if config_id:
        config = repo.get_player_config(config_id)
    elif config_name:
        config = repo.get_player_config_by_name(config_name)
    else:
        # Legacy: create config directly from body fields
        provider = body.get("provider", "")
        model = body.get("model", "")
        api_key = body.get("api_key", "")
        if provider and model:
            config_id = repo.create_player_config(
                name=display_name,
                provider=provider,
                model=model,
                api_key=api_key,
                base_url=body.get("base_url"),
                system_prompt=body.get("system_prompt"),
            )
            config = repo.get_player_config(config_id)

    if config is None and not config_id:
        raise HTTPException(400, detail={"error": {"code": "INVALID_REQUEST", "message": "config_name, config_id, or provider+model required"}})

    if config is None:
        raise HTTPException(404, detail={"error": {"code": "CONFIG_NOT_FOUND", "message": f"Config not found: {config_id}"}})

    player_id = repo.create_player(
        config_id=config["id"],
        display_name=display_name,
    )

    player = repo.get_player_with_config(player_id)
    return {
        "id": player["id"],
        "name": player["display_name"],
        "provider": player.get("provider", ""),
        "model": player.get("model", ""),
        "config_name": player.get("config_name", ""),
        "matches_played": player.get("matches_played", 0),
        "created_at": player["created_at"],
    }


@router.post("/reload")
async def reload_agents(request: Request):
    """Reload player configs and default_players from agents.yaml.

    Creates new configs and players as needed, updates existing configs.
    """
    repo = _repo(request)
    try:
        player_ids = load_agents_from_yaml(repo)
        logger.info("Agent reload complete: %d players synced", len(player_ids))
        return {
            "status": "ok",
            "player_count": len(player_ids),
            "player_ids": player_ids,
        }
    except FileNotFoundError as e:
        raise HTTPException(500, detail={"error": {"code": "RELOAD_FAILED", "message": str(e)}})
    except ValueError as e:
        raise HTTPException(400, detail={"error": {"code": "INVALID_YAML", "message": str(e)}})


@router.put("/{agent_id}")
async def update_agent(agent_id: str, request: Request):
    """Update a player (backward compatible — agent_id = player_id)."""
    body = await request.json()
    repo = _repo(request)

    player = repo.get_player(agent_id)
    if not player:
        raise HTTPException(404, detail={"error": {"code": "PLAYER_NOT_FOUND", "message": f"Player not found: {agent_id}"}})

    updates = {}
    for field in ("display_name", "long_term_memory"):
        if field in body:
            updates[field] = body[field]

    if updates:
        repo.update_player_by_id(agent_id, **updates)

    updated = repo.get_player_with_config(agent_id)
    return {
        "id": updated["id"],
        "name": updated["display_name"],
        "provider": updated.get("provider", ""),
        "model": updated.get("model", ""),
        "config_name": updated.get("config_name", ""),
        "matches_played": updated.get("matches_played", 0),
        "long_term_memory": updated.get("long_term_memory", ""),
        "created_at": updated["created_at"],
        "updated_at": updated["updated_at"],
    }


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str, request: Request):
    """Delete a player."""
    repo = _repo(request)
    player = repo.get_player(agent_id)
    if not player:
        raise HTTPException(404, detail={"error": {"code": "PLAYER_NOT_FOUND", "message": f"Player not found: {agent_id}"}})

    if not repo.delete_player(agent_id):
        raise HTTPException(500, detail={"error": {"code": "INTERNAL_ERROR", "message": "Failed to delete player"}})
