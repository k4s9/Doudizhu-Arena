"""Event bus for match-level WebSocket event broadcasting.

Provides a per-match asyncio.Queue that MatchRunner/TableRunner push events into,
and the WebSocket handler polls to relay to connected clients.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class MatchEventBus:
    """Per-match event queue consumed by WebSocket handler.

    Usage:
        bus = MatchEventBus(match_id, maxsize=10000)
        # In MatchRunner/TableRunner:
        await bus.emit_hand_started(hand_num, dealer, seed, tables_payload)
        # In WS handler:
        event = await bus.get()
    """

    def __init__(self, match_id: str, maxsize: int = 10000) -> None:
        self.match_id = match_id
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    async def put(self, event: dict[str, Any]) -> None:
        """Push an event onto the queue. Non-blocking if queue is full (drops with warning)."""
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "MatchEventBus queue full for match %s, dropping event %s",
                self.match_id,
                event.get("type", "?"),
            )

    async def get(self) -> dict[str, Any] | None:
        """Wait for and return the next event. Returns None if bus is closed."""
        if self._closed and self._queue.empty():
            return None
        try:
            return await self._queue.get()
        except Exception:
            return None

    def close(self) -> None:
        """Signal that no more events will be produced."""
        self._closed = True

    # ── convenience emit methods ────────────────────────────────────────────────

    async def emit_hand_started(
        self,
        hand_num: int,
        dealer: str,
        seed: str,
        tables: dict[str, Any],
        idle_seat: str = "",
    ) -> None:
        await self.put({
            "type": "hand_started",
            "payload": {
                "hand_num": hand_num,
                "dealer": dealer,
                "seed": seed,
                "tables": tables,
                "idle_seat": idle_seat,
            },
        })

    async def emit_bidding_update(
        self,
        table: str,
        hand_num: int,
        seat: str,
        bid: int,
        current_high_bid: int,
        current_high_seat: str,
        bidding_history: list[dict[str, Any]],
    ) -> None:
        await self.put({
            "type": "bidding_update",
            "payload": {
                "table": table,
                "hand_num": hand_num,
                "seat": seat,
                "bid": bid,
                "current_high_bid": current_high_bid,
                "current_high_seat": current_high_seat,
                "bidding_history": bidding_history,
            },
        })

    async def emit_bidding_complete(
        self,
        table: str,
        hand_num: int,
        landlord_seat: str,
        final_bid: int,
        dizhu_cards: list[str],
        void: bool,
        idle_detail: dict[str, Any],
    ) -> None:
        await self.put({
            "type": "bidding_complete",
            "payload": {
                "table": table,
                "hand_num": hand_num,
                "landlord_seat": landlord_seat,
                "final_bid": final_bid,
                "dizhu_cards": dizhu_cards,
                "void": void,
                "idle_participation": idle_detail,
            },
        })

    async def emit_play_turn_started(
        self,
        table: str,
        hand_num: int,
        seat: str,
        timeout_ms: int,
        deadline_ms: int,
    ) -> None:
        """Notify spectators before an LLM play decision begins."""
        await self.put({
            "type": "play_turn_started",
            "payload": {
                "table": table,
                "hand_num": hand_num,
                "seat": seat,
                "timeout_ms": timeout_ms,
                "deadline_ms": deadline_ms,
            },
        })

    async def emit_card_played(
        self,
        table: str,
        hand_num: int,
        round_num: int,
        sub_round: int,
        seat: str,
        cards: list[str],
        pattern: str,
        display: str,
        current_pattern: dict[str, Any] | None,
        next_seat: str,
        timestamp_ms: int,
        thought: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "table": table,
            "hand_num": hand_num,
            "round": round_num,
            "sub_round": sub_round,
            "seat": seat,
            "action": {
                "type": "play",
                "cards": cards,
                "pattern": pattern,
                "display": display,
            },
            "current_pattern": current_pattern,
            "next_seat": next_seat,
            "timestamp_ms": timestamp_ms,
        }
        if thought is not None:
            payload["thought"] = thought
        await self.put({
            "type": "card_played",
            "payload": payload,
        })

    async def emit_pass(
        self,
        table: str,
        hand_num: int,
        round_num: int,
        sub_round: int,
        seat: str,
        pass_count_in_round: int,
        next_seat: str,
        timestamp_ms: int,
        thought: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "table": table,
            "hand_num": hand_num,
            "round": round_num,
            "sub_round": sub_round,
            "seat": seat,
            "pass_count_in_round": pass_count_in_round,
            "next_seat": next_seat,
            "timestamp_ms": timestamp_ms,
        }
        if thought is not None:
            payload["thought"] = thought
        await self.put({
            "type": "pass",
            "payload": payload,
        })

    async def emit_trick_won(
        self,
        table: str,
        hand_num: int,
        round_num: int,
        winner_seat: str,
        new_leader: str,
        reason: str,
    ) -> None:
        await self.put({
            "type": "trick_won",
            "payload": {
                "table": table,
                "hand_num": hand_num,
                "round": round_num,
                "winner_seat": winner_seat,
                "new_leader": new_leader,
                "reason": reason,
            },
        })

    async def emit_thought_update(
        self,
        table: str,
        hand_num: int,
        phase: str,
        agent_id: str,
        seat: str,
        reasoning: str,
        decision: dict[str, Any],
        round_num: int | None,
        sub_round: int | None,
        timestamp_ms: int,
        llm_call_ms: int = 0,
        retry_count: int = 0,
    ) -> None:
        payload: dict[str, Any] = {
            "table": table,
            "hand_num": hand_num,
            "phase": phase,
            "agent_id": agent_id,
            "seat": seat,
            "reasoning": reasoning,
            "decision": decision,
            "timestamp_ms": timestamp_ms,
        }
        if round_num is not None:
            payload["round"] = round_num
        if sub_round is not None:
            payload["sub_round"] = sub_round
        await self.put({
            "type": "thought_update",
            "payload": payload,
        })

    async def emit_hand_ended(
        self,
        table: str,
        hand_num: int,
        winner_team: str,
        winner_role: str,
        score: dict[str, Any],
        remaining_hands: dict[str, list[str]],
        timestamp_ms: int,
    ) -> None:
        await self.put({
            "type": "hand_ended",
            "payload": {
                "table": table,
                "hand_num": hand_num,
                "winner_team": winner_team,
                "winner_role": winner_role,
                "score": score,
                "remaining_hands": remaining_hands,
                "timestamp_ms": timestamp_ms,
            },
        })

    async def emit_score_update(
        self,
        hand_num: int,
        hand_diff: dict[str, Any],
        running_total: dict[str, int],
        remaining_hands: int,
        ko_status: dict[str, Any],
    ) -> None:
        await self.put({
            "type": "score_update",
            "payload": {
                "hand_num": hand_num,
                "hand_diff": hand_diff,
                "running_total": running_total,
                "remaining_hands": remaining_hands,
                "ko_status": ko_status,
            },
        })

    async def emit_match_ended(
        self,
        winner_team: str,
        final_score: dict[str, int],
        total_hands_played: int,
        ko_triggered: bool,
        tiebreaker_hands: int,
        finished_at: str,
    ) -> None:
        await self.put({
            "type": "match_ended",
            "payload": {
                "winner_team": winner_team,
                "final_score": final_score,
                "total_hands_played": total_hands_played,
                "ko_triggered": ko_triggered,
                "tiebreaker_hands": tiebreaker_hands,
                "finished_at": finished_at,
            },
        })

    async def emit_match_paused(
        self,
        reason: str,
        paused_at_hand: int,
        timestamp_ms: int,
    ) -> None:
        await self.put({
            "type": "match_paused",
            "payload": {
                "reason": reason,
                "paused_at_hand": paused_at_hand,
                "timestamp_ms": timestamp_ms,
            },
        })

    async def emit_match_resumed(
        self,
        resumed_at_hand: int,
        timestamp_ms: int,
    ) -> None:
        await self.put({
            "type": "match_resumed",
            "payload": {
                "resumed_at_hand": resumed_at_hand,
                "timestamp_ms": timestamp_ms,
            },
        })

    async def emit_error(
        self,
        code: str,
        message: str,
        table: str = "",
        hand_num: int = 0,
        seat: str = "",
        severity: str = "warning",
    ) -> None:
        await self.put({
            "type": "error",
            "payload": {
                "code": code,
                "table": table,
                "hand_num": hand_num,
                "seat": seat,
                "message": message,
                "severity": severity,
            },
        })
