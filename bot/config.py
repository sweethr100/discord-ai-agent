from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from agent.system_prompt import DEFAULT_SYSTEM_PROMPT


SUPPORTED_PROVIDERS = {"openai", "gemini", "anthropic", "local"}


class ConfigError(ValueError):
    """Raised when required runtime configuration is missing or invalid."""


def _get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_optional_int(name: str) -> int | None:
    value = _get_env(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} 값은 숫자여야 합니다.") from exc


def _get_float(name: str, default: float) -> float:
    value = _get_env(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} 값은 숫자여야 합니다.") from exc


@dataclass(frozen=True)
class AppConfig:
    discord_token: str
    discord_guild_id: int | None
    ai_provider: str
    system_prompt: str
    ai_temperature: float
    ai_max_tokens: int
    request_timeout_seconds: float
    openai_api_key: str
    openai_model: str
    gemini_api_key: str
    gemini_model: str
    anthropic_api_key: str
    anthropic_model: str
    local_base_url: str
    local_model: str
    local_api_key: str

    def validate(self) -> None:
        if not self.discord_token:
            raise ConfigError("DISCORD_TOKEN 값이 비어 있습니다.")

        if self.ai_provider not in SUPPORTED_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
            raise ConfigError(
                f"AI_PROVIDER 값은 {supported} 중 하나여야 합니다. 현재 값: {self.ai_provider!r}"
            )

        if self.ai_max_tokens <= 0:
            raise ConfigError("AI_MAX_TOKENS 값은 1 이상이어야 합니다.")

        if self.request_timeout_seconds <= 0:
            raise ConfigError("REQUEST_TIMEOUT_SECONDS 값은 1 이상이어야 합니다.")

        if self.ai_provider == "openai":
            if not self.openai_api_key:
                raise ConfigError("AI_PROVIDER=openai 인 경우 OPENAI_API_KEY가 필요합니다.")
            if not self.openai_model:
                raise ConfigError("AI_PROVIDER=openai 인 경우 OPENAI_MODEL이 필요합니다.")

        if self.ai_provider == "gemini":
            if not self.gemini_api_key:
                raise ConfigError("AI_PROVIDER=gemini 인 경우 GEMINI_API_KEY가 필요합니다.")
            if not self.gemini_model:
                raise ConfigError("AI_PROVIDER=gemini 인 경우 GEMINI_MODEL이 필요합니다.")

        if self.ai_provider == "anthropic":
            if not self.anthropic_api_key:
                raise ConfigError("AI_PROVIDER=anthropic 인 경우 ANTHROPIC_API_KEY가 필요합니다.")
            if not self.anthropic_model:
                raise ConfigError("AI_PROVIDER=anthropic 인 경우 ANTHROPIC_MODEL이 필요합니다.")

        if self.ai_provider == "local":
            if not self.local_base_url:
                raise ConfigError("AI_PROVIDER=local 인 경우 LOCAL_BASE_URL이 필요합니다.")
            if not self.local_model:
                raise ConfigError("AI_PROVIDER=local 인 경우 LOCAL_MODEL이 필요합니다.")


def load_config() -> AppConfig:
    load_dotenv()

    provider = _get_env("AI_PROVIDER", "openai").lower()
    config = AppConfig(
        discord_token=_get_env("DISCORD_TOKEN"),
        discord_guild_id=_get_optional_int("DISCORD_GUILD_ID"),
        ai_provider=provider,
        system_prompt=_get_env("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
        ai_temperature=_get_float("AI_TEMPERATURE", 0.7),
        ai_max_tokens=_get_optional_int("AI_MAX_TOKENS") or 1024,
        request_timeout_seconds=_get_float("REQUEST_TIMEOUT_SECONDS", 60.0),
        openai_api_key=_get_env("OPENAI_API_KEY"),
        openai_model=_get_env("OPENAI_MODEL", "gpt-4o-mini"),
        gemini_api_key=_get_env("GEMINI_API_KEY"),
        gemini_model=_get_env("GEMINI_MODEL", "gemini-1.5-flash"),
        anthropic_api_key=_get_env("ANTHROPIC_API_KEY"),
        anthropic_model=_get_env("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
        local_base_url=_get_env("LOCAL_BASE_URL", "http://localhost:11434/v1").rstrip("/"),
        local_model=_get_env("LOCAL_MODEL", "llama3.1"),
        local_api_key=_get_env("LOCAL_API_KEY"),
    )
    config.validate()
    return config
