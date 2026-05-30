"""Tests for agent framework: parser, context builder, memory manager, LLM agent."""

import asyncio

import pytest

from arena.agent.base import AgentContext
from arena.agent.context import ContextBuilder
from arena.agent.llm_agent import LLMAgent, MAX_RETRIES
from arena.agent.memory import MemoryManager
from arena.agent.parser import (
    ParseError,
    _extract_json,
    parse_bid_response,
    parse_play_response,
    parse_reflection_response,
    parse_summary_response,
)
from arena.engine.card import Card, Rank, Suit
from arena.engine.trick import PatternType, Trick
from arena.llm.base import AbstractLLMProvider


# ── helpers ──────────────────────────────────────────────────────────────────────


def make_card(rank_str: str, suit_str: str = "") -> Card:
    """Create a Card from display strings."""
    if rank_str in ("大王", "小王"):
        return Card(rank=Rank.from_display(rank_str))
    return Card(rank=Rank.from_display(rank_str), suit=Suit.from_display(suit_str))


def make_hand(*card_strs: str) -> list[Card]:
    """Create a hand from space-separated card strings like '♠A ♥K ♦3'."""
    result = []
    for s in card_strs:
        if s in ("大王", "小王"):
            result.append(Card(rank=Rank.from_display(s)))
        else:
            result.append(Card.from_string(s))
    return result


def make_basic_context(**overrides) -> AgentContext:
    """Create a minimal AgentContext for testing."""
    defaults = dict(
        seat="S",
        role="farmer",
        hand_cards=make_hand("♠A", "♥K", "♦3", "♣3", "♠7"),
        hand_size=5,
        current_high_bid=0,
        current_high_bidder="",
        landlord="",
        active_players=["S", "E", "N"],
        player_hand_sizes={"S": 5, "E": 5, "N": 5, "W": 0},
    )
    return AgentContext(**{**defaults, **overrides})


# ── mock LLM provider ────────────────────────────────────────────────────────────


