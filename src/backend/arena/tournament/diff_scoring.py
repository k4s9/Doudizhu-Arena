"""Match-level diff scoring — accumulates diff scores across hands.

Uses the engine's calculate_diff_score for per-hand calculation.
Adds KO detection and running total tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..engine.scoring import DIFF_CAP, HandScore, calculate_diff_score


@dataclass
class DiffScoreResult:
    """Differential scoring result for one hand."""
    hand_num: int
    red_diff: int
    blue_diff: int
    running_total: dict[str, int]  # {red: ..., blue: ...}
    capped: bool
    table_a_score: HandScore
    table_b_score: HandScore


@dataclass
class KOStatus:
    """KO (knock-out) status for a match."""
    triggered: bool
    winner_team: str  # 'red' | 'blue' | ''
    lead: int
    remaining_hands: int
    threshold: int


@dataclass
class MatchScoreboard:
    """Running scoreboard for a match."""
    red_total: int = 0
    blue_total: int = 0
    hand_results: list[DiffScoreResult] = field(default_factory=list)
    ko_status: KOStatus | None = None
    tiebreaker_count: int = 0

    @property
    def total_hands_played(self) -> int:
        return len(self.hand_results) + self.tiebreaker_count

    def record_hand(
        self,
        hand_num: int,
        table_a_score: HandScore,
        table_b_score: HandScore,
    ) -> DiffScoreResult:
        """Record a hand's scores and update running totals. Returns the diff result."""
        red_diff, blue_diff = calculate_diff_score(table_a_score, table_b_score)
        capped = (
            abs(red_diff) >= DIFF_CAP
            or abs(blue_diff) >= DIFF_CAP
            or (
                table_a_score.red_score + table_b_score.red_score
                - table_a_score.blue_score - table_b_score.blue_score
            )
            != (red_diff - blue_diff)
        )

        self.red_total += red_diff
        self.blue_total += blue_diff

        result = DiffScoreResult(
            hand_num=hand_num,
            red_diff=red_diff,
            blue_diff=blue_diff,
            running_total={"red": self.red_total, "blue": self.blue_total},
            capped=capped,
            table_a_score=table_a_score,
            table_b_score=table_b_score,
        )
        self.hand_results.append(result)
        return result

    def check_ko(
        self, remaining_hands: int, ko_enabled: bool = True
    ) -> KOStatus | None:
        """Check if KO has been triggered.

        KO formula: lead > remaining_hands × 12.
        """
        if not ko_enabled:
            return None

        lead = abs(self.red_total - self.blue_total)
        threshold = remaining_hands * DIFF_CAP

        if lead > threshold:
            winner = "red" if self.red_total > self.blue_total else "blue"
            self.ko_status = KOStatus(
                triggered=True,
                winner_team=winner,
                lead=lead,
                remaining_hands=remaining_hands,
                threshold=threshold,
            )
            return self.ko_status
        return None

    @property
    def winner(self) -> str:
        """Return the winning team: 'red', 'blue', or '' for tie/ongoing.

        Only returns a winner if KO has been triggered (early end).
        Otherwise, returns '' — the match caller decides based on final scores.
        """
        if self.ko_status and self.ko_status.triggered:
            return self.ko_status.winner_team
        return ""

    @property
    def is_tie(self) -> bool:
        return self.red_total == self.blue_total and self.ko_status is None
