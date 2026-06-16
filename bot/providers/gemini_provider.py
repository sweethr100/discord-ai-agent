from __future__ import annotations

from typing import Any, Sequence
from urllib.parse import quote

from providers.base import HttpProvider, Message, ProviderOptions, ProviderResponseError


class GeminiProvider(HttpProvider):
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
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    async def generate_response(
        self,
        messages: Sequence[Message],
        options: ProviderOptions | None = None,
    ) -> str:
        options = options or ProviderOptions()
        system_prompt, contents = _convert_messages(messages)
        generation_config: dict[str, Any] = {}

        if options.temperature is not None:
            generation_config["temperature"] = options.temperature
        if options.max_tokens is not None:
            generation_config["maxOutputTokens"] = options.max_tokens
        if options.reasoning_effort is not None:
            generation_config["thinkingConfig"] = {
                "thinkingLevel": options.reasoning_effort,
            }

        payload: dict[str, Any] = {"contents": contents}
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        if generation_config:
            payload["generationConfig"] = generation_config

        model_path = quote(self.model, safe="")
        data = await self._post_json(
            url=f"{self.base_url}/models/{model_path}:generateContent?key={self.api_key}",
            headers={"Content-Type": "application/json"},
            payload=payload,
        )
        return _extract_gemini_content(data)


def _convert_messages(messages: Sequence[Message]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []

    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if not content:
            continue

        if role == "system":
            system_parts.append(content)
            continue

        gemini_role = "model" if role == "assistant" else "user"
        contents.append({"role": gemini_role, "parts": [{"text": content}]})

    if not contents:
        raise ProviderResponseError("Gemini request did not include any user content.")

    return "\n\n".join(system_parts), contents


def _extract_gemini_content(data: dict[str, Any]) -> str:
    try:
        candidates = data["candidates"]
        first_candidate = candidates[0]
        parts = first_candidate["content"]["parts"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderResponseError("Gemini response did not include message content.") from exc

    text = "".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ).strip()

    if not text:
        raise ProviderResponseError("Gemini response content was empty.")

    if str(first_candidate.get("finishReason", "")).upper() == "MAX_TOKENS":
        text += "\n\n[답변이 출력 길이 한도 때문에 중간에 멈췄어요. AI_MAX_TOKENS를 직접 설정했다면 값을 비우거나 더 크게 조정하세요.]"

    return text
