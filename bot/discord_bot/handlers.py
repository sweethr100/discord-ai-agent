from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord

from agent.styles import STYLE_NAMES, build_system_prompt, is_valid_style
from discord_bot.agent_actions import (
    ActionContext,
    ActionPlan,
    action_requires_confirmation,
    build_action_context,
    build_channel_reference_context,
    build_member_reference_context,
    build_voice_reference_context,
    describe_action_plan,
    execute_agent_action,
    resolve_action_plan_mentions,
    retry_agent_turn_after_validation,
    run_agent_turn,
    validate_action_plan,
)
from discord_bot.channel_context import build_channel_context
from discord_bot.rich_inputs import build_rich_input
from discord_bot.settings_store import AutoChannelSettings
from providers.base import (
    Message,
    ProviderHTTPStatusError,
    ProviderOptions,
    ProviderQuotaError,
    ProviderResponseError,
)
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
SELF_MANUAL_ALWAYS_HINTS = (
    "명령어",
    "사용법",
    "설정법",
    "실행법",
    "기능",
    ".env",
    "env",
)
SELF_MANUAL_TOPIC_HINTS = (
    "자동응답",
    "자동 응답",
    "오토채널",
    "autochannel",
    "스타일",
    "style",
    "provider",
    "프로바이더",
    "모델",
)
SELF_MANUAL_QUESTION_HINTS = (
    "?",
    "뭐",
    "무엇",
    "어떻게",
    "알려",
    "설명",
    "설정",
    "사용",
    "뭐 할 수",
    "할 수 있어",
    "가능해",
)
CONFIRMATION_TIMEOUT_SECONDS = 60.0
FEEDBACK_GENERATION_ATTEMPTS = 2
ACTION_VALIDATION_REPLAN_ATTEMPTS = 2
AUTOCHANNEL_DEBOUNCE_SECONDS = 3.0
RAW_USER_ID_PATTERN = re.compile(r"(?<![<@#&])\b([1-9]\d{16,19})\b(?!>)")
INVALID_USER_MENTION_PATTERN = re.compile(r"<@!?([^<@#&>`\n]{1,100})>")
BACKTICKED_USER_MENTION_PATTERN = re.compile(r"`(<@!?([^<@#&>`\n]{1,100})>)`")
MARKDOWN_CODE_PATTERN = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)
MENTION_ONLY_PROMPT = (
    "내가 봇을 멘션만 했다. 최근 채널 대화 문맥을 보고 내가 지금 무엇에 대해 답을 원하는지 추론해서 자연스럽게 답해줘. "
    "문맥만으로 의도를 알 수 없으면 억지로 꾸미지 말고 무엇을 도와주면 되는지 짧게 물어봐."
)


@dataclass
class PendingAutoChannelRequest:
    bot: "DiscordAIBot"
    mode: str
    message: discord.Message
    contents: list[str] = field(default_factory=list)
    task: asyncio.Task[None] | None = None


_pending_autochannel_requests: dict[tuple[int, int, int], PendingAutoChannelRequest] = {}


def _should_include_self_manual(prompt: str) -> bool:
    normalized = prompt.casefold()
    if any(hint in normalized for hint in SELF_MANUAL_ALWAYS_HINTS):
        return True
    return (
        any(topic in normalized for topic in SELF_MANUAL_TOPIC_HINTS)
        and any(hint in normalized for hint in SELF_MANUAL_QUESTION_HINTS)
    )


