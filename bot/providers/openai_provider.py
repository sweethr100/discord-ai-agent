from __future__ import annotations

from typing import Any, Sequence

from providers.base import HttpProvider, Message, ProviderOptions, ProviderResponseError


class OpenAIProvider(HttpProvider):
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
        self.base_url = "https://api.openai.com/v1"

    async def generate_response(
        self,
        messages: Sequence[Message],
        options: ProviderOptions | None = None,
    ) -> str:
        options = options or ProviderOptions()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
        }

        if options.temperature is not None:
            payload["temperature"] = options.temperature
        if options.max_tokens is not None:
            payload["max_tokens"] = options.max_tokens

        data = await self._post_json(
            url=f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
        )
        return _extract_openai_content(data)


def _extract_openai_content(data: dict[str, Any]) -> str:
    try:
        first_choice = data["choices"][0]
        message = first_choice["message"]
        content = message["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderResponseError("OpenAI response did not include message content.") from exc

    if isinstance(content, list):
        text_parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
        ]
        content = "".join(text_parts)

    if not isinstance(content, str) or not content.strip():
        finish_reason = first_choice.get("finish_reason", "unknown")
        message_keys = ", ".join(sorted(str(key) for key in message.keys())) if isinstance(message, dict) else "unknown"
        raise ProviderResponseError(
            f"OpenAI response content was empty (finish_reason={finish_reason}, message_keys={message_keys})."
        )

    content = content.strip()
    if first_choice.get("finish_reason") == "length":
        content += "\n\n[답변이 출력 길이 한도 때문에 중간에 멈췄어요. AI_MAX_TOKENS를 직접 설정했다면 값을 비우거나 더 크게 조정하세요.]"

    return content
