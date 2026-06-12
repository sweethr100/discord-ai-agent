from __future__ import annotations

import datetime as dt
from typing import Any

import discord


MAX_CONTEXT_MESSAGES = 50
MAX_MESSAGE_CHARS = 500


async def build_channel_context(
    *,
    interaction: discord.Interaction | None,
    message: discord.Message | None,
    limit: int,
    char_limit: int,
) -> str:
    if limit <= 0 or char_limit <= 0:
        return ""

    channel = _get_messageable_channel(interaction, message)
    if channel is None:
        return ""

    if not _can_read_history(channel):
        return ""

    before = _history_before(interaction, message)
    history_kwargs: dict[str, Any] = {"limit": min(limit, MAX_CONTEXT_MESSAGES)}
    if before is not None:
        history_kwargs["before"] = before

    messages: list[discord.Message] = []
    try:
        async for history_message in channel.history(**history_kwargs):
            line = _format_history_message(history_message)
            if line:
                messages.append(history_message)
    except (discord.Forbidden, discord.HTTPException):
        return ""

    lines = [
        _format_history_message(history_message)
        for history_message in reversed(messages)
    ]
    lines = [line for line in lines if line]
    if not lines:
        return ""

    selected: list[str] = []
    total = 0
    for line in reversed(lines):
        line_length = len(line) + 1
        if selected and total + line_length > char_limit:
            break
        selected.append(line)
        total += line_length

    selected.reverse()
    return "\n".join(selected)


def _get_messageable_channel(
    interaction: discord.Interaction | None,
    message: discord.Message | None,
) -> Any | None:
    channel = interaction.channel if interaction else message.channel if message else None
    return channel if hasattr(channel, "history") else None


def _can_read_history(channel: Any) -> bool:
    guild = getattr(channel, "guild", None)
    me = getattr(guild, "me", None)
    if guild is None or me is None or not hasattr(channel, "permissions_for"):
        return True

    permissions = channel.permissions_for(me)
    return bool(permissions.view_channel and permissions.read_message_history)


def _history_before(
    interaction: discord.Interaction | None,
    message: discord.Message | None,
) -> discord.Message | dt.datetime | None:
    if message is not None:
        return message
    if interaction is not None:
        return interaction.created_at
    return None


def _format_history_message(message: discord.Message) -> str:
    content = _message_content(message)
    if not content:
        return ""

    timestamp = message.created_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    author = getattr(message.author, "display_name", str(message.author))
    bot_label = " bot" if message.author.bot else ""
    return f"[{timestamp} UTC] {author}{bot_label}: {content}"


def _message_content(message: discord.Message) -> str:
    parts: list[str] = []
    content = message.clean_content.strip()
    if content:
        parts.append(_single_line(content, MAX_MESSAGE_CHARS))

    if message.attachments:
        attachment_names = ", ".join(attachment.filename for attachment in message.attachments[:3])
        suffix = "..." if len(message.attachments) > 3 else ""
        parts.append(f"[첨부파일: {attachment_names}{suffix}]")

    if message.stickers:
        sticker_names = ", ".join(sticker.name for sticker in message.stickers[:3])
        suffix = "..." if len(message.stickers) > 3 else ""
        parts.append(f"[스티커: {sticker_names}{suffix}]")

    return " ".join(parts).strip()


def _single_line(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1]}..."
