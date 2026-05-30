"""Tests comparing multiple LLM provider/model agent combinations.

Defines mock providers that simulate different LLM play styles (Claude vs OpenAI),
then tests LLMAgent correctness with each and in mixed-provider tables.
Also verifies agents.yaml configuration loading.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile

import pytest

from arena.agent.base import AgentContext
from arena.agent.llm_agent import LLMAgent
from arena.engine.deck import Deck
from arena.llm.base import AbstractLLMProvider
from arena.tournament.seating import get_dealer_idle
from arena.tournament.table import TableRunner


# ── mock providers ─────────────────────────────────────────────────────────────────


class MockClaudeProvider(AbstractLLMProvider):
    """Mock provider simulating Claude's play style.

    Claude-style hallmarks:
    - Verbose, step-by-step reasoning
    - Strategic evaluation of hand quality and positional dynamics
    - Methodical decision-making with thorough analysis
    """

    def __init__(self) -> None:
        self._bid_count = 0
        self._play_count = 0
        self._reflection_count = 0

    @property
    def provider_name(self) -> str:
        return "claude"

    @property
    def model(self) -> str:
        return "claude-opus-4-7"

    async def generate(self, user_prompt: str, system_prompt: str = "") -> str:
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
            return (
                '{"reasoning": "Let me carefully analyze the current game state. '
                'After thorough review, I need more context to determine the optimal action.", '
                '"bid": 0}'
            )

    def _bidding_response(self, prompt: str) -> str:
        self._bid_count += 1

        # Check if there is already a high bid
        m = re.search(r'当前最高叫分：(\d)分', prompt)
        if m:
            high_bid = int(m.group(1))
            if high_bid >= 3:
                return (
                    '{"reasoning": "The current bid is at maximum. While I always '
                    'evaluate every opportunity thoroughly, raising beyond 3 is not '
                    'possible. The expected value analysis confirms passing is optimal.", '
                    '"bid": 0}'
                )
            if high_bid == 2:
                return (
                    '{"reasoning": "Bid at 2. Weighing hand strength against positional '
                    'disadvantage: the marginal benefit of raising to 3 is uncertain '
                    'without dominant cards. Strategic patience dictates a pass.", '
                    '"bid": 0}'
                )
            return (
                '{"reasoning": "Current bid is 1. After careful assessment of my hand '
                'composition and card distribution, I see sufficient potential to raise. '
                'Raising to 2 represents a calculated, strategically sound decision.", '
                '"bid": 2}'
            )

        # First bidder (no bidding history yet)
        if "（尚无叫分记录）" in prompt:
            return (
                '{"reasoning": "As the opening bidder, I conduct a holistic hand '
                'evaluation. The card structure suggests a measured opening bid of 1 '
                'maintains flexibility while establishing initiative. Strategic opening.", '
                '"bid": 1}'
            )

        # No bids so far, but not first bidder
        if "当前尚无叫分" in prompt:
            return (
                '{"reasoning": "No bids registered yet. I observe sufficient card '
                'quality to warrant a modest opening position. Bid 1.", '
                '"bid": 1}'
            )

        # Fallback: pass with verbose reasoning
        return (
            '{"reasoning": "After thorough analysis of hand strength, card distribution, '
            'and positional dynamics, the risk-reward ratio does not favor bidding. '
            'A conservative pass preserves optionality for future hands.", '
            '"bid": 0}'
        )

    def _playing_response(self, prompt: str) -> str:
        self._play_count += 1

        if "新一轮" in prompt:
            hand_cards = self._parse_hand(prompt)
            if hand_cards:
                card = hand_cards[0]
                return (
                    '{"reasoning": "Initiating the round as leader. After evaluating '
                    'my hand structure and considering future round dynamics, leading '
                    f'with {card} establishes board control while preserving strategic '
                    'flexibility for subsequent exchanges.", '
                    f'"action": {{"type": "play", "cards": ["{card}"]}}}}'
                )
            return (
                '{"reasoning": "No viable cards remain to lead. Attempting to force '
                'a play from this position would compromise future rounds. Pass.", '
                '"action": {"type": "pass"}}'
            )
        else:
            return (
                '{"reasoning": "As a follower, I evaluate whether contesting the '
                'current trick aligns with my strategic position. Preserving hand '
                'strength often outweighs marginal gains. Pass is optimal here.", '
                '"action": {"type": "pass"}}'
            )

    def _parse_hand(self, prompt: str) -> list[str]:
        """Extract card strings from the hand section of the prompt."""
        m = re.search(
            r'你的手牌（\d+张）：\n(.+?)(?:\n\n|\n[A-Z])',
            prompt, re.DOTALL,
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
            '{"reflection": "Reflecting on this hand comprehensively: my decisions '
            'generally aligned with optimal play principles. Areas for improvement '
            'include earlier recognition of positional advantage opportunities and '
            'more aggressive contesting when hand strength permits.", '
            '"short_term_memory": "Opponents displayed predictable patterns: passive '
            'following and conservative bidding. Key strategic takeaway: maintain '
            'positional awareness and adjust risk tolerance dynamically."}'
        )

    def _summary_response(self) -> str:
        return (
            '{"summary": "Match performance analysis: A balanced strategic approach '
            'characterized my play. Methodical evaluation served well in most scenarios, '
            'though some high-leverage moments could have benefited from increased '
            'aggression. Overall a solid showing with room for tactical refinement.", '
            '"long_term_memory": "Adopt a flexible multi-modal strategy: bid aggressively '
            'with strong hands (multiple 2s, jokers, organized sequences), play '
            'conservatively when weak, and always factor in the positional dynamics '
            'of the table. Adaptability is the key to long-term success."}'
        )


class MockOpenAIProvider(AbstractLLMProvider):
    """Mock provider simulating OpenAI's play style.

    OpenAI-style hallmarks:
    - Concise, direct reasoning
    - More aggressive and risk-tolerant decisions
    - Short, action-oriented response patterns
    """

    def __init__(self) -> None:
        self._bid_count = 0
        self._play_count = 0

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return "gpt-4o"

    async def generate(self, user_prompt: str, system_prompt: str = "") -> str:
        if "叫分规则" in system_prompt:
            return self._bidding_response(user_prompt)
        elif "出牌规则" in system_prompt:
            return self._playing_response(user_prompt)
        elif "复盘" in system_prompt:
            return self._reflection_response()
        elif "赛后总结" in system_prompt:
            return self._summary_response()
        else:
            return '{"reasoning": "Unknown phase.", "bid": 0}'

    def _bidding_response(self, prompt: str) -> str:
        self._bid_count += 1

        m = re.search(r'当前最高叫分：(\d)分', prompt)
        if m:
            high_bid = int(m.group(1))
            if high_bid >= 3:
                return '{"reasoning": "Max bid. Pass.", "bid": 0}'
            if high_bid == 2:
                return '{"reasoning": "Good hand. Raise to 3.", "bid": 3}'
            return '{"reasoning": "Decent cards. Raise to 2.", "bid": 2}'

        if "（尚无叫分记录）" in prompt:
            return '{"reasoning": "Opening. Bid 2.", "bid": 2}'

        if "当前尚无叫分" in prompt:
            return '{"reasoning": "No bids. Bid 2.", "bid": 2}'

        return '{"reasoning": "Weak hand. Pass.", "bid": 0}'

    def _playing_response(self, prompt: str) -> str:
        self._play_count += 1

        if "新一轮" in prompt:
            hand_cards = self._parse_hand(prompt)
            if hand_cards:
                card = hand_cards[0]
                return (
                    '{"reasoning": "Lead ' + card + '.", '
                    f'"action": {{"type": "play", "cards": ["{card}"]}}}}'
                )
            return '{"reasoning": "No cards.", "action": {"type": "pass"}}'
        else:
            return '{"reasoning": "Pass.", "action": {"type": "pass"}}'

    def _parse_hand(self, prompt: str) -> list[str]:
        """Extract card strings from the hand section of the prompt."""
        m = re.search(
            r'你的手牌（\d+张）：\n(.+?)(?:\n\n|\n[A-Z])',
            prompt, re.DOTALL,
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
        return (
            '{"reflection": "Good hand. Right calls.", '
            '"short_term_memory": "Opponents passive. Stay aggressive."}'
        )

    def _summary_response(self) -> str:
        return (
            '{"summary": "Solid match. Good decisions throughout.", '
            '"long_term_memory": "Aggressive bidding works. Keep pressure on."}'
        )


# ── helper ────────────────────────────────────────────────────────────────────────


def _make_context(**overrides) -> AgentContext:
    """Create a minimal AgentContext for provider comparison tests."""
    defaults = dict(
        seat="S",
        role="bidding",
        hand_cards=[],
        hand_size=5,
        current_high_bid=0,
        current_high_bidder="",
        landlord="",
        active_players=["S", "E", "N"],
        player_hand_sizes={"S": 5, "E": 5, "N": 5, "W": 0},
    )
    return AgentContext(**{**defaults, **overrides})


# ── tests ──────────────────────────────────────────────────────────────────────────


class TestClaudeAgent:
    """LLMAgent with MockClaudeProvider — bidding and playing behaviour."""

    def test_claude_agent_bidding(self):
        """Claude agent returns a valid bid with verbose reasoning."""
        provider = MockClaudeProvider()

        # Verify provider identity
        assert provider.provider_name == "claude"
        assert provider.model == "claude-opus-4-7"

        agent = LLMAgent("claude-agent-1", provider)
        ctx = _make_context(seat="S")
        bid = asyncio.run(agent.decide_bid(ctx))

        assert bid in (0, 1, 2, 3)
        reasoning = agent.get_last_reasoning()
        assert len(reasoning) > 0
        # Claude-style: verbose reasoning (more than just a few words)
        assert len(reasoning) > 50, (
            f"Expected verbose Claude-style reasoning (>50 chars), got {len(reasoning)}: {reasoning}"
        )

    def test_claude_agent_playing(self):
        """Claude agent returns a valid play decision with verbose reasoning."""
        from arena.engine.card import Card, Rank, Suit

        provider = MockClaudeProvider()
        agent = LLMAgent("claude-agent-2", provider)

        hand_cards = [
            Card(rank=Rank.ACE, suit=Suit.SPADE),
            Card(rank=Rank.KING, suit=Suit.HEART),
        ]
        ctx = _make_context(
            seat="S",
            role="landlord",
            hand_cards=hand_cards,
            hand_size=2,
            landlord="S",
            current_trick=None,
        )
        result = asyncio.run(agent.decide_play(ctx))

        assert isinstance(result, list)
        # If leader, Claude plays a card; if no cards matched, passes
        reasoning = agent.get_last_reasoning()
        assert len(reasoning) > 50, (
            f"Expected verbose Claude-style reasoning (>50 chars), got {len(reasoning)}: {reasoning}"
        )


class TestOpenAIAgent:
    """LLMAgent with MockOpenAIProvider — bidding and playing behaviour."""

    def test_openai_agent_bidding(self):
        """OpenAI agent returns a valid bid with concise reasoning."""
        provider = MockOpenAIProvider()

        # Verify provider identity
        assert provider.provider_name == "openai"
        assert provider.model == "gpt-4o"

        agent = LLMAgent("openai-agent-1", provider)
        ctx = _make_context(seat="E")
        bid = asyncio.run(agent.decide_bid(ctx))

        assert bid in (0, 1, 2, 3)
        reasoning = agent.get_last_reasoning()
        assert len(reasoning) > 0
        # OpenAI-style: concise reasoning (much shorter than Claude)
        assert len(reasoning) < 40, (
            f"Expected concise OpenAI-style reasoning (<40 chars), got {len(reasoning)}: {reasoning}"
        )

    def test_openai_agent_playing(self):
        """OpenAI agent returns a valid play decision with concise reasoning."""
        from arena.engine.card import Card, Rank, Suit

        provider = MockOpenAIProvider()
        agent = LLMAgent("openai-agent-2", provider)

        hand_cards = [
            Card(rank=Rank.ACE, suit=Suit.SPADE),
            Card(rank=Rank.KING, suit=Suit.HEART),
        ]
        ctx = _make_context(
            seat="E",
            role="farmer",
            hand_cards=hand_cards,
            hand_size=2,
            landlord="S",
            current_trick=None,
        )
        result = asyncio.run(agent.decide_play(ctx))

        assert isinstance(result, list)
        reasoning = agent.get_last_reasoning()
        assert len(reasoning) < 40, (
            f"Expected concise OpenAI-style reasoning (<40 chars), got {len(reasoning)}: {reasoning}"
        )


class TestMixedProviderTable:
    """Full-table integration with mixed Claude and OpenAI providers."""

    def test_mixed_provider_table_one_hand(self):
        """Complete one hand with 2 Claude + 2 OpenAI agents at one table."""
        claude1 = MockClaudeProvider()
        claude2 = MockClaudeProvider()
        openai1 = MockOpenAIProvider()
        openai2 = MockOpenAIProvider()

        agents = {}
        for seat, provider, aid in [
            ("S", claude1, "claude-s"),
            ("E", openai1, "openai-e"),
            ("N", claude2, "claude-n"),
            ("W", openai2, "openai-w"),
        ]:
            agent = LLMAgent(aid, provider)
            agent.seat = seat
            agents[seat] = agent

        teams = {"S": "red", "E": "blue", "N": "red", "W": "blue"}

        runner = TableRunner("A", agents, teams)
        deck = Deck("mixed-provider-hand")
        deal = deck.deal()
        dealer, idle = get_dealer_idle(1)

        result = asyncio.run(runner.run_hand(1, dealer, idle, deal))

        assert result.table == "A"
        assert result.hand_num == 1
        if result.void:
            assert result.final_bid == 0
        else:
            assert result.landlord in ("S", "E", "N")
            assert result.final_bid in (1, 2, 3)
            assert len(result.play_history) > 0

    def test_mixed_provider_table_two_hands(self):
        """Complete two hands with 2 Claude + 2 OpenAI agents at one table."""
        claude1 = MockClaudeProvider()
        claude2 = MockClaudeProvider()
        openai1 = MockOpenAIProvider()
        openai2 = MockOpenAIProvider()

        agents = {}
        for seat, provider, aid in [
            ("S", claude1, "claude-s"),
            ("E", openai1, "openai-e"),
            ("N", claude2, "claude-n"),
            ("W", openai2, "openai-w"),
        ]:
            agent = LLMAgent(aid, provider)
            agent.seat = seat
            agents[seat] = agent

        teams = {"S": "red", "E": "blue", "N": "red", "W": "blue"}

        runner = TableRunner("A", agents, teams)

        for h in range(1, 3):
            deck = Deck(f"mixed-provider-hand-{h}")
            deal = deck.deal()
            dealer, idle = get_dealer_idle(h)
            result = asyncio.run(runner.run_hand(h, dealer, idle, deal))

            assert result.hand_num == h
            if not result.void:
                assert result.landlord in ("S", "E", "N", "W")
                assert result.landlord != idle
                assert result.final_bid in (1, 2, 3)

    def test_provider_identity_after_table_run(self):
        """After a table run, provider identity metadata is preserved."""
        claude = MockClaudeProvider()
        openai = MockOpenAIProvider()

        agents = {}
        for seat, provider, aid in [
            ("S", claude, "claude-identity"),
            ("E", openai, "openai-identity"),
        ]:
            agent = LLMAgent(aid, provider)
            agent.seat = seat
            agents[seat] = agent

        # Add two more agents for a full table
        extra_claude = MockClaudeProvider()
        extra_openai = MockOpenAIProvider()
        for seat, provider, aid in [
            ("N", extra_claude, "claude-identity-2"),
            ("W", extra_openai, "openai-identity-2"),
        ]:
            agent = LLMAgent(aid, provider)
            agent.seat = seat
            agents[seat] = agent

        teams = {"S": "red", "E": "blue", "N": "red", "W": "blue"}

        runner = TableRunner("A", agents, teams)
        deck = Deck("identity-test")
        deal = deck.deal()
        dealer, idle = get_dealer_idle(1)

        asyncio.run(runner.run_hand(1, dealer, idle, deal))

        # Verify provider identities are intact
        assert claude.provider_name == "claude"
        assert claude.model == "claude-opus-4-7"
        assert openai.provider_name == "openai"
        assert openai.model == "gpt-4o"


class TestAgentsYamlLoading:
    """Verify agents.yaml configuration loads correctly."""

    def test_load_agents_yaml_parses_all_providers(self):
        """Load agents.yaml and verify all 3 agent definitions parse correctly."""
        from arena.agent.loader import load_agents_from_yaml
        from arena.config.settings import settings
        from arena.db.repository import DatabaseRepository

        yaml_path = settings.agents_yaml_path
        assert os.path.isfile(yaml_path), f"agents.yaml not found at {yaml_path}"

        # Use a temp DB to avoid polluting the production database
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_agents.db")
            repo = DatabaseRepository(db_path)
            repo.init()

            try:
                agent_ids = load_agents_from_yaml(repo, yaml_path=yaml_path)

                assert len(agent_ids) == 3, (
                    f"Expected 3 agent definitions, got {len(agent_ids)}"
                )

                # Fetch each agent record and build name-keyed lookups
                agents = {}
                for aid in agent_ids:
                    record = repo.get_agent(aid)
                    assert record is not None, f"Agent {aid} not found in DB"
                    assert "name" in record
                    assert "provider" in record
                    assert "model" in record
                    agents[record["name"]] = record

                # Verify all 3 expected agent names are present
                expected_names = {"Aggressive-Claude", "Balanced-Claude", "Balanced-GPT"}
                assert set(agents.keys()) == expected_names, (
                    f"Expected agents {expected_names}, got {set(agents.keys())}"
                )

                # Verify provider assignments
                assert agents["Aggressive-Claude"]["provider"] == "claude"
                assert agents["Balanced-Claude"]["provider"] == "claude"
                assert agents["Balanced-GPT"]["provider"] == "openai"

                # Verify model assignments
                assert agents["Aggressive-Claude"]["model"] == "claude-opus-4-7"
                assert agents["Balanced-Claude"]["model"] == "claude-opus-4-7"
                assert agents["Balanced-GPT"]["model"] == "gpt-4o"

                # Verify that Balanced-GPT has a system prompt override (conservative play)
                gpt_agent = agents["Balanced-GPT"]
                assert gpt_agent["system_prompt_override"] is not None
                assert len(gpt_agent["system_prompt_override"]) > 0

                # Verify that Claude agents have no system prompt override
                assert agents["Aggressive-Claude"]["system_prompt_override"] is None
                assert agents["Balanced-Claude"]["system_prompt_override"] is None

            finally:
                repo.close()
