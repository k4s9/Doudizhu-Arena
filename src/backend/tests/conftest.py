"""Shared test fixtures for backend tests.

Provides mock_provider, mock_agent, and sample_deck fixtures
used across smoke tests and integration tests.

The SmokeMockProvider and CapturingMockProvider classes are defined in
mock_utils.py so they can be imported by both conftest and test modules.
"""

from __future__ import annotations

import pytest

from arena.agent.llm_agent import LLMAgent
from arena.engine.deck import Deck, DealResult

from tests.mock_utils import SmokeMockProvider


# ── fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_provider() -> SmokeMockProvider:
    """Return a fresh SmokeMockProvider instance."""
    return SmokeMockProvider()


@pytest.fixture
def mock_agent() -> LLMAgent:
    """Create an LLMAgent with a SmokeMockProvider and a known agent ID."""
    provider = SmokeMockProvider()
    agent = LLMAgent("mock-agent", provider)
    agent.seat = "S"
    return agent


@pytest.fixture
def sample_deck() -> DealResult:
    """Return a seeded deck deal for deterministic testing."""
    deck = Deck("test-fixture-seed")
    return deck.deal()
