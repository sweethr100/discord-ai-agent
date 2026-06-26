from __future__ import annotations

import discord
from discord.ext import commands

from agent.agent import AIAgent
from config import AppConfig
from discord_bot.commands import register_commands
from discord_bot.handlers import handle_message
from discord_bot.settings_store import GuildSettingsStore
from utils.logger import get_logger


logger = get_logger(__name__)


class DiscordAIBot(commands.Bot):
    def __init__(self, config: AppConfig, agent: AIAgent) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.config = config
        self.agent = agent
        self.settings = GuildSettingsStore()

    async def setup_hook(self) -> None:
        register_commands(self)

        if self.config.discord_guild_id:
            guild = discord.Object(id=self.config.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            self.tree.clear_commands(guild=None)
            cleared_global = await self.tree.sync()
            logger.info(
                "Synced %s slash command(s) to guild %s",
                len(synced),
                self.config.discord_guild_id,
            )
            logger.info(
                "Global slash command set now has %s command(s) while using guild sync",
                len(cleared_global),
            )
        else:
            synced = await self.tree.sync()
            logger.info("Synced %s global slash command(s)", len(synced))

    async def on_ready(self) -> None:
        if self.user:
            logger.info("Logged in as %s (%s)", self.user, self.user.id)

    async def on_message(self, message: discord.Message) -> None:
        await handle_message(self, message)
