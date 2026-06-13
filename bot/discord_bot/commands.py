from __future__ import annotations

import re
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from agent.styles import STYLE_NAMES, STYLE_PRESETS, format_style_presets, is_valid_style
from discord_bot.handlers import handle_ai_request
from discord_bot.settings_store import AUTOCHANNEL_MODES
from utils.split_message import split_discord_message

if TYPE_CHECKING:
    from discord_bot.client import DiscordAIBot


MODE_CHOICES = [
    app_commands.Choice(name=mode, value=mode)
    for mode in AUTOCHANNEL_MODES
]
STYLE_NAME_PATTERN = re.compile(r"^[a-z0-9_-]{1,32}$")


def register_commands(bot: "DiscordAIBot") -> None:
    async def style_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return _style_choices(bot, interaction, current, include_builtin=True, include_custom=True)

    async def custom_style_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return _style_choices(bot, interaction, current, include_builtin=False, include_custom=True)

    async def channel_style_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return _style_choices(
            bot,
            interaction,
            current,
            include_builtin=True,
            include_custom=True,
            include_server_default=True,
        )

    @bot.tree.command(name="ai", description="AI에게 직접 질문합니다.")
    @app_commands.describe(
        message="AI에게 보낼 내용",
        style="이 요청에만 임시로 적용할 AI 스타일",
    )
    @app_commands.autocomplete(style=style_autocomplete)
    async def ai_command(
        interaction: discord.Interaction,
        message: str,
        style: str = "",
    ) -> None:
        await handle_ai_request(
            bot=bot,
            prompt=message,
            interaction=interaction,
            source="slash_command",
            style_name=style or None,
        )

    autochannel_group = app_commands.Group(
        name="autochannel",
        description="AI 자동 응답 채널을 관리합니다.",
    )

    @autochannel_group.command(name="add", description="AI 자동 응답 채널을 추가합니다.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.describe(
        channel="AI가 자동으로 응답할 채널",
        mode="자동 응답 방식",
        keywords="keyword 모드에서 반응할 쉼표로 구분된 키워드",
    )
    @app_commands.choices(mode=MODE_CHOICES)
    async def autochannel_add(
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        mode: str,
        keywords: str = "",
    ) -> None:
        if not await _require_manage_channels(interaction):
            return

        guild_id = _get_guild_id(interaction)
        if guild_id is None:
            await _send_ephemeral(interaction, "서버 안에서만 사용할 수 있는 명령어예요.")
            return

        keyword_list = _parse_keywords(keywords)
        if mode == "keyword" and not keyword_list:
            await _send_ephemeral(interaction, "`keyword` 모드는 keywords 값을 하나 이상 입력해야 해요.")
            return

        bot.settings.upsert_autochannel(
            guild_id=guild_id,
            channel_id=channel.id,
            mode=mode,
            keywords=keyword_list if mode == "keyword" else [],
        )
        await interaction.response.send_message(
            f"{channel.mention} 채널을 AI 자동 응답 채널로 추가했어요. 모드: `{mode}`"
            f"{_format_keyword_suffix(keyword_list if mode == 'keyword' else [])}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @autochannel_group.command(name="remove", description="AI 자동 응답 채널에서 제거합니다.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.describe(channel="제거할 채널")
    async def autochannel_remove(
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if not await _require_manage_channels(interaction):
            return

        guild_id = _get_guild_id(interaction)
        if guild_id is None:
            await _send_ephemeral(interaction, "서버 안에서만 사용할 수 있는 명령어예요.")
            return

        removed = bot.settings.remove_autochannel(
            guild_id=guild_id,
            channel_id=channel.id,
        )
        if removed:
            await interaction.response.send_message(
                f"{channel.mention} 채널을 AI 자동 응답 채널에서 제거했어요.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await _send_ephemeral(interaction, f"{channel.mention} 채널은 등록되어 있지 않아요.")

    @autochannel_group.command(name="list", description="AI 자동 응답 채널 목록을 봅니다.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    async def autochannel_list(interaction: discord.Interaction) -> None:
        if not await _require_manage_channels(interaction):
            return

        guild_id = _get_guild_id(interaction)
        if guild_id is None or interaction.guild is None:
            await _send_ephemeral(interaction, "서버 안에서만 사용할 수 있는 명령어예요.")
            return

        settings = bot.settings.list_autochannels(guild_id)
        if not settings:
            await interaction.response.send_message("현재 등록된 AI 자동 응답 채널이 없어요.")
            return

        lines = ["현재 AI 자동 응답 채널:"]
        for setting in settings:
            channel = interaction.guild.get_channel(setting.channel_id)
            channel_label = channel.mention if channel else f"<#{setting.channel_id}>"
            lines.append(
                f"- {channel_label}: `{setting.mode}`{_format_keyword_suffix(setting.keywords)}"
            )

        await interaction.response.send_message(
            "\n".join(lines),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    bot.tree.add_command(autochannel_group)

    style_group = app_commands.Group(
        name="style",
        description="서버 기본 AI 스타일을 관리합니다.",
    )

    @style_group.command(name="set", description="서버 기본 AI 스타일을 설정합니다.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(style="서버 기본값으로 사용할 AI 스타일")
    @app_commands.autocomplete(style=style_autocomplete)
    async def style_set(interaction: discord.Interaction, style: str) -> None:
        if not await _require_manage_guild(interaction):
            return

        guild_id = _get_guild_id(interaction)
        if guild_id is None:
            await _send_ephemeral(interaction, "서버 안에서만 사용할 수 있는 명령어예요.")
            return

        style = _normalize_style_name(style) if not is_valid_style(style.strip()) else style.strip()
        if not _style_exists(bot, guild_id, style):
            await _send_ephemeral(interaction, "지원하지 않는 스타일이에요.")
            return

        bot.settings.set_default_style(guild_id, style)
        await interaction.response.send_message(
            f"서버 기본 AI 스타일을 `{style}`로 설정했어요.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @style_group.command(name="show", description="현재 서버의 기본 AI 스타일을 봅니다.")
    @app_commands.guild_only()
    async def style_show(interaction: discord.Interaction) -> None:
        guild_id = _get_guild_id(interaction)
        if guild_id is None:
            await _send_ephemeral(interaction, "서버 안에서만 사용할 수 있는 명령어예요.")
            return

        style = bot.settings.get_default_style(guild_id)
        custom_style = bot.settings.get_custom_style(guild_id, style)
        preset = custom_style or STYLE_PRESETS.get(style, STYLE_PRESETS["default"])
        prompt = (
            bot.settings.get_custom_style_prompt(guild_id)
            if custom_style is None and style == "custom" and bot.settings.get_custom_style_prompt(guild_id)
            else getattr(preset, "prompt", "")
        )
        prompt = prompt or "기본 SYSTEM_PROMPT를 그대로 사용"
        channel_styles = bot.settings.list_channel_styles(guild_id)
        channel_lines = []
        if interaction.guild:
            for channel_id, channel_style in channel_styles:
                channel = interaction.guild.get_channel(channel_id)
                channel_label = channel.mention if channel else f"<#{channel_id}>"
                channel_lines.append(f"- {channel_label}: `{channel_style}`")
        channel_text = "\n채널별 스타일:\n" + "\n".join(channel_lines) if channel_lines else ""
        await interaction.response.send_message(
            f"현재 서버 기본 AI 스타일: `{preset.name}` - {preset.description}\n"
            f"시스템 프롬프트: {prompt}"
            f"{channel_text}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @style_group.command(name="presets", description="사용 가능한 AI 스타일 목록을 봅니다.")
    @app_commands.guild_only()
    async def style_presets(interaction: discord.Interaction) -> None:
        guild_id = _get_guild_id(interaction)
        await _send_command_chunks(
            interaction,
            split_discord_message(
                format_style_presets(
                    bot.settings.list_custom_styles(guild_id),
                    custom_prompt=bot.settings.get_custom_style_prompt(guild_id),
                )
            ),
        )

    @style_group.command(name="add", description="이 서버에만 사용할 AI 스타일을 추가합니다.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        name="추가할 스타일 이름. 영어 소문자, 숫자, _, - 만 가능",
        description="스타일의 간단한 설명",
        prompt="이 스타일에 적용할 시스템 프롬프트",
    )
    async def style_add(
        interaction: discord.Interaction,
        name: str,
        description: str,
        prompt: str,
    ) -> None:
        if not await _require_manage_guild(interaction):
            return

        guild_id = _get_guild_id(interaction)
        if guild_id is None:
            await _send_ephemeral(interaction, "서버 안에서만 사용할 수 있는 명령어예요.")
            return

        name = _normalize_style_name(name)
        description = description.strip()
        prompt = prompt.strip()
        if not _is_valid_custom_style_name(name):
            await _send_ephemeral(interaction, "스타일 이름은 영어 소문자, 숫자, `_`, `-`만 사용해서 1~32자로 입력해 주세요.")
            return
        if is_valid_style(name):
            await _send_ephemeral(interaction, "기본 제공 스타일 이름과 같은 이름은 사용할 수 없어요.")
            return
        if bot.settings.get_custom_style(guild_id, name) is not None:
            await _send_ephemeral(interaction, "이미 이 서버에 같은 이름의 스타일이 있어요. `/style modify`를 사용해 주세요.")
            return
        if not description or not prompt:
            await _send_ephemeral(interaction, "설명과 시스템 프롬프트를 모두 입력해 주세요.")
            return

        bot.settings.upsert_custom_style(
            guild_id,
            name=name,
            description=description,
            prompt=prompt,
        )
        await interaction.response.send_message(
            f"`{name}` 스타일을 이 서버에 추가했어요.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @style_group.command(name="modify", description="이 서버에 추가한 AI 스타일을 수정합니다.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        name="수정할 서버 커스텀 스타일 이름",
        description="새 설명. 비우면 기존 설명 유지",
        prompt="새 시스템 프롬프트. 비우면 기존 프롬프트 유지",
    )
    @app_commands.autocomplete(name=custom_style_autocomplete)
    async def style_modify(
        interaction: discord.Interaction,
        name: str,
        description: str = "",
        prompt: str = "",
    ) -> None:
        if not await _require_manage_guild(interaction):
            return

        guild_id = _get_guild_id(interaction)
        if guild_id is None:
            await _send_ephemeral(interaction, "서버 안에서만 사용할 수 있는 명령어예요.")
            return

        name = _normalize_style_name(name)
        description = description.strip()
        prompt = prompt.strip()
        if not description and not prompt:
            await _send_ephemeral(interaction, "변경할 설명이나 시스템 프롬프트 중 하나는 입력해 주세요.")
            return
        if bot.settings.get_custom_style(guild_id, name) is None:
            await _send_ephemeral(interaction, "이 서버에 추가된 스타일만 수정할 수 있어요.")
            return

        bot.settings.modify_custom_style(
            guild_id,
            name=name,
            description=description or None,
            prompt=prompt or None,
        )
        await interaction.response.send_message(
            f"`{name}` 스타일을 수정했어요.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @style_group.command(name="remove", description="이 서버에 추가한 AI 스타일을 삭제합니다.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(name="삭제할 서버 커스텀 스타일 이름")
    @app_commands.autocomplete(name=custom_style_autocomplete)
    async def style_remove(interaction: discord.Interaction, name: str) -> None:
        if not await _require_manage_guild(interaction):
            return

        guild_id = _get_guild_id(interaction)
        if guild_id is None:
            await _send_ephemeral(interaction, "서버 안에서만 사용할 수 있는 명령어예요.")
            return

        name = _normalize_style_name(name)
        if bot.settings.get_custom_style(guild_id, name) is None:
            await _send_ephemeral(interaction, "이 서버에 추가된 스타일만 삭제할 수 있어요.")
            return

        bot.settings.remove_custom_style(guild_id, name)
        await interaction.response.send_message(
            f"`{name}` 스타일을 삭제했어요. 기본값이나 채널 스타일로 쓰고 있었다면 `default`로 되돌렸어요.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @style_group.command(name="channel", description="특정 채널의 AI 스타일을 설정합니다.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        channel="스타일을 적용할 채널",
        style="채널에 적용할 스타일. server_default는 채널별 설정 제거",
    )
    @app_commands.autocomplete(style=channel_style_autocomplete)
    async def style_channel(
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        style: str,
    ) -> None:
        if not await _require_manage_guild(interaction):
            return

        guild_id = _get_guild_id(interaction)
        if guild_id is None:
            await _send_ephemeral(interaction, "서버 안에서만 사용할 수 있는 명령어예요.")
            return

        style = _normalize_style_name(style) if not is_valid_style(style.strip()) else style.strip()
        if style == "server_default":
            removed = bot.settings.remove_channel_style(guild_id, channel.id)
            message = (
                f"{channel.mention} 채널의 채널별 스타일 설정을 제거했어요."
                if removed
                else f"{channel.mention} 채널에는 채널별 스타일 설정이 없어요."
            )
            await interaction.response.send_message(message, allowed_mentions=discord.AllowedMentions.none())
            return

        if not _style_exists(bot, guild_id, style):
            await _send_ephemeral(interaction, "지원하지 않는 스타일이에요.")
            return

        bot.settings.set_channel_style(guild_id, channel.id, style)
        await interaction.response.send_message(
            f"{channel.mention} 채널의 AI 스타일을 `{style}`로 설정했어요.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    bot.tree.add_command(style_group)


def _get_guild_id(interaction: discord.Interaction) -> int | None:
    return interaction.guild_id


async def _require_manage_channels(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    if permissions and (permissions.administrator or permissions.manage_channels):
        return True

    await _send_ephemeral(interaction, "이 명령어는 관리자 또는 Manage Channels 권한이 필요해요.")
    return False


async def _require_manage_guild(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    if permissions and (permissions.administrator or permissions.manage_guild):
        return True

    await _send_ephemeral(interaction, "이 명령어는 관리자 또는 Manage Guild 권한이 필요해요.")
    return False


async def _send_ephemeral(interaction: discord.Interaction, content: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(
            content,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    await interaction.response.send_message(
        content,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _send_command_chunks(
    interaction: discord.Interaction,
    chunks: list[str],
) -> None:
    if not chunks:
        chunks = ["응답이 비어 있어요."]

    if not interaction.response.is_done():
        await interaction.response.send_message(
            chunks[0],
            allowed_mentions=discord.AllowedMentions.none(),
        )
        start = 1
    else:
        start = 0

    for chunk in chunks[start:]:
        await interaction.followup.send(
            chunk,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def _style_choices(
    bot: "DiscordAIBot",
    interaction: discord.Interaction,
    current: str,
    *,
    include_builtin: bool,
    include_custom: bool,
    include_server_default: bool = False,
) -> list[app_commands.Choice[str]]:
    current = current.casefold().strip()
    names: list[str] = []
    if include_server_default:
        names.append("server_default")
    if include_builtin:
        names.extend(STYLE_NAMES)
    if include_custom:
        names.extend(style.name for style in bot.settings.list_custom_styles(interaction.guild_id))

    seen: set[str] = set()
    choices: list[app_commands.Choice[str]] = []
    for name in names:
        normalized = name.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        if current and current not in normalized:
            continue
        choices.append(app_commands.Choice(name=name, value=name))
        if len(choices) >= 25:
            break
    return choices


def _style_exists(bot: "DiscordAIBot", guild_id: int, style: str) -> bool:
    return is_valid_style(style) or bot.settings.get_custom_style(guild_id, style) is not None


def _normalize_style_name(name: str) -> str:
    return name.strip().casefold().replace(" ", "_")


def _is_valid_custom_style_name(name: str) -> bool:
    return bool(STYLE_NAME_PATTERN.fullmatch(name))


def _parse_keywords(keywords: str | None) -> list[str]:
    if not keywords:
        return []

    parsed: list[str] = []
    seen: set[str] = set()
    for keyword in keywords.split(","):
        clean = keyword.strip()
        normalized = clean.casefold()
        if clean and normalized not in seen:
            parsed.append(clean)
            seen.add(normalized)

    return parsed


def _format_keyword_suffix(keywords: list[str]) -> str:
    if not keywords:
        return ""
    return f" | keywords: {', '.join(f'`{keyword}`' for keyword in keywords)}"
