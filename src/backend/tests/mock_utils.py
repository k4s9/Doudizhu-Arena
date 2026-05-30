"""Shared mock provider classes for tests.

Kept separate from conftest.py so both conftest and test modules can
import these classes without hitting pytest's conftest import restrictions.
"""

from __future__ import annotations

import re

from arena.llm.base import AbstractLLMProvider


class SmokeMockProvider(AbstractLLMProvider):
    """Mock provider that simulates a simple but valid agent.

    Bidding: always bid 1 if possible, else pass.
    Playing: if leader, play leftmost card (biggest). If follower, pass.
    Reflection/Summary: return simple text.
    """

    def __init__(self) -> None:
        self._bid_count = 0
        self._play_count = 0
        self._reflection_count = 0
        self._summary_count = 0
        # Captured prompts for introspection in tests
        self.last_system_prompt: str = ""
        self.last_user_prompt: str = ""

    @property
    def provider_name(self) -> str:
        return "smoke-mock"

    @property
    def model(self) -> str:
        return "smoke-mock-model"

    async def generate(self, user_prompt: str, system_prompt: str = "") -> str:
        self.last_system_prompt = system_prompt
        self.last_user_prompt = user_prompt

        # Determine phase from prompts
        combined = system_prompt + user_prompt

        if "叫分规则" in system_prompt:
            return self._bidding_response(user_prompt)
        elif "出牌规则" in system_prompt:
            return self._playing_response(user_prompt)
        elif "复盘" in system_prompt:
            return self._reflection_response()
        elif "赛后总结" in system_prompt:
            return self._summary_response()
        else:
            return '{"reasoning": "unknown phase", "bid": 0}'

    def _bidding_response(self, prompt: str) -> str:
        self._bid_count += 1

        # Check if there's already a high bid
        m = re.search(r'当前最高叫分：(\d)分', prompt)
        if m:
            high_bid = int(m.group(1))
            if high_bid >= 3:
                return '{"reasoning": "already max", "bid": 0}'
            return f'{{"reasoning": "raise the bid", "bid": {high_bid + 1}}}'

        # Check if this is the first bidder (no bidding history yet)
        if "（尚无叫分记录）" in prompt:
            # First bidder: bid 1
            return '{"reasoning": "decent hand, opening bid", "bid": 1}'

        # Not first bidder, but no current high bid means everyone passed so far
        if "当前尚无叫分" in prompt:
            return '{"reasoning": "someone needs to bid", "bid": 1}'

        return '{"reasoning": "pass", "bid": 0}'

    def _playing_response(self, prompt: str) -> str:
        self._play_count += 1
        # Check if leader (free play) or follower
        if "新一轮" in prompt:
            # Leader: play leftmost (biggest) single card in hand
            hand_cards = self._parse_hand_from_prompt(prompt)
            if hand_cards:
                return (
                    '{"reasoning": "lead with biggest single",'
                    f' "action": {{"type": "play", "cards": ["{hand_cards[0]}"]}}}}'
                )
            return '{"reasoning": "no cards", "action": {"type": "pass"}}'
        else:
            # Follower: always pass to keep things moving
            return '{"reasoning": "pass to keep it simple", "action": {"type": "pass"}}'

    def _parse_hand_from_prompt(self, prompt: str) -> list[str]:
        """Extract card strings from prompt like '♠A ♥K ♦3'."""
        # Find the hand section
        m = re.search(
            r'你的手牌（\d+张）：\n(.+?)(?:\n\n|\n[A-Z])', prompt, re.DOTALL
        )
        if m:
            hand_text = m.group(1).strip()
            cards = []
            for token in hand_text.split():
                token = token.strip()
                if token:
                    cards.append(token)
            return cards
        return []

    def _reflection_response(self) -> str:
        self._reflection_count += 1
        return (
            '{"reflection": "Played a standard game. Made reasonable decisions.",'
            ' "short_term_memory": "Game progressing normally. Opponents playing predictably."}'
        )

    def _summary_response(self) -> str:
        self._summary_count += 1
        return (
            '{"summary": "Match completed. Solid performance overall.",'
            ' "long_term_memory": "Prefer balanced approach. Bid on strong hands, play conservatively when weak."}'
        )


class CapturingMockProvider(SmokeMockProvider):
    """Extended mock that captures all prompts for introspection."""

    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[dict[str, str]] = []

    async def generate(self, user_prompt: str, system_prompt: str = "") -> str:
        self.prompts.append({"system": system_prompt, "user": user_prompt})
        return await super().generate(user_prompt, system_prompt)
