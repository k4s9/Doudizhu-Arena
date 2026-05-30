"""Match summary (比赛总结) prompt template — section 5.4 of the spec.

Called once after the match ends for all participating agents.
Updates long-term memory (cross-match personality/strategy evolution).
"""

from __future__ import annotations

from ..base import AgentContext
from ..context import ContextBuilder
from ..memory import MemoryManager

SUMMARY_SYSTEM_PROMPT = """你是斗地主AI选手 [{agent_name}]。比赛已结束，请进行赛后总结。

{personality_hint}

## 总结目标
回顾整场比赛的表现，提炼长期策略经验。长期记忆应该包含：
1. 你的叫分策略倾向（偏好什么手牌叫几分、在什么位置更激进/保守）
2. 你的行牌风格特征（偏好进攻还是防守、如何处理复杂牌型）
3. 你对不同对手类型的应对策略
4. 你发现自己在哪些方面需要改进

长期记忆将在未来的比赛中被注入到你的prompt中，所以它应该简洁但信息丰富。

## 输出格式
你必须输出完整的 JSON，不要包含任何其他文字。JSON 格式如下：
```json
{{
  "summary": "整场比赛总结：...",
  "long_term_memory": "<更新后的完整长期记忆文本>"
}}
```

long_term_memory 将替换你当前的长期记忆。如果之前有长期记忆，请基于旧记忆进行更新（保留重要经验，添加新经验）。"""


def build_summary_prompt(
    ctx: AgentContext,
    memory: MemoryManager,
    agent_name: str,
    system_prompt_override: str | None = None,
) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) for a post-match summary.

    Returns:
        (system_prompt, user_prompt) tuple
    """
    context_text = ContextBuilder.build_summary_context_text(ctx)

    personality = _get_personality(memory, system_prompt_override)

    current_long_term = memory.get_long_term()
    current_short_term = memory.get_short_term()

    system = SUMMARY_SYSTEM_PROMPT.format(
        agent_name=agent_name,
        personality_hint=personality,
    )

    user = f"""## 比赛信息
{context_text}

## 你的长期记忆（旧）
{current_long_term or "（尚无长期记忆）"}

## 你的短期记忆（本场比赛积累）
{current_short_term or "（无短期记忆）"}

## 任务
总结整场比赛，提炼长期策略经验。long_term_memory 字段请写入完整的更新后长期记忆文本。

输出 JSON："""

    return system, user


def _get_personality(
    memory: MemoryManager,
    override: str | None = None,
) -> str:
    parts = []
    long_term = memory.get_long_term()
    if long_term:
        parts.append(f"你的长期记忆（旧）：\n{long_term}")
    if override:
        parts.append(f"特殊指示：{override}")
    return "\n\n".join(parts)
