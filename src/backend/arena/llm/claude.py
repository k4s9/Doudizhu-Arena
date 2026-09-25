"""Claude API provider via Anthropic SDK."""

from __future__ import annotations

from .base import AbstractLLMProvider, LLMError, LLMUsage


class ClaudeProvider(AbstractLLMProvider):
    """Provider for Anthropic Claude models."""

    def __init__(self, model: str, api_key: str, max_tokens: int = 4096, temperature: float | None = None, top_p: float | None = None, base_url: str | None = None) -> None:
        self._base_url = base_url
        self._model = model
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._top_p = top_p
        self._client = None
        self.last_usage: LLMUsage | None = None
        self.last_response_model: str | None = None
        self.last_system_fingerprint: str | None = None

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
                self._client = anthropic.AsyncAnthropic(api_key=self._api_key, base_url=self._base_url, max_retries=0)
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
        self.last_response_model = None
        self.last_system_fingerprint = None
        try:
            request = dict(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_prompt or "You are a Doudizhu AI player.",
                messages=[{"role": "user", "content": user_prompt}],
            )
            if self._temperature is not None:
                request["temperature"] = self._temperature
            if self._top_p is not None:
                request["top_p"] = self._top_p
            response = await client.messages.create(**request)
            self.last_response_model = getattr(response, "model", None)
            # Capture usage if available
            if hasattr(response, 'usage') and response.usage:
                self.last_usage = LLMUsage.from_counts(
                    prompt_tokens=getattr(response.usage, 'input_tokens', None),
                    completion_tokens=getattr(response.usage, 'output_tokens', None),
                )
            content = response.content
            return "".join(block.text for block in content if getattr(block, "type", "text") == "text")
        except Exception as e:
            self.last_usage = None
            if isinstance(e, LLMError):
                raise
            raise LLMError(f"Claude API call failed: {e}") from e
