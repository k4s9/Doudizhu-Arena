"""LLM provider abstraction layer."""

from __future__ import annotations

from .base import AbstractLLMProvider, LLMError
from .claude import ClaudeProvider
from .openai import OpenAIProvider


def create_provider(
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None = None,
) -> AbstractLLMProvider:
    """Factory function to create an LLM provider from config values."""
    p = provider.lower()
    if p == "claude":
        return ClaudeProvider(model=model, api_key=api_key)
    if p == "openai":
        return OpenAIProvider(model=model, api_key=api_key, base_url=base_url)
    raise ValueError(f"Unknown provider: {provider!r}")


__all__ = [
    "AbstractLLMProvider",
    "ClaudeProvider",
    "LLMError",
    "OpenAIProvider",
    "create_provider",
]
