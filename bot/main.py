import asyncio
import sys

import discord

from agent.agent import AIAgent
from config import ConfigError, load_config
from discord_bot.client import DiscordAIBot
from providers import create_provider
from utils.logger import get_logger, setup_logging


logger = get_logger(__name__)


async def async_main() -> None:
    setup_logging()

    try:
        config = load_config()
        provider = create_provider(config)
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        print(f"설정 오류: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    agent = AIAgent(
        provider=provider,
        system_prompt="",
        temperature=config.ai_temperature,
        max_tokens=config.ai_max_tokens,
        reasoning_effort=config.ai_reasoning_effort,
    )
    bot = DiscordAIBot(config=config, agent=agent)

    try:
        await bot.start(config.discord_token)
    except discord.LoginFailure as exc:
        logger.exception("Discord login failed")
        print("Discord 로그인 실패: DISCORD_TOKEN 값을 확인해 주세요.", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        await bot.close()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
