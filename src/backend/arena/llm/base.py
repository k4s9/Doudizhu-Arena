"""Abstract LLM provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LLMUsage:
    """Token usage info returned alongside the generated text."""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class LLMError(Exception):
    """Raised when an LLM call fails."""


class AbstractLLMProvider(ABC):
    """Minimal abstraction over an LLM API.

    Each provider is responsible for connecting to its specific API
    and returning raw text output with optional usage data.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @property
    @abstractmethod
    def model(self) -> str: ...

    @abstractmethod
    async def generate(
        self,
        user_prompt: str,
        system_prompt: str = "",
    ) -> str:
        """Send a prompt to the LLM and return the raw text response."""
        ...
