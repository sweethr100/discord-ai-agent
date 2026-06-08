from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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
- channel_update: args channel, name, topic, slowmode, nsfw
- role_create: args name, color, mentionable, hoist
- role_add: args member, role
- role_remove: args member, role
- none: args

규칙:
- 실행 요청이 명확할 때만 action을 선택하라.
- "설정법 알려줘", "명령어 뭐야", "할 수 있어?" 같은 설명 요청은 none이다.
- channel/member/role은 Discord mention 형식이 있으면 그대로 넣어라. 예: <#123>, <@456>, <@&789>.
- 현재 채널을 뜻하면 channel을 "current"로 넣어라.
- mode는 always, question_only, keyword 중 하나만 사용하라.
- style은 default, grok, serious, teacher, coder, korean_friend, custom 중 하나만 사용하라.
- slowmode는 초 단위 정수다.
- nsfw, mentionable, hoist는 true/false/null 중 하나다.
- keywords는 배열이다.
- confidence는 0부터 1 사이 숫자다.
"""


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
) -> str | None:
    guild = interaction.guild if interaction else message.guild if message else None
    if guild is None:
        return None

    plan = await _plan_action(bot, prompt)
    if plan is None or plan.action == "none" or plan.confidence < 0.65:
        return None

    context = ActionContext(
        bot=bot,
        guild=guild,
        channel=interaction.channel if interaction else message.channel if message else None,
        user=interaction.user if interaction else message.author if message else None,
    )
    return await _execute_plan(context, plan)


@dataclass
class ActionContext:
    bot: "DiscordAIBot"
    guild: discord.Guild
    channel: discord.abc.GuildChannel | discord.Thread | discord.abc.PrivateChannel | None
    user: discord.User | discord.Member | None


async def _plan_action(bot: "DiscordAIBot", prompt: str) -> ActionPlan | None:
    messages: list[Message] = [
        {"role": "system", "content": ACTION_PLANNER_PROMPT},
        {"role": "user", "content": prompt},
    ]
    raw = await bot.agent.provider.generate_response(
        messages,
        ProviderOptions(temperature=0.0, max_tokens=500),
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


async def _execute_plan(context: ActionContext, plan: ActionPlan) -> str:
    action = plan.action
    args = plan.args

    if action == "autochannel_add":
        return await _autochannel_add(context, args)
    if action == "autochannel_remove":
        return await _autochannel_remove(context, args)
    if action == "autochannel_list":
        return _autochannel_list(context)
    if action == "autochannel_mode":
        return await _autochannel_mode(context, args)
    if action == "style_set":
        return _style_set(context, args)
    if action == "style_show":
        return _style_show(context)
    if action == "style_presets":
        return format_style_presets()
    if action == "style_custom":
        return _style_custom(context, args)
    if action == "channel_update":
        return await _channel_update(context, args)
    if action == "role_create":
        return await _role_create(context, args)
    if action == "role_add":
        return await _role_update_member(context, args, add=True)
    if action == "role_remove":
        return await _role_update_member(context, args, add=False)

    return "그 작업은 아직 제가 실행할 수 있는 도구로 연결되어 있지 않아요."


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


async def _channel_update(context: ActionContext, args: dict[str, Any]) -> str:
    if not _user_has(context, "manage_channels"):
        return "이 작업은 관리자 또는 Manage Channels 권한이 필요해요."
    if not _bot_has(context, "manage_channels"):
        return "봇에게 Manage Channels 권한이 없어서 채널 설정을 바꿀 수 없어요."

    channel = _resolve_text_channel(context, args.get("channel"))
    if channel is None:
        return "설정을 바꿀 텍스트 채널을 찾지 못했어요. 채널을 멘션하거나 '현재 채널'이라고 다시 요청해 주세요."

    edit_kwargs: dict[str, Any] = {}
    name = str(args.get("name") or "").strip()
    topic = str(args.get("topic") or "").strip()
    slowmode = args.get("slowmode")
    nsfw = args.get("nsfw")

    if name:
        edit_kwargs["name"] = name
    if topic:
        edit_kwargs["topic"] = topic
    if slowmode is not None:
        try:
            slowmode_int = int(slowmode)
        except (TypeError, ValueError):
            return "slowmode는 초 단위 숫자여야 해요."
        if slowmode_int < 0 or slowmode_int > 21600:
            return "slowmode 값은 0 이상 21600 이하 초여야 해요."
        edit_kwargs["slowmode_delay"] = slowmode_int
    if isinstance(nsfw, bool):
        edit_kwargs["nsfw"] = nsfw

    if not edit_kwargs:
        return "변경할 채널 설정을 찾지 못했어요. 이름, 주제, 슬로우모드, NSFW 여부 중 하나를 알려주세요."

    try:
        await channel.edit(**edit_kwargs, reason=_audit_reason(context, "AI agent channel update"))
    except discord.Forbidden:
        return "Discord가 채널 변경을 거부했어요. 봇 권한이나 채널별 권한을 확인해 주세요."

    changed = ", ".join(f"`{key}`" for key in edit_kwargs)
    return f"{channel.mention} 채널 설정을 변경했어요: {changed}"


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

    try:
        role = await context.guild.create_role(
            name=name,
            colour=color,
            mentionable=mentionable,
            hoist=hoist,
            reason=_audit_reason(context, "AI agent role create"),
        )
    except discord.Forbidden:
        return "Discord가 역할 생성을 거부했어요. 봇의 Manage Roles 권한이나 역할 위치를 확인해 주세요."

    return f"{role.mention} 역할을 만들었어요."


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


def _resolve_text_channel(context: ActionContext, value: Any) -> discord.TextChannel | None:
    if str(value).strip().casefold() == "current":
        return context.channel if isinstance(context.channel, discord.TextChannel) else None

    channel_id = _extract_id(value)
    if channel_id:
        channel = context.guild.get_channel(channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    name = str(value or "").strip().lstrip("#")
    if not name:
        return context.channel if isinstance(context.channel, discord.TextChannel) else None

    return discord.utils.get(context.guild.text_channels, name=name)


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

    return discord.utils.find(
        lambda member: member.name == name or member.display_name == name or str(member) == name,
        context.guild.members,
    )


def _resolve_role(context: ActionContext, value: Any) -> discord.Role | None:
    role_id = _extract_id(value)
    if role_id:
        return context.guild.get_role(role_id)

    name = str(value or "").strip().lstrip("@")
    if not name:
        return None

    return discord.utils.get(context.guild.roles, name=name)


def _extract_id(value: Any) -> int | None:
    match = re.search(r"\d{15,25}", str(value or ""))
    if not match:
        return None
    return int(match.group(0))


def _user_has(context: ActionContext, permission_name: str) -> bool:
    permissions = getattr(context.user, "guild_permissions", None)
    return bool(permissions and (permissions.administrator or getattr(permissions, permission_name, False)))


def _bot_has(context: ActionContext, permission_name: str) -> bool:
    permissions = getattr(context.guild.me, "guild_permissions", None)
    return bool(permissions and getattr(permissions, permission_name, False))


def _can_manage_role(context: ActionContext, role: discord.Role) -> bool:
    me = context.guild.me
    if me is None or me.top_role <= role:
        return False
    if isinstance(context.user, discord.Member):
        if context.user.id == context.guild.owner_id:
            return True
        return context.user.top_role > role
    return False


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


def _audit_reason(context: ActionContext, action: str) -> str:
    user = context.user
    if user is None:
        return action
    return f"{action} by {user} ({user.id})"
