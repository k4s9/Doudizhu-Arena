"""An explicitly synthetic provider for exercising the fixed-study pipeline."""
import json
import re

from ..llm.base import AbstractLLMProvider, LLMUsage


class FixedStudyMockProvider(AbstractLLMProvider):
    provider_name = "mock"
    model = "fixed-study-mock"
    last_response_model = "fixed-study-mock"
    last_system_fingerprint = None
    last_usage = LLMUsage(20, 10, 30)

    async def generate(self, user_prompt, system_prompt=""):
        if "叫分规则" in system_prompt:
            return '{"bid": 0}'
        if "次重试反馈" not in user_prompt:
            return '{"action":{"type":"play","cards":["不存在的牌"]}}'
        if "新一轮，自由出牌" not in user_prompt:
            return '{"action":{"type":"pass"}}'
        hand = re.search(r"你的手牌（\d+张）：\n([^\n]+)", user_prompt).group(1).split()
        return json.dumps({"action": {"type": "play", "cards": hand[:1]}}, ensure_ascii=False)
