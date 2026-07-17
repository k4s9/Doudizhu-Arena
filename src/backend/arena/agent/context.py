"""Context builder — formats game state into text for LLM prompts.

Key information isolation rules (section 3.10-3.11):
- Own cards: full suit+rank
- Other players' played cards: rank-only
- Dizhu cards: full suit+rank (public after bidding)
- Agent does NOT see other agents' reasoning
"""

from __future__ import annotations

from ..engine.card import Card, SEATS
from ..engine.trick import Trick
from .base import AgentContext


class ContextBuilder:
    """Builds text representations of game state for LLM prompts.

    All methods are static — they transform an AgentContext into
    formatted text strings suitable for inclusion in prompts.
    """

    # ── hand display ─────────────────────────────────────────────────────────

    @staticmethod
    def format_hand(cards: list[Card]) -> str:
        """Format hand in display order: rank desc, suit ♠>♥>♣>♦."""
        return " ".join(str(c) for c in cards)

    @staticmethod
    def format_dizhu_info(
        dizhu_cards: tuple[Card, ...] | None,
        hand_cards: list[Card],
    ) -> str:
        """Format dizhu card info with played/unplayed status."""
        if dizhu_cards is None:
            return "底牌：尚未公开（叫分进行中）"

        # Determine which dizhu cards are still in hand (unplayed)
        from ..engine.hand import Hand
        temp_hand = Hand.from_cards(hand_cards)
        temp_hand.mark_dizhu_cards(dizhu_cards)
        played_status = temp_hand.dizhu_played_status()

        lines = ["底牌内容："]
        for card in dizhu_cards:
            card_str = str(card)
            status = "已打出" if played_status.get(card_str, False) else "未打出"
            lines.append(f"  {card_str} — {status}")
        return "\n".join(lines)

    # ── bidding history ──────────────────────────────────────────────────────

    @staticmethod
    def format_bidding_history(bidding_history: list[dict]) -> str:
        """Format bidding history as text lines."""
        if not bidding_history:
            return "（尚无叫分记录）"
        lines = []
        for r in bidding_history:
            seat = r["seat"]
            bid = r["bid"]
            bid_str = "不叫" if bid == 0 else f"{bid}分"
            lines.append(f"  {seat}：{bid_str}")
        return "\n".join(lines)

    # ── play history (rank-only for others) ──────────────────────────────────

    @staticmethod
    def format_play_history(
        play_history: list[dict],
        own_seat: str,
    ) -> str:
        """Format play history per section 3.11 rules.

        Own plays show suit+rank. Others show rank-only.
        Rounds are marked [R1], [R2], etc.
        """
        if not play_history:
            return "（尚无出牌记录）"

        lines: list[str] = []
        current_round = 0
        current_line_parts: list[str] = []

        for record in play_history:
            rnd = record.get("round", 0)
            seat = record.get("seat", "?")
            action_type = record.get("action_type", "?")
            cards = record.get("cards") or []
            trick_display = record.get("trick_display")

            # Start new round marker
            if rnd != current_round:
                if current_line_parts:
                    lines.append("  ".join(current_line_parts))
                    current_line_parts = []
                current_round = rnd
                current_line_parts.append(f"[R{rnd}]")

            # Build action text
            if action_type == "pass":
                current_line_parts.append(f"{seat}：Pass")
            else:
                if seat == own_seat:
                    # Own cards: full suit+rank from raw cards
                    cards_str = "".join(str(c) for c in cards)
                    current_line_parts.append(f"你：{cards_str}")
                else:
                    # Others: rank-only display only (no suit!)
                    if trick_display:
                        current_line_parts.append(f"{seat}：{trick_display}")
                    else:
                        rank_str = "".join(c.rank_only if isinstance(c, Card) else str(c)[-1] for c in cards)
                        current_line_parts.append(f"{seat}：{rank_str}")

            # Check if this completes a round (trick_won is indicated by next round or end)
            # We flush on round change or at end

        if current_line_parts:
            lines.append("  ".join(current_line_parts))

        return "\n".join(lines)

    # ── player info ──────────────────────────────────────────────────────────

    @staticmethod
    def format_player_info(
        own_seat: str,
        role: str,
        landlord: str,
        active_players: list[str],
        player_hand_sizes: dict[str, int],
        seat_teams: dict[str, str] | None = None,
        effective_idle: str = "",
    ) -> str:
        """Format the player status section."""
        lines = [f"你的座位：{own_seat}"]
        lines.append(f"你的身份：{role}")
        if landlord:
            lines.append(f"地主座位：{landlord}")

        # List players
        for seat in SEATS:
            if seat == own_seat:
                continue
            if seat in player_hand_sizes:
                size = player_hand_sizes[seat]
                if seat == effective_idle:
                    lines.append(f"  {seat}：闲家（观战，{size}张手牌）")
                else:
                    role_str = "地主" if seat == landlord else "农民"
                    lines.append(f"  {seat}：{role_str}，剩余{size}张")
        return "\n".join(lines)

    # ── current trick info ───────────────────────────────────────────────────

    @staticmethod
    def format_current_trick(trick: Trick | None, trick_leader: str = "") -> str:
        """Format the current pattern to beat."""
        if trick is None:
            return "新一轮，自由出牌（场上无牌型需要跟）"
        # Clarify comparison rule for structured patterns
        comparison_hint = f"你需要出更大的{trick.pattern.value}"
        if trick.pattern.value in ("三带一", "三带二"):
            comparison_hint += f"（只需三条部分rank > {trick.main_rank.display if trick.main_rank else '?'}，带的牌任意）"
        elif trick.pattern.value in ("四带二单", "四带二同单", "四带二对"):
            comparison_hint += f"（只需四同rank部分rank > {trick.main_rank.display if trick.main_rank else '?'}，带的牌任意）"
        comparison_hint += "，或者出炸弹/火箭来压"
        return (
            f"场上牌型：{trick.display()}（{trick.pattern.value}）\n"
            f"  领出者：{trick_leader}\n"
            f"  {comparison_hint}"
        )

    # ── complete context builder methods ─────────────────────────────────────

    @staticmethod
    def build_play_context_text(ctx: AgentContext, seat_teams: dict[str, str] = None) -> str:
        """Build the full context text for a play decision prompt."""
        parts = []

        # Player info
        effective_idle = ""
        for seat, size in ctx.player_hand_sizes.items():
            if size == 0 and seat != ctx.seat:
                effective_idle = seat
        parts.append(ContextBuilder.format_player_info(
            ctx.seat, ctx.role, ctx.landlord,
            ctx.active_players, ctx.player_hand_sizes,
            seat_teams, effective_idle,
        ))

        # Hand
        parts.append(f"\n你的手牌（{ctx.hand_size}张）：\n{ContextBuilder.format_hand(ctx.hand_cards)}")

        # Dizhu cards
        if ctx.dizhu_cards is not None:
            parts.append(f"\n{ContextBuilder.format_dizhu_info(ctx.dizhu_cards, ctx.hand_cards)}")

        # Current trick
        parts.append(f"\n{ContextBuilder.format_current_trick(ctx.current_trick, ctx.trick_leader)}")

        # Bidding history
        parts.append(f"\n叫分历史：\n{ContextBuilder.format_bidding_history(ctx.bidding_history)}")

        # Play history
        parts.append(f"\n行牌历史：\n{ContextBuilder.format_play_history(ctx.play_history, ctx.seat)}")

        return "\n".join(parts)

    @staticmethod
    def build_bidding_context_text(ctx: AgentContext) -> str:
        """Build the full context text for a bidding decision prompt."""
        parts = []

        parts.append(f"你的座位：{ctx.seat}")
        bid_order_pos = "首家" if len(ctx.bidding_history) == 0 else \
                        "二家" if len(ctx.bidding_history) == 1 else "尾家"
        parts.append(f"叫分顺序位置：{bid_order_pos}")
        parts.append(f"\n你的手牌（{ctx.hand_size}张）：\n{ContextBuilder.format_hand(ctx.hand_cards)}")

        if ctx.current_high_bid > 0:
            parts.append(f"\n当前最高叫分：{ctx.current_high_bid}分（{ctx.current_high_bidder}）")
        else:
            parts.append("\n当前尚无叫分")

        parts.append(f"\n叫分历史：\n{ContextBuilder.format_bidding_history(ctx.bidding_history)}")

        return "\n".join(parts)

    @staticmethod
    def build_reflection_context_text(ctx: AgentContext) -> str:
        """Build the full context text for a reflection prompt."""
        parts = []

        parts.append(f"你的座位：{ctx.seat}")
        parts.append(f"你本副牌的身份：{ctx.agent_role or ctx.role}")

        if ctx.is_idle_observer:
            parts.append("你本副牌是闲家（观战者），未实际参与出牌。")

        # Original hand
        if ctx.initial_hand:
            parts.append(f"\n你的原始手牌：\n{ContextBuilder.format_hand(list(ctx.initial_hand))}")

        # Dizhu cards
        if ctx.dizhu_cards is not None:
            parts.append(f"\n{ContextBuilder.format_dizhu_info(ctx.dizhu_cards, ctx.hand_cards)}")

        # Bidding history
        parts.append(f"\n叫分历史：\n{ContextBuilder.format_bidding_history(ctx.bidding_history)}")

        # Full play history (rank-only for all, including self — reflection is full-info)
        parts.append(f"\n完整行牌历史（全场明牌视角）：")
        if ctx.play_history:
            for record in ctx.play_history:
                rnd = record.get("round", 0)
                seat = record.get("seat", "?")
                action_type = record.get("action_type", "?")
                cards = record.get("cards") or []
                trick_display = record.get("trick_display")
                if action_type == "pass":
                    parts.append(f"  [R{rnd}] {seat}：Pass")
                else:
                    cards_str = "".join(str(c) for c in cards)
                    display = trick_display or cards_str
                    parts.append(f"  [R{rnd}] {seat}：{display}")
        else:
            parts.append("  （无出牌记录）")

        # Remaining hands
        if ctx.remaining_hands:
            parts.append("\n全场剩余手牌：")
            for seat in SEATS:
                cards = ctx.remaining_hands.get(seat, ())
                if cards:
                    parts.append(f"  {seat}：{' '.join(str(c) for c in cards)}")
                else:
                    parts.append(f"  {seat}：（无剩余）")

        # Hand result
        if ctx.hand_score is not None:
            parts.append(f"\n本副牌结果：{ctx.winner_team}队 {ctx.winner_role} 获胜，得分 {ctx.hand_score}")

        return "\n".join(parts)

    @staticmethod
    def build_summary_context_text(ctx: AgentContext) -> str:
        """Build the full context text for a match summary prompt."""
        parts = []

        parts.append(f"你的名字：{ctx.seat}")
        parts.append(f"比赛ID：{ctx.match_id}")

        if ctx.match_summary:
            parts.append("\n你在本场比赛中的每副牌角色：")
            for hand_num, info in sorted(ctx.match_summary.items()):
                if isinstance(info, dict):
                    parts.append(f"  第{hand_num}副：{info.get('role', '?')}")

        return "\n".join(parts)
