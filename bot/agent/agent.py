from __future__ import annotations

from providers.base import AIProvider, Message, ProviderOptions

from agent.tools import ToolRegistry


class AIAgent:
    def __init__(
        self,
        *,
        provider: AIProvider,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
        tools: ToolRegistry | None = None,
    ) -> None:
        self.provider = provider
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.tools = tools or ToolRegistry()

    async def run(
        self,
        user_message: str,
        *,
        user_id: int | None = None,
        channel_id: int | None = None,
        source: str = "unknown",
        system_prompt: str | None = None,
    ) -> str:
        messages = self._build_messages(
            user_message=user_message,
            user_id=user_id,
            channel_id=channel_id,
            source=source,
            system_prompt=system_prompt or self.system_prompt,
        )
        options = ProviderOptions(
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return await self.provider.generate_response(messages, options)

    def _build_messages(
        self,
        *,
        user_message: str,
        user_id: int | None,
        channel_id: int | None,
        source: str,
        system_prompt: str,
    ) -> list[Message]:
        # Metadata is kept separate from the user-visible prompt so future
        # memory/tool layers can use it without changing provider contracts.
        _metadata = {
            "user_id": user_id,
            "channel_id": channel_id,
            "source": source,
        }
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
