"""Event bus for match-level WebSocket event broadcasting.

Provides a per-match asyncio.Queue that MatchRunner/TableRunner push events into,
and the WebSocket handler polls to relay to connected clients.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class Subscription:
    def __init__(self, maxsize):
        self.queue = asyncio.Queue(maxsize=maxsize)

    async def get(self):
        return await self.queue.get()


class MatchEventBus:
    """Single-process fanout with a durable match-wide transport sequence.

    Sequence is publication order, not a strategic ordering between tables.
    Slow clients receive resync_required and are detached; producers never wait.
    """
    def __init__(self, match_id: str, maxsize: int = 10000, repo=None):
        from collections import deque
        self.match_id, self.maxsize, self.repo = match_id, maxsize, repo
        self._subscribers = set()
        self._history = deque(maxlen=maxsize)
        self._closed = False
        self.seq = 0
        self.snapshot_factory = None
        self._legacy = None
        self.announced_hand = None
        if repo:
            self.seq = repo.conn.execute("SELECT COALESCE(MAX(seq),0) FROM match_events WHERE match_id=?", (match_id,)).fetchone()[0]

    def subscribe(self):
        sub = Subscription(self.maxsize)
        self._subscribers.add(sub)
        if self._closed:
            sub.queue.put_nowait(None)
        return sub

    def unsubscribe(self, sub):
        self._subscribers.discard(sub)

    def snapshot(self):
        from ..engine.projection import digest
        state = self.snapshot_factory() if self.snapshot_factory else {}
        state["stream_id"] = self.match_id
        state["watermark"] = self.seq
        # Timers and timestamps are presentation data, excluded from the hash.
        projection = {k: v for k, v in state.items() if k not in ("tables", "stream_id", "watermark")}
        projection["tables"] = {k: {key: value for key,value in table.items() if key not in ("turn_timer", "play_history", "time_remaining")}
                                for k,table in state.get("tables", {}).items()}
        state["state_hash"] = digest(projection)
        return state

    async def put(self, event):
        import copy
        import json
        if self._closed:
            return
        event = copy.deepcopy(event)
        if event['type'] == 'hand_started':
            self.announced_hand = event['payload']
        if self.repo and event['type'] in ('bidding_update','card_played','pass'):
            payload=event['payload']
            phase='bidding' if event['type']=='bidding_update' else 'playing'
            row=self.repo.conn.execute('''SELECT d.* FROM decisions d JOIN table_hands th ON th.id=d.table_hand_id
                JOIN hands h ON h.id=th.hand_id WHERE d.match_id=? AND th."table"=? AND h.hand_num=?
                AND d.seat=? AND d.phase=? AND d.action_id IS NOT NULL ORDER BY d.action_seq DESC LIMIT 1''',
                (self.match_id,payload['table'],payload['hand_num'],payload['seat'],phase)).fetchone()
            if row:
                for key in ('decision_id','action_seq','state_hash_after','rules_version'):
                    payload[key]=row[key]
                payload['actual_action']=json.loads(row['actual_action'])
        self.seq += 1
        event.update(stream_id=self.match_id, seq=self.seq, event_id=f"{self.match_id}:{self.seq}")
        if self.repo:
            self.repo.conn.execute("INSERT INTO match_events VALUES(?,?,?)", (self.match_id, self.seq, json.dumps(event, ensure_ascii=False)))
            self.repo.conn.commit()
        self._history.append(event)
        for sub in list(self._subscribers):
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                while not sub.queue.empty():
                    sub.queue.get_nowait()
                sub.queue.put_nowait({"type": "resync_required", "payload": {"reason": "slow_client"}})
                self.unsubscribe(sub)

    async def get(self):
        # Compatibility for local single-consumer tools; WS always subscribes.
        if self._legacy is None:
            self._legacy = self.subscribe()
            for event in self._history:
                if not self._legacy.queue.full(): self._legacy.queue.put_nowait(event)
        return await self._legacy.get()

    def close(self):
        self._closed = True
        for sub in list(self._subscribers):
            if not sub.queue.full():
                sub.queue.put_nowait(None)
            else:
                while not sub.queue.empty(): sub.queue.get_nowait()
                sub.queue.put_nowait({"type": "resync_required", "payload": {"reason": "slow_client"}})
        self._subscribers.clear()

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
