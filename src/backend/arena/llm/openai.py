"""OpenAI API provider."""

from __future__ import annotations

from .base import AbstractLLMProvider, LLMError, LLMUsage


class OpenAIProvider(AbstractLLMProvider):
    """Provider for OpenAI-compatible models (GPT-4o, Qwen, Minimax, etc.).

    Pass base_url to use a non-OpenAI endpoint (e.g. Qwen, Minimax, DeepSeek).
    """

    def __init__(self, model: str, api_key: str, base_url: str | None = None, max_tokens: int = 4096, temperature: float | None = None, top_p: float | None = None, seed: int | None = None) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._top_p = top_p
        self._seed = seed
        self._client = None
        self.last_usage: LLMUsage | None = None
        self.last_response_model: str | None = None
        self.last_system_fingerprint: str | None = None

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
                kwargs = {"api_key": self._api_key}
                if self._base_url:
                    kwargs["base_url"] = self._base_url
                self._client = AsyncOpenAI(**kwargs)
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
        self.last_response_model = None
        self.last_system_fingerprint = None
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        try:
            request = {
                "model": self._model,
                "messages": messages,
                "max_tokens": self._max_tokens,
                "timeout": 1800.0,
            }
            if self._temperature is not None: request["temperature"] = self._temperature
            if self._top_p is not None: request["top_p"] = self._top_p
            if self._seed is not None: request["seed"] = self._seed
            response = await client.chat.completions.create(**request)
            self.last_response_model = getattr(response, "model", None)
            self.last_system_fingerprint = getattr(response, "system_fingerprint", None)
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
