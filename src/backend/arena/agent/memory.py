"""MemoryManager — long-term and short-term memory for agents.

Long-term memory: persists across matches (personality, global strategy).
Short-term memory: per-match (mental state, opponent reads, self-assessment).

The tournament layer persists validated snapshots and records their provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_SHORT_TERM_CHARS = 4000
MAX_LONG_TERM_CHARS = 8000
MAX_RETAINED_MATCHES = 8


def validate_memory_text(value: str, *, max_chars: int, allow_empty: bool = False) -> str:
    """Reject invalid updates without truncating or erasing existing experience."""
    if not isinstance(value, str):
        raise ValueError("memory must be text")
    value = value.strip()
    if not value and not allow_empty:
        raise ValueError("memory must not be empty")
    if len(value) > max_chars:
        raise ValueError("memory exceeds character limit")
    return value


@dataclass
class MemoryManager:
    """Manages short-term and long-term memory for an agent.

    Prompt memory is bounded; persistent versions live in the repository.
    """

    agent_id: str
    long_term: str = ""
    short_term: dict[str, str] = field(default_factory=dict)  # match_id → memory
    _current_match_id: str = ""
    long_term_version_id: str = ""

    def __post_init__(self) -> None:
        self.long_term = validate_memory_text(
            self.long_term, max_chars=MAX_LONG_TERM_CHARS, allow_empty=True,
        )

    # ── factory ───────────────────────────────────────────────────────────────

    @classmethod
    def with_long_term(cls, agent_id: str, long_term: str = "") -> MemoryManager:
        """Create a MemoryManager with pre-existing long-term memory."""
        return cls(agent_id=agent_id, long_term=long_term)

    # ── match lifecycle ──────────────────────────────────────────────────────

    def start_match(self, match_id: str) -> None:
        """Begin a new match. Initializes empty short-term memory."""
        if not match_id:
            raise ValueError("match id is required")
        self._current_match_id = match_id
        self.short_term[match_id] = ""
        while len(self.short_term) > MAX_RETAINED_MATCHES:
            self.short_term.pop(next(iter(self.short_term)))

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
        if not mid:
            raise ValueError("start a match before updating short-term memory")
        self.short_term[mid] = validate_memory_text(memory, max_chars=MAX_SHORT_TERM_CHARS)

    # ── long-term memory (post-match summary) ────────────────────────────────

    def get_long_term(self) -> str:
        return self.long_term

    def update_long_term(self, memory: str) -> None:
        """Update long-term memory (called after match summary)."""
        self.long_term = validate_memory_text(memory, max_chars=MAX_LONG_TERM_CHARS)
