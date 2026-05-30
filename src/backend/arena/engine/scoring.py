"""Table score calculation — additive multiplier mode (加算模式).

Single table score = base_score × (1 + bombs + spring + anti_spring)
No cap on single table score. Diff score cap of 12 is applied at tournament level.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HandScore:
    """Score result for a single table hand."""

    winner_team: str       # 'red' | 'blue'
    winner_role: str       # 'landlord' | 'farmer'
    base_score: int        # bid amount (1/2/3), 0 for void
    multiplier: int        # 1 + bombs + spring + anti_spring
    final_score: int       # base_score * multiplier
    bombs: int
    spring: bool
    anti_spring: bool
    void: bool = False

    @property
    def red_score(self) -> int:
        if self.void:
            return 0
        return self.final_score if self.winner_team == "red" else 0

    @property
    def blue_score(self) -> int:
        if self.void:
            return 0
        return self.final_score if self.winner_team == "blue" else 0


def calculate_hand_score(
    *,
    final_bid: int,
    winner_seat: str,
    winner_team: str,
    landlord_seat: str,
    bombs_played: int,
    spring: bool,
    anti_spring: bool,
    void: bool = False,
) -> HandScore:
    """Calculate the score for a completed hand.

    Args:
        final_bid: The winning bid (1/2/3), 0 for void.
        winner_seat: Which seat won.
        winner_team: 'red' or 'blue'.
        landlord_seat: The landlord's seat.
        bombs_played: Number of pure bombs (四带二中的四同不计).
        spring: True if landlord won and farmers played 0 hands.
        anti_spring: True if farmers won and landlord played ≤1 hand.
        void: True if all three passed on bidding.
    """
    if void:
        return HandScore(
            winner_team="",
            winner_role="",
            base_score=0,
            multiplier=0,
            final_score=0,
            bombs=0,
            spring=False,
            anti_spring=False,
            void=True,
        )

    multiplier = 1 + bombs_played
    if spring:
        multiplier += 1
    if anti_spring:
        multiplier += 1

    winner_role = "landlord" if winner_seat == landlord_seat else "farmer"

    return HandScore(
        winner_team=winner_team,
        winner_role=winner_role,
        base_score=final_bid,
        multiplier=multiplier,
        final_score=final_bid * multiplier,
        bombs=bombs_played,
        spring=spring,
        anti_spring=anti_spring,
    )


# ── diff scoring (tournament level) ──────────────────────────────────────────

DIFF_CAP = 12  # max diff score per hand


def calculate_diff_score(
    table_a_score: HandScore,
    table_b_score: HandScore,
) -> tuple[int, int]:
    """Calculate differential score for one hand across both tables.

    Returns (red_diff, blue_diff), each capped at DIFF_CAP.
    """
    if table_a_score.void and table_b_score.void:
        return 0, 0

    red_net = table_a_score.red_score + table_b_score.red_score
    blue_net = table_a_score.blue_score + table_b_score.blue_score

    diff = abs(red_net - blue_net)
    capped_diff = min(diff, DIFF_CAP)

    if red_net > blue_net:
        return capped_diff, 0
    elif blue_net > red_net:
        return 0, capped_diff
    else:
        return 0, 0
