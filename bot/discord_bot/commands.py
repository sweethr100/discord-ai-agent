from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from agent.styles import STYLE_NAMES, STYLE_PRESETS, format_style_presets, is_valid_style
from discord_bot.handlers import handle_ai_request
from discord_bot.settings_store import AUTOCHANNEL_MODES

if TYPE_CHECKING:
    from discord_bot.client import DiscordAIBot


STYLE_CHOICES = [
    app_commands.Choice(name=style, value=style)
    for style in STYLE_NAMES
]
MODE_CHOICES = [
    app_commands.Choice(name=mode, value=mode)
    for mode in AUTOCHANNEL_MODES
]


def register_commands(bot: "DiscordAIBot") -> None:
    @bot.tree.command(name="ai", description="AI에게 직접 질문합니다.")
    @app_commands.describe(
        message="AI에게 보낼 내용",
        style="이 요청에만 임시로 적용할 AI 스타일",
    )
    @app_commands.choices(style=STYLE_CHOICES)
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

    @autochannel_group.command(name="mode", description="등록된 채널의 자동 응답 방식을 바꿉니다.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.describe(
        channel="모드를 변경할 채널",
        mode="새 자동 응답 방식",
        keywords="keyword 모드에서 반응할 쉼표로 구분된 키워드",
    )
    @app_commands.choices(mode=MODE_CHOICES)
    async def autochannel_mode(
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

        existing = bot.settings.get_autochannel(guild_id=guild_id, channel_id=channel.id)
        if existing is None:
            await _send_ephemeral(interaction, f"{channel.mention} 채널은 아직 등록되어 있지 않아요.")
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
            f"{channel.mention} 채널의 자동 응답 모드를 `{mode}`로 변경했어요."
            f"{_format_keyword_suffix(keyword_list if mode == 'keyword' else [])}",
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
    @app_commands.choices(style=STYLE_CHOICES)
    async def style_set(interaction: discord.Interaction, style: str) -> None:
        if not await _require_manage_guild(interaction):
            return

        guild_id = _get_guild_id(interaction)
        if guild_id is None:
            await _send_ephemeral(interaction, "서버 안에서만 사용할 수 있는 명령어예요.")
            return

        if not is_valid_style(style):
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
        preset = STYLE_PRESETS.get(style, STYLE_PRESETS["default"])
        custom_configured = bool(bot.settings.get_custom_style_prompt(guild_id))
        custom_status = "설정됨" if custom_configured else "미설정"
        await interaction.response.send_message(
            f"현재 서버 기본 AI 스타일: `{preset.name}` - {preset.description}\n"
            f"custom 프롬프트: {custom_status}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @style_group.command(name="presets", description="사용 가능한 AI 스타일 목록을 봅니다.")
    @app_commands.guild_only()
    async def style_presets(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            format_style_presets(),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @style_group.command(name="custom", description="custom 스타일의 시스템 프롬프트를 설정합니다.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(prompt="custom 스타일로 사용할 시스템 프롬프트")
    async def style_custom(interaction: discord.Interaction, prompt: str) -> None:
        if not await _require_manage_guild(interaction):
            return

        guild_id = _get_guild_id(interaction)
        if guild_id is None:
            await _send_ephemeral(interaction, "서버 안에서만 사용할 수 있는 명령어예요.")
            return

        prompt = prompt.strip()
        if not prompt:
            await _send_ephemeral(interaction, "custom 프롬프트 내용을 입력해 주세요.")
            return

        bot.settings.set_custom_style_prompt(guild_id, prompt)
        await interaction.response.send_message(
            "custom 스타일의 시스템 프롬프트를 저장했어요. `/style set style:custom`으로 기본값으로 쓸 수 있어요.",
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
