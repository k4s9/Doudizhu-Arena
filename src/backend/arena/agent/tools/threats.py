"""Public-information feasibility checks, without guessing hidden hands.

Rather than enumerating deals, enumerate legal plays and ask whether each can
be extended to a complete allocation of the unseen cards. The remaining
constraints are per-rank lower bounds and hand capacities, so that extension
check is linear in the fifteen ranks. Passes add no ownership constraints.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING

from ...engine.card import Card, Rank, SEATS
from ...engine.rules import InvalidPlayError, can_beat, recognize
from ...engine.trick import PatternType, Trick
from .plays import (
    Counts,
    EMPTY_COUNTS,
    SearchBudget,
    SearchLimitExceeded,
    counts_from_cards,
    generate_plays,
)

if TYPE_CHECKING:
    from ..base import AgentContext

MAX_SEARCH_NODES = 20_000
TIME_LIMIT_MS = 80.0
_DECK_COUNTS: Counts = (4,) * 13 + (1, 1)
_SCOPE = (
    "只检验符合公开信息的点数分配是否存在；假设牌不是实际手牌，"
    "possible 不表示概率或一定能赢，也不推断轮次、领出权或其他玩家的行动。"
    "Pass 不作为缺少可压牌的证据。"
)


class _PublicInformationError(ValueError):
    """The observation is incomplete or contradicts a 54-card allocation."""


@dataclass(frozen=True)
class _Observation:
    pool: Counts
    landlord_minimum: Counts
    sizes: dict[str, int]


def _rank_of(value: object) -> Rank:
    # Opponent Card objects may carry suits internally. Only their ranks enter
    # this tool: even physical-card uniqueness checks would reveal extra info.
    if isinstance(value, Card):
        return value.rank
    if isinstance(value, str):
        try:
            return Rank.from_display(value)
        except ValueError:
            return Card.from_string(value).rank
    raise ValueError("history cards must contain cards or rank strings")


def _observe(ctx: AgentContext) -> _Observation:
    active = ctx.active_players
    if (
        len(active) != 3 or len(set(active)) != 3
        or any(seat not in SEATS for seat in active)
        or ctx.seat not in active or ctx.landlord not in active
    ):
        raise _PublicInformationError("需要三个有效活跃座位和已确定的地主。")
    sizes = ctx.player_hand_sizes
    if not isinstance(sizes, dict) or any(seat not in SEATS for seat in sizes):
        raise _PublicInformationError("公开剩余张数的座位无效。")
    for seat in SEATS:
        size = sizes.get(seat, 0)
        maximum = 20 if seat == ctx.landlord else 17
        if type(size) is not int or not 0 <= size <= maximum:
            raise _PublicInformationError("公开剩余张数无效。")
        if seat in active and size == 0:
            raise _PublicInformationError("活跃玩家已有零张手牌，不属于进行中的对战局面。")
        if seat not in active and size != 0:
            raise _PublicInformationError("闲家不应持有对战手牌。")
    if (
        type(ctx.hand_size) is not int or ctx.hand_size != len(ctx.hand_cards)
        or ctx.hand_size != sizes[ctx.seat]
        or any(not isinstance(card, Card) for card in ctx.hand_cards)
        or len(set(ctx.hand_cards)) != len(ctx.hand_cards)
    ):
        raise _PublicInformationError("自己的手牌与公开剩余张数不一致。")

    played = {seat: [0] * 15 for seat in active}
    for record in ctx.play_history:
        if not isinstance(record, dict) or record.get("seat") not in played:
            raise _PublicInformationError("出牌历史包含无效座位。")
        cards = record.get("cards") or []
        action = record.get("action_type")
        if action == "pass" and not cards:
            continue
        if action != "play" or not isinstance(cards, (list, tuple)) or not cards:
            raise _PublicInformationError("出牌历史缺少可计数的公开牌。")
        try:
            for card in cards:
                played[record["seat"]][_rank_of(card).value - 3] += 1
        except (ValueError, TypeError, IndexError):
            raise _PublicInformationError("出牌历史包含无法识别的点数。") from None

    for seat in active:
        initial_size = 20 if seat == ctx.landlord else 17
        if sum(played[seat]) + sizes[seat] != initial_size:
            raise _PublicInformationError("出牌历史与剩余张数不闭合，不能完整排除隐藏牌组合。")
    own = counts_from_cards(ctx.hand_cards)
    pool = tuple(
        _DECK_COUNTS[i] - own[i] - sum(played[seat][i] for seat in active)
        for i in range(15)
    )
    if any(n < 0 for n in pool) or sum(pool) != sum(
        sizes[seat] for seat in active if seat != ctx.seat
    ):
        raise _PublicInformationError("公开牌的点数计数与一副 54 张牌不一致。")

    bottom = ctx.dizhu_cards
    if (
        bottom is None or len(bottom) != 3
        or any(not isinstance(card, Card) for card in bottom)
        or len(set(bottom)) != 3
    ):
        raise _PublicInformationError("需要三张已公开且不重复的底牌。")
    bottom_counts = counts_from_cards(bottom)
    # Bottom cards already belong to a live hand or public play. Never subtract
    # them from the pool a second time. A landlord play of the same rank could
    # have consumed a bottom card irrespective of its hidden suit.
    minimum = tuple(
        max(0, bottom_counts[i] - played[ctx.landlord][i]) for i in range(15)
    )
    landlord_available = own if ctx.landlord == ctx.seat else pool
    if (
        sum(minimum) > sizes[ctx.landlord]
        or any(minimum[i] > landlord_available[i] for i in range(15))
    ):
        raise _PublicInformationError("底牌的地主点数下界与剩余手牌不一致。")
    return _Observation(pool, minimum, dict(sizes))


def _target_trick(ctx: AgentContext, arguments: dict) -> Trick | None:
    if "cards" not in arguments:
        return ctx.current_trick
    values = arguments["cards"]
    if not isinstance(values, list) or not values or len(values) > 20:
        raise ValueError("cards 必须是非空的完整自家出牌数组，最多 20 张。")
    if any(not isinstance(card, str) for card in values):
        raise ValueError("cards 中每张牌必须采用完整牌面字符串，例如 ♠10 或 大王。")
    try:
        cards = [Card.from_string(value) for value in values]
    except ValueError:
        raise ValueError("cards 包含无效的完整牌面字符串。") from None
    if len(set(cards)) != len(cards) or any(card not in ctx.hand_cards for card in cards):
        raise ValueError("cards 不得重复，且必须全部在自己的当前手牌中。")
    try:
        trick = recognize(cards)
    except InvalidPlayError:
        raise ValueError("cards 不构成合法的完整出牌。") from None
    if len(trick.all_cards) != len(cards) or not can_beat(trick, ctx.current_trick):
        raise ValueError("cards 必须是能压过当前牌型的合法完整出牌。")
    return trick


def _extend_hand(
    play: Counts,
    pool: Counts,
    target_minimum: Counts,
    reserved_for_others: Counts,
    target_size: int,
    other_size: int,
) -> Counts | None:
    """Prove existence and construct a target hand, without enumerating deals.

    Once mandatory cards are assigned, every remaining rank is free to go to
    either side. Coordinate capacity plus total capacity is therefore both
    necessary and sufficient; greedy filling supplies an explicit witness.
    """
    hand = [max(play[i], target_minimum[i]) for i in range(15)]
    if (
        sum(hand) > target_size or sum(reserved_for_others) > other_size
        or any(hand[i] + reserved_for_others[i] > pool[i] for i in range(15))
    ):
        return None
    needed = target_size - sum(hand)
    for i in range(15):
        added = min(needed, pool[i] - hand[i] - reserved_for_others[i])
        hand[i] += added
        needed -= added
    return None if needed else tuple(hand)


def _ranks(counts: Counts) -> list[str]:
    return [Rank(i + 3).display for i, n in enumerate(counts) for _ in range(n)]


def analyze_threat(ctx: AgentContext, arguments: dict) -> dict:
    """Answer ``can_finish`` or ``can_beat`` using public information only.

    Optional ``cards`` is a legal proposed own play. Otherwise the current
    trick is used. Without either, ``can_finish`` assumes free lead; it does
    not infer whether the queried player will actually obtain that lead.

    ``complete`` means a certified existential answer, not full enumeration:
    one full feasible witness proves possible; only exhaustive search proves
    ruled_out. Unknown is returned for missing information or a search limit.
    Invalid model arguments raise ValueError for the shared tool dispatcher.
    """
    started = perf_counter()
    if not isinstance(arguments, dict) or set(arguments) - {"event", "target_seat", "cards"}:
        raise ValueError("仅支持 event、target_seat 和可选 cards 参数。")
    event = arguments.get("event")
    if event not in ("can_finish", "can_beat"):
        raise ValueError("event 必须是 can_finish 或 can_beat。")
    target_seat = arguments.get("target_seat")
    if (
        not isinstance(target_seat, str) or target_seat not in SEATS
        or target_seat == ctx.seat or target_seat not in ctx.active_players
    ):
        raise ValueError("target_seat 必须是另一个当前活跃玩家的座位。")
    target = _target_trick(ctx, arguments)
    if event == "can_beat" and target is None:
        raise ValueError("can_beat 需要当前牌型或 cards 中指定的自家合法候选出牌。")
    result = {
        "tool": "analyze_threat",
        "event": event,
        "target_seat": target_seat,
        "status": "unknown",
        "complete": False,
        "condition": "can_follow_target" if target else "if_given_free_lead",
        "scope": _SCOPE,
        "target_play": None if target is None else {
            "pattern": target.pattern.value,
            "ranks": _ranks(counts_from_cards(target.all_cards)),
        },
    }
    try:
        observation = _observe(ctx)
    except _PublicInformationError as exc:
        result.update(reason="inconsistent_public_information", detail=str(exc))
        result["search"] = {"nodes": 0, "exhaustive": False, "elapsed_ms": round((perf_counter() - started) * 1000, 3)}
        return result

    target_minimum = (
        observation.landlord_minimum if target_seat == ctx.landlord else EMPTY_COUNTS
    )
    reserved = (
        observation.landlord_minimum
        if ctx.landlord not in (ctx.seat, target_seat) else EMPTY_COUNTS
    )
    target_size = observation.sizes[target_seat]
    other_size = sum(observation.pool) - target_size
    # A response is either the same-size pattern, a four-card bomb, or a
    # two-card rocket. This safe size ceiling avoids enumerating unrelated
    # airplane attachments when answering a single/pair/bomb threat.
    maximum_play_size = target_size
    if target is not None:
        response_size = (
            2 if target.pattern == PatternType.ROCKET
            else max(4, len(target.all_cards))
        )
        maximum_play_size = min(target_size, response_size)
    budget = SearchBudget(max_nodes=MAX_SEARCH_NODES, time_limit_ms=TIME_LIMIT_MS)
    exhaustive = False
    try:
        for play in generate_plays(
            observation.pool,
            max_size=maximum_play_size,
            exact_size=target_size if event == "can_finish" else None,
            budget=budget,
        ):
            if target is not None and not can_beat(play.trick, target):
                continue
            assumed_hand = _extend_hand(
                play.counts, observation.pool, target_minimum, reserved,
                target_size, other_size,
            )
            if assumed_hand is None:
                continue
            result.update(
                status="possible", complete=True, reason="feasible_allocation_found",
                witness={
                    "hypothetical": True,
                    "play_ranks": _ranks(play.counts),
                    "pattern": play.trick.pattern.value,
                    "assumed_hand_ranks": _ranks(assumed_hand),
                },
            )
            break
        else:
            exhaustive = True
            result.update(status="ruled_out", complete=True, reason="all_legal_plays_excluded")
    except SearchLimitExceeded:
        result.update(reason=budget.reason or "search_limit")
    result["public_constraints"] = {
        "unseen_card_count": sum(observation.pool),
        "target_hand_size": target_size,
        "landlord_minimum_ranks": _ranks(observation.landlord_minimum),
    }
    result["search"] = {
        "nodes": budget.nodes,
        "exhaustive": exhaustive,
        "node_limit": MAX_SEARCH_NODES,
        "time_limit_ms": TIME_LIMIT_MS,
        "elapsed_ms": round((perf_counter() - started) * 1000, 3),
    }
    return result