async def handle_ai_request(
    bot: "DiscordAIBot",
    prompt: str,
    *,
    interaction: discord.Interaction | None = None,
    message: discord.Message | None = None,
    attachments: tuple[discord.Attachment, ...] = (),
    source: str,
    style_name: str | None = None,
) -> None:
    if interaction is None and message is None:
        raise ValueError("interaction 또는 message 중 하나는 필요합니다.")

    original_prompt = prompt.strip()
    mention_only = not original_prompt and source == "mention"
    if mention_only:
        original_prompt = MENTION_ONLY_PROMPT
    if not original_prompt and not attachments and not (message and message.attachments):
        await _send_short_notice(interaction, message, "메시지를 함께 보내 주세요.")
        return

    thinking_message = await _send_thinking_message(interaction, message)
    prompt_for_error = original_prompt
    system_prompt_for_error = ""
    channel_context_for_error = ""

    try:
        async def update_action_status(content: str) -> None:
            await _replace_thinking_message(
                thinking_message=thinking_message,
                content=content,
                interaction=interaction,
            )

        guild_id = _get_guild_id(interaction, message)
        channel_id = _get_channel_id(interaction, message)
        rich_input = await build_rich_input(
            prompt=original_prompt,
            message=message,
            attachments=attachments,
        )
        prompt = rich_input.text
        prompt_for_error = prompt
        prompt_for_intent = "" if mention_only else (original_prompt or prompt)

        requested_style = _extract_requested_style(prompt_for_intent, bot, guild_id) if style_name is None else None
        effective_style = (
            style_name
            or requested_style
            or bot.settings.get_channel_style(guild_id, channel_id)
            or bot.settings.get_default_style(guild_id)
        )
        custom_style = bot.settings.get_custom_style(guild_id, effective_style)
        system_prompt = build_system_prompt(
            base_prompt="",
            style=effective_style,
            custom_prompt=bot.settings.get_custom_style_prompt(guild_id),
            style_prompt=custom_style.prompt if custom_style else None,
            include_self_manual=_should_include_self_manual(prompt_for_intent),
        )
        system_prompt_for_error = system_prompt
        channel_context = await build_channel_context(
            interaction=interaction,
            message=message,
            limit=bot.config.channel_context_messages,
            char_limit=bot.config.channel_context_char_limit,
        )
        channel_context_for_error = channel_context
        discord_guild = interaction.guild if interaction else message.guild if message else None
        requester = interaction.user if interaction else message.author if message else None
        member_reference_context = (
            await build_member_reference_context(
                guild=discord_guild,
                requester=requester,
                prompt=prompt_for_intent,
                message=message,
            )
            if discord_guild is not None
            else ""
        )
        current_channel = interaction.channel if interaction else message.channel if message else None
        channel_reference_context = (
            build_channel_reference_context(
                guild=discord_guild,
                current_channel=current_channel,
                prompt=prompt_for_intent,
                message=message,
            )
            if discord_guild is not None
            else ""
        )
        voice_reference_context = (
            build_voice_reference_context(guild=discord_guild)
            if discord_guild is not None
            else ""
        )
        agent_turn = await run_agent_turn(
            bot,
            prompt,
            system_prompt=system_prompt,
            prompt_content=rich_input.content,
            channel_context=channel_context,
            member_reference_context=member_reference_context,
            channel_reference_context=channel_reference_context,
            voice_reference_context=voice_reference_context,
        )
        action_plan = agent_turn.action_plan
        if action_plan is not None:
            guild = interaction.guild if interaction else message.guild if message else None
            if guild is None:
                await _replace_thinking_message(
                    thinking_message=thinking_message,
                    content="서버 관리 작업은 Discord 서버 안에서만 실행할 수 있어요.",
                    interaction=interaction,
                )
                return

            action_context = build_action_context(
                bot=bot,
                guild=guild,
                interaction=interaction,
                message=message,
                status_callback=update_action_status,
            )
            validation_error = ""
            for attempt in range(ACTION_VALIDATION_REPLAN_ATTEMPTS + 1):
                validation_error = await validate_action_plan(action_context, action_plan) or ""
                if not validation_error:
                    break
                if attempt >= ACTION_VALIDATION_REPLAN_ATTEMPTS:
                    break

                try:
                    repaired_turn = await retry_agent_turn_after_validation(
                        bot,
                        prompt,
                        failed_plan=action_plan,
                        validation_error=validation_error,
                        system_prompt=system_prompt,
                        channel_context=channel_context,
                        member_reference_context=member_reference_context,
                        channel_reference_context=channel_reference_context,
                        voice_reference_context=voice_reference_context,
                    )
                except ProviderResponseError:
                    logger.warning("Provider returned invalid validation replan for action: %s", action_plan.action)
                    break
                if repaired_turn.action_plan is None:
                    chunks = _response_chunks(repaired_turn.content, interaction=interaction, message=message)
                    await _send_response_chunks(
                        chunks,
                        thinking_message=thinking_message,
                        interaction=interaction,
                        message=message,
                    )
                    return
                action_plan = repaired_turn.action_plan

            if validation_error:
                try:
                    validation_response = await _generate_validation_feedback(
                        bot=bot,
                        prompt=prompt,
                        action_plan=action_plan,
                        validation_error=validation_error,
                        system_prompt=system_prompt,
                        channel_context=channel_context,
                    )
                except ProviderResponseError:
                    logger.warning("Provider returned invalid validation feedback for action: %s", action_plan.action)
                    validation_response = await _generate_plain_feedback_or_fallback(
                        bot=bot,
                        prompt=prompt,
                        raw_message=validation_error,
                        system_prompt=system_prompt,
                        channel_context=channel_context,
                        label="validation feedback fallback",
                    )
                chunks = _response_chunks(validation_response, interaction=interaction, message=message)
                await _send_response_chunks(
                    chunks,
                    thinking_message=thinking_message,
                    interaction=interaction,
                    message=message,
                )
                return

            action_plan = await resolve_action_plan_mentions(action_context, action_plan)

            if action_requires_confirmation(action_plan):
                await _request_action_confirmation(
                    bot=bot,
                    plan=action_plan,
                    context=action_context,
                    thinking_message=thinking_message,
                    interaction=interaction,
                    message=message,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    channel_context=channel_context,
                )
                return

            try:
                execution_result = await execute_agent_action(action_context, action_plan)
            except Exception:
                logger.exception("Failed to execute AI action: %s", action_plan.action)
                execution_result = GENERIC_USER_ERROR
            final_response = await _generate_execution_feedback_or_fallback(
                bot=bot,
                prompt=prompt,
                action_plan=action_plan,
                execution_result=execution_result,
                system_prompt=system_prompt,
                channel_context=channel_context,
                confirmed=False,
            )
            chunks = _response_chunks(final_response, interaction=interaction, message=message)
            await _send_response_chunks(
                chunks,
                thinking_message=thinking_message,
                interaction=interaction,
                message=message,
            )
            return

        response = agent_turn.content
        chunks = _response_chunks(response, interaction=interaction, message=message)
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
        if system_prompt_for_error:
            generic_response = await _generate_plain_feedback_or_fallback(
                bot=bot,
                prompt=prompt_for_error,
                raw_message=GENERIC_USER_ERROR,
                system_prompt=system_prompt_for_error,
                channel_context=channel_context_for_error,
                label="generic error feedback fallback",
            )
        else:
            generic_response = GENERIC_USER_ERROR
        await _replace_thinking_message(
            thinking_message=thinking_message,
            content=generic_response,
            interaction=interaction,
        )


