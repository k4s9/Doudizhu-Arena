"""A bounded tool allowlist bound to one player's immutable decision snapshot."""
from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import asdict

from ..base import AgentContext
from ...engine.card import Card, Rank
from ...engine.rules import recognize
from .plays import cards_from_counts, counts_from_cards
from .plans import compare_hand_plans
from .threats import analyze_threat

TOOL_VERSION = "1"
MAX_TOOL_CALLS = 2
MAX_ARGUMENT_CHARS = 4096
MAX_RESULT_CHARS = 8000
_CARD_ARRAY = {"type": "array", "items": {"type": "string", "maxLength": 8}, "maxItems": 20}
TOOL_SCHEMAS = [
    {
        "name": "compare_hand_plans",
        "description": "比较1至3个合法候选出法后的静态剩牌分组。可要求炸弹/火箭作为独立牌组使用。分组数忽略对手干扰，不是实际出完轮数或胜率；仅exact=true时minimum_groups是最优值。",
        "parameters": {
            "type": "object", "additionalProperties": False, "required": ["actions"],
            "properties": {
                "actions": {"type": "array", "minItems": 1, "maxItems": 3,
                    "items": {"type": "object", "additionalProperties": False,
                        "required": ["cards"], "properties": {"cards": _CARD_ARRAY}}},
                "constraints": {"type": "object", "additionalProperties": False,
                    "properties": {"preserve_bombs": {"type": "boolean"}}},
            },
        },
    },
    {
        "name": "analyze_threat",
        "description": "用公开信息检验另一个活跃玩家是否可能一手走完(can_finish)或压过目标牌(can_beat)。可指定自家合法候选cards，否则使用场上牌；无场上牌时can_finish假设获得自由领出权。possible只有假设可行性，ruled_out为完整排除，unknown未算完或信息不足；不返回概率，不推断实际轮次。",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "required": ["event", "target_seat"],
            "properties": {
                "event": {"type": "string", "enum": ["can_finish", "can_beat"]},
                "target_seat": {"type": "string", "enum": ["S", "E", "N", "W"]},
                "cards": {**_CARD_ARRAY, "minItems": 1},
            },
        },
    },
]
TOOL_GUIDANCE = """\n## 可选的本地算法工具
只在需要比较具体出法或检验威胁时调用工具，也可以直接给出最终出牌JSON。
每次决策最多调用两次工具，工具调用也消耗本次模型轮数和时间预算。
工具结果中的假设手牌不是实际手牌；unknown和搜索未完成都不能解释为安全。
最终出牌仍须遵循原JSON格式，不能把工具结果当作已执行的动作。
"""


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _public_snapshot(ctx: AgentContext) -> AgentContext:
    """Explicit allowlist: no reflection hands, other table, seed or reasoning.

    Opponent suit data present in the internal context is removed before any
    algorithm or observation hash sees it. Own actual cards remain usable.
    """
    history = []
    def rank_label(card):
        if isinstance(card, Card):
            return card.rank.display
        try:
            return Rank.from_display(card).display
        except (ValueError, TypeError):
            return Card.from_string(card).rank.display

    for record in ctx.play_history:
        history.append({
            "seat": record.get("seat"), "action_type": record.get("action_type"),
            "cards": [rank_label(card) for card in record.get("cards") or []],
        })
    trick = ctx.current_trick
    if trick is not None:
        trick = recognize(cards_from_counts(counts_from_cards(trick.all_cards)))
    return AgentContext(
        seat=ctx.seat, role=ctx.role, hand_cards=list(ctx.hand_cards), hand_size=ctx.hand_size,
        dizhu_cards=tuple(ctx.dizhu_cards) if ctx.dizhu_cards is not None else None,
        play_history=history, current_trick=trick, trick_leader=ctx.trick_leader,
        landlord=ctx.landlord, active_players=list(ctx.active_players),
        player_hand_sizes=dict(ctx.player_hand_sizes),
    )


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _validate(value, schema):
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict):
            raise ValueError("expected an object")
        props = schema.get("properties", {})
        if set(value) - set(props) or set(schema.get("required", ())) - set(value):
            raise ValueError("unknown or missing tool arguments")
        for key, item in value.items():
            _validate(item, props[key])
    elif kind == "array":
        if not isinstance(value, list) or not schema.get("minItems", 0) <= len(value) <= schema["maxItems"]:
            raise ValueError("array length exceeds tool limits")
        for item in value:
            _validate(item, schema["items"])
    elif kind == "string":
        if not isinstance(value, str) or len(value) > schema.get("maxLength", 100):
            raise ValueError("invalid string argument")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError("invalid argument choice")
    elif kind == "boolean" and type(value) is not bool:
        raise ValueError("expected a boolean")


class ToolRegistry:
    def __init__(self, ctx: AgentContext):
        self._ctx = _public_snapshot(ctx)
        # default=str handles Card / Trick without exposing forbidden fields.
        encoded = json.dumps(asdict(self._ctx), default=str, ensure_ascii=False, sort_keys=True)
        self.state_hash = digest(encoded)
        self.calls = 0
        self._cache = {}

    def execute(self, name: str, raw_arguments: str) -> dict:
        started = time.perf_counter()
        self.calls += 1
        response = {"tool": name, "version": TOOL_VERSION, "state_hash": self.state_hash,
                    "cached": False}
        try:
            if self.calls > MAX_TOOL_CALLS:
                raise ValueError("tool call limit exceeded")
            schemas = {schema["name"]: schema["parameters"] for schema in TOOL_SCHEMAS}
            if name not in schemas:
                raise ValueError("unknown tool")
            if not isinstance(raw_arguments, str) or len(raw_arguments) > MAX_ARGUMENT_CHARS:
                raise ValueError("tool arguments exceed size limit")
            arguments = json.loads(raw_arguments, object_pairs_hook=_unique_object)
            _validate(arguments, schemas[name])
            key = (name, canonical(arguments))
            if key in self._cache:
                data = copy.deepcopy(self._cache[key])
                response["cached"] = True
            else:
                function = {"compare_hand_plans": compare_hand_plans, "analyze_threat": analyze_threat}[name]
                data = function(self._ctx, arguments)
                if len(canonical(data)) > MAX_RESULT_CHARS - 500:
                    raise ValueError("tool result exceeds size limit")
                self._cache[key] = copy.deepcopy(data)
            response.update(ok=True, data=data)
        except (ValueError, TypeError, RecursionError) as exc:
            response.update(ok=False, error={"code": "invalid_tool_request", "message": str(exc)[:240]})
        response["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return response
