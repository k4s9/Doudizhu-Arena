"""Player config management routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ...agent.loader import load_agents_from_yaml

logger = logging.getLogger("arena.api.configs")

router = APIRouter(prefix="/configs", tags=["configs"])


def _repo(request: Request):
    return request.app.state.db_repo


@router.get("")
async def list_configs(request: Request):
    """List all player configs."""
    repo = _repo(request)
    configs = repo.list_player_configs()
    return {
        "configs": [
            {
                "id": c["id"],
                "name": c["name"],
                "provider": c["provider"],
                "model": c["model"],
                "base_url": c.get("base_url"),
                "system_prompt": c.get("system_prompt"),
                "player_count": repo.count_players_for_config(c["id"]),
                "created_at": c["created_at"],
                "updated_at": c["updated_at"],
            }
            for c in configs
        ]
    }


@router.post("", status_code=201)
async def create_config(request: Request):
    """Create a new player config."""
    body = await request.json()
    repo = _repo(request)

    name = body.get("name", "")
    if not name:
        raise HTTPException(400, detail={"error": {"code": "INVALID_REQUEST", "message": "name is required"}})

    provider = body.get("provider", "")
    if provider not in ("claude", "openai", "random"):
        raise HTTPException(400, detail={"error": {"code": "INVALID_REQUEST", "message": "provider must be 'claude', 'openai', or 'random'"}})

    model = body.get("model", "")
    api_key = body.get("api_key", "")
    base_url = body.get("base_url")
    system_prompt = body.get("system_prompt")

    config_id = repo.create_player_config(
        name=name,
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        system_prompt=system_prompt,
    )

    config = repo.get_player_config(config_id)
    return {
        "id": config["id"],
        "name": config["name"],
        "provider": config["provider"],
        "model": config["model"],
        "base_url": config.get("base_url"),
        "system_prompt": config.get("system_prompt"),
        "created_at": config["created_at"],
    }


@router.post("/reload")
async def reload_configs(request: Request):
    """Reload configs and default_players from agents.yaml."""
    repo = _repo(request)
    try:
        player_ids = load_agents_from_yaml(repo)
        logger.info("Config reload complete: %d players synced", len(player_ids))
        return {
            "status": "ok",
            "player_count": len(player_ids),
            "player_ids": player_ids,
        }
    except FileNotFoundError as e:
        raise HTTPException(500, detail={"error": {"code": "RELOAD_FAILED", "message": str(e)}})
    except ValueError as e:
        raise HTTPException(400, detail={"error": {"code": "INVALID_YAML", "message": str(e)}})


@router.put("/{config_id}")
async def update_config(config_id: str, request: Request):
    """Update a player config."""
    body = await request.json()
    repo = _repo(request)

    config = repo.get_player_config(config_id)
    if not config:
        raise HTTPException(404, detail={"error": {"code": "CONFIG_NOT_FOUND", "message": f"Config not found: {config_id}"}})

    updates = {}
    for field in ("name", "model", "base_url", "system_prompt", "provider", "api_key"):
        if field in body:
            updates[field] = body[field]

    if updates:
        repo.update_player_config(config_id, **updates)

    updated = repo.get_player_config(config_id)
    return {
        "id": updated["id"],
        "name": updated["name"],
        "provider": updated["provider"],
        "model": updated["model"],
        "base_url": updated.get("base_url"),
        "system_prompt": updated.get("system_prompt"),
        "created_at": updated["created_at"],
        "updated_at": updated["updated_at"],
    }


@router.delete("/{config_id}", status_code=204)
async def delete_config(config_id: str, request: Request):
    """Delete a player config (only if no players reference it)."""
    repo = _repo(request)
    config = repo.get_player_config(config_id)
    if not config:
        raise HTTPException(404, detail={"error": {"code": "CONFIG_NOT_FOUND", "message": f"Config not found: {config_id}"}})

    player_count = repo.count_players_for_config(config_id)
    if player_count > 0:
        raise HTTPException(400, detail={"error": {"code": "CONFIG_IN_USE", "message": f"Cannot delete config with {player_count} players. Delete players first."}})

    if not repo.delete_player_config(config_id):
        raise HTTPException(500, detail={"error": {"code": "INTERNAL_ERROR", "message": "Failed to delete config"}})
