from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import aiohttp
import discord

from agent.styles import STYLE_NAMES, STYLE_PRESETS, format_style_presets, is_valid_style
from discord_bot.settings_store import AUTOCHANNEL_MODES
from providers.base import Message, ProviderOptions

if TYPE_CHECKING:
    from discord_bot.client import DiscordAIBot


ACTION_PLANNER_PROMPT = """\
너는 Discord AI Agent Bot의 자연어 도구 라우터다.
사용자 메시지가 봇 자체 설정 또는 Discord 서버 관리 실행 요청이면 JSON만 출력하라.
일반 질문, 설명 요청, 잡담, 코딩 질문이면 {"action":"none","args":{},"confidence":0}를 출력하라.

지원 action:
- autochannel_add: args channel, mode, keywords
- autochannel_remove: args channel
- autochannel_list: args
- autochannel_mode: args channel, mode, keywords
- style_set: args style
- style_show: args
- style_presets: args
- style_custom: args prompt
- channel_create: args type, name, category, topic, slowmode, nsfw, bitrate, user_limit, position, default_auto_archive_duration, default_thread_slowmode, rtc_region, video_quality_mode, default_layout, default_sort_order, require_tag
- channel_update: args channel, name, topic, slowmode, nsfw, bitrate, user_limit, category, position, sync_permissions, default_auto_archive_duration, default_thread_slowmode, rtc_region, video_quality_mode, default_layout, default_sort_order, require_tag
- channel_delete: args channel
- channel_clone: args channel, name, category
- channel_follow: args source_channel, destination_channel
- channel_pins_list: args channel, limit
- channel_permission_set: args channel, target, permissions, clear
- role_create: args name, color, secondary_color, tertiary_color, mentionable, hoist, icon_url, unicode_emoji
- role_update: args role, name, color, secondary_color, tertiary_color, mentionable, hoist, position, icon_url, unicode_emoji
- role_permissions_update: args role, allow, deny
- role_delete: args role
- role_add: args member, role
- role_remove: args member, role
- emoji_create: args name, url
- emoji_update: args emoji, name
- emoji_delete: args emoji
- sticker_create: args name, description, emoji, url
- sticker_update: args sticker, name, description, emoji
- sticker_delete: args sticker
- sound_create: args name, url, volume, emoji
- sound_update: args sound, name, volume, emoji
- sound_delete: args sound
- webhook_create: args channel, name, avatar_url
- webhook_list: args channel
- webhook_delete: args webhook
- invite_create: args channel, max_age, max_uses, temporary
- invite_list: args channel
- invite_delete: args invite
- audit_log_show: args limit
- guild_update: args name, description, icon_url, banner_url, splash_url, system_channel, rules_channel, public_updates_channel, preferred_locale, premium_progress_bar_enabled, invites_disabled
- template_create: args name, description
- template_list: args
- template_sync: args template
- template_delete: args template
- automod_rule_create: args name, keywords, regex_patterns, allow_list, exempt_roles, exempt_channels, enabled, custom_message
- automod_rule_list: args
- automod_rule_update: args rule, name, keywords, regex_patterns, allow_list, exempt_roles, exempt_channels, enabled, custom_message
- automod_rule_delete: args rule
- member_prune: args days, roles
- member_kick: args member, reason
- member_ban: args member, reason, delete_message_days
- member_unban: args user, reason
- member_timeout: args member, duration_minutes, reason
- member_nickname: args member, nickname
- member_move_voice: args member, channel
- member_mute_voice: args member, muted
- member_deafen_voice: args member, deafened
- member_disconnect_voice: args member
- message_purge: args channel, limit
- message_pin: args channel, message_id
- message_unpin: args channel, message_id
- thread_create: args channel, name
- thread_update: args thread, name, archived, locked, slowmode
- thread_delete: args thread
- forum_tag_create: args forum, name, emoji, moderated
- forum_tag_list: args forum
- forum_tag_update: args forum, tag, name, emoji, moderated
- forum_tag_delete: args forum, tag
- event_create: args name, start_time, end_time, description, channel, location
- event_update: args event, name, start_time, end_time, description, channel, location
- event_cancel: args event
- event_delete: args event
- welcome_screen_update: args enabled, description, channels
- widget_update: args enabled, channel
- onboarding_update: args enabled, default_channels
- integration_list: args
- integration_delete: args integration
- ban_list: args limit
- bulk_ban: args users, reason, delete_message_days
- vanity_invite_show: args
- none: args

규칙:
- 실행 요청이 명확할 때만 action을 선택하라.
- "설정법 알려줘", "명령어 뭐야", "할 수 있어?" 같은 설명 요청은 none이다.
- 삭제, 차단, 추방, 대량 삭제 같은 파괴적 작업은 사용자가 명확히 실행을 요청한 경우에만 선택하라.
- channel/member/role/thread/forum/emoji/sticker/sound/event/webhook/integration은 Discord mention 또는 ID가 있으면 그대로 넣어라. 예: <#123>, <@456>, <@&789>.
- member가 멘션이나 ID가 아니어도 사용자가 말한 별명, 표시 이름, 유저명을 문자열 그대로 넣어라.
- 현재 채널을 뜻하면 channel을 "current"로 넣어라.
- type은 text, voice, stage, category, forum, media 중 하나만 사용하라.
- mode는 always, question_only, keyword 중 하나만 사용하라.
- style은 default, grok, serious, teacher, coder, korean_friend, custom 중 하나만 사용하라.
- slowmode는 초 단위 정수다.
- position, bitrate, user_limit, default_auto_archive_duration, default_thread_slowmode는 정수다.
- video_quality_mode는 auto 또는 full 중 하나다.
- default_layout은 not_set, list_view, gallery_view 중 하나다.
- default_sort_order는 latest_activity 또는 creation_date 중 하나다.
- nsfw, mentionable, hoist, temporary, muted, deafened, archived, locked는 true/false/null 중 하나다.
- start_time/end_time은 가능하면 ISO 8601 형식으로 넣어라.
- 파일이 필요한 emoji/sticker/sound 생성은 첨부파일을 사용한다고 판단되면 url을 비워도 된다.
- permissions, allow, deny는 Discord permission 이름 배열이다. 예: ["send_messages", "view_channel"].
- channel_permission_set의 target은 역할 또는 멤버다. clear가 true면 해당 target의 채널 권한 덮어쓰기를 제거한다.
- keywords는 배열이다.
- welcome_screen_update의 channels와 onboarding_update의 default_channels는 채널 mention/ID/name 배열이다.
- bulk_ban의 users는 사용자 mention/ID 배열이다.
- confidence는 0부터 1 사이 숫자다.
"""


ACTION_STATUS_LABELS = {
    "autochannel_add": "자동 응답 채널 추가",
    "autochannel_remove": "자동 응답 채널 제거",
    "autochannel_list": "자동 응답 채널 조회",
    "autochannel_mode": "자동 응답 채널 모드 변경",
    "style_set": "AI 스타일 설정",
    "style_show": "AI 스타일 조회",
    "style_presets": "AI 스타일 목록 조회",
    "style_custom": "커스텀 스타일 저장",
    "channel_create": "채널 생성",
    "channel_update": "채널 수정",
    "channel_delete": "채널 삭제",
    "channel_clone": "채널 복제",
    "channel_follow": "공지 채널 팔로우",
    "channel_pins_list": "고정 메시지 조회",
    "channel_permission_set": "채널 권한 설정",
    "role_create": "역할 생성",
    "role_update": "역할 수정",
    "role_permissions_update": "역할 권한 수정",
    "role_delete": "역할 삭제",
    "role_add": "역할 추가",
    "role_remove": "역할 제거",
    "emoji_create": "이모지 생성",
    "emoji_update": "이모지 수정",
    "emoji_delete": "이모지 삭제",
    "sticker_create": "스티커 생성",
    "sticker_update": "스티커 수정",
    "sticker_delete": "스티커 삭제",
    "sound_create": "사운드 생성",
    "sound_update": "사운드 수정",
    "sound_delete": "사운드 삭제",
    "webhook_create": "웹훅 생성",
    "webhook_list": "웹훅 조회",
    "webhook_delete": "웹훅 삭제",
    "invite_create": "초대 링크 생성",
    "invite_list": "초대 링크 조회",
    "invite_delete": "초대 링크 삭제",
    "audit_log_show": "감사 로그 조회",
    "guild_update": "서버 설정 변경",
    "template_create": "서버 템플릿 생성",
    "template_list": "서버 템플릿 조회",
    "template_sync": "서버 템플릿 동기화",
    "template_delete": "서버 템플릿 삭제",
    "automod_rule_create": "AutoMod 규칙 생성",
    "automod_rule_list": "AutoMod 규칙 조회",
    "automod_rule_update": "AutoMod 규칙 수정",
    "automod_rule_delete": "AutoMod 규칙 삭제",
    "member_prune": "비활동 멤버 정리",
    "member_kick": "멤버 추방",
    "member_ban": "멤버 차단",
    "member_unban": "멤버 차단 해제",
    "member_timeout": "멤버 타임아웃",
    "member_nickname": "멤버 별명 변경",
    "member_move_voice": "음성 멤버 이동",
    "member_mute_voice": "음성 멤버 음소거",
    "member_deafen_voice": "음성 멤버 헤드셋 음소거",
    "member_disconnect_voice": "음성 연결 끊기",
    "message_purge": "메시지 삭제",
    "message_pin": "메시지 고정",
    "message_unpin": "메시지 고정 해제",
    "thread_create": "스레드 생성",
    "thread_update": "스레드 수정",
    "thread_delete": "스레드 삭제",
    "forum_tag_create": "포럼 태그 생성",
    "forum_tag_list": "포럼 태그 조회",
    "forum_tag_update": "포럼 태그 수정",
    "forum_tag_delete": "포럼 태그 삭제",
    "event_create": "이벤트 생성",
    "event_update": "이벤트 수정",
    "event_cancel": "이벤트 취소",
    "event_delete": "이벤트 삭제",
    "welcome_screen_update": "환영 화면 변경",
    "widget_update": "서버 위젯 변경",
    "onboarding_update": "온보딩 변경",
    "integration_list": "통합 목록 조회",
    "integration_delete": "통합 삭제",
    "ban_list": "차단 목록 조회",
    "bulk_ban": "대량 차단",
    "vanity_invite_show": "커스텀 초대 조회",
}

READ_ONLY_ACTIONS = {
    "autochannel_list",
    "style_show",
    "style_presets",
    "channel_pins_list",
    "webhook_list",
    "invite_list",
    "audit_log_show",
    "template_list",
    "automod_rule_list",
    "forum_tag_list",
    "integration_list",
    "ban_list",
    "vanity_invite_show",
}


@dataclass(frozen=True)
class ActionPlan:
    action: str
    args: dict[str, Any]
    confidence: float


async def try_handle_agent_action(
    bot: "DiscordAIBot",
    prompt: str,
    *,
    interaction: discord.Interaction | None,
    message: discord.Message | None,
    status_callback: Callable[[str], Awaitable[None]] | None = None,
) -> str | None:
    guild = interaction.guild if interaction else message.guild if message else None
    if guild is None:
        return None

    plan = await plan_agent_action(bot, prompt)
    if plan is None:
        return None

    context = build_action_context(
        bot=bot,
        guild=guild,
        interaction=interaction,
        message=message,
        status_callback=status_callback,
    )
    return await execute_agent_action(context, plan)


async def plan_agent_action(bot: "DiscordAIBot", prompt: str) -> ActionPlan | None:
    rule_plan = _rule_based_action_plan(prompt)
    if rule_plan is not None:
        return rule_plan

    plan = await _plan_action(bot, prompt)
    if plan is None or plan.action == "none" or plan.confidence < 0.65:
        return None
    return plan


def build_action_context(
    *,
    bot: "DiscordAIBot",
    guild: discord.Guild,
    interaction: discord.Interaction | None,
    message: discord.Message | None,
    status_callback: Callable[[str], Awaitable[None]] | None = None,
) -> "ActionContext":
    return ActionContext(
        bot=bot,
        guild=guild,
        channel=interaction.channel if interaction else message.channel if message else None,
        user=interaction.user if interaction else message.author if message else None,
        message=message,
        status_callback=status_callback,
    )


async def execute_agent_action(context: "ActionContext", plan: ActionPlan) -> str:
    return await _execute_plan(context, plan)


def action_requires_confirmation(plan: ActionPlan) -> bool:
    return plan.action not in READ_ONLY_ACTIONS


def describe_action_plan(plan: ActionPlan) -> str:
    label = _action_label(plan.action)
    args = _format_action_args(plan.args)
    if not args:
        return f"작업 내용: **{label}**"
    return f"작업 내용: **{label}** / {args}"


def _rule_based_action_plan(prompt: str) -> ActionPlan | None:
    text = " ".join(prompt.strip().split())
    if not text:
        return None

    if _contains_any(text, "타임아웃", "타임 아웃", "timeout", "채팅금지", "채팅 금지"):
        duration = _extract_duration_minutes(text)
        is_clear = _contains_any(text, "해제", "풀어", "풀어줘", "취소")
        if duration is None and not is_clear:
            return None

        member = _extract_member_query_from_request(
            text,
            action_patterns=(
                r"타임\s*아웃",
                r"timeout",
                r"채팅\s*금지",
            ),
        )
        if not member:
            return None

        args: dict[str, Any] = {
            "member": member,
            "duration_minutes": 0 if is_clear else duration,
        }
        reason = _extract_reason(text)
        if reason:
            args["reason"] = reason
        return ActionPlan(action="member_timeout", args=args, confidence=0.95)

    if _contains_any(text, "추방", "킥", "kick"):
        member = _extract_member_query_from_request(
            text,
            action_patterns=(
                r"추방",
                r"킥",
                r"kick",
            ),
        )
        if not member:
            return None
        args = {"member": member}
        reason = _extract_reason(text)
        if reason:
            args["reason"] = reason
        return ActionPlan(action="member_kick", args=args, confidence=0.92)

    if _contains_any(text, "차단", "밴", "ban") and not _contains_any(text, "해제", "풀어"):
        member = _extract_member_query_from_request(
            text,
            action_patterns=(
                r"차단",
                r"밴",
                r"ban",
            ),
        )
        if not member:
            return None
        args = {"member": member}
        reason = _extract_reason(text)
        if reason:
            args["reason"] = reason
        return ActionPlan(action="member_ban", args=args, confidence=0.92)

    return None


def _contains_any(text: str, *needles: str) -> bool:
    normalized = text.casefold()
    return any(needle.casefold() in normalized for needle in needles)


