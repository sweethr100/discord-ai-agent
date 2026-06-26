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


@dataclass(frozen=True)
class GuildCustomStyle:
    name: str
    description: str
    prompt: str


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

    def get_channel_style(self, guild_id: int | None, channel_id: int | None) -> str:
        if guild_id is None or channel_id is None:
            return ""
        channel_styles = self._get_guild(guild_id).get("channel_styles", {})
        if not isinstance(channel_styles, dict):
            return ""
        return str(channel_styles.get(str(channel_id)) or "").strip()

    def set_channel_style(self, guild_id: int, channel_id: int, style: str) -> None:
        guild = self._ensure_guild(guild_id)
        channel_styles = guild.setdefault("channel_styles", {})
        channel_styles[str(channel_id)] = style
        self._save()

    def remove_channel_style(self, guild_id: int, channel_id: int) -> bool:
        guild = self._ensure_guild(guild_id)
        channel_styles = guild.setdefault("channel_styles", {})
        removed = channel_styles.pop(str(channel_id), None) is not None
        if removed:
            self._save()
        return removed

    def list_admin_delegates(self, guild_id: int | None) -> list[int]:
        if guild_id is None:
            return []

        raw_delegates = self._get_guild(guild_id).get("admin_delegates", [])
        if not isinstance(raw_delegates, list):
            return []

        delegate_ids: list[int] = []
        seen: set[int] = set()
        for raw_user_id in raw_delegates:
            try:
                user_id = int(raw_user_id)
            except (TypeError, ValueError):
                continue
            if user_id in seen:
                continue
            delegate_ids.append(user_id)
            seen.add(user_id)
        return sorted(delegate_ids)

    def is_admin_delegate(self, guild_id: int | None, user_id: int | None) -> bool:
        if guild_id is None or user_id is None:
            return False
        return user_id in self.list_admin_delegates(guild_id)

    def add_admin_delegate(self, guild_id: int, user_id: int) -> bool:
        guild = self._ensure_guild(guild_id)
        delegates = guild.setdefault("admin_delegates", [])
        if not isinstance(delegates, list):
            delegates = []
            guild["admin_delegates"] = delegates

        user_id_text = str(user_id)
        if user_id_text in {str(delegate) for delegate in delegates}:
            return False

        delegates.append(user_id_text)
        self._save()
        return True

    def remove_admin_delegate(self, guild_id: int, user_id: int) -> bool:
        guild = self._ensure_guild(guild_id)
        delegates = guild.setdefault("admin_delegates", [])
        if not isinstance(delegates, list):
            guild["admin_delegates"] = []
            return False

        before = len(delegates)
        guild["admin_delegates"] = [
            delegate
            for delegate in delegates
            if str(delegate) != str(user_id)
        ]
        removed = len(guild["admin_delegates"]) != before
        if removed:
            self._save()
        return removed

    def list_channel_styles(self, guild_id: int | None) -> list[tuple[int, str]]:
        if guild_id is None:
            return []

        channel_styles = self._get_guild(guild_id).get("channel_styles", {})
        if not isinstance(channel_styles, dict):
            return []

        result: list[tuple[int, str]] = []
        for channel_id_text, style in channel_styles.items():
            try:
                channel_id = int(channel_id_text)
            except ValueError:
                continue
            style_name = str(style).strip()
            if style_name:
                result.append((channel_id, style_name))
        return sorted(result, key=lambda item: item[0])

    def get_custom_style_prompt(self, guild_id: int | None) -> str:
        if guild_id is None:
            return ""
        return self._get_guild(guild_id).get("custom_style_prompt", "")

    def set_custom_style_prompt(self, guild_id: int, prompt: str) -> None:
        guild = self._ensure_guild(guild_id)
        guild["custom_style_prompt"] = prompt.strip()
        self._save()

    def get_custom_style(self, guild_id: int | None, name: str) -> GuildCustomStyle | None:
        if guild_id is None:
            return None

        styles = self._get_guild(guild_id).get("custom_styles", {})
        if not isinstance(styles, dict):
            return None

        raw = styles.get(name)
        if not isinstance(raw, dict):
            return None

        return self._to_custom_style(name, raw)

    def list_custom_styles(self, guild_id: int | None) -> list[GuildCustomStyle]:
        if guild_id is None:
            return []

        styles = self._get_guild(guild_id).get("custom_styles", {})
        if not isinstance(styles, dict):
            return []

        result: list[GuildCustomStyle] = []
        for name, raw in styles.items():
            if isinstance(raw, dict):
                result.append(self._to_custom_style(str(name), raw))
        return sorted(result, key=lambda style: style.name.casefold())

    def upsert_custom_style(
        self,
        guild_id: int,
        *,
        name: str,
        description: str,
        prompt: str,
    ) -> None:
        guild = self._ensure_guild(guild_id)
        styles = guild.setdefault("custom_styles", {})
        styles[name] = {
            "description": description.strip(),
            "prompt": prompt.strip(),
        }
        self._save()

    def modify_custom_style(
        self,
        guild_id: int,
        *,
        name: str,
        description: str | None = None,
        prompt: str | None = None,
    ) -> bool:
        guild = self._ensure_guild(guild_id)
        styles = guild.setdefault("custom_styles", {})
        raw = styles.get(name)
        if not isinstance(raw, dict):
            return False

        if description is not None:
            raw["description"] = description.strip()
        if prompt is not None:
            raw["prompt"] = prompt.strip()
        self._save()
        return True

    def remove_custom_style(self, guild_id: int, name: str) -> bool:
        guild = self._ensure_guild(guild_id)
        styles = guild.setdefault("custom_styles", {})
        removed = styles.pop(name, None) is not None
        if not removed:
            return False

        if guild.get("default_style") == name:
            guild["default_style"] = "default"

        channel_styles = guild.setdefault("channel_styles", {})
        for channel_id, style in list(channel_styles.items()):
            if style == name:
                channel_styles.pop(channel_id, None)

        self._save()
        return True

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
                "custom_styles": {},
                "channel_styles": {},
                "autochannels": {},
                "admin_delegates": [],
            },
        )

        if not isinstance(guild, dict):
            guild = {}
            guilds[str(guild_id)] = guild

        guild.setdefault("default_style", "default")
        guild.setdefault("custom_style_prompt", "")
        guild.setdefault("custom_styles", {})
        guild.setdefault("channel_styles", {})
        guild.setdefault("autochannels", {})
        guild.setdefault("admin_delegates", [])
        return guild

    def _to_custom_style(self, name: str, raw: dict[str, Any]) -> GuildCustomStyle:
        return GuildCustomStyle(
            name=name,
            description=str(raw.get("description") or "").strip(),
            prompt=str(raw.get("prompt") or "").strip(),
        )

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
