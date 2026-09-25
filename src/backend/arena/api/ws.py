"""WebSocket handler for real-time match streaming.

Implements the WS protocol defined in the API design plan:
- Client connects to /ws/match/{match_id}
- Server sends match_state (full snapshot) on connect
- Server continuously polls MatchEventBus and streams events to clients
- Client can send: subscribe, pause_request, resume_request, ping
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .event_bus import MatchEventBus

logger = logging.getLogger(__name__)

router = APIRouter()

# Global registry of active WebSocket connections per match
_connections: dict[str, set[WebSocket]] = {}


def _get_repo(ws: WebSocket):
    return ws.app.state.db_repo


def _get_active_matches(ws: WebSocket) -> dict:
    if not hasattr(ws.app.state, "active_matches"):
        ws.app.state.active_matches = {}
    return ws.app.state.active_matches


async def broadcast(match_id: str, event: dict[str, Any]) -> None:
    """Send an event to all connected clients for a given match."""
    if match_id not in _connections:
        return
    dead: list[WebSocket] = []
    message = json.dumps(event, ensure_ascii=False)
    for ws in list(_connections[match_id]):
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _connections[match_id].discard(ws)
    if not _connections[match_id]:
        del _connections[match_id]


async def _send_error(ws: WebSocket, code: str, message: str, table: str = "", hand_num: int = 0, seat: str = "") -> None:
    """Send an error event to a single WebSocket client."""
    await ws.send_json({
        "type": "error",
        "payload": {
            "code": code,
            "table": table,
            "hand_num": hand_num,
            "seat": seat,
            "message": message,
            "severity": "error",
        },
    })


def _build_match_state(repo, match: dict, runner) -> dict[str, Any]:
    """Build the full match_state snapshot payload."""
    tables: dict[str, dict[str, Any]] = {}
    for table_key in ("A", "B"):
        table_runner = runner.table_a if table_key == "A" else runner.table_b
        state = table_runner._state
        players: dict[str, dict[str, Any]] = {}
        for seat in ["S", "E", "N", "W"]:
            player_id = state.seat_agents.get(seat, "")
            player = repo.get_player(player_id) if player_id else None
            team = state.seat_teams.get(seat, "")
            role = state.role_of(seat) if state.phase and state.phase.value not in ("dealing",) else ""
            hand = state.live_hands.get(seat)
            hand_size = hand.size if hand else 0
            # Include hand cards for spectator view (god mode)
            hand_cards = [str(c) for c in hand.cards] if hand else []
            players[seat] = {
                "agent_name": player["display_name"] if player else "",
                "player_id": player_id,
                "team": team,
                "role": role,
                "hand_size": hand_size,
                "hand_cards": hand_cards,
            }

        play_history = []
        for r in state.play_history:
            play_history.append({
                "round": r.round_num,
                "sub_round": r.sub_round,
                "seat": r.seat,
                "action": {
                    "type": r.action_type,
                    "cards": [str(c) for c in r.cards] if r.cards else None,
                    "pattern": r.trick.pattern.value if r.trick else None,
                    "display": r.trick.display() if r.trick else None,
                },
                "timestamp_ms": r.timestamp_ms,
            })

        current_pattern = None
        if state.current_trick:
            current_pattern = {
                "pattern": state.current_trick.pattern.value,
                "length": state.current_trick.length,
                "max_rank": str(state.current_trick.main_rank) if state.current_trick.main_rank else None,
            }

        # Build bidding_order from game state
        bidding_order = list(state.bidding_order) if state.bidding_order else []
        idle_seat = getattr(state, 'original_idle', '')
        effective_idle = getattr(state, 'effective_idle', idle_seat)

        # Build bidding_history from game state
        bidding_history = [
            {"seat": r.seat, "bid": r.bid}
            for r in state.bidding_history
        ]

        # Determine current_seat for all phases
        current_seat = ""
        if state.phase:
            if state.phase.value == "playing":
                current_seat = state.current_player
            elif state.phase.value == "bidding" and state.bidding_order and state.current_bidder_idx < len(state.bidding_order):
                current_seat = state.bidding_order[state.current_bidder_idx]

        tables[table_key] = {
            "phase": state.phase.value if state.phase else "",
            "hand_num": state.hand_num,
            "current_seat": current_seat,
            "current_pattern": current_pattern,
            "dealer": state.dealer,
            "landlord": state.landlord,
            "dizhu_cards": [str(c) for c in state.dizhu_cards] if state.dizhu_cards else [],
            "players": players,
            "play_history": play_history,
            "bidding_order": bidding_order,
            "idle_seat": idle_seat,
            "effective_idle": effective_idle,
            "bidding_history": bidding_history,
            "current_high_bid": state.current_high_bid,
            "current_high_bidder": state.current_high_bidder,
            "time_remaining": {
                "red_team_ms": 0,
                "blue_team_ms": 0,
            },
            "turn_timer": getattr(table_runner, "active_turn", None),
        }

    announced = runner.event_bus.announced_hand if runner.event_bus else None
    if announced:
        for key in ("A", "B"):
            if tables[key]["hand_num"] < announced["hand_num"]:
                tables[key] = {**announced["tables"][key], "hand_num": announced["hand_num"]}

    return {
        "match_id": match["id"],
        "status": match["status"],
        "current_hand": max(match.get("current_hand", 0), announced["hand_num"] if announced else 0),
        "score": {
            "red": match.get("score_red", 0),
            "blue": match.get("score_blue", 0),
        },
        "tables": tables,
    }


def build_persisted_match_state(repo, match):
    """An inactive match still has an authoritative snapshot on reconnect."""
    from .routes.replay import _build_table_detail
    bus = MatchEventBus(match['id'], repo=repo)
    tables = {}
    hands = repo.get_hands_for_match(match['id'])
    if hands:
        hand = hands[-1]
        for th in repo.get_table_hands_for_hand(hand['id']):
            detail = _build_table_detail(repo, th, match['id'], hand['hand_num'], hand['dealer'], hand['idle_seat'], hand['seed'])
            remaining = repo.get_remaining_hands_for_table(th['id'])
            players = {}
            for seat, player in detail.get('players', {}).items():
                cards = remaining.get(seat, detail.get('initial_hands', {}).get(seat, []))
                players[seat] = {**player, 'hand_cards': cards, 'hand_size': len(cards)}
            effective_idle = next((s for s, p in players.items() if p['role'] == 'idle'), hand['idle_seat'])
            tables[th['table']] = {**detail, 'phase': th['status'], 'players': players,
                'hand_num': hand['hand_num'], 'dealer': hand['dealer'], 'current_seat': '',
                'current_pattern': None, 'turn_timer': None,
                'idle_seat': hand['idle_seat'], 'effective_idle': effective_idle}
    bus.snapshot_factory = lambda: {'match_id': match['id'], 'status': match['status'],
        'current_hand': hands[-1]['hand_num'] if hands else 0,
        'score': {'red': match['score_red'], 'blue': match['score_blue']}, 'tables': tables, 'live': False}
    return bus.snapshot()


@router.websocket("/ws/match/{match_id}")
async def match_ws(ws: WebSocket, match_id: str):
    """WebSocket endpoint for real-time match viewing."""
    await ws.accept()
    repo = _get_repo(ws)

    match = repo.get_match(match_id)
    if not match:
        await _send_error(ws, "MATCH_NOT_FOUND", f"Match not found: {match_id}")
        await ws.close()
        return

    _connections.setdefault(match_id, set()).add(ws)

    runner = _get_active_matches(ws).get(match_id)
    bus = runner.event_bus if runner else None
    subscription = bus.subscribe() if bus else None
    # Subscribe and capture snapshot synchronously on this event loop. No event
    # can be published between these operations; queued events follow watermark.
    if bus:
        state = bus.snapshot()
        watermark = state["watermark"]
        await ws.send_json({"type": "match_state", "payload": state})
    else:
        state = build_persisted_match_state(repo, match)
        watermark = state['watermark']
        await ws.send_json({'type': 'match_state', 'payload': state})
    relay_task = asyncio.create_task(_relay_events(ws, subscription, watermark)) if subscription else None

    try:
        while True:
            try:
                data = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "pong"})
                continue

            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                await _send_error(ws, "INVALID_REQUEST", "Invalid JSON")
                continue

            msg_type = msg.get("type", "")

            if msg_type == "ping":
                await ws.send_json({"type": "pong"})

            elif msg_type == "pong":
                pass

            elif msg_type == "subscribe":
                # State events are never filtered: filtering would create sequence gaps.
                # Clients may filter presentation locally.
                if bus:
                    await ws.send_json({"type": "match_state", "payload": bus.snapshot()})

            elif msg_type == "pause_request":
                active_matches = _get_active_matches(ws)
                r = active_matches.get(match_id)
                if r:
                    r.request_pause()
                    repo.update_match_status(match_id, "paused")
                    bus2 = r.event_bus if hasattr(r, 'event_bus') else None
                    if bus2:
                        await bus2.emit_match_paused("user_requested", match.get("current_hand", 0), _now_ms())

            elif msg_type == "resume_request":
                active_matches = _get_active_matches(ws)
                r = active_matches.get(match_id)
                if r:
                    # Resume match-level pause (unblocks _check_pause in run() loop)
                    r.pause.resume()
                    # Resume table-level pauses (unblocks _check_pause in play/bid loops)
                    r.resume_tables()
                    repo.update_match_status(match_id, "running")
                    bus2 = r.event_bus if hasattr(r, 'event_bus') else None
                    if bus2:
                        await bus2.emit_match_resumed(match.get("current_hand", 0), _now_ms())

            else:
                await _send_error(ws, "INVALID_REQUEST", f"Unknown message type: {msg_type}")

    except WebSocketDisconnect:
        pass
    finally:
        if bus and subscription:
            bus.unsubscribe(subscription)
        if relay_task and not relay_task.done():
            relay_task.cancel()
            try:
                await relay_task
            except asyncio.CancelledError:
                pass
        conns = _connections.get(match_id, set())
        conns.discard(ws)
        if not conns:
            _connections.pop(match_id, None)


async def _relay_events(ws, subscription, watermark=0):
    while True:
        event = await subscription.get()
        if event is None:
            break
        if event.get("seq", watermark + 1) <= watermark:
            continue
        try:
            await asyncio.wait_for(ws.send_json(event), timeout=5.0)
            if event["type"] == "resync_required":
                await ws.close(code=1013)
                break
        except (asyncio.TimeoutError, Exception):
            await ws.close(code=1013)
            break


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)
