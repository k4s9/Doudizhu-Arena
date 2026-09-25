"""Deterministic synthetic provider for plumbing tests, never model evidence."""
import json
import re
from ..llm.base import AbstractLLMProvider, LLMUsage


class ReliabilityMockProvider(AbstractLLMProvider):
    provider_name = "mock"
    model = "reliability-mock-v1"
    last_response_model = "reliability-mock-v1"
    last_system_fingerprint = "synthetic"

    def __init__(self, scenario='initial_error'):
        self.calls = 0
        self.last_usage = None
        self.scenario = scenario
        self.play_error_sent = False

    async def generate(self, user_prompt, system_prompt=""):
        self.calls += 1
        # One malformed initial output per player creates an auditable recovery.
        if self.scenario == 'initial_error' and self.calls == 1:
            raw = 'not json'
        elif self.scenario == 'play_error' and '出牌规则' in system_prompt and not self.play_error_sent:
            self.play_error_sent = True
            raw = 'not json'
        elif "叫分规则" in system_prompt:
            raw = json.dumps({"bid": 3, "reasoning": "synthetic bid"})
        else:
            match = re.search(r'你的手牌（\d+张）：\n([^\n]+)', user_prompt)
            cards = match.group(1).split() if match else []
            # ContextBuilder puts the current trick in the user prompt; the
            # longer thinking guidance is part of the system prompt.
            lead = "新一轮，自由出牌" in user_prompt
            action = {"type": "play", "cards": cards[:1]} if lead and cards else {"type": "pass"}
            raw = json.dumps({"action": action, "reasoning": "synthetic single/pass"}, ensure_ascii=False)
        self.last_usage = LLMUsage(len((system_prompt+user_prompt).encode()), len(raw.encode()), len((system_prompt+user_prompt+raw).encode()))
        return raw
