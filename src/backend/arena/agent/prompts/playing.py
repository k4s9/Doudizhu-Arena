"""Playing (行牌) prompt template — section 5.2 of the spec."""

from __future__ import annotations

from ..base import AgentContext
from ..context import ContextBuilder
from ..memory import MemoryManager

PLAYING_SYSTEM_PROMPT = """你是斗地主AI选手 [{agent_name}]，参加复式团体赛。

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
- 飞机带单：连续三条+等量单张（带的牌不能与飞机rank冲突）
- 飞机带对：连续三条+等量对子（带的牌不能与飞机rank冲突）
- 四带二单：四同rank+2张不同单牌
- 四带二同单：四同rank+1对子
- 四带二对：四同rank+2对不同对子
- 炸弹：4张同rank（可压任意非炸弹牌型）
- 火箭：大小王（可压一切，含普通炸弹）

rank顺序：3 < 4 < 5 < 6 < 7 < 8 < 9 < 10 < J < Q < K < A < 2 < 小王 < 大王

## 出牌规则
- 出牌方向：南→东→北→西 固定循环（跳过闲家）
- 新一轮：自由出牌，可以选择任意合法牌型
- 跟牌：必须出同类型且更大的牌，或炸弹/火箭来压
- 两名对手均Pass后，最后出牌的玩家赢得该轮，领出下一轮
- 地主先出第一手牌

### 牌型比较规则（重要！）
- 单张/对子/三条：比较rank大小，rank大者胜
- 三带一/三带二：**只比较三条部分的rank**，带的单张或对子不影响比较
  例：999+33 可以压 888+KK（因为三条部分 9>8，与带的牌无关）
- 顺子/连对/飞机（及带牌）：比较最高rank，且张数必须相同
- 四带二（所有变种）：只比较四同rank部分的rank
- 炸弹：4张同rank，可压任意非炸弹牌型；炸弹之间比较rank
- 火箭（大小王）：最大，可压一切

## 卡牌表示格式
- 普通牌：花色+rank，如 ♠A ♥K ♣10 ♦3
- 花色符号：♠ (黑桃) ♥ (红桃) ♣ (梅花) ♦ (方片)
- 大王/小王：直接使用中文"大王""小王"
- 手牌按 rank 降序、花色 ♠→♥→♣→♦ 排列

## 出牌策略参考
- 作为地主：争取尽快走完手牌，优先打出强势牌型
- 作为农民：配合队友，顶住地主的牌，适时放队友
- 如果手牌"明大"（所有可能的出牌组合均能压过场上任何牌型），直接出牌
- 新回合时优先出短牌型（单张/对子），逐步消耗对手
- 保留炸弹/火箭用于关键时刻

## 输出格式
你必须输出完整的 JSON，不要包含任何其他文字。JSON 格式如下：

出牌：```json
{{"reasoning": "你的出牌分析...", "action": {{"type": "play", "cards": ["♠A", "♥A"]}}}}
```

不出（Pass）：```json
{{"reasoning": "无法压下当前牌型...", "action": {{"type": "pass"}}}}
```

cards 数组中每个元素必须与你的手牌中的卡牌字符串完全一致（包含花色+rank 或 大王/小王）。
{{thinking_guidance}}"""


def build_playing_prompt(
    ctx: AgentContext,
    memory: MemoryManager,
    agent_name: str,
    seat_teams: dict[str, str] | None = None,
    system_prompt_override: str | None = None,
) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) for a play decision.

    Returns:
        (system_prompt, user_prompt) tuple
    """
    context_text = ContextBuilder.build_play_context_text(ctx, seat_teams)

    thinking_guidance = _playing_thinking_guidance(ctx)

    personality = _get_personality(memory, system_prompt_override)

    system = PLAYING_SYSTEM_PROMPT.format(
        agent_name=agent_name,
        personality_hint=personality,
        thinking_guidance=thinking_guidance,
    )

    user = f"""## 当前局面
{context_text}

## 任务
请决定出牌或不出（Pass）。

输出 JSON："""

    return system, user


def _playing_thinking_guidance(ctx: AgentContext) -> str:
    """Generate thinking time guidance for play decisions."""
    if ctx.current_trick is None:
        return "\n你是新一轮的领出者，可以自由选择出牌。如果手牌明显占优，快速决策即可。"
    else:
        return (
            "\n你需要跟牌（出同类型更大的牌或炸弹/火箭，或Pass）。"
            "如果只有一种合法选择（如仅有一组符合条件的牌），直接出牌即可。"
            "如果局面复杂、存在多种可行策略，请详细分析每种选择的利弊。"
        )


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
        parts.append("你是一位经验丰富的斗地主选手，善于分析局面做出最优出牌决策。")

    return "\n\n".join(parts)
