"""Bidding (叫分) prompt template — section 5.1 of the spec."""

from __future__ import annotations

from ..base import AgentContext
from ..context import ContextBuilder
from ..memory import MemoryManager

BIDDING_SYSTEM_PROMPT = """你是斗地主AI选手 [{agent_name}]，参加复式团体赛。

{personality_hint}

## 牌型规则
- 单张：1张牌
- 对子：2张同rank牌
- 三条：3张同rank牌
- 三带一：三条+1单张
- 三带二：三条+1对子
- 顺子：>=5张连续（3-A，不含2和王）
- 连对：>=3对连续（3-A，不含2和王）
- 飞机（不带）：>=2组连续三条
- 飞机带单：连续三条+等量单张
- 飞机带对：连续三条+等量对子
- 四带二单：四同rank+2张不同单牌
- 四带二同单：四同rank+1对子
- 四带二对：四同rank+2对不同对子
- 炸弹：4张同rank
- 火箭：大小王

rank顺序：3 < 4 < 5 < 6 < 7 < 8 < 9 < 10 < J < Q < K < A < 2 < 小王 < 大王

## 叫分规则
- 从首家开始，按顺序每家一次叫分机会（仅一轮，不回环）
- 叫分选项：不叫(0) / 1分 / 2分 / 3分
- 后叫者若选择叫分，必须大于当前最高叫分（但可以选择不叫）
- 叫3分 → 直接成交地主，终止叫分
- 无人叫3分 → 最高分值者成交地主
- 三家均不叫 → 流局，本副牌不计分

## 得分规则
- 本桌得分 = 叫分 x (1 + 炸弹数 + 春天/反春天)
- 每个打出的纯炸弹+1（火箭算1个；拆牌使用的不计；四带二中使用的四同rank不计）
- 春天（地主赢+农民0手出牌）：+1
- 反春天（农民赢+地主仅出1手牌）：+1

## 叫分策略参考
- 手牌含大王+2炸 → 牌力足够叫3分
- 手牌含1炸+2 + 多个A/K → 可考虑叫2分
- 手牌含多个顺子/连对/飞机 + 中等牌 → 可考虑叫1分
- 手牌散乱、缺乏炸弹和大牌 → 倾向于不叫
- 作为首家叫分，需要综合评估手牌强度
- 作为后叫者，可以根据前家的叫分调整策略

## 输出格式
你必须输出完整的 JSON，不要包含任何其他文字。JSON 格式如下：
```json
{{"reasoning": "你的牌力分析...", "bid": 0}}
```
bid 取值：0=不叫, 1/2/3=叫分分数。
{{thinking_guidance}}"""


def build_bidding_prompt(
    ctx: AgentContext,
    memory: MemoryManager,
    agent_name: str,
    system_prompt_override: str | None = None,
) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) for a bidding decision.

    Returns:
        (system_prompt, user_prompt) tuple
    """
    context_text = ContextBuilder.build_bidding_context_text(ctx)

    # Thinking time guidance
    thinking_guidance = _bidding_thinking_guidance(ctx)

    # Personality hint
    personality = _get_personality(memory, system_prompt_override)

    system = BIDDING_SYSTEM_PROMPT.format(
        agent_name=agent_name,
        personality_hint=personality,
        thinking_guidance=thinking_guidance,
    )

    user = f"""## 当前局面
{context_text}

## 任务
请评估手牌并给出叫分决策。

输出 JSON："""

    return system, user


def _bidding_thinking_guidance(ctx: AgentContext) -> str:
    """Generate thinking time guidance based on hand complexity."""
    hand_size = ctx.hand_size
    if hand_size <= 0:
        return ""

    # Check for obvious strong/weak hands
    from ...engine.card import Rank
    has_big_joker = any(c.rank == Rank.BIG_JOKER for c in ctx.hand_cards)
    has_small_joker = any(c.rank == Rank.SMALL_JOKER for c in ctx.hand_cards)
    has_two = sum(1 for c in ctx.hand_cards if c.rank == Rank.TWO)

    if has_big_joker and has_two >= 2:
        return "\n你的手牌很强（含大王+2），直接评估叫分即可，无需冗长分析。"
    if not has_big_joker and not has_small_joker and has_two == 0:
        return "\n你的手牌缺乏大牌，快速判断是否值得叫分即可。"

    return "\n请详细分析手牌强度，考虑炸弹数量、大牌分布、顺子/连对/飞机的组织性。"


def _get_personality(
    memory: MemoryManager,
    override: str | None = None,
) -> str:
    """Build the personality section of the system prompt."""
    parts = []

    long_term = memory.get_long_term()
    if long_term:
        parts.append(f"你的长期记忆（性格/策略）：\n{long_term}")

    short_term = memory.get_short_term()
    if short_term:
        parts.append(f"你本场比赛的短期记忆：\n{short_term}")

    if override:
        parts.append(f"特殊指示：{override}")
    elif not long_term:
        parts.append("你是一位经验丰富的斗地主选手，善于根据手牌强度做出合理的叫分决策。")

    return "\n\n".join(parts)
