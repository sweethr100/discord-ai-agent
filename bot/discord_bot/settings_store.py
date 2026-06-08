from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


AUTOCHANNEL_MODES = ("always", "question_only", "keyword")


@dataclass(frozen=True)
class AutoChannelSettings:
    channel_id: int
    mode: str
    keywords: list[str] = field(default_factory=list)


class GuildSettingsStore:
    def __init__(self, path: str | Path = "data/guild_settings.json") -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = {"guilds": {}}
        self._load()

    def get_default_style(self, guild_id: int | None) -> str:
        if guild_id is None:
            return "default"
        return self._get_guild(guild_id).get("default_style", "default")

    def set_default_style(self, guild_id: int, style: str) -> None:
        guild = self._ensure_guild(guild_id)
        guild["default_style"] = style
        self._save()

    def get_custom_style_prompt(self, guild_id: int | None) -> str:
        if guild_id is None:
            return ""
        return self._get_guild(guild_id).get("custom_style_prompt", "")

    def set_custom_style_prompt(self, guild_id: int, prompt: str) -> None:
        guild = self._ensure_guild(guild_id)
        guild["custom_style_prompt"] = prompt.strip()
        self._save()

    def upsert_autochannel(
        self,
        *,
        guild_id: int,
        channel_id: int,
        mode: str,
        keywords: list[str],
    ) -> None:
        if mode not in AUTOCHANNEL_MODES:
            raise ValueError(f"Unsupported autochannel mode: {mode}")

        guild = self._ensure_guild(guild_id)
        autochannels = guild.setdefault("autochannels", {})
        autochannels[str(channel_id)] = {
            "mode": mode,
            "keywords": keywords,
        }
        self._save()

    def remove_autochannel(self, *, guild_id: int, channel_id: int) -> bool:
        guild = self._ensure_guild(guild_id)
        autochannels = guild.setdefault("autochannels", {})
        removed = autochannels.pop(str(channel_id), None) is not None
        if removed:
            self._save()
        return removed

    def get_autochannel(
        self,
        *,
        guild_id: int | None,
        channel_id: int,
    ) -> AutoChannelSettings | None:
        if guild_id is None:
            return None

        guild = self._get_guild(guild_id)
        raw = guild.get("autochannels", {}).get(str(channel_id))
        if not isinstance(raw, dict):
            return None

        return self._to_autochannel_settings(channel_id, raw)

    def list_autochannels(self, guild_id: int) -> list[AutoChannelSettings]:
        guild = self._get_guild(guild_id)
        autochannels = guild.get("autochannels", {})
        if not isinstance(autochannels, dict):
            return []

        settings: list[AutoChannelSettings] = []
        for channel_id_text, raw in autochannels.items():
            if not isinstance(raw, dict):
                continue
            try:
                channel_id = int(channel_id_text)
            except ValueError:
                continue
            settings.append(self._to_autochannel_settings(channel_id, raw))

        return sorted(settings, key=lambda item: item.channel_id)

    def _load(self) -> None:
        if not self.path.exists():
            return

        with self.path.open("r", encoding="utf-8") as file:
            loaded = json.load(file)

        if isinstance(loaded, dict) and isinstance(loaded.get("guilds"), dict):
            self._data = loaded

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = Path(f"{self.path}.tmp")
        with tmp_path.open("w", encoding="utf-8") as file:
            json.dump(self._data, file, ensure_ascii=False, indent=2)
            file.write("\n")
        tmp_path.replace(self.path)

    def _get_guild(self, guild_id: int) -> dict[str, Any]:
        guilds = self._data.setdefault("guilds", {})
        guild = guilds.get(str(guild_id), {})
        return guild if isinstance(guild, dict) else {}

    def _ensure_guild(self, guild_id: int) -> dict[str, Any]:
        guilds = self._data.setdefault("guilds", {})
        guild = guilds.setdefault(
            str(guild_id),
            {
                "default_style": "default",
                "custom_style_prompt": "",
                "autochannels": {},
            },
        )

        if not isinstance(guild, dict):
            guild = {}
            guilds[str(guild_id)] = guild

        guild.setdefault("default_style", "default")
        guild.setdefault("custom_style_prompt", "")
        guild.setdefault("autochannels", {})
        return guild

    def _to_autochannel_settings(
        self,
        channel_id: int,
        raw: dict[str, Any],
    ) -> AutoChannelSettings:
        mode = raw.get("mode", "always")
        if mode not in AUTOCHANNEL_MODES:
            mode = "always"

        raw_keywords = raw.get("keywords", [])
        keywords = [
            str(keyword).strip()
            for keyword in raw_keywords
            if str(keyword).strip()
        ]

        return AutoChannelSettings(
            channel_id=channel_id,
            mode=mode,
            keywords=keywords,
        )
