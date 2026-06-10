from __future__ import annotations

from typing import Any, Sequence

from providers.base import HttpProvider, Message, ProviderOptions, ProviderResponseError


class AnthropicProvider(HttpProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.anthropic.com/v1"

    async def generate_response(
        self,
        messages: Sequence[Message],
        options: ProviderOptions | None = None,
    ) -> str:
        options = options or ProviderOptions()
        system_prompt, anthropic_messages = _convert_messages(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": anthropic_messages,
            "max_tokens": options.max_tokens or 1024,
        }

        if system_prompt:
            payload["system"] = system_prompt
        if options.temperature is not None:
            payload["temperature"] = options.temperature

        data = await self._post_json(
            url=f"{self.base_url}/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            payload=payload,
        )
        return _extract_anthropic_content(data)


def _convert_messages(messages: Sequence[Message]) -> tuple[str, list[dict[str, str]]]:
    system_parts: list[str] = []
    converted: list[dict[str, str]] = []

    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if not content:
            continue

        if role == "system":
            system_parts.append(content)
            continue

        converted.append(
            {
                "role": "assistant" if role == "assistant" else "user",
                "content": content,
            }
        )

    if not converted:
        raise ProviderResponseError("Anthropic request did not include any user content.")

    return "\n\n".join(system_parts), converted


def _extract_anthropic_content(data: dict[str, Any]) -> str:
    try:
        content_blocks = data["content"]
    except KeyError as exc:
        raise ProviderResponseError("Anthropic response did not include content.") from exc

    if not isinstance(content_blocks, list):
        raise ProviderResponseError("Anthropic response content had an unexpected shape.")

    text = "".join(
        block.get("text", "")
        for block in content_blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()

    if not text:
        raise ProviderResponseError("Anthropic response content was empty.")

    if data.get("stop_reason") == "max_tokens":
        text += "\n\n[답변이 AI_MAX_TOKENS 제한 때문에 중간에 멈췄어요. 더 길게 보려면 .env의 AI_MAX_TOKENS 값을 올린 뒤 봇을 재시작하세요.]"

    return text