async def _request_action_confirmation(
    *,
    bot: "DiscordAIBot",
    plan: ActionPlan,
    context: ActionContext,
    thinking_message: discord.Message | discord.InteractionMessage,
    interaction: discord.Interaction | None,
    message: discord.Message | None,
    prompt: str,
    system_prompt: str,
    channel_context: str,
    prefix_content: str = "",
) -> None:
    requester = interaction.user if interaction else message.author if message else None
    view = AgentActionConfirmView(
        bot=bot,
        plan=plan,
        context=context,
        response_message=thinking_message,
        requester_id=requester.id if requester else 0,
        prompt=prompt,
        system_prompt=system_prompt,
        channel_context=channel_context,
        prefix_content=prefix_content,
    )
    content = _prefer_member_mentions(describe_action_plan(plan), context.guild)
    if prefix_content.strip():
        content = _append_message_content(prefix_content, f"새 작업 제안: {content}")
    await _edit_ai_message(
        thinking_message,
        content=content,
        view=view,
    )


async def _generate_validation_feedback(
    *,
    bot: "DiscordAIBot",
    prompt: str,
    action_plan: ActionPlan,
    validation_error: str,
    system_prompt: str,
    channel_context: str,
) -> str:
    messages: list[Message] = [
        {
            "role": "system",
            "content": (
                f"{system_prompt}\n\n"
                "서버 관리 도구 호출이 실행 전 검증에서 실패했다. "
                "사용자에게 확인 버튼을 띄우지 말고, 지금 실행할 수 없는 이유를 현재 스타일에 맞춰 자연스럽게 설명하라. "
                "부족한 정보가 있으면 필요한 만큼만 물어보고, 불필요한 추가 제안은 덧붙이지 마라. "
                "멤버를 가리킬 때는 숫자 ID나 `<@이름>` 가짜 멘션이 아니라, 실제 숫자 ID가 들어간 `<@id>` 유저 멘션 형식으로 말하라. "
                "도구 호출 JSON, 내부 action 이름, args 키 이름은 말하지 마라."
            ),
        },
    ]
    if channel_context.strip():
        messages.append(
            {
                "role": "user",
                "content": (
                    "최근 채널 대화 문맥이다. 사용자가 무엇을 하려 했는지 이해하는 데만 참고하라.\n"
                    f"{channel_context.strip()}"
                ),
            }
        )
    messages.append(
        {
            "role": "user",
            "content": (
                f"사용자 요청: {prompt}\n"
                f"요청된 작업: {describe_action_plan(action_plan)}\n"
                f"검증 실패 이유: {validation_error}"
            ),
        }
    )
    return await _generate_feedback_response(
        bot=bot,
        messages=messages,
        label="validation feedback",
    )


async def _generate_rejection_feedback(
    *,
    bot: "DiscordAIBot",
    prompt: str,
    action_plan: ActionPlan,
    system_prompt: str,
    channel_context: str,
    rejection_note: str = "",
) -> str:
    messages: list[Message] = [
        {
            "role": "system",
            "content": (
                f"{system_prompt}\n\n"
                "서버 관리 도구 호출 확인에서 사용자가 거절을 눌렀다. "
                "해당 작업은 실행되지 않았음을 현재 스타일에 맞춰 자연스럽게 인정하라. "
                "사용자의 원래 의도나 거절 메모에 직접 관련된 내용만 답하고, 대안이나 제안을 억지로 붙이지 마라. "
                "사용자가 거절하면서 추가 메시지를 남겼다면 그 내용을 우선 반영하라. "
                "멤버를 가리킬 때는 숫자 ID나 `<@이름>` 가짜 멘션이 아니라, 실제 숫자 ID가 들어간 `<@id>` 유저 멘션 형식으로 말하라. "
                "새 도구 호출 JSON을 만들지 말고, 확인 버튼을 텍스트로 흉내 내지 마라."
            ),
        },
    ]
    if channel_context.strip():
        messages.append(
            {
                "role": "user",
                "content": (
                    "최근 채널 대화 문맥이다. 사용자의 의도를 이해하는 데만 참고하라.\n"
                    f"{channel_context.strip()}"
                ),
            }
        )
    user_content = (
        f"사용자 원래 요청: {prompt}\n"
        f"거절된 작업: {describe_action_plan(action_plan)}\n"
        "상태: 사용자가 거절 버튼을 눌러 작업을 실행하지 않았다."
    )
    rejection_note = rejection_note.strip()
    if rejection_note:
        user_content += f"\n사용자가 거절하면서 추가로 남긴 메시지: {rejection_note}"

    messages.append(
        {
            "role": "user",
            "content": user_content,
        }
    )
    return await _generate_feedback_response(
        bot=bot,
        messages=messages,
        label="rejection feedback",
    )