class MockLLMProvider(AbstractLLMProvider):
    """Mock provider that returns pre-configured responses."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = responses or []
        self.call_count = 0
        self.last_system = ""
        self.last_user = ""

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def model(self) -> str:
        return "mock-model"

    async def generate(self, user_prompt: str, system_prompt: str = "") -> str:
        self.last_system = system_prompt
        self.last_user = user_prompt
        if self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
            self.call_count += 1
            return resp
        self.call_count += 1
        return '{"reasoning": "fallback", "bid": 0}'


class FailingMockProvider(AbstractLLMProvider):
    """Provider that always fails."""

    @property
    def provider_name(self) -> str:
        return "failing"

    @property
    def model(self) -> str:
        return "failing-model"

    async def generate(self, user_prompt: str, system_prompt: str = "") -> str:
        from arena.llm.base import LLMError
        raise LLMError("Simulated LLM failure")


# ── JSON extraction tests ────────────────────────────────────────────────────────


class TestJsonExtraction:
    def test_plain_json(self):
        result = _extract_json('{"bid": 3, "reasoning": "good hand"}')
        assert '"bid": 3' in result

    def test_json_with_markdown_fence(self):
        result = _extract_json('```json\n{"bid": 2}\n```')
        assert '"bid": 2' in result

    def test_json_with_text_before(self):
        result = _extract_json('Here is my response: {"bid": 1, "reasoning": "ok"}')
        assert '"bid": 1' in result

    def test_json_with_text_after(self):
        result = _extract_json('{"bid": 0}\nSome extra text')
        assert '"bid": 0' in result

    def test_nested_json(self):
        result = _extract_json(
            '{"action": {"type": "play", "cards": ["♠A", "♥K"]}, "reasoning": "..."}'
        )
        assert '"type": "play"' in result

    def test_missing_json(self):
        with pytest.raises(ParseError, match="未找到 JSON 对象"):
            _extract_json("no json here at all")


# ── bidding parser tests ─────────────────────────────────────────────────────────


class TestBidParser:
    def test_valid_bid(self):
        bid, reasoning = parse_bid_response(
            '{"reasoning": "strong hand with rocket", "bid": 3}'
        )
        assert bid == 3
        assert "strong hand" in reasoning

    def test_pass(self):
        bid, reasoning = parse_bid_response('{"reasoning": "weak hand", "bid": 0}')
        assert bid == 0

    def test_bid_not_greater_than_high_bid(self):
        with pytest.raises(ParseError, match="必须大于当前最高叫分"):
            parse_bid_response('{"reasoning": "ok", "bid": 2}', ctx_high_bid=2)

    def test_invalid_bid_value(self):
        with pytest.raises(ParseError, match="必须是 0"):
            parse_bid_response('{"reasoning": "bad", "bid": 5}')

    def test_missing_bid_field(self):
        with pytest.raises(ParseError, match="缺少 'bid'"):
            parse_bid_response('{"reasoning": "forgot bid"}')

    def test_bid_valid_when_greater(self):
        bid, _ = parse_bid_response('{"reasoning": "ok", "bid": 3}', ctx_high_bid=2)
        assert bid == 3

    def test_bid_zero_no_restriction(self):
        bid, _ = parse_bid_response('{"reasoning": "pass", "bid": 0}', ctx_high_bid=3)
        assert bid == 0


# ── playing parser tests ─────────────────────────────────────────────────────────


class TestPlayParser:
    def test_valid_play(self):
        hand = make_hand("♠A", "♥K")
        cards, reasoning = parse_play_response(
            '{"reasoning": "lead with ace", "action": {"type": "play", "cards": ["♠A"]}}',
            hand,
        )
        assert len(cards) == 1
        assert str(cards[0]) == "♠A"

    def test_pass(self):
        hand = make_hand("♠A", "♥K")
        cards, reasoning = parse_play_response(
            '{"reasoning": "cannot beat", "action": {"type": "pass"}}',
            hand,
        )
        assert cards == []

    def test_card_not_in_hand(self):
        hand = make_hand("♠A", "♥K")
        with pytest.raises(ParseError, match="不在你的手牌中"):
            parse_play_response(
                '{"reasoning": "...", "action": {"type": "play", "cards": ["♦3"]}}',
                hand,
            )

    def test_invalid_pattern(self):
        hand = make_hand("♠A", "♥K", "♦3")
        with pytest.raises(ParseError, match="不构成合法的斗地主牌型"):
            parse_play_response(
                '{"reasoning": "...", "action": {"type": "play", "cards": ["♠A", "♥K", "♦3"]}}',
                hand,
            )

    def test_cannot_beat_current_trick(self):
        hand = make_hand("♥3")
        current = Trick(
            pattern=PatternType.SINGLE,
            main_cards=(make_card("K", "♠"),),
            main_rank=Rank.KING,
        )
        with pytest.raises(ParseError, match="无法压下当前牌型"):
            parse_play_response(
                '{"reasoning": "...", "action": {"type": "play", "cards": ["♥3"]}}',
                hand,
                current_trick=current,
            )

    def test_bomb_beats_single(self):
        hand = make_hand("♠7", "♥7", "♣7", "♦7")
        current = Trick(
            pattern=PatternType.SINGLE,
            main_cards=(make_card("A", "♠"),),
            main_rank=Rank.ACE,
        )
        cards, _ = parse_play_response(
            '{"reasoning": "bomb it", "action": {"type": "play", "cards": ["♠7", "♥7", "♣7", "♦7"]}}',
            hand,
            current_trick=current,
        )
        assert len(cards) == 4

    def test_missing_action_field(self):
        hand = make_hand("♠A")
        with pytest.raises(ParseError, match="缺少 'action'"):
            parse_play_response('{"reasoning": "oops"}', hand)

    def test_invalid_action_type(self):
        hand = make_hand("♠A")
        with pytest.raises(ParseError, match="必须是 'play' 或 'pass'"):
            parse_play_response(
                '{"reasoning": "...", "action": {"type": "fold"}}', hand
            )

    def test_joker_play(self):
        hand = make_hand("大王")
        cards, _ = parse_play_response(
            '{"reasoning": "big joker", "action": {"type": "play", "cards": ["大王"]}}',
            hand,
        )
        assert len(cards) == 1
        assert str(cards[0]) == "大王"


# ── reflection / summary parser tests ────────────────────────────────────────────


class TestReflectionParser:
    def test_valid_reflection(self):
        result = parse_reflection_response(
            '{"reflection": "should have bid higher", "short_term_memory": "opponent is aggressive"}'
        )
        assert "should have bid" in result["reflection"]
        assert "opponent is aggressive" in result["short_term_memory"]

    def test_missing_fields(self):
        with pytest.raises(ParseError, match="缺少字段"):
            parse_reflection_response('{"reflection": "only reflection"}')


class TestSummaryParser:
    def test_valid_summary(self):
        result = parse_summary_response(
            '{"summary": "played well overall", "long_term_memory": "prefer aggressive bidding"}'
        )
        assert "played well" in result["summary"]
        assert "aggressive" in result["long_term_memory"]

    def test_missing_fields(self):
        with pytest.raises(ParseError, match="缺少字段"):
            parse_summary_response('{"summary": "only summary"}')


# ── memory manager tests ─────────────────────────────────────────────────────────


class TestMemoryManager:
    def test_initial_state(self):
        mm = MemoryManager(agent_id="test-agent")
        assert mm.get_long_term() == ""
        assert mm.get_short_term() == ""

    def test_long_term_memory(self):
        mm = MemoryManager.with_long_term("test-agent", "I am aggressive")
        assert mm.get_long_term() == "I am aggressive"
        mm.update_long_term("I am now conservative")
        assert mm.get_long_term() == "I am now conservative"

    def test_short_term_memory(self):
        mm = MemoryManager(agent_id="test-agent")
        mm.start_match("match-1")
        mm.update_short_term("Round 1: played badly")
        assert mm.get_short_term() == "Round 1: played badly"

    def test_short_term_per_match(self):
        mm = MemoryManager(agent_id="test-agent")
        mm.start_match("match-1")
        mm.update_short_term("Match 1 memory")
        mm.start_match("match-2")
        mm.update_short_term("Match 2 memory")
        assert mm.get_short_term("match-1") == "Match 1 memory"
        assert mm.get_short_term("match-2") == "Match 2 memory"


# ── context builder tests ────────────────────────────────────────────────────────


class TestContextBuilder:
    def test_format_hand(self):
        hand = make_hand("♠A", "♥K", "♦3")
        result = ContextBuilder.format_hand(hand)
        assert "♠A" in result
        assert "♥K" in result
        assert "♦3" in result

    def test_format_bidding_history(self):
        history = [
            {"seat": "S", "bid": 0},
            {"seat": "E", "bid": 2},
            {"seat": "N", "bid": 0},
        ]
        result = ContextBuilder.format_bidding_history(history)
        assert "不叫" in result
        assert "2分" in result

    def test_format_bidding_history_empty(self):
        result = ContextBuilder.format_bidding_history([])
        assert "尚无叫分记录" in result

    def test_format_play_history_own_vs_others(self):
        history = [
            {"round": 1, "seat": "S", "action_type": "play",
             "cards": [make_card("A", "♠")], "trick_display": "♠A"},
            {"round": 1, "seat": "E", "action_type": "pass"},
        ]
        result = ContextBuilder.format_play_history(history, own_seat="S")
        assert "[R1]" in result
        assert "你" in result  # own play uses "你"
        assert "Pass" in result

    def test_format_play_history_empty(self):
        result = ContextBuilder.format_play_history([], own_seat="S")
        assert "尚无出牌记录" in result

    def test_format_current_trick_new_round(self):
        result = ContextBuilder.format_current_trick(None)
        assert "新一轮" in result

    def test_format_current_trick_with_pattern(self):
        trick = Trick(
            pattern=PatternType.PAIR,
            main_cards=(make_card("K", "♠"), make_card("K", "♥")),
            main_rank=Rank.KING,
        )
        result = ContextBuilder.format_current_trick(trick)
        assert "对子" in result

    def test_build_bidding_context_text(self):
        ctx = make_basic_context()
        text = ContextBuilder.build_bidding_context_text(ctx)
        assert "你的座位" in text
        assert "你的手牌" in text

    def test_build_play_context_text(self):
        ctx = make_basic_context(
            landlord="S",
            role="landlord",
        )
        text = ContextBuilder.build_play_context_text(ctx)
        assert "你的身份" in text
        assert "你的手牌" in text

    def test_build_reflection_context_text(self):
        ctx = make_basic_context(
            hand_score=9,
            winner_team="red",
            winner_role="landlord",
        )
        text = ContextBuilder.build_reflection_context_text(ctx)
        assert "红" in text or "red" in text


# ── LLM agent tests (with mock provider) ─────────────────────────────────────────


class TestLLMAgent:
    def test_decide_bid_success(self):
        mock = MockLLMProvider([
            '{"reasoning": "great hand with rocket", "bid": 3}'
        ])
        agent = LLMAgent("test-1", mock)
        ctx = make_basic_context(hand_cards=make_hand("大王", "小王", "♠2", "♥2"))
        result = asyncio.run(agent.decide_bid(ctx))
        assert result == 3
        assert mock.call_count == 1

    def test_decide_bid_retry_on_bad_json(self):
        mock = MockLLMProvider([
            'not json at all',
            '{"reasoning": "ok", "bid": 2}',
        ])
        agent = LLMAgent("test-2", mock)
        ctx = make_basic_context()
        result = asyncio.run(agent.decide_bid(ctx))
        assert result == 2
        assert mock.call_count == 2

    def test_decide_bid_returns_0_after_max_retries(self):
        mock = MockLLMProvider(['bad'] * (MAX_RETRIES + 2))
        agent = LLMAgent("test-3", mock)
        ctx = make_basic_context()
        result = asyncio.run(agent.decide_bid(ctx))
        assert result == 0
        assert agent.consecutive_failures >= 1

    def test_decide_play_success(self):
        mock = MockLLMProvider([
            '{"reasoning": "lead ace", "action": {"type": "play", "cards": ["♠A"]}}'
        ])
        agent = LLMAgent("test-4", mock)
        ctx = make_basic_context(hand_cards=make_hand("♠A", "♥K"))
        result = asyncio.run(agent.decide_play(ctx))
        assert len(result) == 1
        assert str(result[0]) == "♠A"

    def test_decide_play_pass(self):
        mock = MockLLMProvider([
            '{"reasoning": "cannot beat", "action": {"type": "pass"}}'
        ])
        agent = LLMAgent("test-5", mock)
        ctx = make_basic_context()
        result = asyncio.run(agent.decide_play(ctx))
        assert result == []

    def test_decide_play_retry_on_parse_error(self):
        mock = MockLLMProvider([
            '{"reasoning": "...", "action": {"type": "play", "cards": ["♠ZZ"]}}',
            '{"reasoning": "ok", "action": {"type": "play", "cards": ["♠A"]}}',
        ])
        agent = LLMAgent("test-6", mock)
        ctx = make_basic_context(hand_cards=make_hand("♠A", "♥K"))
        result = asyncio.run(agent.decide_play(ctx))
        assert len(result) == 1
        assert mock.call_count == 2

    def test_llm_provider_error_fallback(self):
        mock = FailingMockProvider()
        agent = LLMAgent("test-7", mock)
        ctx = make_basic_context()
        result = asyncio.run(agent.decide_bid(ctx))
        assert result == 0
        assert agent.consecutive_failures > 0

    def test_consecutive_failures_tracking(self):
        mock = FailingMockProvider()
        agent = LLMAgent("test-8", mock)
        assert agent.consecutive_failures == 0
        ctx = make_basic_context()
        asyncio.run(agent.decide_bid(ctx))
        # Each LLM error increments failures; MAX_RETRIES+1 = 4 attempts
        assert agent.consecutive_failures == MAX_RETRIES + 1
        agent.reset_failures()
        assert agent.consecutive_failures == 0

    def test_reflect_success(self):
        mock = MockLLMProvider([
            '{"reflection": "should have bid 3", "short_term_memory": "opponent passive"}'
        ])
        agent = LLMAgent("test-9", mock)
        ctx = make_basic_context()
        result = asyncio.run(agent.reflect(ctx))
        assert "should have bid" in result["reflection"]
        assert "opponent passive" in result["short_term_memory"]

    def test_summarize_success(self):
        mock = MockLLMProvider([
            '{"summary": "good match overall", "long_term_memory": "become more aggressive"}'
        ])
        agent = LLMAgent("test-10", mock)
        ctx = make_basic_context()
        result = asyncio.run(agent.summarize(ctx))
        assert "good match" in result["summary"]
        assert "aggressive" in result["long_term_memory"]

    def test_memory_property(self):
        agent = LLMAgent("test-11", MockLLMProvider(), long_term_memory="I am bold")
        assert agent.memory.get_long_term() == "I am bold"

    def test_seat_property(self):
        agent = LLMAgent("test-12", MockLLMProvider())
        agent.seat = "N"
        assert agent.seat == "N"
