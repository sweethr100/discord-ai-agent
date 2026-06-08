from __future__ import annotations

from typing import Any, Sequence

from providers.base import HttpProvider, Message, ProviderOptions
from providers.openai_provider import _extract_openai_content


class LocalProvider(HttpProvider):
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout_seconds: float = 60.0,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

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

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        data = await self._post_json(
            url=f"{self.base_url}/chat/completions",
            headers=headers,
            payload=payload,
        )
        return _extract_openai_content(data)
