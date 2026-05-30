"""Agent management routes — list, create, update, delete agent configurations."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/agents", tags=["agents"])


def _repo(request: Request):
    return request.app.state.db_repo


@router.get("")
async def list_agents(request: Request):
    """List all registered agents."""
    repo = _repo(request)
    agents = repo.list_agents()
    return {
        "agents": [
            {
                "id": a["id"],
                "name": a["name"],
                "provider": a["provider"],
                "model": a["model"],
                "created_at": a["created_at"],
                "updated_at": a["updated_at"],
            }
            for a in agents
        ]
    }


@router.post("", status_code=201)
async def create_agent(request: Request):
    """Create a new agent configuration."""
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
    system_prompt_override = body.get("system_prompt_override")
    long_term_memory = body.get("long_term_memory", "")

    agent_id = repo.create_agent(
        name=name,
        provider=provider,
        model=model,
        api_key=api_key,
        system_prompt_override=system_prompt_override,
        long_term_memory=long_term_memory,
    )

    agent = repo.get_agent(agent_id)
    return {
        "id": agent["id"],
        "name": agent["name"],
        "provider": agent["provider"],
        "model": agent["model"],
        "system_prompt_override": agent.get("system_prompt_override"),
        "long_term_memory": agent.get("long_term_memory", ""),
        "created_at": agent["created_at"],
    }


@router.put("/{agent_id}")
async def update_agent(agent_id: str, request: Request):
    """Update an existing agent configuration."""
    body = await request.json()
    repo = _repo(request)

    agent = repo.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, detail={"error": {"code": "AGENT_NOT_FOUND", "message": f"Agent not found: {agent_id}"}})

    updates = {}
    for field in ("name", "model", "system_prompt_override", "provider"):
        if field in body:
            updates[field] = body[field]

    if updates:
        repo.update_agent_by_id(agent_id, **updates)

    updated = repo.get_agent(agent_id)
    return {
        "id": updated["id"],
        "name": updated["name"],
        "provider": updated["provider"],
        "model": updated["model"],
        "system_prompt_override": updated.get("system_prompt_override"),
        "long_term_memory": updated.get("long_term_memory", ""),
        "created_at": updated["created_at"],
        "updated_at": updated["updated_at"],
    }


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str, request: Request):
    """Delete an agent."""
    repo = _repo(request)
    agent = repo.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, detail={"error": {"code": "AGENT_NOT_FOUND", "message": f"Agent not found: {agent_id}"}})

    if not repo.delete_agent_from_db(agent_id):
        raise HTTPException(500, detail={"error": {"code": "INTERNAL_ERROR", "message": "Failed to delete agent"}})
