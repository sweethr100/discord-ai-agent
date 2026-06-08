from __future__ import annotations

from typing import TYPE_CHECKING

from providers.anthropic_provider import AnthropicProvider
from providers.base import AIProvider
from providers.gemini_provider import GeminiProvider
from providers.local_provider import LocalProvider
from providers.openai_provider import OpenAIProvider

if TYPE_CHECKING:
    from config import AppConfig


def create_provider(config: "AppConfig") -> AIProvider:
    if config.ai_provider == "openai":
        return OpenAIProvider(
            api_key=config.openai_api_key,
            model=config.openai_model,
            timeout_seconds=config.request_timeout_seconds,
        )

    if config.ai_provider == "gemini":
        return GeminiProvider(
            api_key=config.gemini_api_key,
            model=config.gemini_model,
            timeout_seconds=config.request_timeout_seconds,
        )

    if config.ai_provider == "anthropic":
        return AnthropicProvider(
            api_key=config.anthropic_api_key,
            model=config.anthropic_model,
            timeout_seconds=config.request_timeout_seconds,
        )

    if config.ai_provider == "local":
        return LocalProvider(
            base_url=config.local_base_url,
            model=config.local_model,
            api_key=config.local_api_key,
            timeout_seconds=config.request_timeout_seconds,
        )

    raise ValueError(f"Unsupported provider: {config.ai_provider}")
