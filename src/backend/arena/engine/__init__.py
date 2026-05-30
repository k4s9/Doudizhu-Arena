"""Doudizhu game engine — Card types, pattern recognition, state machine, scoring, timeout."""

from .card import (
    ALL_CARDS,
    RANK_COMPARE_ORDER,
    STRAIGHT_RANK_VALUES,
    STRAIGHT_RANKS,
    SUIT_DISPLAY_ORDER,
    Card,
    Rank,
    Suit,
)
from .deck import SEATS, DealResult, Deck
from .hand import Hand
from .pause import InMemoryPauseStore, PauseManager, PauseState
from .rules import InvalidPlayError, can_beat, is_pattern_match, recognize, validate_play
from .scoring import DIFF_CAP, HandScore, calculate_diff_score, calculate_hand_score
from .state import (
    BiddingRecord,
    GameEngine,
    PlayRecord,
    TablePhase,
    TableState,
)
from .timeout import (
    PlayerTimeoutState,
    TeamTimePool,
    TimeoutConfig,
    TimeoutManager,
    TimeoutStatus,
)
from .trick import PatternType, Trick

__all__ = [
    # card
    "Card", "Rank", "Suit", "ALL_CARDS",
    "RANK_COMPARE_ORDER", "STRAIGHT_RANK_VALUES", "STRAIGHT_RANKS",
    "SUIT_DISPLAY_ORDER",
    # deck
    "SEATS", "Deck", "DealResult",
    # hand
    "Hand",
    # trick
    "PatternType", "Trick",
    # rules
    "recognize", "validate_play", "can_beat", "is_pattern_match",
    "InvalidPlayError",
    # state
    "TablePhase", "TableState", "GameEngine", "BiddingRecord", "PlayRecord",
    # scoring
    "HandScore", "calculate_hand_score", "calculate_diff_score", "DIFF_CAP",
    # timeout
    "TimeoutConfig", "TimeoutManager", "TimeoutStatus",
    "TeamTimePool", "PlayerTimeoutState",
    # pause
    "PauseManager", "PauseState", "InMemoryPauseStore",
]
