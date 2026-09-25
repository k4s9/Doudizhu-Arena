"""Inline reasoning from a real compatible gateway must not become an action."""
import pytest

from arena.agent.parser import ParseError, parse_bid_response, parse_play_response
from arena.engine.card import Card
from arena.evaluation.observations import validate_output


def test_final_bid_wins_over_valid_example_in_thinking():
    raw = '<think>Example: {"bid": 0}. Choose three instead.</think>\n{"bid": 3}'
    assert parse_bid_response(raw)[0] == 3
    assert validate_output({'phase': 'bidding', 'observation': {'current_high_bid': 0}}, raw) == 3


def test_thinking_fence_does_not_shadow_final_play():
    raw = '<think>```json\n{"action":{"type":"pass"}}\n```</think>\n' \
          '```json\n{"action":{"type":"play","cards":["♠3"]}}\n```'
    hand = [Card.from_string('♠3')]
    assert parse_play_response(raw, hand)[0] == hand


@pytest.mark.parametrize('raw', [
    '<think>{"bid": 3}',
    '<think>{"bid": 3}</think>',
    '<think>{"bid": 3}</think>{"bid": 4}',
])
def test_missing_or_invalid_final_answer_stays_a_failure(raw):
    with pytest.raises(ParseError):
        parse_bid_response(raw)


def test_tag_text_inside_final_json_is_preserved():
    assert parse_bid_response('{"reasoning":"<think>not a leading block</think>","bid":1}') == (
        1, '<think>not a leading block</think>')
