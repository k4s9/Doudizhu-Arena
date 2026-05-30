"""Output parser — parses LLM JSON responses with validation and retry feedback.

Validation tiers:
1. JSON is well-formed
2. Required fields present and correctly typed
3. Cards exist in hand (for play decisions)
4. Play forms a valid pattern (via rules.recognize)
5. Play can beat current trick (via rules.can_beat)
6. Bid is valid (0-3, > current_high_bid if > 0)
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..engine.card import Card
from ..engine.hand import Hand
from ..engine.rules import InvalidPlayError, can_beat, recognize
from ..engine.trick import Trick


class ParseError(Exception):
    """Raised when LLM output cannot be parsed or is invalid.

    The message is suitable as retry feedback to the LLM.
    """
    pass


def _extract_json(text: str) -> str:
    """Extract JSON from LLM output, handling markdown code fences and extra text."""
    text = text.strip()

    # Try to find JSON in markdown code fences ```json ... ```
    fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()

    # Try to find JSON object directly
    brace_start = text.find('{')
    if brace_start == -1:
        raise ParseError(
            f"输出中未找到 JSON 对象。请确保你的回复包含一个 "
            f"完整的 JSON 对象，以 {{ 开始，以 }} 结束。\n"
            f"你的输出：{text[:500]}"
        )

    # Find matching closing brace
    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[brace_start:i + 1]

    raise ParseError(
        f"JSON 对象未正确闭合。请确保回复以 {{ 开始并以 }} 结束。\n"
        f"你的输出：{text[:500]}"
    )


def _parse_json(text: str) -> dict[str, Any]:
    """Parse JSON from text, returning a dict."""
    json_str = _extract_json(text)
    try:
        result = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ParseError(
            f"JSON 解析错误：{e}。请确保输出是有效的 JSON 格式。\n"
            f"解析到的 JSON 片段：{json_str[:300]}"
        )
    if not isinstance(result, dict):
        raise ParseError("JSON 输出必须是对象（dict），不能是数组或其他类型。")
    return result


def _find_card_in_hand(card_str: str, hand: list[Card]) -> Card | None:
    """Find a Card in hand by its string representation."""
    for c in hand:
        if str(c) == card_str:
            return c
    # Try case-insensitive match
    card_lower = card_str.strip().lower()
    for c in hand:
        if str(c).lower() == card_lower:
            return c
    return None


# ── bidding parser ──────────────────────────────────────────────────────────────


def parse_bid_response(text: str, ctx_high_bid: int = 0) -> tuple[int, str]:
    """Parse a bidding response from the LLM.

    Args:
        text: Raw LLM output text.
        ctx_high_bid: Current highest bid (for validation).

    Returns:
        (bid, reasoning) tuple. bid is 0/1/2/3.

    Raises:
        ParseError: If parsing fails or the bid is invalid.
    """
    data = _parse_json(text)

    if "bid" not in data:
        raise ParseError(
            f"JSON 中缺少 'bid' 字段。请确保输出包含："
            f'{{"reasoning": "...", "bid": 0}}'
        )

    bid = data["bid"]
    if not isinstance(bid, int) and not (isinstance(bid, float) and bid == int(bid)):
        raise ParseError(
            f"'bid' 必须是整数 (0/1/2/3)，收到：{bid!r} (类型：{type(bid).__name__})"
        )
    bid = int(bid)

    if bid not in (0, 1, 2, 3):
        raise ParseError(
            f"'bid' 必须是 0（不叫）或 1/2/3（叫分），收到：{bid}"
        )

    if bid > 0 and bid <= ctx_high_bid:
        raise ParseError(
            f"你叫了 {bid} 分，但当前最高叫分已经是 {ctx_high_bid} 分。"
            f"叫分必须大于当前最高叫分（或选择不叫）。请重新考虑。"
        )

    reasoning = data.get("reasoning", "")
    return bid, reasoning


# ── playing parser ──────────────────────────────────────────────────────────────


def parse_play_response(
    text: str,
    hand_cards: list[Card],
    current_trick: Trick | None = None,
) -> tuple[list[Card], str]:
    """Parse a play response from the LLM.

    Args:
        text: Raw LLM output text.
        hand_cards: The agent's current hand cards (for card lookup).
        current_trick: The current trick to beat (None = new round leader).

    Returns:
        (cards, reasoning) tuple. Empty cards list = pass.

    Raises:
        ParseError: If parsing fails, cards not in hand, or play is illegal.
    """
    data = _parse_json(text)

    if "action" not in data:
        raise ParseError(
            f"JSON 中缺少 'action' 字段。请确保输出包含："
            f'{{"reasoning": "...", "action": {{"type": "play"|"pass", ...}}}}'
        )

    action = data["action"]
    if not isinstance(action, dict):
        raise ParseError(f"'action' 必须是对象，收到：{type(action).__name__}")

    action_type = action.get("type")
    if action_type not in ("play", "pass"):
        raise ParseError(
            f"'action.type' 必须是 'play' 或 'pass'，收到：{action_type!r}"
        )

    reasoning = data.get("reasoning", "")

    if action_type == "pass":
        return [], reasoning

    # action_type == "play"
    card_strings = action.get("cards")
    if not isinstance(card_strings, list) or len(card_strings) == 0:
        raise ParseError(
            f"'action.cards' 必须是非空数组，每个元素是卡牌字符串（如 ♠A、大王）。"
        )

    # Resolve cards from hand
    cards: list[Card] = []
    hand_copy = list(hand_cards)
    for cs in card_strings:
        if not isinstance(cs, str):
            raise ParseError(f"cards 数组中每个元素必须是字符串，收到：{cs!r}")
        found = _find_card_in_hand(cs, hand_copy)
        if found is None:
            raise ParseError(
                f"卡牌 '{cs}' 不在你的手牌中。你的手牌：\n"
                f"{' '.join(str(c) for c in hand_cards)}\n"
                f"请检查卡牌字符串是否与手牌完全一致。"
            )
        cards.append(found)
        hand_copy.remove(found)

    # Validate the play
    try:
        trick = recognize(cards)
    except InvalidPlayError as e:
        raise ParseError(
            f"你出的牌 {' '.join(str(c) for c in cards)} 不构成合法的斗地主牌型：{e}\n"
            f"请检查牌型是否符合15种合法牌型之一。"
        )

    if current_trick is not None:
        if not can_beat(trick, current_trick):
            raise ParseError(
                f"你出的 {trick.display()}（{trick.pattern.value}）"
                f"无法压下当前牌型 {current_trick.display()}（{current_trick.pattern.value}）。\n"
                f"请出更大的同类型牌型，或使用炸弹/火箭，或选择 Pass。"
            )

    return cards, reasoning


# ── reflection parser ───────────────────────────────────────────────────────────


def parse_reflection_response(text: str) -> dict[str, str]:
    """Parse a reflection response from the LLM.

    Returns:
        {'reflection': str, 'short_term_memory': str}

    Raises:
        ParseError: If parsing fails.
    """
    data = _parse_json(text)

    missing = []
    for field in ("reflection", "short_term_memory"):
        if field not in data:
            missing.append(field)

    if missing:
        raise ParseError(
            f"JSON 中缺少字段：{', '.join(missing)}。"
            f"请确保输出包含 'reflection' 和 'short_term_memory'。"
        )

    return {
        "reflection": str(data.get("reflection", "")),
        "short_term_memory": str(data.get("short_term_memory", "")),
    }


# ── summary parser ──────────────────────────────────────────────────────────────


def parse_summary_response(text: str) -> dict[str, str]:
    """Parse a match summary response from the LLM.

    Returns:
        {'summary': str, 'long_term_memory': str}

    Raises:
        ParseError: If parsing fails.
    """
    data = _parse_json(text)

    missing = []
    for field in ("summary", "long_term_memory"):
        if field not in data:
            missing.append(field)

    if missing:
        raise ParseError(
            f"JSON 中缺少字段：{', '.join(missing)}。"
            f"请确保输出包含 'summary' 和 'long_term_memory'。"
        )

    return {
        "summary": str(data.get("summary", "")),
        "long_term_memory": str(data.get("long_term_memory", "")),
    }
