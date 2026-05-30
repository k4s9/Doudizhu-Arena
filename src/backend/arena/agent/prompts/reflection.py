"""Reflection (复盘) prompt template — section 5.3 of the spec.

Called after each hand for agents that participated (including idle observers).
"""

from __future__ import annotations

from ..base import AgentContext
from ..context import ContextBuilder
from ..memory import MemoryManager

REFLECTION_SYSTEM_PROMPT = """你是斗地主AI选手 [{agent_name}]。请对本副牌进行策略复盘。

{personality_hint}

## 复盘目标
通过分析本副牌的得失，更新你对本场比赛的短期记忆。短期记忆应该包含：
1. 对其他牌手的行牌倾向观察（激进/保守/偏好某种牌型）
2. 你本副牌的关键决策回顾（叫分是否合理、行牌是否最优）
3. 当前心理状态调整（领先/落后感、信心水平）
4. 下一副牌的策略调整方向

## 输出格式
你必须输出完整的 JSON，不要包含任何其他文字。JSON 格式如下：
```json
{{
  "reflection": "本副牌核心教训：...",
  "short_term_memory": "<更新后的完整短期记忆文本>"
}}
```

short_term_memory 将替换你当前的短期记忆，所以它应该包含本副牌的新经验以及之前记忆中的重要信息。
如果之前的短期记忆为空，直接创建新的短期记忆即可。"""


def build_reflection_prompt(
    ctx: AgentContext,
    memory: MemoryManager,
    agent_name: str,
    system_prompt_override: str | None = None,
) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) for a post-hand reflection.

    Returns:
        (system_prompt, user_prompt) tuple
    """
    context_text = ContextBuilder.build_reflection_context_text(ctx)

    personality = _get_personality(memory, system_prompt_override)

    current_short_term = memory.get_short_term()

    system = REFLECTION_SYSTEM_PROMPT.format(
        agent_name=agent_name,
        personality_hint=personality,
    )

    user = f"""## 本副牌完整信息
{context_text}

## 你当前的短期记忆
{current_short_term or "（尚无短期记忆）"}

## 任务
总结本副牌经验，更新比赛短期记忆。short_term_memory 字段请写入完整的更新后短期记忆文本。

输出 JSON："""

    return system, user


def _get_personality(
    memory: MemoryManager,
    override: str | None = None,
) -> str:
    parts = []
    long_term = memory.get_long_term()
    if long_term:
        parts.append(f"你的长期记忆（性格/策略）：\n{long_term}")
    if override:
        parts.append(f"特殊指示：{override}")
    return "\n\n".join(parts)
