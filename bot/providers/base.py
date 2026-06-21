from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
from typing import Any, Sequence

import aiohttp


MessageContent = str | list[dict[str, Any]]
Message = dict[str, Any]


@dataclass(frozen=True)
class ProviderOptions:
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None


class ProviderError(RuntimeError):
    """Base exception for provider call failures."""


class ProviderResponseError(ProviderError):
    """Raised when a provider returns an invalid or unsuccessful response."""


class ProviderHTTPStatusError(ProviderResponseError):
    """Raised when a provider returns a non-success HTTP status."""

    def __init__(
        self,
        *,
        status_code: int,
        response_text: str,
        provider_status: str = "",
        provider_message: str = "",
    ) -> None:
        self.status_code = status_code
        self.response_text = response_text
        self.provider_status = provider_status
        self.provider_message = provider_message
        detail = provider_message or response_text[:500]
        super().__init__(f"Provider HTTP {status_code}: {detail}")


class ProviderQuotaError(ProviderHTTPStatusError):
    """Raised when a provider rejects the request because quota or credits are exhausted."""


class AIProvider(ABC):
    @abstractmethod
    async def generate_response(
        self,
        messages: Sequence[Message],
        options: ProviderOptions | None = None,
    ) -> str:
        """Generate a response from a common chat message interface."""


class HttpProvider(AIProvider):
    def __init__(self, timeout_seconds: float = 60.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def _post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                response_text = await response.text()
                if response.status < 200 or response.status >= 300:
                    provider_status, provider_message = _extract_provider_error(response_text)
                    error_type = (
                        ProviderQuotaError
                        if _is_quota_error(
                            status_code=response.status,
                            provider_status=provider_status,
                            provider_message=provider_message,
                            response_text=response_text,
                        )
                        else ProviderHTTPStatusError
                    )
                    raise error_type(
                        status_code=response.status,
                        response_text=response_text[:2000],
                        provider_status=provider_status,
                        provider_message=provider_message,
                    )

                try:
                    data = await response.json()
                except aiohttp.ContentTypeError as exc:
                    raise ProviderResponseError(
                        f"Provider returned non-JSON response: {response_text[:500]}"
                    ) from exc

        if not isinstance(data, dict):
            raise ProviderResponseError("Provider returned an unexpected JSON shape.")

        return data


def _extract_provider_error(response_text: str) -> tuple[str, str]:
    try:
        data = json.loads(response_text)
    except json.JSONDecodeError:
        return "", ""

    if not isinstance(data, dict):
        return "", ""

    error = data.get("error")
    if not isinstance(error, dict):
        return "", ""

    status = error.get("status", "")
    message = error.get("message", "")
    return str(status), str(message)


def _is_quota_error(
    *,
    status_code: int,
    provider_status: str,
    provider_message: str,
    response_text: str,
) -> bool:
    combined = f"{provider_status} {provider_message} {response_text}".casefold()
    quota_markers = (
        "resource_exhausted",
        "quota",
        "rate limit",
        "rate_limit",
        "credits are depleted",
        "insufficient_quota",
        "billing",
    )
    return status_code == 429 or any(marker in combined for marker in quota_markers)
