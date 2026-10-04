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

    @classmethod
    def from_counts(cls, prompt_tokens, completion_tokens, total_tokens=None) -> LLMUsage | None:
        """Keep partial/invalid gateway usage unknown instead of inventing zero."""
        if any(type(n) is not int or n < 0 for n in (prompt_tokens, completion_tokens)):
            return None
        total = prompt_tokens + completion_tokens if total_tokens is None else total_tokens
        if type(total) is not int or total < 0:
            return None
        return cls(prompt_tokens, completion_tokens, total)


class LLMError(Exception):
    """Raised when an LLM call fails."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A native function request; arguments remain JSON text for validation."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """One assistant turn, including any native function requests."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


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

    @property
    def supports_tools(self) -> bool:
        """Whether this adapter implements native tool turns.

        Endpoint/model support must still be checked by the caller's opt-in
        configuration. Existing text-only providers need no changes.
        """
        return False

    async def generate_turn(
        self,
        messages: list[dict],
        system_prompt: str = "",
        tools: list[dict] | None = None,
        *,
        allow_tools: bool = True,
    ) -> LLMResponse:
        """Send OpenAI-shaped messages and generic function definitions.

        Functions have ``name``, ``description`` and JSON Schema ``parameters``.
        Message roles are user, assistant (optional tool_calls), and tool
        (tool_call_id and content). ``allow_tools=False`` disables new calls
        while retaining schemas needed to interpret tool history. No implicit
        fallback or retry is performed.
        """
        raise LLMError(f"{self.provider_name} does not support native tools")

    @abstractmethod
    async def generate(
        self,
        user_prompt: str,
        system_prompt: str = "",
    ) -> str:
        """Send a prompt to the LLM and return the raw text response."""
        ...
