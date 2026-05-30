"""Claude API provider via Anthropic SDK."""

from __future__ import annotations

from .base import AbstractLLMProvider, LLMError, LLMUsage


class ClaudeProvider(AbstractLLMProvider):
    """Provider for Anthropic Claude models."""

    def __init__(self, model: str, api_key: str) -> None:
        self._model = model
        self._api_key = api_key
        self._client = None
        self.last_usage: LLMUsage | None = None

    @property
    def provider_name(self) -> str:
        return "claude"

    @property
    def model(self) -> str:
        return self._model

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
            except ImportError:
                raise LLMError(
                    "anthropic package not installed. "
                    "Install with: pip install anthropic"
                )
        return self._client

    async def generate(
        self,
        user_prompt: str,
        system_prompt: str = "",
    ) -> str:
        client = self._get_client()
        self.last_usage = None
        try:
            response = await client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=system_prompt or "You are a Doudizhu AI player.",
                messages=[{"role": "user", "content": user_prompt}],
            )
            # Capture usage if available
            if hasattr(response, 'usage') and response.usage:
                self.last_usage = LLMUsage(
                    prompt_tokens=getattr(response.usage, 'input_tokens', 0),
                    completion_tokens=getattr(response.usage, 'output_tokens', 0),
                    total_tokens=getattr(response.usage, 'input_tokens', 0)
                    + getattr(response.usage, 'output_tokens', 0),
                )
            content = response.content
            if not content:
                raise LLMError("Empty response from Claude")
            return content[0].text
        except Exception as e:
            self.last_usage = None
            if isinstance(e, LLMError):
                raise
            raise LLMError(f"Claude API call failed: {e}") from e
