"""Tournament Manager — match scheduling, seating, AB-table orchestration, diff scoring."""

from .diff_scoring import (
    DIFF_CAP,
    DiffScoreResult,
    KOStatus,
    MatchScoreboard,
)
from .match import MatchConfig, MatchHandRecord, MatchResult, MatchRunner
from .seating import MatchSeating, assign_seating, get_dealer_idle
from .table import HandResult, TableRunner

__all__ = [
    # seating
    "MatchSeating",
    "assign_seating",
    "get_dealer_idle",
    # table
    "TableRunner",
    "HandResult",
    # diff scoring
    "DIFF_CAP",
    "DiffScoreResult",
    "KOStatus",
    "MatchScoreboard",
    # match
    "MatchConfig",
    "MatchRunner",
    "MatchResult",
    "MatchHandRecord",
]
