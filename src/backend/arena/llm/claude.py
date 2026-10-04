"""Claude API provider via Anthropic SDK."""

from __future__ import annotations

import json

from .base import AbstractLLMProvider, LLMError, LLMResponse, LLMUsage, ToolCall


def _claude_messages(messages: list[dict]) -> list[dict]:
    """Translate the shared conversation, grouping consecutive tool results."""
    converted: list[dict] = []
    for message in messages:
        role = message["role"]
        content = message.get("content")
        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message["tool_call_id"],
                "content": content or "",
            }
            if converted and converted[-1]["role"] == "user":
                converted[-1]["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block]})
            continue
        if role not in ("user", "assistant"):
            raise LLMError(f"Unsupported conversation role: {role}")
        if content is not None and not isinstance(content, str):
            raise LLMError("Tool conversations require text message content")
        blocks = [{"type": "text", "text": content}] if content else []
        for call in message.get("tool_calls") or ():
            if role != "assistant" or call.get("type", "function") != "function":
                raise LLMError("Tool calls require an assistant function message")
            function = call["function"]
            arguments = json.loads(function["arguments"])
            if not isinstance(arguments, dict):
                raise LLMError("Claude tool input must be a JSON object")
            blocks.append({
                "type": "tool_use", "id": call["id"],
                "name": function["name"], "input": arguments,
            })
        converted.append({"role": role, "content": blocks})
    return converted


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

    @property
    def supports_tools(self) -> bool:
        return True

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

    async def generate_turn(
        self,
        messages: list[dict],
        system_prompt: str = "",
        tools: list[dict] | None = None,
        *,
        allow_tools: bool = True,
    ) -> LLMResponse:
        self.last_usage = None
        self.last_response_model = None
        self.last_system_fingerprint = None
        try:
            request = {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "system": system_prompt or "You are a Doudizhu AI player.",
                "messages": _claude_messages(messages),
            }
            if tools:
                request["tools"] = [
                    {
                        "name": definition["name"],
                        "description": definition.get("description", ""),
                        "input_schema": definition["parameters"],
                    }
                    for definition in tools
                ]
            if not allow_tools:
                request["tool_choice"] = {"type": "none"}
            if self._temperature is not None:
                request["temperature"] = self._temperature
            if self._top_p is not None:
                request["top_p"] = self._top_p
            response = await self._get_client().messages.create(**request)
            self.last_response_model = getattr(response, "model", None)
            usage = getattr(response, "usage", None)
            if usage is not None:
                self.last_usage = LLMUsage.from_counts(
                    getattr(usage, "input_tokens", None),
                    getattr(usage, "output_tokens", None),
                )
            texts, calls = [], []
            for block in response.content:
                kind = getattr(block, "type", "text")
                if kind == "text":
                    texts.append(block.text)
                elif kind == "tool_use":
                    if not isinstance(block.id, str) or not block.id or not isinstance(block.name, str) or not block.name:
                        raise LLMError("Malformed Claude tool call")
                    if not isinstance(block.input, dict):
                        raise LLMError("Claude tool input must be a JSON object")
                    calls.append(ToolCall(
                        block.id, block.name,
                        json.dumps(block.input, ensure_ascii=False, separators=(",", ":")),
                    ))
            return LLMResponse("".join(texts), tuple(calls))
        except Exception as exc:
            if isinstance(exc, LLMError):
                raise
            raise LLMError(f"Claude API call failed: {exc}") from exc
