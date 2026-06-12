from __future__ import annotations

import re
from typing import TYPE_CHECKING

import discord

from agent.styles import build_system_prompt
from discord_bot.agent_actions import try_handle_agent_action
from discord_bot.channel_context import build_channel_context
from discord_bot.settings_store import AutoChannelSettings
from providers.base import ProviderHTTPStatusError, ProviderQuotaError
from utils.discord_markdown import normalize_discord_markdown
from utils.logger import get_logger
from utils.split_message import split_discord_message

if TYPE_CHECKING:
    from discord_bot.client import DiscordAIBot


logger = get_logger(__name__)
GENERIC_USER_ERROR = "응답을 만드는 중 문제가 생겼어요. 잠시 후 다시 시도해 주세요."
PROVIDER_QUOTA_ERROR = (
    "현재 AI provider의 사용량 한도나 크레딧이 부족해서 응답할 수 없어요. "
    "서버 관리자에게 provider 결제/쿼터 설정을 확인해 달라고 알려주세요."
)
PROVIDER_HTTP_ERROR = (
    "AI provider가 요청을 처리하지 못했어요. "
    "서버 관리자에게 provider 설정과 콘솔 로그를 확인해 달라고 알려주세요."
)
QUESTION_HINTS = (
    "?",
    "뭐",
    "무엇",
    "왜",
    "어떻게",
    "언제",
    "어디",
    "누구",
    "얼마",
    "몇",
    "추천",
    "알려줘",
    "해줘",
    "인가",
    "나요",
    "할까",
    "가능",
)


async def handle_ai_request(
    bot: "DiscordAIBot",
    prompt: str,
    *,
    interaction: discord.Interaction | None = None,
    message: discord.Message | None = None,
    source: str,
    style_name: str | None = None,
) -> None:
    if interaction is None and message is None:
        raise ValueError("interaction 또는 message 중 하나는 필요합니다.")

    prompt = prompt.strip()
    if not prompt:
        await _send_short_notice(interaction, message, "메시지를 함께 보내 주세요.")
        return

    thinking_message = await _send_thinking_message(interaction, message)

    try:
        async def update_action_status(content: str) -> None:
            await _replace_thinking_message(
                thinking_message=thinking_message,
                content=content,
                interaction=interaction,
            )

        user_id = _get_user_id(interaction, message)
        channel_id = _get_channel_id(interaction, message)
        guild_id = _get_guild_id(interaction, message)
        action_response = await try_handle_agent_action(
            bot,
            prompt,
            interaction=interaction,
            message=message,
            status_callback=update_action_status,
        )
        if action_response is not None:
            chunks = split_discord_message(normalize_discord_markdown(action_response))
            await _send_response_chunks(
                chunks,
                thinking_message=thinking_message,
                interaction=interaction,
                message=message,
            )
            return

        effective_style = style_name or bot.settings.get_default_style(guild_id)
        system_prompt = build_system_prompt(
            base_prompt=bot.config.system_prompt,
            style=effective_style,
            custom_prompt=bot.settings.get_custom_style_prompt(guild_id),
        )
        channel_context = await build_channel_context(
            interaction=interaction,
            message=message,
            limit=bot.config.channel_context_messages,
            char_limit=bot.config.channel_context_char_limit,
        )
        response = await bot.agent.run(
            prompt,
            user_id=user_id,
            channel_id=channel_id,
            source=source,
            system_prompt=system_prompt,
            channel_context=channel_context,
        )
        chunks = split_discord_message(normalize_discord_markdown(response))
        await _send_response_chunks(
            chunks,
            thinking_message=thinking_message,
            interaction=interaction,
            message=message,
        )
    except ProviderQuotaError as exc:
        logger.warning(
            "Provider quota exhausted while handling %s with provider=%s status=%s provider_status=%s message=%s",
            source,
            bot.config.ai_provider,
            exc.status_code,
            exc.provider_status,
            exc.provider_message or exc.response_text[:500],
        )
        await _replace_thinking_message(
            thinking_message=thinking_message,
            content=PROVIDER_QUOTA_ERROR,
            interaction=interaction,
        )
    except ProviderHTTPStatusError as exc:
        logger.exception(
            "Provider HTTP error while handling %s with provider=%s status=%s provider_status=%s response=%s",
            source,
            bot.config.ai_provider,
            exc.status_code,
            exc.provider_status,
            exc.provider_message or exc.response_text[:500],
        )
        await _replace_thinking_message(
            thinking_message=thinking_message,
            content=PROVIDER_HTTP_ERROR,
            interaction=interaction,
        )
    except Exception:
        logger.exception("Failed to handle AI request from %s", source)
        await _replace_thinking_message(
            thinking_message=thinking_message,
            content=GENERIC_USER_ERROR,
            interaction=interaction,
        )


