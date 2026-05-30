"""OpenAI API provider."""

from __future__ import annotations

from .base import AbstractLLMProvider, LLMError, LLMUsage


class OpenAIProvider(AbstractLLMProvider):
    """Provider for OpenAI models (GPT-4o, etc.)."""

    def __init__(self, model: str, api_key: str) -> None:
        self._model = model
        self._api_key = api_key
        self._client = None
        self.last_usage: LLMUsage | None = None

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(api_key=self._api_key)
            except ImportError:
                raise LLMError(
                    "openai package not installed. "
                    "Install with: pip install openai"
                )
        return self._client

    async def generate(
        self,
        user_prompt: str,
        system_prompt: str = "",
    ) -> str:
        client = self._get_client()
        self.last_usage = None
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        try:
            response = await client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=4096,
            )
            # Capture usage if available
            if hasattr(response, 'usage') and response.usage:
                self.last_usage = LLMUsage(
                    prompt_tokens=response.usage.prompt_tokens or 0,
                    completion_tokens=response.usage.completion_tokens or 0,
                    total_tokens=response.usage.total_tokens or 0,
                )
            choice = response.choices[0]
            content = choice.message.content
            if not content:
                raise LLMError("Empty response from OpenAI")
            return content
        except Exception as e:
            self.last_usage = None
            if isinstance(e, LLMError):
                raise
            raise LLMError(f"OpenAI API call failed: {e}") from e
