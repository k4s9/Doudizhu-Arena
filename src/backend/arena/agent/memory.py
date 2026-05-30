"""MemoryManager — long-term and short-term memory for agents.

Long-term memory: persists across matches (personality, global strategy).
Short-term memory: per-match (mental state, opponent reads, self-assessment).

M3 stores everything in-memory. M4 will add DB persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MemoryManager:
    """Manages short-term and long-term memory for an agent.

    All storage is in-memory for now. The LLM Agent reads these
    values to inject into prompts, and updates them via reflection/summary.
    """

    agent_id: str
    long_term: str = ""
    short_term: dict[str, str] = field(default_factory=dict)  # match_id → memory
    _current_match_id: str = ""

    # ── factory ───────────────────────────────────────────────────────────────

    @classmethod
    def with_long_term(cls, agent_id: str, long_term: str = "") -> MemoryManager:
        """Create a MemoryManager with pre-existing long-term memory."""
        return cls(agent_id=agent_id, long_term=long_term)

    # ── match lifecycle ──────────────────────────────────────────────────────

    def start_match(self, match_id: str) -> None:
        """Begin a new match. Initializes empty short-term memory."""
        self._current_match_id = match_id
        self.short_term[match_id] = ""

    def end_match(self) -> None:
        """End the current match."""
        self._current_match_id = ""

    # ── short-term memory (per-hand reflection) ──────────────────────────────

    def get_short_term(self, match_id: str | None = None) -> str:
        """Get short-term memory for the current (or specified) match."""
        mid = match_id or self._current_match_id
        return self.short_term.get(mid, "")

    def update_short_term(self, memory: str, match_id: str | None = None) -> None:
        """Update short-term memory (called after each hand's reflection)."""
        mid = match_id or self._current_match_id
        self.short_term[mid] = memory

    # ── long-term memory (post-match summary) ────────────────────────────────

    def get_long_term(self) -> str:
        return self.long_term

    def update_long_term(self, memory: str) -> None:
        """Update long-term memory (called after match summary)."""
        self.long_term = memory