async def handle_message(bot: "DiscordAIBot", message: discord.Message) -> None:
    if message.author.bot:
        return

    if bot.user is None:
        return

    if any(mentioned.id == bot.user.id for mentioned in message.mentions):
        prompt = _strip_bot_mentions(message.content, bot.user.id)
        await handle_ai_request(
            bot=bot,
            prompt=prompt,
            message=message,
            source="mention",
        )
        return

    autochannel = bot.settings.get_autochannel(
        guild_id=message.guild.id if message.guild else None,
        channel_id=message.channel.id,
    )
    if autochannel is None:
        return

    if not _should_auto_respond(autochannel, message.content):
        return

    await handle_ai_request(
        bot=bot,
        prompt=message.content,
        message=message,
        source="autochannel",
    )


def _strip_bot_mentions(content: str, bot_user_id: int) -> str:
    return re.sub(fr"<@!?{bot_user_id}>", "", content).strip()


def _get_user_id(
    interaction: discord.Interaction | None,
    message: discord.Message | None,
) -> int | None:
    if interaction:
        return interaction.user.id
    if message:
        return message.author.id
    return None


def _get_channel_id(
    interaction: discord.Interaction | None,
    message: discord.Message | None,
) -> int | None:
    if interaction and interaction.channel:
        return interaction.channel.id
    if message and message.channel:
        return message.channel.id
    return None


def _get_guild_id(
    interaction: discord.Interaction | None,
    message: discord.Message | None,
) -> int | None:
    if interaction:
        return interaction.guild_id
    if message and message.guild:
        return message.guild.id
    return None


def _should_auto_respond(setting: AutoChannelSettings, content: str) -> bool:
    content = content.strip()
    if not content:
        return False

    if setting.mode == "always":
        return True

    if setting.mode == "question_only":
        return _looks_like_question(content)

    if setting.mode == "keyword":
        normalized = content.casefold()
        return any(keyword.casefold() in normalized for keyword in setting.keywords)

    return False


def _looks_like_question(content: str) -> bool:
    normalized = content.casefold()
    return any(hint in normalized for hint in QUESTION_HINTS)


async def _send_short_notice(
    interaction: discord.Interaction | None,
    message: discord.Message | None,
    content: str,
) -> None:
    if interaction:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)
        return

    if message:
        await message.reply(
            content,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def _send_thinking_message(
    interaction: discord.Interaction | None,
    message: discord.Message | None,
) -> discord.Message | discord.InteractionMessage:
    if interaction:
        await interaction.response.send_message(
            "생각 중...",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return await interaction.original_response()

    if message is None:
        raise ValueError("message가 필요합니다.")

    return await message.reply(
        "생각 중...",
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _send_response_chunks(
    chunks: list[str],
    *,
    thinking_message: discord.Message | discord.InteractionMessage,
    interaction: discord.Interaction | None,
    message: discord.Message | None,
) -> None:
    if not chunks:
        chunks = ["응답이 비어 있어요."]

    await _replace_thinking_message(
        thinking_message=thinking_message,
        content=chunks[0],
        interaction=interaction,
    )

    for chunk in chunks[1:]:
        if interaction:
            await interaction.followup.send(
                chunk,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            continue

        if message is None:
            raise ValueError("message가 필요합니다.")

        await message.channel.send(
            chunk,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def _replace_thinking_message(
    *,
    thinking_message: discord.Message | discord.InteractionMessage,
    content: str,
    interaction: discord.Interaction | None,
) -> None:
    if interaction:
        await interaction.edit_original_response(
            content=content,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    await thinking_message.edit(
        content=content,
        allowed_mentions=discord.AllowedMentions.none(),
    )