async def _generate_execution_feedback(
    *,
    bot: "DiscordAIBot",
    prompt: str,
    action_plan: ActionPlan,
    execution_result: str,
    system_prompt: str,
    channel_context: str,
) -> str:
    messages: list[Message] = [
        {
            "role": "system",
            "content": (
                f"{system_prompt}\n\n"
                "서버 관리 도구 실행이 끝났다. 실행 결과를 바탕으로 사용자에게 최종 답변을 하라. "
                "현재 스타일 지시가 최우선이다. 도구 실행 결과 문장을 그대로 복사하지 말고, 스타일에 맞는 말투로 다시 말하라. "
                "이미 실행된 작업을 다시 확인하지 말고, 수락/거절 버튼을 텍스트로 흉내 내지 마라. "
                "성공이면 무엇이 완료됐는지 말하고, 실패나 제한이면 이유를 자연스럽게 알려라. "
                "후속 안내나 체크리스트는 사용자가 원했거나 꼭 필요할 때만 붙여라. "
                "성공 답변에는 대상 멤버/채널/역할과 변경된 값이 실행 결과에 있으면 반드시 포함하라. "
                "멤버를 가리킬 때는 숫자 ID나 `<@이름>` 가짜 멘션이 아니라, 실제 숫자 ID가 들어간 `<@id>` 유저 멘션 형식으로 말하라. "
                "내부 action 이름이나 args 키 이름은 말하지 마라."
            ),
        },
    ]
    if channel_context.strip():
        messages.append(
            {
                "role": "user",
                "content": (
                    "최근 채널 대화 문맥이다. 사용자의 의도를 이해하는 데만 참고하라.\n"
                    f"{channel_context.strip()}"
                ),
            }
        )
    messages.append(
        {
            "role": "user",
            "content": (
                f"사용자 원래 요청: {prompt}\n"
                f"실행한 작업: {describe_action_plan(action_plan)}\n"
                f"도구 실행 결과: {execution_result}"
            ),
        }
    )
    return await _generate_feedback_response(
        bot=bot,
        messages=messages,
        label="execution feedback",
    )


async def _generate_execution_feedback_or_fallback(
    *,
    bot: "DiscordAIBot",
    prompt: str,
    action_plan: ActionPlan,
    execution_result: str,
    system_prompt: str,
    channel_context: str,
    confirmed: bool,
) -> str:
    try:
        return await _generate_execution_feedback(
            bot=bot,
            prompt=prompt,
            action_plan=action_plan,
            execution_result=execution_result,
            system_prompt=system_prompt,
            channel_context=channel_context,
        )
    except ProviderResponseError:
        logger.warning(
            "Failed to generate %sAI action feedback after execution: %s",
            "confirmed " if confirmed else "",
            action_plan.action,
        )
        return await _generate_plain_feedback_or_fallback(
            bot=bot,
            prompt=prompt,
            raw_message=execution_result,
            system_prompt=system_prompt,
            channel_context=channel_context,
            label="execution feedback fallback",
        )


async def _generate_plain_feedback_or_fallback(
    *,
    bot: "DiscordAIBot",
    prompt: str,
    raw_message: str,
    system_prompt: str,
    channel_context: str,
    label: str,
) -> str:
    messages: list[Message] = [
        {
            "role": "system",
            "content": (
                f"{system_prompt}\n\n"
                "아래 상태 메시지를 바탕으로 Discord에 보낼 최종 답변을 작성하라. "
                "현재 스타일/말투 지시를 반드시 유지하고, 내부 오류명이나 JSON 키는 말하지 마라. "
                "원문의 의미는 바꾸지 말고, 정해진 템플릿 없이 자연스럽게 답하라. "
                "멤버를 가리킬 때는 숫자 ID나 `<@이름>` 가짜 멘션이 아니라, 실제 숫자 ID가 들어간 `<@id>` 유저 멘션 형식으로 말하라. "
                "추가 제안은 꼭 필요할 때만 포함하라."
            ),
        },
    ]
    if channel_context.strip():
        messages.append(
            {
                "role": "user",
                "content": (
                    "최근 채널 대화 문맥이다. 사용자의 의도를 이해하는 데만 참고하라.\n"
                    f"{channel_context.strip()}"
                ),
            }
        )
    messages.append(
        {
            "role": "user",
            "content": (
                f"사용자 요청: {prompt}\n"
                f"상태 메시지: {raw_message}"
            ),
        }
    )

    try:
        return await _generate_feedback_response(
            bot=bot,
            messages=messages,
            label=label,
        )
    except Exception:
        logger.exception("Failed to generate styled %s.", label)
        return raw_message


async def _generate_feedback_response(
    *,
    bot: "DiscordAIBot",
    messages: list[Message],
    label: str,
) -> str:
    retry_messages = list(messages)
    last_error: ProviderResponseError | None = None
    for attempt in range(FEEDBACK_GENERATION_ATTEMPTS + 1):
        try:
            return await bot.agent.provider.generate_response(
                retry_messages,
                ProviderOptions(
                    temperature=bot.agent.temperature,
                    max_tokens=bot.agent.max_tokens,
                    reasoning_effort=bot.agent.reasoning_effort,
                ),
            )
        except ProviderResponseError as exc:
            last_error = exc
            logger.warning(
                "Provider returned invalid %s on attempt %s/%s: %s",
                label,
                attempt + 1,
                FEEDBACK_GENERATION_ATTEMPTS + 1,
                exc,
            )
            if attempt >= FEEDBACK_GENERATION_ATTEMPTS:
                break
            retry_messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "이전 출력이 비어 있었다. "
                        f"관찰된 문제: {exc}. "
                        "이전 지시와 사용자 요청을 유지하되, Discord에 보낼 짧은 최종 답변 텍스트만 다시 출력하라. "
                        "빈 응답, JSON, Markdown 코드블록은 출력하지 마라."
                    ),
                },
            ]

    raise last_error or ProviderResponseError(f"Provider failed to generate {label}.")


class AgentActionConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        bot: "DiscordAIBot",
        plan: ActionPlan,
        context: ActionContext,
        response_message: discord.Message | discord.InteractionMessage,
        requester_id: int,
        prompt: str,
        system_prompt: str,
        channel_context: str,
        prefix_content: str = "",
    ) -> None:
        super().__init__(timeout=CONFIRMATION_TIMEOUT_SECONDS)
        self.bot = bot
        self.plan = plan
        self.context = context
        self.response_message = response_message
        self.requester_id = requester_id
        self.prompt = prompt
        self.system_prompt = system_prompt
        self.channel_context = channel_context
        self.prefix_content = prefix_content.strip()
        self.completed = False
        self._completion_lock = asyncio.Lock()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True

        await interaction.response.send_message(
            "이 작업은 요청한 사용자만 수락하거나 거절할 수 있어요.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return False

    @discord.ui.button(label="수락", style=discord.ButtonStyle.danger)
    async def accept(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        async with self._completion_lock:
            if self.completed:
                await interaction.response.send_message("이미 처리된 작업이에요.", ephemeral=True)
                return
            self.completed = True
            self._disable_buttons()
            accepted_content = self._status_content(
                f"{_prefer_member_mentions(describe_action_plan(self.plan), self.context.guild)} 수락됨."
            )
            await interaction.response.edit_message(
                content=accepted_content,
                view=self,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        async def update_status(content: str) -> None:
            await _edit_ai_message(
                self.response_message,
                content=_append_message_content(accepted_content, content),
                view=None,
            )

        self.context.status_callback = update_status
        try:
            execution_result = await execute_agent_action(self.context, self.plan)
        except Exception:
            logger.exception("Failed to execute confirmed AI action: %s", self.plan.action)
            response = await _generate_execution_feedback_or_fallback(
                bot=self.bot,
                prompt=self.prompt,
                action_plan=self.plan,
                execution_result=GENERIC_USER_ERROR,
                system_prompt=self.system_prompt,
                channel_context=self.channel_context,
                confirmed=True,
            )
            chunks = _response_chunks(response, guild=self.context.guild)
            await _send_response_chunks_to_message(self.response_message, chunks)
            self.stop()
            return

        response = await _generate_execution_feedback_or_fallback(
            bot=self.bot,
            prompt=self.prompt,
            action_plan=self.plan,
            execution_result=execution_result,
            system_prompt=self.system_prompt,
            channel_context=self.channel_context,
            confirmed=True,
        )
        chunks = _response_chunks(response, guild=self.context.guild)
        await _send_response_chunks_to_message(self.response_message, chunks)
        self.stop()

    @discord.ui.button(label="거절", style=discord.ButtonStyle.secondary)
    async def reject(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self.completed:
            await interaction.response.send_message("이미 처리된 작업이에요.", ephemeral=True)
            return

        await interaction.response.send_modal(AgentActionRejectModal(self))

    async def handle_reject_submission(
        self,
        interaction: discord.Interaction,
        *,
        rejection_note: str,
    ) -> None:
        async with self._completion_lock:
            if self.completed:
                await interaction.response.send_message("이미 처리된 작업이에요.", ephemeral=True)
                return
            self.completed = True
            self._disable_buttons()
            rejected_content = self._status_content(
                f"{_prefer_member_mentions(describe_action_plan(self.plan), self.context.guild)} 거절됨."
            )
            await interaction.response.defer()
            await _edit_ai_message(
                self.response_message,
                content=_append_message_content(rejected_content, "생각 중..."),
                view=self,
            )

        if rejection_note.strip():
            handled = await self._try_replan_after_rejection(
                rejected_content=rejected_content,
                rejection_note=rejection_note.strip(),
            )
            if handled:
                self.stop()
                return

        try:
            response = await _generate_rejection_feedback(
                bot=self.bot,
                prompt=self.prompt,
                action_plan=self.plan,
                system_prompt=self.system_prompt,
                channel_context=self.channel_context,
                rejection_note=rejection_note,
            )
        except Exception:
            logger.exception("Failed to generate rejection feedback: %s", self.plan.action)
            response = await _generate_plain_feedback_or_fallback(
                bot=self.bot,
                prompt=self.prompt,
                raw_message=GENERIC_USER_ERROR,
                system_prompt=self.system_prompt,
                channel_context=self.channel_context,
                label="rejection feedback fallback",
            )
            await _send_response_chunks_to_message(
                self.response_message,
                _response_chunks(response, guild=self.context.guild),
            )
            self.stop()
            return

        chunks = _response_chunks(response, guild=self.context.guild)
        await _send_response_chunks_to_message(self.response_message, chunks)
        self.stop()

    async def _try_replan_after_rejection(
        self,
        *,
        rejected_content: str,
        rejection_note: str,
    ) -> bool:
        replan_prompt = (
            f"사용자 원래 요청: {self.prompt}\n"
            f"거절된 작업: {describe_action_plan(self.plan)}\n"
            f"사용자가 거절하면서 추가로 남긴 메시지: {rejection_note}\n\n"
            "추가 메시지를 반영해서 원래 요청을 다시 처리해줘. "
            "새 서버 관리 작업이 필요하면 도구 호출 JSON을 출력하고, 실행할 작업이 아니면 자연어로 답해줘."
        )
        member_reference_context = await build_member_reference_context(
            guild=self.context.guild,
            requester=self.context.user,
            prompt=replan_prompt,
            message=self.context.message,
        )
        channel_reference_context = build_channel_reference_context(
            guild=self.context.guild,
            current_channel=self.context.channel,
            prompt=replan_prompt,
            message=self.context.message,
        )
        voice_reference_context = build_voice_reference_context(guild=self.context.guild)

        try:
            agent_turn = await run_agent_turn(
                self.bot,
                replan_prompt,
                system_prompt=self.system_prompt,
                channel_context=self.channel_context,
                member_reference_context=member_reference_context,
                channel_reference_context=channel_reference_context,
                voice_reference_context=voice_reference_context,
            )
        except ProviderResponseError:
            logger.warning("Provider returned invalid replan after rejected AI action: %s", self.plan.action)
            return False

        action_plan = agent_turn.action_plan
        if action_plan is None:
            chunks = _response_chunks(agent_turn.content, guild=self.context.guild)
            await _append_response_chunks_to_message(self.response_message, rejected_content, chunks)
            return True

        validation_error = ""
        for attempt in range(ACTION_VALIDATION_REPLAN_ATTEMPTS + 1):
            validation_error = await validate_action_plan(self.context, action_plan) or ""
            if not validation_error:
                break
            if attempt >= ACTION_VALIDATION_REPLAN_ATTEMPTS:
                break
            try:
                repaired_turn = await retry_agent_turn_after_validation(
                    self.bot,
                    replan_prompt,
                    failed_plan=action_plan,
                    validation_error=validation_error,
                    system_prompt=self.system_prompt,
                    channel_context=self.channel_context,
                    member_reference_context=member_reference_context,
                    channel_reference_context=channel_reference_context,
                    voice_reference_context=voice_reference_context,
                )
            except ProviderResponseError:
                logger.warning("Provider returned invalid rejection replan repair: %s", action_plan.action)
                break
            if repaired_turn.action_plan is None:
                chunks = _response_chunks(repaired_turn.content, guild=self.context.guild)
                await _append_response_chunks_to_message(self.response_message, rejected_content, chunks)
                return True
            action_plan = repaired_turn.action_plan

        if validation_error:
            try:
                validation_response = await _generate_validation_feedback(
                    bot=self.bot,
                    prompt=replan_prompt,
                    action_plan=action_plan,
                    validation_error=validation_error,
                    system_prompt=self.system_prompt,
                    channel_context=self.channel_context,
                )
            except ProviderResponseError:
                validation_response = await _generate_plain_feedback_or_fallback(
                    bot=self.bot,
                    prompt=replan_prompt,
                    raw_message=validation_error,
                    system_prompt=self.system_prompt,
                    channel_context=self.channel_context,
                    label="rejection validation feedback fallback",
                )
            chunks = _response_chunks(validation_response, guild=self.context.guild)
            await _append_response_chunks_to_message(self.response_message, rejected_content, chunks)
            return True

        action_plan = await resolve_action_plan_mentions(self.context, action_plan)
        if action_requires_confirmation(action_plan):
            view = AgentActionConfirmView(
                bot=self.bot,
                plan=action_plan,
                context=self.context,
                response_message=self.response_message,
                requester_id=self.requester_id,
                prompt=replan_prompt,
                system_prompt=self.system_prompt,
                channel_context=self.channel_context,
                prefix_content=rejected_content,
            )
            await _edit_ai_message(
                self.response_message,
                content=_append_message_content(
                    rejected_content,
                    f"새 작업 제안: {_prefer_member_mentions(describe_action_plan(action_plan), self.context.guild)}",
                ),
                view=view,
            )
            return True

        try:
            execution_result = await execute_agent_action(self.context, action_plan)
        except Exception:
            logger.exception("Failed to execute rejection replan AI action: %s", action_plan.action)
            execution_result = GENERIC_USER_ERROR
        response = await _generate_execution_feedback_or_fallback(
            bot=self.bot,
            prompt=replan_prompt,
            action_plan=action_plan,
            execution_result=execution_result,
            system_prompt=self.system_prompt,
            channel_context=self.channel_context,
            confirmed=False,
        )
        chunks = _response_chunks(response, guild=self.context.guild)
        await _send_response_chunks_to_message(self.response_message, chunks)
        return True

    async def on_timeout(self) -> None:
        if self.completed:
            return

        self._disable_buttons()
        timeout_content = self._status_content(
            f"시간 초과로 취소했습니다. {_prefer_member_mentions(describe_action_plan(self.plan), self.context.guild)}"
        )
        await _edit_ai_message(
            self.response_message,
            content=timeout_content,
            view=self,
        )

    def _status_content(self, status: str) -> str:
        if not self.prefix_content:
            return status
        return _append_message_content(self.prefix_content, status)

    def _disable_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True


class AgentActionRejectModal(discord.ui.Modal):
    def __init__(self, confirm_view: AgentActionConfirmView) -> None:
        super().__init__(title="관리 작업 거절")
        self.confirm_view = confirm_view
        self.note = discord.ui.TextInput(
            label="LLM에게 같이 보낼 추가 메시지",
            placeholder="예: 대상이 틀렸어. 다른 채널로 다시 물어봐줘.",
            required=False,
            max_length=500,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.confirm_view.requester_id:
            await interaction.response.send_message(
                "이 작업은 요청한 사용자만 거절할 수 있어요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        await self.confirm_view.handle_reject_submission(
            interaction,
            rejection_note=str(self.note.value or "").strip(),
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

    if autochannel.mode in {"always", "question_only"}:
        _queue_autochannel_request(bot, message, autochannel.mode)
        return

    if not _should_auto_respond(autochannel, message.content):
        return

    await handle_ai_request(
        bot=bot,
        prompt=message.content,
        message=message,
        source="autochannel",
    )


def _queue_autochannel_request(
    bot: "DiscordAIBot",
    message: discord.Message,
    mode: str,
) -> None:
    if message.guild is None:
        return

    content = message.content.strip()
    if not content and message.attachments and mode == "always":
        content = "첨부된 이미지나 URL을 읽고 설명해줘."
    if not content:
        return

    key = (message.guild.id, message.channel.id, message.author.id)
    pending = _pending_autochannel_requests.get(key)
    if pending is None:
        pending = PendingAutoChannelRequest(bot=bot, mode=mode, message=message)
        _pending_autochannel_requests[key] = pending

    pending.bot = bot
    pending.mode = mode
    pending.message = message
    pending.contents.append(content)

    if pending.task is not None:
        pending.task.cancel()
    pending.task = asyncio.create_task(_flush_autochannel_request_after_delay(key))


async def _flush_autochannel_request_after_delay(key: tuple[int, int, int]) -> None:
    pending = _pending_autochannel_requests.get(key)
    if pending is None:
        return

    channel = pending.message.channel
    try:
        async with channel.typing():
            await asyncio.sleep(AUTOCHANNEL_DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        raise
    except (discord.Forbidden, discord.HTTPException):
        await asyncio.sleep(AUTOCHANNEL_DEBOUNCE_SECONDS)

    pending = _pending_autochannel_requests.pop(key, None)
    if pending is None:
        return

    prompt = "\n".join(pending.contents).strip()
    if not prompt:
        return

    if pending.mode == "question_only" and not _looks_like_question(prompt):
        return

    await handle_ai_request(
        bot=pending.bot,
        prompt=prompt,
        message=pending.message,
        source="autochannel",
    )


def _strip_bot_mentions(content: str, bot_user_id: int) -> str:
    return re.sub(fr"<@!?{bot_user_id}>", "", content).strip()


def _get_guild_id(
    interaction: discord.Interaction | None,
    message: discord.Message | None,
) -> int | None:
    if interaction:
        return interaction.guild_id
    if message and message.guild:
        return message.guild.id
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


def _response_chunks(
    content: str,
    *,
    interaction: discord.Interaction | None = None,
    message: discord.Message | None = None,
    guild: discord.Guild | None = None,
) -> list[str]:
    guild = guild or _get_response_guild(interaction, message)
    content = _prefer_member_mentions(content, guild)
    return split_discord_message(normalize_discord_markdown(content))


def _get_response_guild(
    interaction: discord.Interaction | None,
    message: discord.Message | None,
) -> discord.Guild | None:
    if interaction is not None:
        return interaction.guild
    if message is not None:
        return message.guild
    return None


def _prefer_member_mentions(content: str, guild: discord.Guild | None) -> str:
    if guild is None or not content:
        return content

    content = BACKTICKED_USER_MENTION_PATTERN.sub(
        lambda match: _member_mention_for_label(guild, match.group(2), fallback=match.group(1)),
        content,
    )
    parts = MARKDOWN_CODE_PATTERN.split(content)
    for index, part in enumerate(parts):
        if not part or part.startswith("`"):
            continue
        part = INVALID_USER_MENTION_PATTERN.sub(
            lambda match: _member_mention_for_label(guild, match.group(1), fallback=match.group(0)),
            part,
        )
        part = RAW_USER_ID_PATTERN.sub(lambda match: _member_mention_for_id(guild, match), part)
        parts[index] = part
    return "".join(parts)


def _member_mention_for_id(guild: discord.Guild, match: re.Match[str]) -> str:
    user_id = int(match.group(1))
    member = guild.get_member(user_id)
    return member.mention if member is not None else match.group(0)


def _member_mention_for_label(guild: discord.Guild, label: str, *, fallback: str) -> str:
    label = label.strip()
    if not label or label.isdigit():
        return fallback

    member = _find_guild_member_by_label(guild, label)
    return member.mention if member is not None else fallback


def _find_guild_member_by_label(guild: discord.Guild, label: str) -> discord.Member | None:
    normalized = _normalize_member_label(label)
    if not normalized:
        return None

    exact_matches: list[discord.Member] = []
    partial_matches: list[discord.Member] = []
    for member in guild.members:
        values = (
            member.display_name,
            member.name,
            member.global_name or "",
            member.nick or "",
            str(member),
        )
        normalized_values = [_normalize_member_label(value) for value in values if value]
        if normalized in normalized_values:
            exact_matches.append(member)
            continue
        if any(normalized in value for value in normalized_values):
            partial_matches.append(member)

    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(partial_matches) == 1:
        return partial_matches[0]
    return None


def _normalize_member_label(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold().strip("@")


def _extract_requested_style(prompt: str, bot: "DiscordAIBot", guild_id: int | None) -> str | None:
    text = prompt.casefold()
    available_styles = list(STYLE_NAMES)
    available_styles.extend(style.name for style in bot.settings.list_custom_styles(guild_id))

    aliases = {
        "기본": "default",
        "기본 스타일": "default",
        "예전 gpt": "classic",
        "gpt": "classic",
        "장단": "classic",
        "장단맞춰": "classic",
        "장단 맞춰": "classic",
        "효율": "efficient",
        "효율적": "efficient",
        "효율적으로": "efficient",
        "간결": "efficient",
        "간결하게": "efficient",
        "꾸밈없이": "efficient",
        "꾸밈없게": "efficient",
        "담백": "efficient",
        "담백하게": "efficient",
        "학습": "study",
        "학습 모드": "study",
        "가이드 학습": "study",
        "제미나이": "study",
        "gemini": "study",
        "그록": "grok",
        "grok": "grok",
        "매운": "spicy",
        "맵게": "spicy",
        "스파이시": "spicy",
        "spicy": "spicy",
        "19금": "spicy",
        "잼민이": "kids",
        "잼민": "kids",
        "키즈": "kids",
        "kids": "kids",
        "시비": "kids",
        "까불": "kids",
        "초딩": "kids",
        "진지": "efficient",
        "진지한": "efficient",
        "전문적": "efficient",
        "선생님": "study",
        "교사": "study",
        "강사": "study",
        "teacher": "study",
        "코더": "efficient",
        "개발자": "efficient",
        "코딩": "efficient",
        "coder": "efficient",
        "친구": "classic",
        "한국 친구": "classic",
        "한국어 친구": "classic",
    }

    for name in available_styles:
        lowered = name.casefold()
        if _has_style_request(text, lowered):
            return name

    for alias, style in aliases.items():
        if _has_style_request(text, alias) and (
            is_valid_style(style) or bot.settings.get_custom_style(guild_id, style) is not None
        ):
            return style

    return None


def _has_style_request(text: str, style_text: str) -> bool:
    style_text = style_text.casefold().strip()
    if not style_text:
        return False

    compact_text = text.replace(" ", "")
    compact_style = style_text.replace(" ", "")
    phrases = (
        f"{style_text} 스타일",
        f"{style_text} 모드",
        f"{style_text} 톤",
        f"{style_text} 말투",
        f"{style_text}로",
        f"{style_text}으로",
        f"{style_text}처럼",
        f"{style_text}답게",
        f"{style_text} 답",
        f"{style_text} 대답",
        f"{style_text} 설명",
    )
    compact_phrases = (
        f"{compact_style}스타일",
        f"{compact_style}모드",
        f"{compact_style}톤",
        f"{compact_style}말투",
        f"{compact_style}로",
        f"{compact_style}으로",
        f"{compact_style}처럼",
        f"{compact_style}답게",
        f"{compact_style}답",
        f"{compact_style}대답",
        f"{compact_style}설명",
    )
    return any(phrase in text for phrase in phrases) or any(phrase in compact_text for phrase in compact_phrases)


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


async def _send_response_chunks_to_message(
    response_message: discord.Message | discord.InteractionMessage,
    chunks: list[str],
) -> None:
    if not chunks:
        chunks = ["응답이 비어 있어요."]

    await _edit_ai_message(response_message, content=chunks[0], view=None)

    channel = getattr(response_message, "channel", None)
    if channel is None:
        return

    for chunk in chunks[1:]:
        await channel.send(
            chunk,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def _append_response_chunks_to_message(
    response_message: discord.Message | discord.InteractionMessage,
    header: str,
    chunks: list[str],
) -> None:
    if not chunks:
        chunks = ["응답이 비어 있어요."]

    content = "\n\n".join(chunk for chunk in chunks if chunk.strip())
    await _edit_ai_message(
        response_message,
        content=_append_message_content(header, content or "응답이 비어 있어요."),
        view=None,
    )


def _append_message_content(header: str, content: str) -> str:
    header = header.strip()
    content = content.strip()
    if not content:
        return header

    separator = "\n\n"
    max_length = 2000
    available = max_length - len(header) - len(separator)
    if available <= 0:
        return header[:max_length]
    if len(content) > available:
        content = f"{content[: max(0, available - 1)]}..."
    return f"{header}{separator}{content}"


async def _send_followup_chunks_to_channel(
    response_message: discord.Message | discord.InteractionMessage,
    chunks: list[str],
) -> None:
    if not chunks:
        chunks = ["응답이 비어 있어요."]

    channel = getattr(response_message, "channel", None)
    if channel is None:
        return

    for chunk in chunks:
        await channel.send(
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


async def _edit_ai_message(
    response_message: discord.Message | discord.InteractionMessage,
    *,
    content: str,
    view: discord.ui.View | None,
) -> None:
    await response_message.edit(
        content=content,
        view=view,
        allowed_mentions=discord.AllowedMentions.none(),
    )