def _extract_duration_minutes(text: str) -> int | None:
    match = re.search(
        r"(?P<number>\d+)\s*(?P<unit>초|분|시간|일|주|seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        amount = int(match.group("number"))
        return _duration_to_minutes(amount, match.group("unit"))

    korean_numbers = {
        "한": 1,
        "두": 2,
        "세": 3,
        "네": 4,
        "다섯": 5,
        "여섯": 6,
        "일곱": 7,
        "여덟": 8,
        "아홉": 9,
        "열": 10,
    }
    match = re.search(
        r"(?P<number>한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*(?P<unit>분|시간|일|주)",
        text,
    )
    if match:
        return _duration_to_minutes(korean_numbers[match.group("number")], match.group("unit"))

    return None


def _duration_to_minutes(amount: int, unit: str) -> int:
    normalized = unit.casefold()
    if normalized in {"초", "second", "seconds", "sec", "secs"}:
        return max(1, (amount + 59) // 60)
    if normalized in {"분", "minute", "minutes", "min", "mins"}:
        return amount
    if normalized in {"시간", "hour", "hours", "hr", "hrs"}:
        return amount * 60
    if normalized in {"일", "day", "days"}:
        return amount * 24 * 60
    if normalized in {"주", "week", "weeks"}:
        return amount * 7 * 24 * 60
    return amount


def _extract_member_query_from_request(text: str, *, action_patterns: tuple[str, ...]) -> str:
    member_mentions = re.findall(r"<@!?\d{15,25}>", text)
    if member_mentions:
        return member_mentions[0]

    cleaned = text
    cleaned = re.sub(
        r"(?:사유|이유|reason)\s*[:=]?\s*.+$",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\d+\s*(?:초|분|시간|일|주|seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*(?:분|시간|일|주)", " ", cleaned)

    for pattern in action_patterns:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(
        r"(?:좀|제발|바로|빨리|적용|처리|실행|해줘|해주세요|해라|걸어줘|시켜줘|"
        r"해제|풀어줘|풀어|취소|그\s*사람|이\s*사람|저\s*사람|걔|쟤|얘)",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"[`*_~>|\\[\](){}:;,!?]", " ", cleaned)
    tokens = []
    for token in cleaned.split():
        token = token.strip("@ ")
        token = re.sub(r"(?:님|씨|에게|한테|으로|로|을|를)$", "", token)
        if token and token not in {"좀", "제발", "바로", "빨리"}:
            tokens.append(token)
    cleaned = " ".join(tokens)
    return cleaned[:100]


def _extract_reason(text: str) -> str:
    match = re.search(r"(?:사유|이유|reason)\s*[:=]?\s*(.+)$", text, flags=re.IGNORECASE)
    if not match:
        return ""
    return " ".join(match.group(1).split())[:512]


@dataclass
class ActionContext:
    bot: "DiscordAIBot"
    guild: discord.Guild
    channel: discord.abc.GuildChannel | discord.Thread | discord.abc.PrivateChannel | None
    user: discord.User | discord.Member | None
    message: discord.Message | None = None
    status_callback: Callable[[str], Awaitable[None]] | None = None


@dataclass(frozen=True)
class BinaryAsset:
    data: bytes
    filename: str


async def _plan_action(bot: "DiscordAIBot", prompt: str) -> ActionPlan | None:
    messages: list[Message] = [
        {"role": "system", "content": ACTION_PLANNER_PROMPT},
        {"role": "user", "content": prompt},
    ]
    raw = await bot.agent.provider.generate_response(
        messages,
        ProviderOptions(temperature=0.0, max_tokens=800),
    )
    data = _loads_json_object(raw)
    if not data:
        return None

    action = str(data.get("action", "none"))
    args = data.get("args", {})
    confidence = data.get("confidence", 0)
    if not isinstance(args, dict):
        args = {}
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0

    return ActionPlan(action=action, args=args, confidence=confidence)


def _loads_json_object(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    return data if isinstance(data, dict) else None


async def _emit_status(context: ActionContext, content: str) -> None:
    if context.status_callback is None:
        return
    await context.status_callback(content)


def _action_label(action: str) -> str:
    return ACTION_STATUS_LABELS.get(action, "작업")


def _format_action_args(args: dict[str, Any]) -> str:
    if not args:
        return ""

    parts: list[str] = []
    for key, value in args.items():
        if value is None or value == "" or value == []:
            continue
        rendered = _shorten_arg_value(value)
        parts.append(f"`{key}`={rendered}")
        if len(parts) >= 8:
            parts.append("...")
            break
    return " / ".join(parts)


def _shorten_arg_value(value: Any, *, limit: int = 120) -> str:
    if isinstance(value, (dict, list)):
        rendered = json.dumps(value, ensure_ascii=False)
    else:
        rendered = str(value)
    rendered = " ".join(rendered.split())
    if len(rendered) <= limit:
        return rendered
    return f"{rendered[: limit - 1]}..."


def _looks_successful(result: str) -> bool:
    failure_markers = (
        "필요해요",
        "필요합니다",
        "찾지 못",
        "거부",
        "에 실패",
        "실패했어요",
        "없어서",
        "지원하지",
        "수 없",
        "아직",
        "알려주세요",
        "확인해 주세요",
    )
    return not any(marker in result for marker in failure_markers)


async def _execute_plan(context: ActionContext, plan: ActionPlan) -> str:
    action = plan.action
    args = plan.args
    action_label = _action_label(action)
    await _emit_status(context, f"{action_label} 중...")

    if action == "autochannel_add":
        result = await _autochannel_add(context, args)
    elif action == "autochannel_remove":
        result = await _autochannel_remove(context, args)
    elif action == "autochannel_list":
        result = _autochannel_list(context)
    elif action == "autochannel_mode":
        result = await _autochannel_mode(context, args)
    elif action == "style_set":
        result = _style_set(context, args)
    elif action == "style_show":
        result = _style_show(context)
    elif action == "style_presets":
        result = format_style_presets()
    elif action == "style_custom":
        result = _style_custom(context, args)
    elif action == "channel_create":
        result = await _channel_create(context, args)
    elif action == "channel_update":
        result = await _channel_update(context, args)
    elif action == "channel_delete":
        result = await _channel_delete(context, args)
    elif action == "channel_clone":
        result = await _channel_clone(context, args)
    elif action == "channel_follow":
        result = await _channel_follow(context, args)
    elif action == "channel_pins_list":
        result = await _channel_pins_list(context, args)
    elif action == "channel_permission_set":
        result = await _channel_permission_set(context, args)
    elif action == "role_create":
        result = await _role_create(context, args)
    elif action == "role_update":
        result = await _role_update(context, args)
    elif action == "role_permissions_update":
        result = await _role_permissions_update(context, args)
    elif action == "role_delete":
        result = await _role_delete(context, args)
    elif action == "role_add":
        result = await _role_update_member(context, args, add=True)
    elif action == "role_remove":
        result = await _role_update_member(context, args, add=False)
    elif action == "emoji_create":
        result = await _emoji_create(context, args)
    elif action == "emoji_update":
        result = await _emoji_update(context, args)
    elif action == "emoji_delete":
        result = await _emoji_delete(context, args)
    elif action == "sticker_create":
        result = await _sticker_create(context, args)
    elif action == "sticker_update":
        result = await _sticker_update(context, args)
    elif action == "sticker_delete":
        result = await _sticker_delete(context, args)
    elif action == "sound_create":
        result = await _sound_create(context, args)
    elif action == "sound_update":
        result = await _sound_update(context, args)
    elif action == "sound_delete":
        result = await _sound_delete(context, args)
    elif action == "webhook_create":
        result = await _webhook_create(context, args)
    elif action == "webhook_list":
        result = await _webhook_list(context, args)
    elif action == "webhook_delete":
        result = await _webhook_delete(context, args)
    elif action == "invite_create":
        result = await _invite_create(context, args)
    elif action == "invite_list":
        result = await _invite_list(context, args)
    elif action == "invite_delete":
        result = await _invite_delete(context, args)
    elif action == "audit_log_show":
        result = await _audit_log_show(context, args)
    elif action == "guild_update":
        result = await _guild_update(context, args)
    elif action == "template_create":
        result = await _template_create(context, args)
    elif action == "template_list":
        result = await _template_list(context)
    elif action == "template_sync":
        result = await _template_sync(context, args)
    elif action == "template_delete":
        result = await _template_delete(context, args)
    elif action == "automod_rule_create":
        result = await _automod_rule_create(context, args)
    elif action == "automod_rule_list":
        result = await _automod_rule_list(context)
    elif action == "automod_rule_update":
        result = await _automod_rule_update(context, args)
    elif action == "automod_rule_delete":
        result = await _automod_rule_delete(context, args)
    elif action == "member_prune":
        result = await _member_prune(context, args)
    elif action == "member_kick":
        result = await _member_kick(context, args)
    elif action == "member_ban":
        result = await _member_ban(context, args)
    elif action == "member_unban":
        result = await _member_unban(context, args)
    elif action == "member_timeout":
        result = await _member_timeout(context, args)
    elif action == "member_nickname":
        result = await _member_nickname(context, args)
    elif action == "member_move_voice":
        result = await _member_move_voice(context, args)
    elif action == "member_mute_voice":
        result = await _member_voice_state(context, args, field="mute")
    elif action == "member_deafen_voice":
        result = await _member_voice_state(context, args, field="deafen")
    elif action == "member_disconnect_voice":
        result = await _member_disconnect_voice(context, args)
    elif action == "message_purge":
        result = await _message_purge(context, args)
    elif action == "message_pin":
        result = await _message_pin(context, args, pin=True)
    elif action == "message_unpin":
        result = await _message_pin(context, args, pin=False)
    elif action == "thread_create":
        result = await _thread_create(context, args)
    elif action == "thread_update":
        result = await _thread_update(context, args)
    elif action == "thread_delete":
        result = await _thread_delete(context, args)
    elif action == "forum_tag_create":
        result = await _forum_tag_create(context, args)
    elif action == "forum_tag_list":
        result = _forum_tag_list(context, args)
    elif action == "forum_tag_update":
        result = await _forum_tag_update(context, args)
    elif action == "forum_tag_delete":
        result = await _forum_tag_delete(context, args)
    elif action == "event_create":
        result = await _event_create(context, args)
    elif action == "event_update":
        result = await _event_update(context, args)
    elif action == "event_cancel":
        result = await _event_cancel(context, args)
    elif action == "event_delete":
        result = await _event_delete(context, args)
    elif action == "welcome_screen_update":
        result = await _welcome_screen_update(context, args)
    elif action == "widget_update":
        result = await _widget_update(context, args)
    elif action == "onboarding_update":
        result = await _onboarding_update(context, args)
    elif action == "integration_list":
        result = await _integration_list(context)
    elif action == "integration_delete":
        result = await _integration_delete(context, args)
    elif action == "ban_list":
        result = await _ban_list(context, args)
    elif action == "bulk_ban":
        result = await _bulk_ban(context, args)
    elif action == "vanity_invite_show":
        result = await _vanity_invite_show(context)
    else:
        result = "그 작업은 아직 제가 실행할 수 있는 도구로 연결되어 있지 않아요."

    if _looks_successful(result):
        await _emit_status(context, f"{action_label}했습니다.")
        await asyncio.sleep(0.6)
    else:
        await _emit_status(context, "실행하지 못했습니다.")
        await asyncio.sleep(0.3)
    return result


async def _autochannel_add(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_channels"):
        return "이 작업은 관리자 또는 Manage Channels 권한이 필요해요."

    channel = _resolve_text_channel(context, args.get("channel"))
    if channel is None:
        return "자동 응답 채널로 등록할 텍스트 채널을 찾지 못했어요. 채널을 멘션해서 다시 요청해 주세요."

    mode = str(args.get("mode", "always"))
    if mode not in AUTOCHANNEL_MODES:
        return f"mode는 {', '.join(f'`{mode}`' for mode in AUTOCHANNEL_MODES)} 중 하나여야 해요."

    keywords = _normalize_keywords(args.get("keywords"))
    if mode == "keyword" and not keywords:
        return "`keyword` 모드는 키워드가 하나 이상 필요해요."

    context.bot.settings.upsert_autochannel(
        guild_id=context.guild.id,
        channel_id=channel.id,
        mode=mode,
        keywords=keywords if mode == "keyword" else [],
    )
    return f"{channel.mention} 채널을 AI 자동 응답 채널로 등록했어요. 모드: `{mode}`{_keyword_suffix(keywords if mode == 'keyword' else [])}"


async def _autochannel_remove(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_channels"):
        return "이 작업은 관리자 또는 Manage Channels 권한이 필요해요."

    channel = _resolve_text_channel(context, args.get("channel"))
    if channel is None:
        return "제거할 자동 응답 채널을 찾지 못했어요. 채널을 멘션해서 다시 요청해 주세요."

    removed = context.bot.settings.remove_autochannel(
        guild_id=context.guild.id,
        channel_id=channel.id,
    )
    if not removed:
        return f"{channel.mention} 채널은 자동 응답 채널로 등록되어 있지 않아요."
    return f"{channel.mention} 채널을 AI 자동 응답 채널에서 제거했어요."


def _autochannel_list(context: ActionContext) -> str:
    if not _user_has(context, "manage_channels"):
        return "이 작업은 관리자 또는 Manage Channels 권한이 필요해요."

    settings = context.bot.settings.list_autochannels(context.guild.id)
    if not settings:
        return "현재 등록된 AI 자동 응답 채널이 없어요."

    lines = ["현재 AI 자동 응답 채널:"]
    for setting in settings:
        channel = context.guild.get_channel(setting.channel_id)
        label = channel.mention if channel else f"<#{setting.channel_id}>"
        lines.append(f"- {label}: `{setting.mode}`{_keyword_suffix(setting.keywords)}")
    return "\n".join(lines)


async def _autochannel_mode(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_channels"):
        return "이 작업은 관리자 또는 Manage Channels 권한이 필요해요."

    channel = _resolve_text_channel(context, args.get("channel"))
    if channel is None:
        return "모드를 바꿀 자동 응답 채널을 찾지 못했어요. 채널을 멘션해서 다시 요청해 주세요."

    existing = context.bot.settings.get_autochannel(
        guild_id=context.guild.id,
        channel_id=channel.id,
    )
    if existing is None:
        return f"{channel.mention} 채널은 아직 자동 응답 채널로 등록되어 있지 않아요."

    mode = str(args.get("mode", "always"))
    if mode not in AUTOCHANNEL_MODES:
        return f"mode는 {', '.join(f'`{mode}`' for mode in AUTOCHANNEL_MODES)} 중 하나여야 해요."

    keywords = _normalize_keywords(args.get("keywords"))
    if mode == "keyword" and not keywords:
        return "`keyword` 모드는 키워드가 하나 이상 필요해요."

    context.bot.settings.upsert_autochannel(
        guild_id=context.guild.id,
        channel_id=channel.id,
        mode=mode,
        keywords=keywords if mode == "keyword" else [],
    )
    return f"{channel.mention} 채널의 자동 응답 모드를 `{mode}`로 변경했어요.{_keyword_suffix(keywords if mode == 'keyword' else [])}"


def _style_set(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Guild 권한이 필요해요."

    style = str(args.get("style", "")).strip()
    if not is_valid_style(style):
        return f"지원하지 않는 스타일이에요. 사용 가능: {', '.join(f'`{name}`' for name in STYLE_NAMES)}"

    context.bot.settings.set_default_style(context.guild.id, style)
    return f"서버 기본 AI 스타일을 `{style}`로 설정했어요."


def _style_show(context: ActionContext) -> str:
    style = context.bot.settings.get_default_style(context.guild.id)
    preset = STYLE_PRESETS.get(style, STYLE_PRESETS["default"])
    custom_configured = bool(context.bot.settings.get_custom_style_prompt(context.guild.id))
    custom_status = "설정됨" if custom_configured else "미설정"
    return f"현재 서버 기본 AI 스타일: `{preset.name}` - {preset.description}\ncustom 프롬프트: {custom_status}"


def _style_custom(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Guild 권한이 필요해요."

    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return "custom 스타일의 시스템 프롬프트 내용을 함께 알려주세요."

    context.bot.settings.set_custom_style_prompt(context.guild.id, prompt)
    return "custom 스타일의 시스템 프롬프트를 저장했어요. 서버 기본값으로 쓰려면 `custom 스타일로 바꿔줘`라고 요청하면 돼요."


async def _channel_create(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_channels"):
        return "이 작업은 관리자 또는 Manage Channels 권한이 필요해요."
    if not _bot_has(context, "manage_channels"):
        return "봇에게 Manage Channels 권한이 없어서 채널을 만들 수 없어요."

    name = str(args.get("name") or "").strip()
    if not name:
        return "만들 채널 이름을 알려주세요."

    channel_type = str(args.get("type") or "text").strip().casefold()
    category = _resolve_category(context, args.get("category"))
    reason = _audit_reason(context, "AI agent channel create")
    position = _optional_int(args.get("position"))
    default_archive = _optional_int(args.get("default_auto_archive_duration"))
    default_thread_slowmode = _optional_int(args.get("default_thread_slowmode"))
    video_quality_mode = _parse_video_quality_mode(args.get("video_quality_mode"))

    try:
        if channel_type == "category":
            create_kwargs: dict[str, Any] = {"reason": reason}
            if position is not None:
                create_kwargs["position"] = position
            channel = await context.guild.create_category(name=name, **create_kwargs)
        elif channel_type == "voice":
            create_kwargs: dict[str, Any] = {
                "category": category,
                "nsfw": _optional_bool(args.get("nsfw")) or False,
                "reason": reason,
            }
            if position is not None:
                create_kwargs["position"] = position
            if _optional_int(args.get("bitrate")) is not None:
                create_kwargs["bitrate"] = _optional_int(args.get("bitrate"))
            if _optional_int(args.get("user_limit")) is not None:
                create_kwargs["user_limit"] = _optional_int(args.get("user_limit"))
            if str(args.get("rtc_region") or "").strip():
                create_kwargs["rtc_region"] = str(args.get("rtc_region")).strip()
            if video_quality_mode is not None:
                create_kwargs["video_quality_mode"] = video_quality_mode
            channel = await context.guild.create_voice_channel(name=name, **create_kwargs)
        elif channel_type == "stage":
            create_kwargs = {
                "category": category,
                "nsfw": _optional_bool(args.get("nsfw")) or False,
                "reason": reason,
            }
            if position is not None:
                create_kwargs["position"] = position
            if _optional_int(args.get("bitrate")) is not None:
                create_kwargs["bitrate"] = _optional_int(args.get("bitrate"))
            if _optional_int(args.get("user_limit")) is not None:
                create_kwargs["user_limit"] = _optional_int(args.get("user_limit"))
            if str(args.get("rtc_region") or "").strip():
                create_kwargs["rtc_region"] = str(args.get("rtc_region")).strip()
            if video_quality_mode is not None:
                create_kwargs["video_quality_mode"] = video_quality_mode
            channel = await context.guild.create_stage_channel(name=name, **create_kwargs)
        elif channel_type == "forum":
            create_kwargs = _forum_create_kwargs(args, category, reason, media=False)
            channel = await context.guild.create_forum(name=name, **create_kwargs)
            require_tag = _optional_bool(args.get("require_tag"))
            if require_tag is not None:
                channel = await channel.edit(require_tag=require_tag, reason=reason) or channel
        elif channel_type == "media":
            create_kwargs = _forum_create_kwargs(args, category, reason, media=True)
            channel = await context.guild.create_forum(name=name, **create_kwargs)
            require_tag = _optional_bool(args.get("require_tag"))
            if require_tag is not None:
                channel = await channel.edit(require_tag=require_tag, reason=reason) or channel
        else:
            create_kwargs = {
                "category": category,
                "topic": str(args.get("topic") or ""),
                "slowmode_delay": _optional_int(args.get("slowmode")) or 0,
                "nsfw": _optional_bool(args.get("nsfw")) or False,
                "reason": reason,
            }
            if position is not None:
                create_kwargs["position"] = position
            if default_archive is not None:
                create_kwargs["default_auto_archive_duration"] = default_archive
            if default_thread_slowmode is not None:
                create_kwargs["default_thread_slowmode_delay"] = default_thread_slowmode
            channel = await context.guild.create_text_channel(name=name, **create_kwargs)
    except discord.Forbidden:
        return "Discord가 채널 생성을 거부했어요. 봇 권한이나 역할 위치를 확인해 주세요."
    except discord.HTTPException as exc:
        return f"채널 생성에 실패했어요: {exc.text or exc}"

    label = channel.mention if hasattr(channel, "mention") else f"`{channel.name}`"
    return f"{label} 채널을 만들었어요."


async def _channel_update(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_channels"):
        return "이 작업은 관리자 또는 Manage Channels 권한이 필요해요."
    if not _bot_has(context, "manage_channels"):
        return "봇에게 Manage Channels 권한이 없어서 채널 설정을 바꿀 수 없어요."

    channel = _resolve_guild_channel(context, args.get("channel"))
    if channel is None:
        return "설정을 바꿀 채널을 찾지 못했어요. 채널을 멘션하거나 '현재 채널'이라고 다시 요청해 주세요."

    edit_kwargs: dict[str, Any] = {}
    name = str(args.get("name") or "").strip()
    topic = str(args.get("topic") or "").strip()
    slowmode = args.get("slowmode")
    nsfw = args.get("nsfw")
    bitrate = args.get("bitrate")
    user_limit = args.get("user_limit")
    category = _resolve_category(context, args.get("category"))
    position = _optional_int(args.get("position"))
    sync_permissions = _optional_bool(args.get("sync_permissions"))
    default_archive = _optional_int(args.get("default_auto_archive_duration"))
    default_thread_slowmode = _optional_int(args.get("default_thread_slowmode"))
    rtc_region = str(args.get("rtc_region") or "").strip()
    video_quality_mode = _parse_video_quality_mode(args.get("video_quality_mode"))
    default_layout = _parse_forum_layout(args.get("default_layout"))
    default_sort_order = _parse_forum_sort_order(args.get("default_sort_order"))
    require_tag = _optional_bool(args.get("require_tag"))

    if name:
        edit_kwargs["name"] = name
    if topic and hasattr(channel, "topic"):
        edit_kwargs["topic"] = topic
    if slowmode is not None:
        try:
            slowmode_int = int(slowmode)
        except (TypeError, ValueError):
            return "slowmode는 초 단위 숫자여야 해요."
        if slowmode_int < 0 or slowmode_int > 21600:
            return "slowmode 값은 0 이상 21600 이하 초여야 해요."
        if hasattr(channel, "slowmode_delay"):
            edit_kwargs["slowmode_delay"] = slowmode_int
    if isinstance(nsfw, bool):
        edit_kwargs["nsfw"] = nsfw
    if bitrate is not None and hasattr(channel, "bitrate"):
        edit_kwargs["bitrate"] = _optional_int(bitrate)
    if user_limit is not None and hasattr(channel, "user_limit"):
        edit_kwargs["user_limit"] = _optional_int(user_limit)
    if category is not None and hasattr(channel, "category"):
        edit_kwargs["category"] = category
    if position is not None:
        edit_kwargs["position"] = position
    if sync_permissions is not None:
        edit_kwargs["sync_permissions"] = sync_permissions
    if default_archive is not None and isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
        edit_kwargs["default_auto_archive_duration"] = default_archive
    if default_thread_slowmode is not None and isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
        edit_kwargs["default_thread_slowmode_delay"] = default_thread_slowmode
    if rtc_region and isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
        edit_kwargs["rtc_region"] = rtc_region
    if video_quality_mode is not None and isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
        edit_kwargs["video_quality_mode"] = video_quality_mode
    if default_layout is not None and isinstance(channel, discord.ForumChannel):
        edit_kwargs["default_layout"] = default_layout
    if default_sort_order is not None and isinstance(channel, discord.ForumChannel):
        edit_kwargs["default_sort_order"] = default_sort_order
    if require_tag is not None and isinstance(channel, discord.ForumChannel):
        edit_kwargs["require_tag"] = require_tag

    if not edit_kwargs:
        return "변경할 채널 설정을 찾지 못했어요. 이름, 주제, 슬로우모드, NSFW, 비트레이트, 유저 제한, 카테고리, 위치, 포럼 기본값 중 하나를 알려주세요."

    try:
        await channel.edit(**edit_kwargs, reason=_audit_reason(context, "AI agent channel update"))
    except discord.Forbidden:
        return "Discord가 채널 변경을 거부했어요. 봇 권한이나 채널별 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"채널 변경에 실패했어요: {exc.text or exc}"

    changed = ", ".join(f"`{key}`" for key in edit_kwargs)
    label = channel.mention if hasattr(channel, "mention") else f"`{channel.name}`"
    return f"{label} 채널 설정을 변경했어요: {changed}"


async def _channel_delete(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_channels"):
        return "이 작업은 관리자 또는 Manage Channels 권한이 필요해요."
    if not _bot_has(context, "manage_channels"):
        return "봇에게 Manage Channels 권한이 없어서 채널을 삭제할 수 없어요."

    channel = _resolve_guild_channel(context, args.get("channel"))
    if channel is None:
        return "삭제할 채널을 찾지 못했어요. 채널을 멘션해서 다시 요청해 주세요."

    name = channel.name
    try:
        await channel.delete(reason=_audit_reason(context, "AI agent channel delete"))
    except discord.Forbidden:
        return "Discord가 채널 삭제를 거부했어요. 봇 권한이나 채널별 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"채널 삭제에 실패했어요: {exc.text or exc}"

    return f"`{name}` 채널을 삭제했어요."


async def _channel_clone(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_channels"):
        return "이 작업은 관리자 또는 Manage Channels 권한이 필요해요."
    if not _bot_has(context, "manage_channels"):
        return "봇에게 Manage Channels 권한이 없어서 채널을 복제할 수 없어요."

    channel = _resolve_guild_channel(context, args.get("channel"))
    if not isinstance(channel, discord.abc.GuildChannel):
        return "복제할 채널을 찾지 못했어요. 채널을 멘션해서 다시 요청해 주세요."

    clone_kwargs: dict[str, Any] = {
        "reason": _audit_reason(context, "AI agent channel clone"),
    }
    name = str(args.get("name") or "").strip()
    category = _resolve_category(context, args.get("category"))
    if name:
        clone_kwargs["name"] = name
    if category is not None and not isinstance(channel, discord.CategoryChannel):
        clone_kwargs["category"] = category

    try:
        cloned = await channel.clone(**clone_kwargs)
    except TypeError:
        clone_kwargs.pop("category", None)
        cloned = await channel.clone(**clone_kwargs)
    except discord.Forbidden:
        return "Discord가 채널 복제를 거부했어요. 봇 권한이나 채널별 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"채널 복제에 실패했어요: {exc.text or exc}"

    label = cloned.mention if hasattr(cloned, "mention") else f"`{cloned.name}`"
    return f"{label} 채널을 복제했어요."


async def _channel_follow(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_webhooks"):
        return "이 작업은 관리자 또는 Manage Webhooks 권한이 필요해요."
    if not _bot_has(context, "manage_webhooks"):
        return "봇에게 Manage Webhooks 권한이 없어서 공지 채널을 팔로우할 수 없어요."

    source = _resolve_text_channel(context, args.get("source_channel") or args.get("channel"))
    destination = _resolve_text_channel(context, args.get("destination_channel"))
    if source is None:
        return "팔로우할 원본 공지 채널을 찾지 못했어요."
    if destination is None:
        return "공지 메시지를 받을 대상 텍스트 채널을 찾지 못했어요."

    try:
        await source.follow(
            destination=destination,
            reason=_audit_reason(context, "AI agent channel follow"),
        )
    except discord.Forbidden:
        return "Discord가 공지 채널 팔로우를 거부했어요. 웹훅 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"공지 채널 팔로우에 실패했어요: {exc.text or exc}"

    return f"{source.mention} 공지 채널을 {destination.mention} 채널로 팔로우했어요."


async def _channel_pins_list(context: ActionContext, args: dict[str, Any]) -> str:
    if not (_user_has(context, "read_message_history") or _user_has(context, "manage_messages")):
        return "이 작업은 관리자 또는 Read Message History 권한이 필요해요."
    if not (_bot_has(context, "read_message_history") or _bot_has(context, "manage_messages")):
        return "봇에게 Read Message History 권한이 없어서 고정 메시지를 볼 수 없어요."

    channel = _resolve_guild_channel(context, args.get("channel"))
    if channel is None or not hasattr(channel, "pins"):
        return "고정 메시지를 볼 채널을 찾지 못했어요."

    limit = min(max(_optional_int(args.get("limit")) or 10, 1), 20)
    pins: list[discord.Message] = []
    try:
        async for pinned in channel.pins(limit=limit):
            pins.append(pinned)
    except discord.Forbidden:
        return "Discord가 고정 메시지 조회를 거부했어요. 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"고정 메시지 조회에 실패했어요: {exc.text or exc}"

    if not pins:
        return "이 채널에는 고정 메시지가 없어요."

    lines = [f"{getattr(channel, 'mention', f'`{channel.name}`')} 채널의 고정 메시지:"]
    for pinned in pins:
        content = pinned.clean_content.replace("\n", " ").strip() or "(내용 없음)"
        lines.append(f"- {pinned.author}: {content[:100]} | {pinned.jump_url}")
    return "\n".join(lines)



async def _channel_permission_set(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_channels"):
        return "이 작업은 관리자 또는 Manage Channels 권한이 필요해요."
    if not _bot_has(context, "manage_channels"):
        return "봇에게 Manage Channels 권한이 없어서 채널 권한을 바꿀 수 없어요."

    channel = _resolve_guild_channel(context, args.get("channel"))
    if not isinstance(channel, discord.abc.GuildChannel):
        return "권한을 바꿀 채널을 찾지 못했어요."

    target = await _resolve_permission_target(context, args.get("target"))
    if target is None:
        return "권한을 적용할 역할이나 멤버를 찾지 못했어요."

    if _optional_bool(args.get("clear")):
        try:
            await channel.set_permissions(
                target,
                overwrite=None,
                reason=_audit_reason(context, "AI agent channel permission clear"),
            )
        except discord.Forbidden:
            return "Discord가 채널 권한 삭제를 거부했어요. 권한을 확인해 주세요."
        return f"{channel.mention} 채널에서 {target.mention} 권한 덮어쓰기를 제거했어요."

    overwrite = _build_permission_overwrite(args)
    if overwrite is None:
        return "적용할 권한을 찾지 못했어요. 예: `send_messages 허용`, `view_channel 거부`처럼 요청해 주세요."

    try:
        await channel.set_permissions(
            target,
            overwrite=overwrite,
            reason=_audit_reason(context, "AI agent channel permission set"),
        )
    except discord.Forbidden:
        return "Discord가 채널 권한 변경을 거부했어요. 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"채널 권한 변경에 실패했어요: {exc.text or exc}"

    return f"{channel.mention} 채널에서 {target.mention} 권한 덮어쓰기를 변경했어요."


async def _role_create(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_roles"):
        return "이 작업은 관리자 또는 Manage Roles 권한이 필요해요."
    if not _bot_has(context, "manage_roles"):
        return "봇에게 Manage Roles 권한이 없어서 역할을 만들 수 없어요."

    name = str(args.get("name") or "").strip()
    if not name:
        return "만들 역할 이름을 알려주세요."

    color = _parse_color(str(args.get("color") or ""))
    if color is None:
        return "역할 색상은 `#5865F2` 같은 6자리 hex 형식이어야 해요."

    mentionable = bool(args.get("mentionable", False))
    hoist = bool(args.get("hoist", False))
    create_kwargs: dict[str, Any] = {
        "name": name,
        "colour": color,
        "mentionable": mentionable,
        "hoist": hoist,
        "reason": _audit_reason(context, "AI agent role create"),
    }
    secondary_color = _parse_optional_color(args.get("secondary_color"))
    tertiary_color = _parse_optional_color(args.get("tertiary_color"))
    if secondary_color is not None:
        create_kwargs["secondary_colour"] = secondary_color
    if tertiary_color is not None:
        create_kwargs["tertiary_colour"] = tertiary_color

    unicode_emoji = str(args.get("unicode_emoji") or "").strip()
    if unicode_emoji:
        create_kwargs["display_icon"] = unicode_emoji
    elif args.get("icon_url") is not None:
        asset = await _get_binary_asset(context, args.get("icon_url"))
        if asset is None:
            return "역할 아이콘으로 사용할 이미지 첨부파일이나 이미지 URL이 필요해요."
        create_kwargs["display_icon"] = asset.data

    try:
        role = await context.guild.create_role(**create_kwargs)
    except discord.Forbidden:
        return "Discord가 역할 생성을 거부했어요. 봇의 Manage Roles 권한이나 역할 위치를 확인해 주세요."
    except discord.HTTPException as exc:
        return f"역할 생성에 실패했어요: {exc.text or exc}"

    return f"{role.mention} 역할을 만들었어요."


async def _role_update(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_roles"):
        return "이 작업은 관리자 또는 Manage Roles 권한이 필요해요."
    if not _bot_has(context, "manage_roles"):
        return "봇에게 Manage Roles 권한이 없어서 역할을 수정할 수 없어요."

    role = _resolve_role(context, args.get("role"))
    if role is None:
        return "수정할 역할을 찾지 못했어요. 역할을 멘션해서 다시 요청해 주세요."
    if not _can_manage_role(context, role):
        return "봇과 실행 사용자의 가장 높은 역할이 대상 역할보다 높아야 이 역할을 수정할 수 있어요."

    edit_kwargs: dict[str, Any] = {}
    name = str(args.get("name") or "").strip()
    color = str(args.get("color") or "").strip()
    secondary_color = _parse_optional_color(args.get("secondary_color"))
    tertiary_color = _parse_optional_color(args.get("tertiary_color"))
    mentionable = args.get("mentionable")
    hoist = args.get("hoist")
    position = _optional_int(args.get("position"))

    if name:
        edit_kwargs["name"] = name
    if color:
        parsed_color = _parse_color(color)
        if parsed_color is None:
            return "역할 색상은 `#5865F2` 같은 6자리 hex 형식이어야 해요."
        edit_kwargs["colour"] = parsed_color
    if args.get("secondary_color") is not None:
        if secondary_color is None:
            return "역할 보조 색상은 `#5865F2` 같은 6자리 hex 형식이어야 해요."
        edit_kwargs["secondary_colour"] = secondary_color
    if args.get("tertiary_color") is not None:
        if tertiary_color is None:
            return "역할 세 번째 색상은 `#5865F2` 같은 6자리 hex 형식이어야 해요."
        edit_kwargs["tertiary_colour"] = tertiary_color
    if isinstance(mentionable, bool):
        edit_kwargs["mentionable"] = mentionable
    if isinstance(hoist, bool):
        edit_kwargs["hoist"] = hoist
    if position is not None:
        edit_kwargs["position"] = position

    unicode_emoji = str(args.get("unicode_emoji") or "").strip()
    if unicode_emoji:
        edit_kwargs["display_icon"] = unicode_emoji
    elif args.get("icon_url") is not None:
        asset = await _get_binary_asset(context, args.get("icon_url"))
        if asset is None:
            return "역할 아이콘으로 사용할 이미지 첨부파일이나 이미지 URL이 필요해요."
        edit_kwargs["display_icon"] = asset.data

    if not edit_kwargs:
        return "변경할 역할 설정을 찾지 못했어요. 이름, 색상, 아이콘, 위치, 멘션 가능 여부, 표시 여부 중 하나를 알려주세요."

    try:
        updated = await role.edit(
            **edit_kwargs,
            reason=_audit_reason(context, "AI agent role update"),
        )
    except discord.Forbidden:
        return "Discord가 역할 수정을 거부했어요. 봇 권한이나 역할 위치를 확인해 주세요."
    except discord.HTTPException as exc:
        return f"역할 수정에 실패했어요: {exc.text or exc}"

    updated_role = updated or role
    changed = ", ".join(f"`{key}`" for key in edit_kwargs)
    return f"{updated_role.mention} 역할을 수정했어요: {changed}"


async def _role_permissions_update(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_roles"):
        return "이 작업은 관리자 또는 Manage Roles 권한이 필요해요."
    if not _bot_has(context, "manage_roles"):
        return "봇에게 Manage Roles 권한이 없어서 역할 권한을 수정할 수 없어요."

    role = _resolve_role(context, args.get("role"))
    if role is None:
        return "권한을 수정할 역할을 찾지 못했어요."
    if not _can_manage_role(context, role):
        return "봇과 실행 사용자의 가장 높은 역할이 대상 역할보다 높아야 이 역할 권한을 수정할 수 있어요."

    permissions = discord.Permissions(role.permissions.value)
    changed = _apply_permission_names(permissions, args.get("allow"), True)
    changed += _apply_permission_names(permissions, args.get("deny"), False)
    changed += _apply_permission_dict(permissions, args.get("permissions"))
    if not changed:
        return "변경할 역할 권한을 찾지 못했어요. 예: `manage_messages 허용`, `send_messages 거부`처럼 요청해 주세요."

    try:
        updated = await role.edit(
            permissions=permissions,
            reason=_audit_reason(context, "AI agent role permissions update"),
        )
    except discord.Forbidden:
        return "Discord가 역할 권한 수정을 거부했어요. 봇 권한이나 역할 위치를 확인해 주세요."
    except discord.HTTPException as exc:
        return f"역할 권한 수정에 실패했어요: {exc.text or exc}"

    updated_role = updated or role
    return f"{updated_role.mention} 역할 권한을 변경했어요: {', '.join(f'`{name}`' for name in changed)}"


async def _role_delete(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_roles"):
        return "이 작업은 관리자 또는 Manage Roles 권한이 필요해요."
    if not _bot_has(context, "manage_roles"):
        return "봇에게 Manage Roles 권한이 없어서 역할을 삭제할 수 없어요."

    role = _resolve_role(context, args.get("role"))
    if role is None:
        return "삭제할 역할을 찾지 못했어요. 역할을 멘션해서 다시 요청해 주세요."
    if not _can_manage_role(context, role):
        return "봇과 실행 사용자의 가장 높은 역할이 대상 역할보다 높아야 이 역할을 삭제할 수 있어요."

    name = role.name
    try:
        await role.delete(reason=_audit_reason(context, "AI agent role delete"))
    except discord.Forbidden:
        return "Discord가 역할 삭제를 거부했어요. 봇 권한이나 역할 위치를 확인해 주세요."
    except discord.HTTPException as exc:
        return f"역할 삭제에 실패했어요: {exc.text or exc}"

    return f"`{name}` 역할을 삭제했어요."


async def _role_update_member(context: ActionContext, args: dict[str, Any], *, add: bool) -> str:
    if not _user_has(context, "manage_roles"):
        return "이 작업은 관리자 또는 Manage Roles 권한이 필요해요."
    if not _bot_has(context, "manage_roles"):
        return "봇에게 Manage Roles 권한이 없어서 역할을 관리할 수 없어요."

    member = await _resolve_member(context, args.get("member"))
    role = _resolve_role(context, args.get("role"))
    if member is None:
        return "대상 멤버를 찾지 못했어요. 멤버를 멘션해서 다시 요청해 주세요."
    if role is None:
        return "대상 역할을 찾지 못했어요. 역할을 멘션해서 다시 요청해 주세요."
    if not _can_manage_role(context, role):
        return "봇과 실행 사용자의 가장 높은 역할이 대상 역할보다 높아야 이 역할을 관리할 수 있어요."

    try:
        if add:
            await member.add_roles(role, reason=_audit_reason(context, "AI agent role add"))
            return f"{member.mention}에게 {role.mention} 역할을 추가했어요."
        await member.remove_roles(role, reason=_audit_reason(context, "AI agent role remove"))
        return f"{member.mention}에게서 {role.mention} 역할을 제거했어요."
    except discord.Forbidden:
        return "Discord가 역할 변경을 거부했어요. 봇 권한이나 역할 위치를 확인해 주세요."


async def _emoji_create(context: ActionContext, args: dict[str, Any]) -> str:
    if not _can_manage_expressions(context, create=True):
        return "이 작업은 관리자 또는 Create Expressions/Manage Expressions 권한이 필요하고, 봇에게도 표현 생성/관리 권한이 필요해요."

    name = _sanitize_expression_name(str(args.get("name") or ""))
    if not name:
        return "만들 이모지 이름을 알려주세요. 영문, 숫자, 밑줄을 사용할 수 있어요."

    asset = await _get_binary_asset(context, args.get("url"))
    if asset is None:
        return "이모지로 만들 이미지 첨부파일이나 이미지 URL이 필요해요."

    try:
        emoji = await context.guild.create_custom_emoji(
            name=name,
            image=asset.data,
            reason=_audit_reason(context, "AI agent emoji create"),
        )
    except discord.Forbidden:
        return "Discord가 이모지 생성을 거부했어요. 표현 생성/관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"이모지 생성에 실패했어요: {exc.text or exc}"

    return f"{emoji} 이모지 `:{emoji.name}:`를 만들었어요."


async def _emoji_update(context: ActionContext, args: dict[str, Any]) -> str:
    if not _can_manage_expressions(context):
        return "이 작업은 관리자 또는 Manage Expressions 권한이 필요하고, 봇에게도 Manage Expressions 권한이 필요해요."

    emoji = _resolve_emoji(context, args.get("emoji"))
    name = _sanitize_expression_name(str(args.get("name") or ""))
    if emoji is None:
        return "수정할 이모지를 찾지 못했어요. 이모지 이름이나 ID를 알려주세요."
    if not name:
        return "새 이모지 이름을 알려주세요."

    try:
        updated = await emoji.edit(
            name=name,
            reason=_audit_reason(context, "AI agent emoji update"),
        )
    except discord.Forbidden:
        return "Discord가 이모지 수정을 거부했어요. 표현 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"이모지 수정에 실패했어요: {exc.text or exc}"

    return f"{updated} 이모지 이름을 `:{updated.name}:`로 변경했어요."


async def _emoji_delete(context: ActionContext, args: dict[str, Any]) -> str:
    if not _can_manage_expressions(context):
        return "이 작업은 관리자 또는 Manage Expressions 권한이 필요하고, 봇에게도 Manage Expressions 권한이 필요해요."

    emoji = _resolve_emoji(context, args.get("emoji"))
    if emoji is None:
        return "삭제할 이모지를 찾지 못했어요. 이모지 이름이나 ID를 알려주세요."

    name = emoji.name
    try:
        await emoji.delete(reason=_audit_reason(context, "AI agent emoji delete"))
    except discord.Forbidden:
        return "Discord가 이모지 삭제를 거부했어요. 표현 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"이모지 삭제에 실패했어요: {exc.text or exc}"

    return f"`:{name}:` 이모지를 삭제했어요."


async def _sticker_create(context: ActionContext, args: dict[str, Any]) -> str:
    if not _can_manage_expressions(context, create=True):
        return "이 작업은 관리자 또는 Create Expressions/Manage Expressions 권한이 필요하고, 봇에게도 표현 생성/관리 권한이 필요해요."

    name = str(args.get("name") or "").strip()
    if not name:
        return "만들 스티커 이름을 알려주세요."

    asset = await _get_binary_asset(context, args.get("url"))
    if asset is None:
        return "스티커로 만들 이미지 첨부파일이나 이미지 URL이 필요해요."

    emoji = str(args.get("emoji") or "🙂").strip() or "🙂"
    description = str(args.get("description") or name).strip()

    try:
        sticker = await context.guild.create_sticker(
            name=name,
            description=description,
            emoji=emoji,
            file=discord.File(io.BytesIO(asset.data), filename=asset.filename),
            reason=_audit_reason(context, "AI agent sticker create"),
        )
    except discord.Forbidden:
        return "Discord가 스티커 생성을 거부했어요. 표현 생성/관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"스티커 생성에 실패했어요: {exc.text or exc}"

    return f"`{sticker.name}` 스티커를 만들었어요."


async def _sticker_update(context: ActionContext, args: dict[str, Any]) -> str:
    if not _can_manage_expressions(context):
        return "이 작업은 관리자 또는 Manage Expressions 권한이 필요하고, 봇에게도 Manage Expressions 권한이 필요해요."

    sticker = _resolve_sticker(context, args.get("sticker"))
    if sticker is None:
        return "수정할 스티커를 찾지 못했어요. 스티커 이름이나 ID를 알려주세요."

    edit_kwargs: dict[str, Any] = {}
    name = str(args.get("name") or "").strip()
    description = str(args.get("description") or "").strip()
    emoji = str(args.get("emoji") or "").strip()
    if name:
        edit_kwargs["name"] = name
    if description:
        edit_kwargs["description"] = description
    if emoji:
        edit_kwargs["emoji"] = emoji
    if not edit_kwargs:
        return "변경할 스티커 설정을 알려주세요. 이름, 설명, 대표 이모지를 바꿀 수 있어요."

    try:
        updated = await sticker.edit(
            **edit_kwargs,
            reason=_audit_reason(context, "AI agent sticker update"),
        )
    except discord.Forbidden:
        return "Discord가 스티커 수정을 거부했어요. 표현 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"스티커 수정에 실패했어요: {exc.text or exc}"

    return f"`{updated.name}` 스티커를 수정했어요."


async def _sticker_delete(context: ActionContext, args: dict[str, Any]) -> str:
    if not _can_manage_expressions(context):
        return "이 작업은 관리자 또는 Manage Expressions 권한이 필요하고, 봇에게도 Manage Expressions 권한이 필요해요."

    sticker = _resolve_sticker(context, args.get("sticker"))
    if sticker is None:
        return "삭제할 스티커를 찾지 못했어요. 스티커 이름이나 ID를 알려주세요."

    name = sticker.name
    try:
        await sticker.delete(reason=_audit_reason(context, "AI agent sticker delete"))
    except discord.Forbidden:
        return "Discord가 스티커 삭제를 거부했어요. 표현 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"스티커 삭제에 실패했어요: {exc.text or exc}"

    return f"`{name}` 스티커를 삭제했어요."


async def _sound_create(context: ActionContext, args: dict[str, Any]) -> str:
    if not _can_manage_expressions(context, create=True):
        return "이 작업은 관리자 또는 Create Expressions/Manage Expressions 권한이 필요하고, 봇에게도 표현 생성/관리 권한이 필요해요."

    name = str(args.get("name") or "").strip()
    if not name:
        return "만들 사운드 이름을 알려주세요."

    asset = await _get_binary_asset(context, args.get("url"))
    if asset is None:
        return "사운드보드에 추가할 오디오 첨부파일이나 URL이 필요해요."

    volume = _optional_float(args.get("volume"))
    if volume is None:
        volume = 1.0
    volume = max(0.0, min(volume, 1.0))

    try:
        sound = await context.guild.create_soundboard_sound(
            name=name,
            sound=asset.data,
            volume=volume,
            emoji=str(args.get("emoji") or "") or None,
            reason=_audit_reason(context, "AI agent sound create"),
        )
    except discord.Forbidden:
        return "Discord가 사운드 생성을 거부했어요. 표현 생성/관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"사운드 생성에 실패했어요: {exc.text or exc}"

    return f"`{sound.name}` 사운드를 추가했어요."


async def _sound_update(context: ActionContext, args: dict[str, Any]) -> str:
    if not _can_manage_expressions(context):
        return "이 작업은 관리자 또는 Manage Expressions 권한이 필요하고, 봇에게도 Manage Expressions 권한이 필요해요."

    sound = _resolve_sound(context, args.get("sound"))
    if sound is None:
        return "수정할 사운드를 찾지 못했어요. 사운드 이름이나 ID를 알려주세요."

    edit_kwargs: dict[str, Any] = {}
    name = str(args.get("name") or "").strip()
    volume = _optional_float(args.get("volume"))
    emoji = str(args.get("emoji") or "").strip()
    if name:
        edit_kwargs["name"] = name
    if volume is not None:
        edit_kwargs["volume"] = max(0.0, min(volume, 1.0))
    if emoji:
        edit_kwargs["emoji"] = emoji
    if not edit_kwargs:
        return "변경할 사운드 설정을 알려주세요. 이름, 볼륨, 이모지를 바꿀 수 있어요."

    try:
        updated = await sound.edit(
            **edit_kwargs,
            reason=_audit_reason(context, "AI agent sound update"),
        )
    except discord.Forbidden:
        return "Discord가 사운드 수정을 거부했어요. 표현 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"사운드 수정에 실패했어요: {exc.text or exc}"

    return f"`{updated.name}` 사운드를 수정했어요."


async def _sound_delete(context: ActionContext, args: dict[str, Any]) -> str:
    if not _can_manage_expressions(context):
        return "이 작업은 관리자 또는 Manage Expressions 권한이 필요하고, 봇에게도 Manage Expressions 권한이 필요해요."

    sound = _resolve_sound(context, args.get("sound"))
    if sound is None:
        return "삭제할 사운드를 찾지 못했어요. 사운드 이름이나 ID를 알려주세요."

    name = sound.name
    try:
        await sound.delete(reason=_audit_reason(context, "AI agent sound delete"))
    except discord.Forbidden:
        return "Discord가 사운드 삭제를 거부했어요. 표현 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"사운드 삭제에 실패했어요: {exc.text or exc}"

    return f"`{name}` 사운드를 삭제했어요."


async def _webhook_create(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_webhooks"):
        return "이 작업은 관리자 또는 Manage Webhooks 권한이 필요해요."
    if not _bot_has(context, "manage_webhooks"):
        return "봇에게 Manage Webhooks 권한이 없어서 웹훅을 만들 수 없어요."

    channel = _resolve_text_channel(context, args.get("channel"))
    if channel is None:
        return "웹훅을 만들 텍스트 채널을 찾지 못했어요."

    name = str(args.get("name") or "AI Agent Webhook").strip()
    avatar_asset = await _get_binary_asset(context, args.get("avatar_url"), allow_attachment=False)

    try:
        webhook = await channel.create_webhook(
            name=name,
            avatar=avatar_asset.data if avatar_asset else None,
            reason=_audit_reason(context, "AI agent webhook create"),
        )
    except discord.Forbidden:
        return "Discord가 웹훅 생성을 거부했어요. 웹훅 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"웹훅 생성에 실패했어요: {exc.text or exc}"

    return f"{channel.mention} 채널에 `{webhook.name}` 웹훅을 만들었어요."


async def _webhook_list(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_webhooks"):
        return "이 작업은 관리자 또는 Manage Webhooks 권한이 필요해요."
    if not _bot_has(context, "manage_webhooks"):
        return "봇에게 Manage Webhooks 권한이 없어서 웹훅을 볼 수 없어요."

    channel = _resolve_text_channel(context, args.get("channel"))
    try:
        webhooks = await channel.webhooks() if channel else await context.guild.webhooks()
    except discord.Forbidden:
        return "Discord가 웹훅 조회를 거부했어요."

    if not webhooks:
        return "조회 가능한 웹훅이 없어요."

    lines = ["웹훅 목록:"]
    for webhook in webhooks[:20]:
        location = f"#{webhook.channel.name}" if webhook.channel else "알 수 없는 채널"
        lines.append(f"- `{webhook.name}` (`{webhook.id}`) | {location}")
    return "\n".join(lines)


async def _webhook_delete(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_webhooks"):
        return "이 작업은 관리자 또는 Manage Webhooks 권한이 필요해요."
    if not _bot_has(context, "manage_webhooks"):
        return "봇에게 Manage Webhooks 권한이 없어서 웹훅을 삭제할 수 없어요."

    webhook = await _resolve_webhook(context, args.get("webhook"))
    if webhook is None:
        return "삭제할 웹훅을 찾지 못했어요. 웹훅 ID나 이름을 알려주세요."

    name = webhook.name
    try:
        await webhook.delete(reason=_audit_reason(context, "AI agent webhook delete"))
    except discord.Forbidden:
        return "Discord가 웹훅 삭제를 거부했어요. 웹훅 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"웹훅 삭제에 실패했어요: {exc.text or exc}"

    return f"`{name}` 웹훅을 삭제했어요."


async def _invite_create(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "create_instant_invite"):
        return "이 작업은 관리자 또는 Create Invite 권한이 필요해요."

    channel = _resolve_guild_channel(context, args.get("channel"))
    if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel)):
        return "초대 링크를 만들 채널을 찾지 못했어요."

    max_age = _optional_int(args.get("max_age")) or 0
    max_uses = _optional_int(args.get("max_uses")) or 0
    temporary = _optional_bool(args.get("temporary")) or False

    try:
        invite = await channel.create_invite(
            max_age=max_age,
            max_uses=max_uses,
            temporary=temporary,
            reason=_audit_reason(context, "AI agent invite create"),
        )
    except discord.Forbidden:
        return "Discord가 초대 링크 생성을 거부했어요. 초대 만들기 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"초대 링크 생성에 실패했어요: {exc.text or exc}"

    return f"초대 링크를 만들었어요: {invite.url}"


async def _invite_list(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Server 권한이 필요해요."
    if not _bot_has(context, "manage_guild"):
        return "봇에게 Manage Server 권한이 없어서 초대 목록을 볼 수 없어요."

    try:
        invites = await context.guild.invites()
    except discord.Forbidden:
        return "Discord가 초대 목록 조회를 거부했어요."

    channel = _resolve_guild_channel(context, args.get("channel"))
    if channel is not None:
        invites = [invite for invite in invites if invite.channel and invite.channel.id == channel.id]

    if not invites:
        return "조회 가능한 초대 링크가 없어요."

    lines = ["초대 링크 목록:"]
    for invite in invites[:20]:
        channel_name = f"#{invite.channel.name}" if invite.channel else "알 수 없는 채널"
        lines.append(f"- `{invite.code}` | {channel_name} | uses: {invite.uses or 0}")
    return "\n".join(lines)


async def _invite_delete(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Server 권한이 필요해요."
    if not _bot_has(context, "manage_guild"):
        return "봇에게 Manage Server 권한이 없어서 초대를 삭제할 수 없어요."

    invite = await _resolve_invite(context, args.get("invite"))
    if invite is None:
        return "삭제할 초대 링크를 찾지 못했어요. 초대 코드나 URL을 알려주세요."

    code = invite.code
    try:
        await invite.delete(reason=_audit_reason(context, "AI agent invite delete"))
    except discord.Forbidden:
        return "Discord가 초대 삭제를 거부했어요."
    return f"`{code}` 초대 링크를 삭제했어요."


async def _audit_log_show(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "view_audit_log"):
        return "이 작업은 관리자 또는 View Audit Log 권한이 필요해요."
    if not _bot_has(context, "view_audit_log"):
        return "봇에게 View Audit Log 권한이 없어서 감사 로그를 볼 수 없어요."

    limit = min(max(_optional_int(args.get("limit")) or 5, 1), 10)
    lines = [f"최근 감사 로그 {limit}개:"]
    try:
        async for entry in context.guild.audit_logs(limit=limit):
            user = entry.user or "알 수 없음"
            target = entry.target or "알 수 없음"
            lines.append(f"- `{entry.action.name}` | 사용자: {user} | 대상: {target}")
    except discord.Forbidden:
        return "Discord가 감사 로그 조회를 거부했어요."

    return "\n".join(lines)


async def _guild_update(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Server 권한이 필요해요."
    if not _bot_has(context, "manage_guild"):
        return "봇에게 Manage Server 권한이 없어서 서버 설정을 바꿀 수 없어요."

    edit_kwargs: dict[str, Any] = {}
    name = str(args.get("name") or "").strip()
    description = str(args.get("description") or "").strip()
    if name:
        edit_kwargs["name"] = name
    if description:
        edit_kwargs["description"] = description
    for arg_name, edit_name in (
        ("icon_url", "icon"),
        ("banner_url", "banner"),
        ("splash_url", "splash"),
        ("discovery_splash_url", "discovery_splash"),
    ):
        asset = await _get_binary_asset(context, args.get(arg_name), allow_attachment=False)
        if asset is not None:
            edit_kwargs[edit_name] = asset.data

    for arg_name, edit_name in (
        ("system_channel", "system_channel"),
        ("rules_channel", "rules_channel"),
        ("public_updates_channel", "public_updates_channel"),
        ("safety_alerts_channel", "safety_alerts_channel"),
    ):
        channel = _resolve_text_channel(context, args.get(arg_name))
        if channel is not None:
            edit_kwargs[edit_name] = channel

    if str(args.get("preferred_locale") or "").strip():
        locale = _parse_locale(args.get("preferred_locale"))
        if locale is not None:
            edit_kwargs["preferred_locale"] = locale
    for bool_arg in ("premium_progress_bar_enabled", "invites_disabled", "raid_alerts_disabled"):
        value = _optional_bool(args.get(bool_arg))
        if value is not None:
            edit_kwargs[bool_arg] = value
    if not edit_kwargs:
        return "변경할 서버 설정을 알려주세요. 이름, 설명, 아이콘, 배너, 시스템 채널, 규칙 채널 등을 바꿀 수 있어요."

    try:
        await context.guild.edit(
            **edit_kwargs,
            reason=_audit_reason(context, "AI agent guild update"),
        )
    except discord.Forbidden:
        return "Discord가 서버 설정 변경을 거부했어요. 서버 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"서버 설정 변경에 실패했어요: {exc.text or exc}"

    changed = ", ".join(f"`{key}`" for key in edit_kwargs)
    return f"서버 설정을 변경했어요: {changed}"


async def _template_create(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Server 권한이 필요해요."
    if not _bot_has(context, "manage_guild"):
        return "봇에게 Manage Server 권한이 없어서 템플릿을 만들 수 없어요."

    name = str(args.get("name") or "").strip()
    if not name:
        return "만들 서버 템플릿 이름을 알려주세요."
    description = str(args.get("description") or "").strip()
    try:
        template = await context.guild.create_template(name=name, description=description)
    except discord.Forbidden:
        return "Discord가 서버 템플릿 생성을 거부했어요."
    except discord.HTTPException as exc:
        return f"서버 템플릿 생성에 실패했어요: {exc.text or exc}"

    return f"`{template.name}` 서버 템플릿을 만들었어요: {template.url}"


async def _template_list(context: ActionContext) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Server 권한이 필요해요."
    if not _bot_has(context, "manage_guild"):
        return "봇에게 Manage Server 권한이 없어서 템플릿 목록을 볼 수 없어요."

    try:
        templates = await context.guild.templates()
    except discord.Forbidden:
        return "Discord가 서버 템플릿 조회를 거부했어요."

    if not templates:
        return "서버 템플릿이 없어요."

    lines = ["서버 템플릿 목록:"]
    for template in templates[:20]:
        lines.append(f"- `{template.name}` (`{template.code}`) | uses: {template.uses}")
    return "\n".join(lines)


async def _template_sync(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Server 권한이 필요해요."
    template = await _resolve_template(context, args.get("template"))
    if template is None:
        return "동기화할 서버 템플릿을 찾지 못했어요."
    try:
        synced = await template.sync()
    except discord.Forbidden:
        return "Discord가 서버 템플릿 동기화를 거부했어요."
    return f"`{synced.name}` 서버 템플릿을 현재 서버 상태로 동기화했어요."


async def _template_delete(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Server 권한이 필요해요."
    template = await _resolve_template(context, args.get("template"))
    if template is None:
        return "삭제할 서버 템플릿을 찾지 못했어요."
    name = template.name
    try:
        await template.delete()
    except discord.Forbidden:
        return "Discord가 서버 템플릿 삭제를 거부했어요."
    return f"`{name}` 서버 템플릿을 삭제했어요."


async def _automod_rule_create(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Server 권한이 필요해요."
    if not _bot_has(context, "manage_guild"):
        return "봇에게 Manage Server 권한이 없어서 AutoMod 규칙을 만들 수 없어요."

    name = str(args.get("name") or "").strip()
    keywords = _normalize_keywords(args.get("keywords"))
    regex_patterns = _normalize_keywords(args.get("regex_patterns"))
    if not name:
        return "만들 AutoMod 규칙 이름을 알려주세요."
    if not keywords and not regex_patterns:
        return "AutoMod 키워드나 정규식 패턴이 하나 이상 필요해요."

    try:
        rule = await context.guild.create_automod_rule(
            name=name,
            event_type=discord.AutoModRuleEventType.message_send,
            trigger=discord.AutoModTrigger(
                type=discord.AutoModRuleTriggerType.keyword,
                keyword_filter=keywords,
                regex_patterns=regex_patterns,
                allow_list=_normalize_keywords(args.get("allow_list")),
            ),
            actions=[
                discord.AutoModRuleAction(
                    type=discord.AutoModRuleActionType.block_message,
                    custom_message=str(args.get("custom_message") or "") or None,
                )
            ],
            enabled=_optional_bool(args.get("enabled")) if _optional_bool(args.get("enabled")) is not None else True,
            exempt_roles=_resolve_roles(context, args.get("exempt_roles")),
            exempt_channels=_resolve_channels(context, args.get("exempt_channels")),
            reason=_audit_reason(context, "AI agent automod create"),
        )
    except discord.Forbidden:
        return "Discord가 AutoMod 규칙 생성을 거부했어요."
    except discord.HTTPException as exc:
        return f"AutoMod 규칙 생성에 실패했어요: {exc.text or exc}"

    return f"`{rule.name}` AutoMod 규칙을 만들었어요."


async def _automod_rule_list(context: ActionContext) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Server 권한이 필요해요."
    try:
        rules = await context.guild.fetch_automod_rules()
    except discord.Forbidden:
        return "Discord가 AutoMod 규칙 조회를 거부했어요."

    if not rules:
        return "AutoMod 규칙이 없어요."

    lines = ["AutoMod 규칙 목록:"]
    for rule in rules[:20]:
        lines.append(f"- `{rule.name}` (`{rule.id}`) | enabled: {rule.enabled}")
    return "\n".join(lines)


async def _automod_rule_update(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Server 권한이 필요해요."

    rule = await _resolve_automod_rule(context, args.get("rule"))
    if rule is None:
        return "수정할 AutoMod 규칙을 찾지 못했어요."

    edit_kwargs: dict[str, Any] = {}
    name = str(args.get("name") or "").strip()
    if name:
        edit_kwargs["name"] = name
    if _optional_bool(args.get("enabled")) is not None:
        edit_kwargs["enabled"] = _optional_bool(args.get("enabled"))
    if any(args.get(key) is not None for key in ("keywords", "regex_patterns", "allow_list")):
        edit_kwargs["trigger"] = discord.AutoModTrigger(
            type=discord.AutoModRuleTriggerType.keyword,
            keyword_filter=_normalize_keywords(args.get("keywords")),
            regex_patterns=_normalize_keywords(args.get("regex_patterns")),
            allow_list=_normalize_keywords(args.get("allow_list")),
        )
    if args.get("custom_message") is not None:
        edit_kwargs["actions"] = [
            discord.AutoModRuleAction(
                type=discord.AutoModRuleActionType.block_message,
                custom_message=str(args.get("custom_message") or "") or None,
            )
        ]
    if args.get("exempt_roles") is not None:
        edit_kwargs["exempt_roles"] = _resolve_roles(context, args.get("exempt_roles"))
    if args.get("exempt_channels") is not None:
        edit_kwargs["exempt_channels"] = _resolve_channels(context, args.get("exempt_channels"))
    if not edit_kwargs:
        return "변경할 AutoMod 설정을 알려주세요."

    try:
        updated = await rule.edit(
            **edit_kwargs,
            reason=_audit_reason(context, "AI agent automod update"),
        )
    except discord.Forbidden:
        return "Discord가 AutoMod 규칙 수정을 거부했어요."
    except discord.HTTPException as exc:
        return f"AutoMod 규칙 수정에 실패했어요: {exc.text or exc}"

    return f"`{updated.name}` AutoMod 규칙을 수정했어요."


async def _automod_rule_delete(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Server 권한이 필요해요."

    rule = await _resolve_automod_rule(context, args.get("rule"))
    if rule is None:
        return "삭제할 AutoMod 규칙을 찾지 못했어요."
    name = rule.name
    try:
        await rule.delete(reason=_audit_reason(context, "AI agent automod delete"))
    except discord.Forbidden:
        return "Discord가 AutoMod 규칙 삭제를 거부했어요."
    return f"`{name}` AutoMod 규칙을 삭제했어요."


async def _member_prune(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "kick_members"):
        return "이 작업은 관리자 또는 Kick Members 권한이 필요해요."
    if not _bot_has(context, "kick_members"):
        return "봇에게 Kick Members 권한이 없어서 멤버 정리를 실행할 수 없어요."

    days = min(max(_optional_int(args.get("days")) or 7, 1), 30)
    roles = _resolve_roles(context, args.get("roles"))
    try:
        count = await context.guild.prune_members(
            days=days,
            roles=roles,
            reason=_audit_reason(context, "AI agent member prune"),
        )
    except discord.Forbidden:
        return "Discord가 멤버 정리를 거부했어요."
    return f"{days}일 이상 미활동 멤버 정리를 실행했어요. 예상/처리 인원: {count if count is not None else '알 수 없음'}"


async def _member_kick(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "kick_members"):
        return "이 작업은 관리자 또는 Kick Members 권한이 필요해요."
    if not _bot_has(context, "kick_members"):
        return "봇에게 Kick Members 권한이 없어서 멤버를 추방할 수 없어요."

    member = await _resolve_member(context, args.get("member"))
    if member is None:
        return "추방할 멤버를 찾지 못했어요. 멤버를 멘션해서 다시 요청해 주세요."
    if not _can_manage_member(context, member):
        return "봇과 실행 사용자의 가장 높은 역할이 대상 멤버보다 높아야 추방할 수 있어요."

    reason = str(args.get("reason") or "AI agent kick").strip()
    try:
        await member.kick(reason=_audit_reason(context, reason))
    except discord.Forbidden:
        return "Discord가 추방을 거부했어요. 권한이나 역할 위치를 확인해 주세요."

    return f"{member.mention} 멤버를 추방했어요."


async def _member_ban(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "ban_members"):
        return "이 작업은 관리자 또는 Ban Members 권한이 필요해요."
    if not _bot_has(context, "ban_members"):
        return "봇에게 Ban Members 권한이 없어서 멤버를 차단할 수 없어요."

    member = await _resolve_member(context, args.get("member"))
    if member is None:
        return "차단할 멤버를 찾지 못했어요. 멤버를 멘션해서 다시 요청해 주세요."
    if not _can_manage_member(context, member):
        return "봇과 실행 사용자의 가장 높은 역할이 대상 멤버보다 높아야 차단할 수 있어요."

    reason = str(args.get("reason") or "AI agent ban").strip()
    delete_days = min(max(_optional_int(args.get("delete_message_days")) or 0, 0), 7)
    try:
        await member.ban(
            reason=_audit_reason(context, reason),
            delete_message_days=delete_days,
        )
    except discord.Forbidden:
        return "Discord가 차단을 거부했어요. 권한이나 역할 위치를 확인해 주세요."

    return f"{member.mention} 멤버를 차단했어요."


async def _member_unban(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "ban_members"):
        return "이 작업은 관리자 또는 Ban Members 권한이 필요해요."
    if not _bot_has(context, "ban_members"):
        return "봇에게 Ban Members 권한이 없어서 차단을 해제할 수 없어요."

    user_id = _extract_id(args.get("user"))
    if user_id is None:
        return "차단 해제할 사용자 ID를 알려주세요."

    try:
        user = await context.bot.fetch_user(user_id)
        await context.guild.unban(
            user,
            reason=_audit_reason(context, str(args.get("reason") or "AI agent unban")),
        )
    except discord.NotFound:
        return "해당 사용자를 찾지 못했거나 이 서버에서 차단된 상태가 아니에요."
    except discord.Forbidden:
        return "Discord가 차단 해제를 거부했어요. 권한을 확인해 주세요."

    return f"{user} 사용자의 차단을 해제했어요."


async def _member_timeout(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "moderate_members"):
        return "이 작업은 관리자 또는 Moderate Members 권한이 필요해요."
    if not _bot_has(context, "moderate_members"):
        return "봇에게 Moderate Members 권한이 없어서 타임아웃을 적용할 수 없어요."

    member = await _resolve_member(context, args.get("member"))
    if member is None:
        return "타임아웃할 멤버를 찾지 못했어요. 멤버를 멘션해서 다시 요청해 주세요."
    if not _can_manage_member(context, member):
        return "봇과 실행 사용자의 가장 높은 역할이 대상 멤버보다 높아야 타임아웃할 수 있어요."

    minutes = _optional_int(args.get("duration_minutes"))
    until = None if not minutes or minutes <= 0 else dt.timedelta(minutes=min(minutes, 40320))
    reason = str(args.get("reason") or "AI agent timeout").strip()
    try:
        await member.timeout(until, reason=_audit_reason(context, reason))
    except discord.Forbidden:
        return "Discord가 타임아웃 변경을 거부했어요. 권한이나 역할 위치를 확인해 주세요."

    if until is None:
        return f"{member.mention} 멤버의 타임아웃을 해제했어요."
    return f"{member.mention} 멤버에게 {minutes}분 타임아웃을 적용했어요."


async def _member_nickname(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_nicknames"):
        return "이 작업은 관리자 또는 Manage Nicknames 권한이 필요해요."
    if not _bot_has(context, "manage_nicknames"):
        return "봇에게 Manage Nicknames 권한이 없어서 별명을 바꿀 수 없어요."

    member = await _resolve_member(context, args.get("member"))
    if member is None:
        return "별명을 바꿀 멤버를 찾지 못했어요. 멤버를 멘션해서 다시 요청해 주세요."
    if not _can_manage_member(context, member):
        return "봇과 실행 사용자의 가장 높은 역할이 대상 멤버보다 높아야 별명을 바꿀 수 있어요."

    nickname = str(args.get("nickname") or "").strip() or None
    try:
        await member.edit(
            nick=nickname,
            reason=_audit_reason(context, "AI agent nickname update"),
        )
    except discord.Forbidden:
        return "Discord가 별명 변경을 거부했어요. 권한이나 역할 위치를 확인해 주세요."

    return f"{member.mention} 멤버의 별명을 변경했어요."


async def _member_move_voice(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "move_members"):
        return "이 작업은 관리자 또는 Move Members 권한이 필요해요."
    if not _bot_has(context, "move_members"):
        return "봇에게 Move Members 권한이 없어서 멤버를 이동할 수 없어요."

    member = await _resolve_member(context, args.get("member"))
    channel = _resolve_voice_channel(context, args.get("channel"))
    if member is None:
        return "이동할 멤버를 찾지 못했어요."
    if channel is None:
        return "이동할 음성 채널을 찾지 못했어요."
    if member.voice is None:
        return "대상 멤버가 현재 음성 채널에 접속해 있지 않아요."

    try:
        await member.move_to(channel, reason=_audit_reason(context, "AI agent voice move"))
    except discord.Forbidden:
        return "Discord가 음성 이동을 거부했어요. 권한을 확인해 주세요."

    return f"{member.mention} 멤버를 `{channel.name}` 음성 채널로 이동했어요."


async def _member_voice_state(context: ActionContext, args: dict[str, Any], *, field: str) -> str:
    permission = "mute_members" if field == "mute" else "deafen_members"
    label = "음소거" if field == "mute" else "헤드셋 음소거"
    if not _user_has(context, permission):
        return f"이 작업은 관리자 또는 {permission} 권한이 필요해요."
    if not _bot_has(context, permission):
        return f"봇에게 {permission} 권한이 없어서 {label}를 변경할 수 없어요."

    member = await _resolve_member(context, args.get("member"))
    if member is None:
        return "대상 멤버를 찾지 못했어요."

    value = _optional_bool(args.get("muted" if field == "mute" else "deafened"))
    if value is None:
        value = True

    try:
        if field == "mute":
            await member.edit(mute=value, reason=_audit_reason(context, "AI agent voice mute"))
        else:
            await member.edit(deafen=value, reason=_audit_reason(context, "AI agent voice deafen"))
    except discord.Forbidden:
        return "Discord가 음성 상태 변경을 거부했어요. 권한을 확인해 주세요."

    return f"{member.mention} 멤버의 {label} 상태를 `{value}`로 변경했어요."


async def _member_disconnect_voice(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "move_members"):
        return "이 작업은 관리자 또는 Move Members 권한이 필요해요."
    if not _bot_has(context, "move_members"):
        return "봇에게 Move Members 권한이 없어서 멤버 연결을 끊을 수 없어요."

    member = await _resolve_member(context, args.get("member"))
    if member is None:
        return "연결을 끊을 멤버를 찾지 못했어요."
    if member.voice is None:
        return "대상 멤버가 현재 음성 채널에 접속해 있지 않아요."

    try:
        await member.move_to(None, reason=_audit_reason(context, "AI agent voice disconnect"))
    except discord.Forbidden:
        return "Discord가 음성 연결 끊기를 거부했어요. 권한을 확인해 주세요."

    return f"{member.mention} 멤버의 음성 연결을 끊었어요."


async def _message_purge(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_messages"):
        return "이 작업은 관리자 또는 Manage Messages 권한이 필요해요."
    if not _bot_has(context, "manage_messages"):
        return "봇에게 Manage Messages 권한이 없어서 메시지를 삭제할 수 없어요."

    channel = _resolve_text_channel(context, args.get("channel"))
    if channel is None:
        return "메시지를 정리할 텍스트 채널을 찾지 못했어요."

    limit = min(max(_optional_int(args.get("limit")) or 10, 1), 100)
    try:
        deleted = await channel.purge(
            limit=limit,
            reason=_audit_reason(context, "AI agent message purge"),
        )
    except discord.Forbidden:
        return "Discord가 메시지 삭제를 거부했어요. 권한을 확인해 주세요."

    return f"{channel.mention} 채널에서 메시지 {len(deleted)}개를 삭제했어요."


async def _message_pin(context: ActionContext, args: dict[str, Any], *, pin: bool) -> str:
    if not _user_has(context, "pin_messages"):
        return "이 작업은 관리자 또는 Pin Messages 권한이 필요해요."
    if not _bot_has(context, "pin_messages"):
        return "봇에게 Pin Messages 권한이 없어서 고정 상태를 바꿀 수 없어요."

    channel = _resolve_text_channel(context, args.get("channel"))
    message_id = _extract_id(args.get("message_id"))
    if channel is None or message_id is None:
        return "고정하거나 고정 해제할 메시지의 채널과 메시지 ID가 필요해요."

    try:
        target = await channel.fetch_message(message_id)
        if pin:
            await target.pin(reason=_audit_reason(context, "AI agent message pin"))
            return "메시지를 고정했어요."
        await target.unpin(reason=_audit_reason(context, "AI agent message unpin"))
        return "메시지 고정을 해제했어요."
    except discord.NotFound:
        return "해당 메시지를 찾지 못했어요."
    except discord.Forbidden:
        return "Discord가 메시지 고정 변경을 거부했어요. 권한을 확인해 주세요."


async def _thread_create(context: ActionContext, args: dict[str, Any]) -> str:
    if not (_user_has(context, "create_public_threads") or _user_has(context, "manage_threads")):
        return "이 작업은 관리자 또는 Create Public Threads/Manage Threads 권한이 필요해요."

    channel = _resolve_text_channel(context, args.get("channel"))
    if channel is None:
        return "스레드를 만들 텍스트 채널을 찾지 못했어요."
    name = str(args.get("name") or "").strip()
    if not name:
        return "만들 스레드 이름을 알려주세요."

    try:
        thread = await channel.create_thread(
            name=name,
            reason=_audit_reason(context, "AI agent thread create"),
        )
    except discord.Forbidden:
        return "Discord가 스레드 생성을 거부했어요. 권한을 확인해 주세요."

    return f"{thread.mention} 스레드를 만들었어요."


async def _thread_update(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_threads"):
        return "이 작업은 관리자 또는 Manage Threads 권한이 필요해요."

    thread = _resolve_thread(context, args.get("thread"))
    if thread is None:
        return "수정할 스레드를 찾지 못했어요."

    edit_kwargs: dict[str, Any] = {}
    name = str(args.get("name") or "").strip()
    if name:
        edit_kwargs["name"] = name
    if isinstance(args.get("archived"), bool):
        edit_kwargs["archived"] = args["archived"]
    if isinstance(args.get("locked"), bool):
        edit_kwargs["locked"] = args["locked"]
    if args.get("slowmode") is not None:
        edit_kwargs["slowmode_delay"] = _optional_int(args.get("slowmode")) or 0
    if not edit_kwargs:
        return "변경할 스레드 설정을 알려주세요. 이름, 보관, 잠금, 슬로우모드를 바꿀 수 있어요."

    try:
        await thread.edit(**edit_kwargs, reason=_audit_reason(context, "AI agent thread update"))
    except discord.Forbidden:
        return "Discord가 스레드 수정을 거부했어요. 권한을 확인해 주세요."

    return f"{thread.mention} 스레드를 수정했어요."


async def _thread_delete(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_threads"):
        return "이 작업은 관리자 또는 Manage Threads 권한이 필요해요."

    thread = _resolve_thread(context, args.get("thread"))
    if thread is None:
        return "삭제할 스레드를 찾지 못했어요."

    name = thread.name
    try:
        await thread.delete(reason=_audit_reason(context, "AI agent thread delete"))
    except discord.Forbidden:
        return "Discord가 스레드 삭제를 거부했어요. 권한을 확인해 주세요."

    return f"`{name}` 스레드를 삭제했어요."


async def _forum_tag_create(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_channels"):
        return "이 작업은 관리자 또는 Manage Channels 권한이 필요해요."
    if not _bot_has(context, "manage_channels"):
        return "봇에게 Manage Channels 권한이 없어서 포럼 태그를 만들 수 없어요."

    forum = _resolve_forum_channel(context, args.get("forum") or args.get("channel"))
    if forum is None:
        return "태그를 만들 포럼 채널을 찾지 못했어요."

    name = str(args.get("name") or "").strip()
    if not name:
        return "만들 포럼 태그 이름을 알려주세요."

    moderated = _optional_bool(args.get("moderated"))
    try:
        tag = await forum.create_tag(
            name=name,
            emoji=_parse_emoji_input(args.get("emoji")),
            moderated=moderated if moderated is not None else False,
            reason=_audit_reason(context, "AI agent forum tag create"),
        )
    except discord.Forbidden:
        return "Discord가 포럼 태그 생성을 거부했어요. 채널 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"포럼 태그 생성에 실패했어요: {exc.text or exc}"

    return f"{forum.mention} 포럼에 `{tag.name}` 태그를 만들었어요."


def _forum_tag_list(context: ActionContext, args: dict[str, Any]) -> str:
    forum = _resolve_forum_channel(context, args.get("forum") or args.get("channel"))
    if forum is None:
        return "태그를 볼 포럼 채널을 찾지 못했어요."

    tags = list(forum.available_tags)
    if not tags:
        return f"{forum.mention} 포럼에는 태그가 없어요."

    lines = [f"{forum.mention} 포럼 태그 목록:"]
    for tag in tags:
        emoji = f" {tag.emoji}" if tag.emoji else ""
        lines.append(f"- `{tag.name}` (`{tag.id}`){emoji} | moderated: {tag.moderated}")
    return "\n".join(lines)


async def _forum_tag_update(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_channels"):
        return "이 작업은 관리자 또는 Manage Channels 권한이 필요해요."
    if not _bot_has(context, "manage_channels"):
        return "봇에게 Manage Channels 권한이 없어서 포럼 태그를 수정할 수 없어요."

    forum = _resolve_forum_channel(context, args.get("forum") or args.get("channel"))
    if forum is None:
        return "태그를 수정할 포럼 채널을 찾지 못했어요."

    tag = _resolve_forum_tag(forum, args.get("tag"))
    if tag is None:
        return "수정할 포럼 태그를 찾지 못했어요."

    new_name = str(args.get("name") or "").strip()
    new_emoji = _parse_emoji_input(args.get("emoji")) if args.get("emoji") is not None else tag.emoji
    new_moderated = _optional_bool(args.get("moderated"))
    if not new_name and args.get("emoji") is None and new_moderated is None:
        return "변경할 포럼 태그 설정을 알려주세요. 이름, 이모지, moderated 값을 바꿀 수 있어요."

    replacement = discord.ForumTag(
        name=new_name or tag.name,
        emoji=new_emoji,
        moderated=new_moderated if new_moderated is not None else tag.moderated,
    )
    replacement.id = tag.id
    updated_tags = [
        replacement if existing.id == tag.id else existing
        for existing in forum.available_tags
    ]

    try:
        await forum.edit(
            available_tags=updated_tags,
            reason=_audit_reason(context, "AI agent forum tag update"),
        )
    except discord.Forbidden:
        return "Discord가 포럼 태그 수정을 거부했어요. 채널 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"포럼 태그 수정에 실패했어요: {exc.text or exc}"

    return f"{forum.mention} 포럼의 `{tag.name}` 태그를 수정했어요."


async def _forum_tag_delete(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_channels"):
        return "이 작업은 관리자 또는 Manage Channels 권한이 필요해요."
    if not _bot_has(context, "manage_channels"):
        return "봇에게 Manage Channels 권한이 없어서 포럼 태그를 삭제할 수 없어요."

    forum = _resolve_forum_channel(context, args.get("forum") or args.get("channel"))
    if forum is None:
        return "태그를 삭제할 포럼 채널을 찾지 못했어요."

    tag = _resolve_forum_tag(forum, args.get("tag"))
    if tag is None:
        return "삭제할 포럼 태그를 찾지 못했어요."

    updated_tags = [existing for existing in forum.available_tags if existing.id != tag.id]
    try:
        await forum.edit(
            available_tags=updated_tags,
            reason=_audit_reason(context, "AI agent forum tag delete"),
        )
    except discord.Forbidden:
        return "Discord가 포럼 태그 삭제를 거부했어요. 채널 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"포럼 태그 삭제에 실패했어요: {exc.text or exc}"

    return f"{forum.mention} 포럼에서 `{tag.name}` 태그를 삭제했어요."


async def _event_create(context: ActionContext, args: dict[str, Any]) -> str:
    if not (_user_has(context, "create_events") or _user_has(context, "manage_events")):
        return "이 작업은 관리자 또는 Create Events/Manage Events 권한이 필요해요."

    name = str(args.get("name") or "").strip()
    if not name:
        return "만들 이벤트 이름을 알려주세요."
    start_time = _parse_datetime(args.get("start_time"))
    if start_time is None:
        return "이벤트 시작 시간을 ISO 형식으로 알려주세요. 예: `2026-06-10T19:00:00+09:00`"

    end_time = _parse_datetime(args.get("end_time"))
    channel = _resolve_voice_channel(context, args.get("channel"))
    location = str(args.get("location") or "").strip()
    description = str(args.get("description") or "").strip()

    if channel is None and not location:
        return "이벤트는 음성/스테이지 채널 또는 외부 장소(location)가 필요해요."

    try:
        if channel is not None:
            event = await context.guild.create_scheduled_event(
                name=name,
                start_time=start_time,
                end_time=end_time,
                description=description,
                channel=channel,
                reason=_audit_reason(context, "AI agent event create"),
            )
        else:
            event = await context.guild.create_scheduled_event(
                name=name,
                start_time=start_time,
                end_time=end_time,
                description=description,
                location=location,
                entity_type=discord.EntityType.external,
                reason=_audit_reason(context, "AI agent event create"),
            )
    except discord.Forbidden:
        return "Discord가 이벤트 생성을 거부했어요. 이벤트 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"이벤트 생성에 실패했어요: {exc.text or exc}"

    return f"`{event.name}` 이벤트를 만들었어요."


async def _event_update(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_events"):
        return "이 작업은 관리자 또는 Manage Events 권한이 필요해요."

    event = _resolve_event(context, args.get("event"))
    if event is None:
        return "수정할 이벤트를 찾지 못했어요. 이벤트 이름이나 ID를 알려주세요."

    edit_kwargs: dict[str, Any] = {}
    name = str(args.get("name") or "").strip()
    description = str(args.get("description") or "").strip()
    start_time = _parse_datetime(args.get("start_time"))
    end_time = _parse_datetime(args.get("end_time"))
    channel = _resolve_voice_channel(context, args.get("channel"))
    location = str(args.get("location") or "").strip()

    if name:
        edit_kwargs["name"] = name
    if description:
        edit_kwargs["description"] = description
    if start_time:
        edit_kwargs["start_time"] = start_time
    if end_time:
        edit_kwargs["end_time"] = end_time
    if channel is not None:
        edit_kwargs["channel"] = channel
    if location:
        edit_kwargs["location"] = location
        edit_kwargs["entity_type"] = discord.EntityType.external

    if not edit_kwargs:
        return "변경할 이벤트 설정을 알려주세요. 이름, 설명, 시간, 채널, 장소를 바꿀 수 있어요."

    try:
        updated = await event.edit(
            **edit_kwargs,
            reason=_audit_reason(context, "AI agent event update"),
        )
    except discord.Forbidden:
        return "Discord가 이벤트 수정을 거부했어요."
    except discord.HTTPException as exc:
        return f"이벤트 수정에 실패했어요: {exc.text or exc}"

    return f"`{updated.name}` 이벤트를 수정했어요."


async def _event_cancel(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_events"):
        return "이 작업은 관리자 또는 Manage Events 권한이 필요해요."

    event = _resolve_event(context, args.get("event"))
    if event is None:
        return "취소할 이벤트를 찾지 못했어요. 이벤트 이름이나 ID를 알려주세요."

    try:
        cancelled = await event.cancel(reason=_audit_reason(context, "AI agent event cancel"))
    except discord.Forbidden:
        return "Discord가 이벤트 취소를 거부했어요."
    except discord.HTTPException as exc:
        return f"이벤트 취소에 실패했어요: {exc.text or exc}"

    return f"`{cancelled.name}` 이벤트를 취소했어요."


async def _event_delete(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_events"):
        return "이 작업은 관리자 또는 Manage Events 권한이 필요해요."

    event = _resolve_event(context, args.get("event"))
    if event is None:
        return "삭제할 이벤트를 찾지 못했어요. 이벤트 이름이나 ID를 알려주세요."

    name = event.name
    try:
        await event.delete(reason=_audit_reason(context, "AI agent event delete"))
    except discord.Forbidden:
        return "Discord가 이벤트 삭제를 거부했어요. 이벤트 권한을 확인해 주세요."

    return f"`{name}` 이벤트를 삭제했어요."


async def _welcome_screen_update(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Server 권한이 필요해요."
    if not _bot_has(context, "manage_guild"):
        return "봇에게 Manage Server 권한이 없어서 환영 화면을 변경할 수 없어요."

    edit_kwargs: dict[str, Any] = {}
    enabled = _optional_bool(args.get("enabled"))
    description = str(args.get("description") or "").strip()
    if enabled is not None:
        edit_kwargs["enabled"] = enabled
    if description:
        edit_kwargs["description"] = description
    if args.get("channels") is not None:
        welcome_channels = _build_welcome_channels(context, args.get("channels"))
        if args.get("channels") and not welcome_channels:
            return "환영 화면에 넣을 채널을 찾지 못했어요."
        edit_kwargs["welcome_channels"] = welcome_channels

    if not edit_kwargs:
        return "변경할 환영 화면 설정을 알려주세요. enabled, description, channels를 바꿀 수 있어요."

    try:
        await context.guild.edit_welcome_screen(
            **edit_kwargs,
            reason=_audit_reason(context, "AI agent welcome screen update"),
        )
    except discord.Forbidden:
        return "Discord가 환영 화면 변경을 거부했어요. 서버 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"환영 화면 변경에 실패했어요: {exc.text or exc}"

    changed = ", ".join(f"`{key}`" for key in edit_kwargs)
    return f"환영 화면 설정을 변경했어요: {changed}"


async def _widget_update(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Server 권한이 필요해요."
    if not _bot_has(context, "manage_guild"):
        return "봇에게 Manage Server 권한이 없어서 서버 위젯을 변경할 수 없어요."

    edit_kwargs: dict[str, Any] = {}
    enabled = _optional_bool(args.get("enabled"))
    if enabled is not None:
        edit_kwargs["enabled"] = enabled
    if args.get("channel") is not None:
        channel = _resolve_guild_channel(context, args.get("channel"))
        if channel is None:
            return "위젯 채널로 설정할 채널을 찾지 못했어요."
        edit_kwargs["channel"] = channel

    if not edit_kwargs:
        return "변경할 서버 위젯 설정을 알려주세요. enabled 또는 channel을 바꿀 수 있어요."

    try:
        await context.guild.edit_widget(
            **edit_kwargs,
            reason=_audit_reason(context, "AI agent widget update"),
        )
    except discord.Forbidden:
        return "Discord가 서버 위젯 변경을 거부했어요. 서버 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"서버 위젯 변경에 실패했어요: {exc.text or exc}"

    changed = ", ".join(f"`{key}`" for key in edit_kwargs)
    return f"서버 위젯 설정을 변경했어요: {changed}"


async def _onboarding_update(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Server 권한이 필요해요."
    if not _bot_has(context, "manage_guild"):
        return "봇에게 Manage Server 권한이 없어서 온보딩을 변경할 수 없어요."

    edit_kwargs: dict[str, Any] = {}
    enabled = _optional_bool(args.get("enabled"))
    if enabled is not None:
        edit_kwargs["enabled"] = enabled
    if args.get("default_channels") is not None:
        channels = _resolve_channels(context, args.get("default_channels"))
        if not channels:
            return "온보딩 기본 채널로 설정할 채널을 찾지 못했어요."
        edit_kwargs["default_channels"] = channels

    if not edit_kwargs:
        return "변경할 온보딩 설정을 알려주세요. enabled 또는 default_channels를 바꿀 수 있어요."

    try:
        await context.guild.edit_onboarding(
            **edit_kwargs,
            reason=_audit_reason(context, "AI agent onboarding update"),
        )
    except discord.Forbidden:
        return "Discord가 온보딩 변경을 거부했어요. 서버 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"온보딩 변경에 실패했어요: {exc.text or exc}"

    changed = ", ".join(f"`{key}`" for key in edit_kwargs)
    return f"온보딩 설정을 변경했어요: {changed}"


async def _integration_list(context: ActionContext) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Server 권한이 필요해요."
    if not _bot_has(context, "manage_guild"):
        return "봇에게 Manage Server 권한이 없어서 통합 목록을 볼 수 없어요."

    try:
        integrations = await context.guild.integrations()
    except discord.Forbidden:
        return "Discord가 통합 목록 조회를 거부했어요. 서버 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"통합 목록 조회에 실패했어요: {exc.text or exc}"

    if not integrations:
        return "서버 통합이 없어요."

    lines = ["서버 통합 목록:"]
    for integration in integrations[:20]:
        enabled = getattr(integration, "enabled", "unknown")
        lines.append(f"- `{integration.name}` (`{integration.id}`) | type: `{integration.type}` | enabled: {enabled}")
    return "\n".join(lines)


async def _integration_delete(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Server 권한이 필요해요."
    if not _bot_has(context, "manage_guild"):
        return "봇에게 Manage Server 권한이 없어서 통합을 삭제할 수 없어요."

    integration = await _resolve_integration(context, args.get("integration"))
    if integration is None:
        return "삭제할 통합을 찾지 못했어요. 통합 이름이나 ID를 알려주세요."

    name = integration.name
    try:
        await integration.delete(reason=_audit_reason(context, "AI agent integration delete"))
    except discord.Forbidden:
        return "Discord가 통합 삭제를 거부했어요. 서버 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"통합 삭제에 실패했어요: {exc.text or exc}"

    return f"`{name}` 통합을 삭제했어요."


async def _ban_list(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "ban_members"):
        return "이 작업은 관리자 또는 Ban Members 권한이 필요해요."
    if not _bot_has(context, "ban_members"):
        return "봇에게 Ban Members 권한이 없어서 차단 목록을 볼 수 없어요."

    limit = min(max(_optional_int(args.get("limit")) or 10, 1), 50)
    entries: list[discord.guild.BanEntry] = []
    try:
        async for entry in context.guild.bans(limit=limit):
            entries.append(entry)
    except discord.Forbidden:
        return "Discord가 차단 목록 조회를 거부했어요. 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"차단 목록 조회에 실패했어요: {exc.text or exc}"

    if not entries:
        return "차단된 사용자가 없어요."

    lines = [f"최근 차단 사용자 {len(entries)}명:"]
    for entry in entries:
        reason = entry.reason or "사유 없음"
        lines.append(f"- `{entry.user}` (`{entry.user.id}`) | {reason[:100]}")
    return "\n".join(lines)


async def _bulk_ban(context: ActionContext, args: dict[str, Any]) -> str:
    if not (_user_has(context, "ban_members") and _user_has(context, "manage_guild")):
        return "이 작업은 관리자 또는 Ban Members와 Manage Server 권한이 모두 필요해요."
    if not (_bot_has(context, "ban_members") and _bot_has(context, "manage_guild")):
        return "봇에게 Ban Members와 Manage Server 권한이 없어서 대량 차단을 실행할 수 없어요."

    user_ids = _extract_ids(args.get("users"))
    if not user_ids:
        return "대량 차단할 사용자 mention 또는 ID 목록을 알려주세요."
    user_ids = list(dict.fromkeys(user_ids))[:200]
    users = [discord.Object(id=user_id, type=discord.User) for user_id in user_ids]
    delete_days = min(max(_optional_int(args.get("delete_message_days")) or 1, 0), 7)

    try:
        result = await context.guild.bulk_ban(
            users,
            reason=_audit_reason(context, str(args.get("reason") or "AI agent bulk ban")),
            delete_message_seconds=delete_days * 86400,
        )
    except discord.Forbidden:
        return "Discord가 대량 차단을 거부했어요. 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"대량 차단에 실패했어요: {exc.text or exc}"

    banned = getattr(result, "banned", [])
    failed = getattr(result, "failed", [])
    return f"대량 차단을 실행했어요. 성공: {len(banned)}명, 실패: {len(failed)}명"


async def _vanity_invite_show(context: ActionContext) -> str:
    if not _user_has(context, "manage_guild"):
        return "이 작업은 관리자 또는 Manage Server 권한이 필요해요."
    if not _bot_has(context, "manage_guild"):
        return "봇에게 Manage Server 권한이 없어서 커스텀 초대를 볼 수 없어요."

    try:
        invite = await context.guild.vanity_invite()
    except discord.Forbidden:
        return "Discord가 커스텀 초대 조회를 거부했어요. 서버 관리 권한을 확인해 주세요."
    except discord.HTTPException as exc:
        return f"커스텀 초대 조회에 실패했어요: {exc.text or exc}"

    if invite is None:
        return "이 서버에는 커스텀 초대 URL이 없어요."

    uses = invite.uses if invite.uses is not None else "알 수 없음"
    return f"서버 커스텀 초대: {invite.url} | uses: {uses}"


def _resolve_text_channel(context: ActionContext, value: Any) -> discord.TextChannel | None:
    channel = _resolve_guild_channel(context, value)
    return channel if isinstance(channel, discord.TextChannel) else None


def _resolve_guild_channel(
    context: ActionContext,
    value: Any,
) -> discord.abc.GuildChannel | discord.Thread | None:
    if str(value).strip().casefold() == "current":
        return context.channel if isinstance(context.channel, (discord.abc.GuildChannel, discord.Thread)) else None

    channel_id = _extract_id(value)
    if channel_id:
        channel = context.guild.get_channel_or_thread(channel_id)
        return channel if isinstance(channel, (discord.abc.GuildChannel, discord.Thread)) else None

    name = str(value or "").strip().lstrip("#")
    if not name:
        return context.channel if isinstance(context.channel, (discord.abc.GuildChannel, discord.Thread)) else None

    return discord.utils.find(
        lambda channel: getattr(channel, "name", None) == name,
        [*context.guild.channels, *context.guild.threads],
    )


def _resolve_voice_channel(
    context: ActionContext,
    value: Any,
) -> discord.VoiceChannel | discord.StageChannel | None:
    channel = _resolve_guild_channel(context, value)
    return channel if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)) else None


def _resolve_forum_channel(context: ActionContext, value: Any) -> discord.ForumChannel | None:
    channel = _resolve_guild_channel(context, value)
    return channel if isinstance(channel, discord.ForumChannel) else None


def _resolve_category(context: ActionContext, value: Any) -> discord.CategoryChannel | None:
    channel = _resolve_guild_channel(context, value)
    if isinstance(channel, discord.CategoryChannel):
        return channel

    name = str(value or "").strip().lstrip("#")
    if not name:
        return None

    return discord.utils.get(context.guild.categories, name=name)


def _resolve_thread(context: ActionContext, value: Any) -> discord.Thread | None:
    channel = _resolve_guild_channel(context, value)
    if isinstance(channel, discord.Thread):
        return channel

    if isinstance(context.channel, discord.Thread) and str(value or "").strip().casefold() == "current":
        return context.channel

    return None


async def _resolve_member(context: ActionContext, value: Any) -> discord.Member | None:
    member_id = _extract_id(value)
    if member_id:
        member = context.guild.get_member(member_id)
        if member is not None:
            return member
        try:
            return await context.guild.fetch_member(member_id)
        except discord.NotFound:
            return None

    name = str(value or "").strip().lstrip("@")
    if not name:
        return None

    cached_match = _find_member_by_name(context.guild.members, name)
    if cached_match is not None:
        return cached_match

    try:
        queried = await context.guild.query_members(query=name, limit=10)
    except (discord.Forbidden, discord.HTTPException):
        queried = []

    return _find_member_by_name(queried, name)


def _find_member_by_name(members: list[discord.Member] | tuple[discord.Member, ...], query: str) -> discord.Member | None:
    normalized_query = _normalize_lookup_text(query)
    if not normalized_query:
        return None

    exact_matches: list[discord.Member] = []
    partial_matches: list[discord.Member] = []
    for member in members:
        candidates = {
            member.name,
            member.display_name,
            member.global_name or "",
            str(member),
        }
        normalized_candidates = {_normalize_lookup_text(candidate) for candidate in candidates if candidate}
        if normalized_query in normalized_candidates:
            exact_matches.append(member)
            continue
        if any(normalized_query in candidate for candidate in normalized_candidates):
            partial_matches.append(member)

    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(partial_matches) == 1:
        return partial_matches[0]
    return None


def _normalize_lookup_text(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().casefold())


def _resolve_role(context: ActionContext, value: Any) -> discord.Role | None:
    role_id = _extract_id(value)
    if role_id:
        return context.guild.get_role(role_id)

    name = str(value or "").strip().lstrip("@")
    if not name:
        return None

    return discord.utils.get(context.guild.roles, name=name)


async def _resolve_permission_target(
    context: ActionContext,
    value: Any,
) -> discord.Role | discord.Member | None:
    text = str(value or "").strip()
    if text.casefold() in {"everyone", "@everyone", "기본 역할", "모두"}:
        return context.guild.default_role

    role = _resolve_role(context, value)
    if role is not None:
        return role
    return await _resolve_member(context, value)


def _resolve_emoji(context: ActionContext, value: Any) -> discord.Emoji | None:
    emoji_id = _extract_id(value)
    if emoji_id:
        return context.guild.get_emoji(emoji_id)

    name = _sanitize_expression_name(str(value or ""))
    if not name:
        return None

    return discord.utils.get(context.guild.emojis, name=name)


def _resolve_sticker(context: ActionContext, value: Any) -> discord.GuildSticker | None:
    sticker_id = _extract_id(value)
    if sticker_id:
        return discord.utils.get(context.guild.stickers, id=sticker_id)

    name = str(value or "").strip()
    if not name:
        return None

    return discord.utils.get(context.guild.stickers, name=name)


def _resolve_sound(context: ActionContext, value: Any) -> discord.SoundboardSound | None:
    sound_id = _extract_id(value)
    sounds = getattr(context.guild, "soundboard_sounds", [])
    if sound_id:
        return discord.utils.get(sounds, id=sound_id)

    name = str(value or "").strip()
    if not name:
        return None

    return discord.utils.get(sounds, name=name)


async def _resolve_webhook(context: ActionContext, value: Any) -> discord.Webhook | None:
    webhook_id = _extract_id(value)
    name = str(value or "").strip()

    for channel in context.guild.text_channels:
        try:
            webhooks = await channel.webhooks()
        except discord.Forbidden:
            continue
        for webhook in webhooks:
            if webhook_id and webhook.id == webhook_id:
                return webhook
            if name and webhook.name == name:
                return webhook

    return None


def _resolve_event(context: ActionContext, value: Any) -> discord.ScheduledEvent | None:
    event_id = _extract_id(value)
    events = list(context.guild.scheduled_events)
    if event_id:
        return discord.utils.get(events, id=event_id)

    name = str(value or "").strip()
    if not name:
        return None

    return discord.utils.get(events, name=name)


async def _resolve_invite(context: ActionContext, value: Any) -> discord.Invite | None:
    code = str(value or "").strip()
    if not code:
        return None
    code = code.rstrip("/").split("/")[-1]
    try:
        invites = await context.guild.invites()
    except discord.Forbidden:
        return None
    return discord.utils.get(invites, code=code)


async def _resolve_template(context: ActionContext, value: Any) -> discord.Template | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.rstrip("/").split("/")[-1]
    try:
        templates = await context.guild.templates()
    except discord.Forbidden:
        return None
    return discord.utils.find(
        lambda template: template.code == text or template.name == text,
        templates,
    )


async def _resolve_automod_rule(context: ActionContext, value: Any) -> discord.AutoModRule | None:
    rule_id = _extract_id(value)
    try:
        if rule_id:
            return await context.guild.fetch_automod_rule(rule_id)
        rules = await context.guild.fetch_automod_rules()
    except (discord.Forbidden, discord.NotFound):
        return None

    name = str(value or "").strip()
    if not name:
        return None
    return discord.utils.get(rules, name=name)


async def _resolve_integration(context: ActionContext, value: Any) -> discord.Integration | None:
    integration_id = _extract_id(value)
    name = str(value or "").strip()
    try:
        integrations = await context.guild.integrations()
    except discord.Forbidden:
        return None

    for integration in integrations:
        if integration_id and integration.id == integration_id:
            return integration
        if name and integration.name == name:
            return integration
    return None


def _resolve_forum_tag(forum: discord.ForumChannel, value: Any) -> discord.ForumTag | None:
    tag_id = _extract_id(value)
    name = str(value or "").strip()
    for tag in forum.available_tags:
        if tag_id and tag.id == tag_id:
            return tag
        if name and tag.name == name:
            return tag
    return None


def _extract_id(value: Any) -> int | None:
    match = re.search(r"\d{15,25}", str(value or ""))
    if not match:
        return None
    return int(match.group(0))


def _extract_ids(value: Any) -> list[int]:
    return [int(match) for match in re.findall(r"\d{15,25}", str(value or ""))]


def _user_has(context: ActionContext, permission_name: str) -> bool:
    permissions = getattr(context.user, "guild_permissions", None)
    return bool(permissions and (permissions.administrator or getattr(permissions, permission_name, False)))


def _bot_has(context: ActionContext, permission_name: str) -> bool:
    permissions = getattr(context.guild.me, "guild_permissions", None)
    return bool(permissions and (permissions.administrator or getattr(permissions, permission_name, False)))


def _can_manage_role(context: ActionContext, role: discord.Role) -> bool:
    me = context.guild.me
    if me is None or me.top_role <= role:
        return False
    if isinstance(context.user, discord.Member):
        if context.user.id == context.guild.owner_id:
            return True
        return context.user.top_role > role
    return False


def _can_manage_member(context: ActionContext, member: discord.Member) -> bool:
    me = context.guild.me
    if me is None or me.top_role <= member.top_role:
        return False
    if isinstance(context.user, discord.Member):
        if context.user.id == context.guild.owner_id:
            return True
        return context.user.top_role > member.top_role
    return False


def _can_manage_expressions(context: ActionContext, *, create: bool = False) -> bool:
    if create:
        user_allowed = _user_has(context, "create_expressions") or _user_has(context, "manage_expressions")
        bot_allowed = _bot_has(context, "create_expressions") or _bot_has(context, "manage_expressions")
        return user_allowed and bot_allowed
    return _user_has(context, "manage_expressions") and _bot_has(context, "manage_expressions")


def _resolve_roles(context: ActionContext, value: Any) -> list[discord.Role]:
    items = value if isinstance(value, list) else [value]
    roles: list[discord.Role] = []
    seen: set[int] = set()
    for item in items:
        ids = _extract_ids(item)
        if ids:
            for role_id in ids:
                role = context.guild.get_role(role_id)
                if role and role.id not in seen:
                    roles.append(role)
                    seen.add(role.id)
            continue
        for piece in str(item or "").split(","):
            role = _resolve_role(context, piece.strip())
            if role and role.id not in seen:
                roles.append(role)
                seen.add(role.id)
    return roles


def _resolve_channels(context: ActionContext, value: Any) -> list[discord.abc.GuildChannel]:
    items = value if isinstance(value, list) else [value]
    channels: list[discord.abc.GuildChannel] = []
    seen: set[int] = set()
    for item in items:
        ids = _extract_ids(item)
        if ids:
            for channel_id in ids:
                channel = context.guild.get_channel(channel_id)
                if isinstance(channel, discord.abc.GuildChannel) and channel.id not in seen:
                    channels.append(channel)
                    seen.add(channel.id)
            continue
        for piece in str(item or "").split(","):
            channel = _resolve_guild_channel(context, piece.strip())
            if isinstance(channel, discord.abc.GuildChannel) and channel.id not in seen:
                channels.append(channel)
                seen.add(channel.id)
    return channels


def _build_welcome_channels(context: ActionContext, value: Any) -> list[discord.WelcomeChannel]:
    items = value if isinstance(value, list) else [value]
    welcome_channels: list[discord.WelcomeChannel] = []
    for item in items:
        if isinstance(item, dict):
            channel_value = item.get("channel")
            description = str(item.get("description") or "서버에 오신 것을 환영해요.").strip()
            emoji_value = item.get("emoji")
        else:
            channel_value = item
            description = "서버에 오신 것을 환영해요."
            emoji_value = None

        channel = _resolve_guild_channel(context, channel_value)
        if not isinstance(channel, discord.abc.GuildChannel):
            continue
        welcome_channels.append(
            discord.WelcomeChannel(
                channel=channel,
                description=description[:100] or "서버에 오신 것을 환영해요.",
                emoji=_parse_emoji_input(emoji_value),
            )
        )

    return welcome_channels[:5]


def _normalize_keywords(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_keywords = value
    else:
        raw_keywords = str(value or "").split(",")

    keywords: list[str] = []
    seen: set[str] = set()
    for keyword in raw_keywords:
        clean = str(keyword).strip()
        normalized = clean.casefold()
        if clean and normalized not in seen:
            keywords.append(clean)
            seen.add(normalized)
    return keywords


def _keyword_suffix(keywords: list[str]) -> str:
    if not keywords:
        return ""
    return f" | keywords: {', '.join(f'`{keyword}`' for keyword in keywords)}"


def _forum_create_kwargs(
    args: dict[str, Any],
    category: discord.CategoryChannel | None,
    reason: str,
    *,
    media: bool,
) -> dict[str, Any]:
    create_kwargs: dict[str, Any] = {
        "category": category,
        "topic": str(args.get("topic") or ""),
        "slowmode_delay": _optional_int(args.get("slowmode")) or 0,
        "nsfw": _optional_bool(args.get("nsfw")) or False,
        "media": media,
        "reason": reason,
    }
    position = _optional_int(args.get("position"))
    default_archive = _optional_int(args.get("default_auto_archive_duration"))
    default_thread_slowmode = _optional_int(args.get("default_thread_slowmode"))
    default_layout = _parse_forum_layout(args.get("default_layout"))
    default_sort_order = _parse_forum_sort_order(args.get("default_sort_order"))
    if position is not None:
        create_kwargs["position"] = position
    if default_archive is not None:
        create_kwargs["default_auto_archive_duration"] = default_archive
    if default_thread_slowmode is not None:
        create_kwargs["default_thread_slowmode_delay"] = default_thread_slowmode
    if default_layout is not None:
        create_kwargs["default_layout"] = default_layout
    if default_sort_order is not None:
        create_kwargs["default_sort_order"] = default_sort_order
    return create_kwargs


def _parse_color(value: str) -> discord.Colour | None:
    value = value.strip()
    if not value:
        return discord.Colour.default()
    value = value.removeprefix("#")
    if len(value) != 6:
        return None
    try:
        return discord.Colour(int(value, 16))
    except ValueError:
        return None


def _parse_optional_color(value: Any) -> discord.Colour | None:
    text = str(value or "").strip()
    if not text:
        return None
    return _parse_color(text)


def _parse_video_quality_mode(value: Any) -> discord.VideoQualityMode | None:
    normalized = str(value or "").strip().casefold().replace("-", "_")
    if normalized in {"auto", "자동"}:
        return discord.VideoQualityMode.auto
    if normalized in {"full", "720p", "고화질"}:
        return discord.VideoQualityMode.full
    return None


def _parse_forum_layout(value: Any) -> discord.ForumLayoutType | None:
    normalized = str(value or "").strip().casefold().replace("-", "_")
    aliases = {
        "not_set": discord.ForumLayoutType.not_set,
        "none": discord.ForumLayoutType.not_set,
        "list": discord.ForumLayoutType.list_view,
        "list_view": discord.ForumLayoutType.list_view,
        "gallery": discord.ForumLayoutType.gallery_view,
        "gallery_view": discord.ForumLayoutType.gallery_view,
    }
    return aliases.get(normalized)


def _parse_forum_sort_order(value: Any) -> discord.ForumOrderType | None:
    normalized = str(value or "").strip().casefold().replace("-", "_")
    aliases = {
        "latest": discord.ForumOrderType.latest_activity,
        "latest_activity": discord.ForumOrderType.latest_activity,
        "activity": discord.ForumOrderType.latest_activity,
        "creation": discord.ForumOrderType.creation_date,
        "creation_date": discord.ForumOrderType.creation_date,
        "created": discord.ForumOrderType.creation_date,
    }
    return aliases.get(normalized)


def _build_permission_overwrite(args: dict[str, Any]) -> discord.PermissionOverwrite | None:
    overwrite = discord.PermissionOverwrite()
    changed = _apply_permission_names(overwrite, args.get("allow"), True)
    changed += _apply_permission_names(overwrite, args.get("deny"), False)
    changed += _apply_permission_dict(overwrite, args.get("permissions"))
    return overwrite if changed else None


def _apply_permission_names(target: Any, value: Any, state: bool) -> list[str]:
    names = _normalize_keywords(value)
    changed: list[str] = []
    for name in names:
        permission_name = _normalize_permission_name(name)
        if not permission_name:
            continue
        setattr(target, permission_name, state)
        changed.append(permission_name)
    return changed


def _apply_permission_dict(target: Any, value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    changed: list[str] = []
    for raw_name, raw_state in value.items():
        permission_name = _normalize_permission_name(str(raw_name))
        if not permission_name:
            continue
        state = _optional_bool(raw_state)
        if state is None:
            continue
        setattr(target, permission_name, state)
        changed.append(permission_name)
    return changed


def _normalize_permission_name(value: str) -> str | None:
    normalized = value.strip().casefold().replace(" ", "_").replace("-", "_")
    aliases = {
        "administrator": "administrator",
        "admin": "administrator",
        "manage_server": "manage_guild",
        "manage_guild": "manage_guild",
        "view_channel": "view_channel",
        "read_messages": "read_messages",
        "send_message": "send_messages",
        "send_messages": "send_messages",
        "manage_message": "manage_messages",
        "manage_messages": "manage_messages",
        "pin_message": "pin_messages",
        "pin_messages": "pin_messages",
        "manage_role": "manage_roles",
        "manage_roles": "manage_roles",
        "manage_channel": "manage_channels",
        "manage_channels": "manage_channels",
        "connect": "connect",
        "speak": "speak",
        "mute_members": "mute_members",
        "deafen_members": "deafen_members",
        "move_members": "move_members",
        "use_voice_activation": "use_voice_activation",
        "create_invite": "create_instant_invite",
        "create_instant_invite": "create_instant_invite",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in discord.Permissions.VALID_FLAGS else None


def _sanitize_expression_name(value: str) -> str:
    value = value.strip()
    match = re.search(r"<a?:([A-Za-z0-9_]{2,32}):\d+>", value)
    if match:
        value = match.group(1)
    value = value.strip(":")
    value = re.sub(r"\W+", "_", value)
    return value[:32]


def _parse_emoji_input(value: Any) -> discord.PartialEmoji | discord.Emoji | str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"<a?:[A-Za-z0-9_]{2,32}:\d{15,25}>", text):
        return discord.PartialEmoji.from_str(text)
    return text[:100]


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().casefold()
    if normalized in {"true", "yes", "y", "1", "on", "켜", "켜줘", "활성화"}:
        return True
    if normalized in {"false", "no", "n", "0", "off", "꺼", "꺼줘", "비활성화"}:
        return False
    return None


def _parse_datetime(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _parse_locale(value: Any) -> discord.Locale | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return discord.Locale(text)
    except ValueError:
        return None


async def _get_binary_asset(
    context: ActionContext,
    url: Any,
    *,
    allow_attachment: bool = True,
    max_bytes: int = 8 * 1024 * 1024,
) -> BinaryAsset | None:
    if allow_attachment and context.message and context.message.attachments:
        attachment = context.message.attachments[0]
        if attachment.size > max_bytes:
            return None
        return BinaryAsset(
            data=await attachment.read(),
            filename=attachment.filename or "asset.bin",
        )

    url_text = str(url or "").strip()
    if not url_text:
        return None

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        async with session.get(url_text) as response:
            if response.status < 200 or response.status >= 300:
                return None
            data = await response.read()
            if len(data) > max_bytes:
                return None

    filename = url_text.rstrip("/").split("/")[-1].split("?")[0] or "asset.bin"
    return BinaryAsset(data=data, filename=filename)


def _audit_reason(context: ActionContext, action: str) -> str:
    user = context.user
    if user is None:
        return action
    return f"{action} by {user} ({user.id})"
