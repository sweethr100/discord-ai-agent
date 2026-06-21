from __future__ import annotations

from typing import Any, Sequence

from providers.base import HttpProvider, Message, MessageContent, ProviderOptions, ProviderResponseError


ANTHROPIC_FALLBACK_MAX_TOKENS = 8192


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
            # Anthropic Messages API requires max_tokens. Other providers can omit
            # this when AI_MAX_TOKENS is unset, but Claude needs a compatibility value.
            "max_tokens": options.max_tokens or ANTHROPIC_FALLBACK_MAX_TOKENS,
        }

        if system_prompt:
            payload["system"] = system_prompt
        if options.temperature is not None:
            payload["temperature"] = options.temperature
        if options.reasoning_effort is not None:
            payload["output_config"] = {"effort": options.reasoning_effort}

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


def _convert_messages(messages: Sequence[Message]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []

    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if not content:
            continue

        if role == "system":
            system_text = _content_text(content)
            if system_text:
                system_parts.append(system_text)
            continue

        converted_content = _convert_content(content)
        if converted_content:
            converted.append(
                {
                    "role": "assistant" if role == "assistant" else "user",
                    "content": converted_content,
                }
            )

    if not converted:
        raise ProviderResponseError("Anthropic request did not include any user content.")

    return "\n\n".join(system_parts), converted


def _convert_content(content: MessageContent) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content

    blocks: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue

        part_type = part.get("type")
        if part_type == "text":
            text = str(part.get("text") or "").strip()
            if text:
                blocks.append({"type": "text", "text": text})
            continue

        if part_type == "image_url":
            image_url = part.get("image_url")
            url = image_url.get("url") if isinstance(image_url, dict) else ""
            parsed = _parse_data_url(str(url or ""))
            if parsed is not None:
                media_type, data = parsed
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": data,
                        },
                    }
                )

    return blocks


def _content_text(content: MessageContent) -> str:
    if isinstance(content, str):
        return content.strip()
    return "\n".join(
        str(part.get("text") or "").strip()
        for part in content
        if isinstance(part, dict) and part.get("type") == "text" and str(part.get("text") or "").strip()
    )


def _parse_data_url(url: str) -> tuple[str, str] | None:
    if not url.startswith("data:") or ";base64," not in url:
        return None

    header, data = url.split(",", 1)
    media_type = header[5:].split(";", 1)[0].strip() or "image/jpeg"
    if media_type not in {"image/jpeg", "image/png", "image/gif", "image/webp"} or not data.strip():
        return None
    return media_type, data


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
        text += "\n\n[답변이 출력 길이 한도 때문에 중간에 멈췄어요. AI_MAX_TOKENS를 직접 설정했다면 값을 비우거나 더 크게 조정하세요.]"

    return text
