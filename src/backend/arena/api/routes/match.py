"""Match CRUD + control routes — create, list, get, start, pause, resume, delete."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from ...tournament.match import MatchConfig, MatchRunner
from ...tournament.seating import MatchSeating, assign_seating
from ...engine.timeout import TimeoutConfig

router = APIRouter(prefix="/matches", tags=["matches"])


def _repo(request: Request):
    return request.app.state.db_repo


def _match_config_from_dict(d: dict) -> MatchConfig:
    """Build MatchConfig from request JSON, applying server defaults."""
    from ...config.settings import settings

    return MatchConfig(
        total_hands=d.get("total_hands", settings.total_hands),
        ko_enabled=d.get("ko_enabled", settings.ko_enabled),
        seed=d.get("seed", ""),
        timeout_config=TimeoutConfig(
            bidding_seconds=d.get(
                "timeout_bidding_seconds", settings.bidding_timeout_seconds
            ),
            individual_play_seconds=d.get(
                "timeout_individual_seconds", settings.individual_play_timeout_seconds
            ),
            team_pool_seconds=d.get(
                "timeout_team_seconds", settings.team_pool_timeout_seconds
            ),
        ),
    )


def _match_row_to_summary(row: dict) -> dict[str, Any]:
    """Convert a DB match row into the API summary format."""
    config = row.get("config")
    if isinstance(config, str):
        config = json.loads(config)
    total_hands = config.get("total_hands", 0) if config else 0
    return {
        "id": row["id"],
        "name": row["name"],
        "status": row["status"],
        "created_at": row["created_at"],
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "current_hand": row.get("current_hand", 0),
        "total_hands": total_hands,
        "score": {
            "red": row.get("score_red", 0),
            "blue": row.get("score_blue", 0),
        },
        "ko_result": row.get("ko_result"),
    }


def _match_row_to_detail(row: dict, participants: list[dict]) -> dict[str, Any]:
    """Convert a DB match row into the API detail response."""
    config = row.get("config")
    if isinstance(config, str):
        config = json.loads(config)

    teams: dict[str, dict[str, Any]] = {"red": {"name": "", "agents": []}, "blue": {"name": "", "agents": []}}
    seating: dict[str, dict[str, str]] = {"table_a": {}, "table_b": {}}

    for p in participants:
        team = p["team"]
        player_id = p["player_id"]
        teams[team]["agents"].append(player_id)
        if p.get("seat_table_a"):
            seating["table_a"][p["seat_table_a"]] = player_id
        if p.get("seat_table_b"):
            seating["table_b"][p["seat_table_b"]] = player_id

    # Build score history from hands
    score_history: list[dict[str, Any]] = []

    return {
        "id": row["id"],
        "name": row["name"],
        "status": row["status"],
        "config": config if config else {},
        "teams": teams,
        "seating": seating,
        "score": {
            "red": row.get("score_red", 0),
            "blue": row.get("score_blue", 0),
        },
        "score_history": score_history,
        "current_hand": row.get("current_hand", 0),
        "created_at": row["created_at"],
        "started_at": row.get("started_at"),
        "paused_at": row.get("paused_at"),
        "finished_at": row.get("finished_at"),
    }


@router.get("")
async def list_matches(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
):
    """List matches with optional status filter and pagination."""
    if status and status not in ("created", "running", "paused", "finished"):
        raise HTTPException(400, detail={"error": {"code": "INVALID_REQUEST", "message": f"Invalid status: {status}"}})
    repo = _repo(request)
    matches, total = repo.list_matches(status=status, page=page, page_size=page_size)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "matches": [_match_row_to_summary(m) for m in matches],
    }


@router.post("", status_code=201)
async def create_match(request: Request):
    """Create a new match. Assigns seating from provided agent IDs."""
    body = await request.json()
    repo = _repo(request)

    name = body.get("name", "")
    if not name:
        raise HTTPException(400, detail={"error": {"code": "INVALID_REQUEST", "message": "name is required"}})

    config_dict = body.get("config", {})
    match_config = _match_config_from_dict(config_dict)

    # Validate teams
    team_red = body.get("team_red", {})
    team_blue = body.get("team_blue", {})
    red_agents = team_red.get("agents", [])
    blue_agents = team_blue.get("agents", [])

    if len(red_agents) != 4 or len(blue_agents) != 4:
        raise HTTPException(
            400,
            detail={
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "Each team must have exactly 4 agent IDs",
                }
            },
        )

    # Verify all players exist
    for pid in red_agents + blue_agents:
        if repo.get_player(pid) is None:
            raise HTTPException(404, detail={"error": {"code": "PLAYER_NOT_FOUND", "message": f"Player not found: {pid}"}})

    # Assign seating
    try:
        seating = assign_seating(red_agents, blue_agents)
    except ValueError as e:
        raise HTTPException(400, detail={"error": {"code": "INVALID_REQUEST", "message": str(e)}})

    # Create match in DB
    match_id = repo.create_match(name, config_dict, match_config.seed or "")

    # Persist participants
    for seat, aid in seating.table_a.items():
        repo.add_participant(match_id, aid, seating.table_a_teams[seat], seat_table_a=seat)
    for seat, aid in seating.table_b.items():
        repo.add_participant(match_id, aid, seating.table_b_teams[seat], seat_table_b=seat)

    match = repo.get_match(match_id)
    return {
        "id": match_id,
        "name": name,
        "status": "created",
        "created_at": match["created_at"] if match else "",
        "seating": {
            "table_a": seating.table_a,
            "table_b": seating.table_b,
        },
    }


@router.get("/{match_id}")
async def get_match(match_id: str, request: Request):
    """Get full match details."""
    repo = _repo(request)
    match = repo.get_match(match_id)
    if not match:
        raise HTTPException(404, detail={"error": {"code": "MATCH_NOT_FOUND", "message": f"Match not found: {match_id}"}})

    participants = repo.get_participants(match_id)

    # Build score history from hands
    hands = repo.get_hands_for_match(match_id)
    score_history: list[dict[str, Any]] = []
    for h in hands:
        if h.get("status") == "finished":
            score_history.append({
                "hand": h["hand_num"],
                "red_diff": h.get("diff_score_red", 0),
                "blue_diff": h.get("diff_score_blue", 0),
                "running_total": {
                    "red": match.get("score_red", 0) if h["hand_num"] == match.get("current_hand", 0) else 0,
                    "blue": match.get("score_blue", 0) if h["hand_num"] == match.get("current_hand", 0) else 0,
                },
            })

    # Build running totals from hands
    running_red = 0
    running_blue = 0
    for entry in score_history:
        running_red += entry["red_diff"]
        running_blue += entry["blue_diff"]
        entry["running_total"] = {"red": running_red, "blue": running_blue}

    detail = _match_row_to_detail(match, participants)
    detail["score_history"] = score_history

    # Add time remaining if match is running
    if match["status"] == "running":
        detail["time_remaining"] = {
            "table_a": {"red_team_ms": 0, "blue_team_ms": 0},
            "table_b": {"red_team_ms": 0, "blue_team_ms": 0},
        }

    return detail


@router.post("/{match_id}/start")
async def start_match(match_id: str, request: Request, background_tasks: BackgroundTasks):
    """Start a match — runs the full match async in a background task."""
    repo = _repo(request)
    match = repo.get_match(match_id)
    if not match:
        raise HTTPException(404, detail={"error": {"code": "MATCH_NOT_FOUND", "message": f"Match not found: {match_id}"}})
    if match["status"] != "created":
        raise HTTPException(400, detail={"error": {"code": "MATCH_ALREADY_STARTED", "message": "Match already started"}})

    # Load players — each participant references a player ID
    from ...agent.llm_agent import LLMAgent
    from ...agent.random_agent import RandomAgent
    from ...llm.base import AbstractLLMProvider

    player_ids_in_match: set[str] = set()
    for p in repo.get_participants(match_id):
        player_ids_in_match.add(p["player_id"])

    # Build Agent instances — load player + config, create provider, instantiate agent
    agents_dict: dict[str, Any] = {}
    for pid in player_ids_in_match:
        player_row = repo.get_player_with_config(pid)
        if player_row is None:
            raise HTTPException(500, detail={"error": {"code": "INTERNAL_ERROR", "message": f"Player row not found: {pid}"}})

        provider_name = player_row["provider"]
        if provider_name == "random":
            agents_dict[pid] = RandomAgent(agent_id=pid)
        else:
            api_key = player_row.get("api_key", "")
            base_url = player_row.get("base_url")
            if provider_name == "claude":
                from ...llm.claude import ClaudeProvider
                provider: AbstractLLMProvider = ClaudeProvider(
                    model=player_row["model"], api_key=api_key,
                )
            else:
                from ...llm.openai import OpenAIProvider
                provider = OpenAIProvider(
                    model=player_row["model"], api_key=api_key,
                    base_url=base_url,
                )
            agents_dict[pid] = LLMAgent(
                agent_id=pid,
                provider=provider,
                long_term_memory=player_row.get("long_term_memory", ""),
                system_prompt_override=player_row.get("system_prompt"),
            )

    # Build seating from DB participants
    participants = repo.get_participants(match_id)
    table_a: dict[str, str] = {}
    table_b: dict[str, str] = {}
    table_a_teams: dict[str, str] = {}
    table_b_teams: dict[str, str] = {}
    agent_seats: dict[str, tuple[str, str]] = {}
    agent_teams: dict[str, str] = {}

    for p in participants:
        if p.get("seat_table_a"):
            table_a[p["seat_table_a"]] = p["player_id"]
            table_a_teams[p["seat_table_a"]] = p["team"]
            agent_seats[p["player_id"]] = ("A", p["seat_table_a"])
            agent_teams[p["player_id"]] = p["team"]
        if p.get("seat_table_b"):
            table_b[p["seat_table_b"]] = p["player_id"]
            table_b_teams[p["seat_table_b"]] = p["team"]
            agent_seats[p["player_id"]] = ("B", p["seat_table_b"])
            agent_teams[p["player_id"]] = p["team"]

    seating = MatchSeating(
        table_a=table_a,
        table_b=table_b,
        table_a_teams=table_a_teams,
        table_b_teams=table_b_teams,
        agent_seats=agent_seats,
        agent_teams=agent_teams,
    )

    config = match.get("config")
    if isinstance(config, str):
        config = json.loads(config)
    match_config = _match_config_from_dict(config if config else {})

    runner = MatchRunner(
        config=match_config,
        seating=seating,
        agents=agents_dict,
        db_repo=repo,
        match_name=match["name"],
    )
    # Override the match_id so it uses the existing DB match
    runner.match_id = match_id
    runner.table_a.match_id = match_id
    runner.table_b.match_id = match_id
    runner._db_initialized = True  # match + participants already in DB via API

    # Store the runner on app state so WebSocket handler can access it
    if not hasattr(request.app.state, "active_matches"):
        request.app.state.active_matches = {}
    request.app.state.active_matches[match_id] = runner

    async def run_and_cleanup():
        try:
            await runner.run()
        finally:
            if hasattr(request.app.state, "active_matches"):
                request.app.state.active_matches.pop(match_id, None)

    background_tasks.add_task(run_and_cleanup)

    return {"status": "running", "started_at": MatchRunner._now_iso()}


@router.post("/{match_id}/pause")
async def pause_match(match_id: str, request: Request):
    """Request a pause. Takes effect after current play completes."""
    repo = _repo(request)
    match = repo.get_match(match_id)
    if not match:
        raise HTTPException(404, detail={"error": {"code": "MATCH_NOT_FOUND", "message": f"Match not found: {match_id}"}})
    if match["status"] != "running":
        raise HTTPException(400, detail={"error": {"code": "INVALID_STATE_TRANSITION", "message": "Match is not running"}})

    runner = getattr(request.app.state, "active_matches", {}).get(match_id)
    if runner is None:
        raise HTTPException(400, detail={"error": {"code": "INVALID_STATE_TRANSITION", "message": "No active runner found — match may be running externally"}})

    runner.request_pause()
    repo.update_match_status(match_id, "paused")
    return {
        "status": "paused",
        "paused_at": MatchRunner._now_iso(),
        "paused_at_hand": match.get("current_hand", 0),
        "reason": "user_requested",
    }


@router.post("/{match_id}/resume")
async def resume_match(match_id: str, request: Request):
    """Resume a paused match."""
    repo = _repo(request)
    match = repo.get_match(match_id)
    if not match:
        raise HTTPException(404, detail={"error": {"code": "MATCH_NOT_FOUND", "message": f"Match not found: {match_id}"}})
    if match["status"] != "paused":
        raise HTTPException(400, detail={"error": {"code": "INVALID_STATE_TRANSITION", "message": "Match is not paused"}})

    runner = getattr(request.app.state, "active_matches", {}).get(match_id)
    if runner is None:
        raise HTTPException(400, detail={"error": {"code": "INVALID_STATE_TRANSITION", "message": "No active runner found"}})

    runner.pause.resume()
    runner.resume_tables()
    repo.update_match_status(match_id, "running")
    return {
        "status": "running",
        "resumed_at": MatchRunner._now_iso(),
    }


@router.delete("/{match_id}", status_code=204)
async def delete_match(match_id: str, request: Request):
    """Delete a match. Running matches must be stopped first."""
    repo = _repo(request)
    match = repo.get_match(match_id)
    if not match:
        raise HTTPException(404, detail={"error": {"code": "MATCH_NOT_FOUND", "message": f"Match not found: {match_id}"}})
    if match["status"] == "running":
        raise HTTPException(400, detail={"error": {"code": "INVALID_STATE_TRANSITION", "message": "Cannot delete a running match — stop it first"}})

    if not repo.delete_match_from_db(match_id):
        raise HTTPException(500, detail={"error": {"code": "INTERNAL_ERROR", "message": "Failed to delete match"}})
