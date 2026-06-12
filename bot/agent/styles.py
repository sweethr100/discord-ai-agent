from __future__ import annotations

from dataclasses import dataclass

from agent.self_manual import append_self_manual
from agent.system_prompt import DEFAULT_SYSTEM_PROMPT


@dataclass(frozen=True)
class StylePreset:
    name: str
    title: str
    description: str
    prompt: str


STYLE_PRESETS: dict[str, StylePreset] = {
    "default": StylePreset(
        name="default",
        title="Default",
        description="기본 친절한 Discord AI 에이전트",
        prompt="",
    ),
    "grok": StylePreset(
        name="grok",
        title="Grok",
        description="재치 있고 직설적이지만 무례하지 않은 답변",
        prompt=(
            "재치 있고 에너지 있게 답하라. 핵심은 직설적으로 말하되, 사용자를 비꼬거나 "
            "공격하지 말고 실용적인 다음 행동을 함께 제안하라."
        ),
    ),
    "serious": StylePreset(
        name="serious",
        title="Serious",
        description="차분하고 전문적인 답변",
        prompt="차분하고 전문적인 톤으로 답하라. 과장 없이 근거, 위험, 한계를 명확히 구분하라.",
    ),
    "teacher": StylePreset(
        name="teacher",
        title="Teacher",
        description="개념을 단계별로 설명하는 선생님 스타일",
        prompt=(
            "좋은 선생님처럼 답하라. 먼저 결론을 짧게 말하고, 필요한 개념을 쉬운 단계로 "
            "풀어 설명하며, 사용자가 바로 따라 할 수 있는 예시를 포함하라."
        ),
    ),
    "coder": StylePreset(
        name="coder",
        title="Coder",
        description="개발자에게 유용한 코드 중심 답변",
        prompt=(
            "시니어 개발자처럼 답하라. 구현 가능성, 엣지 케이스, 명령어, 코드 예시를 "
            "중심으로 간결하게 설명하라."
        ),
    ),
    "korean_friend": StylePreset(
        name="korean_friend",
        title="Korean Friend",
        description="한국어로 편하게 말해주는 친구 스타일",
        prompt="한국어로 자연스럽고 편하게 답하라. 다정하지만 과하게 들뜨지 않게, 실용적으로 도와줘라.",
    ),
    "custom": StylePreset(
        name="custom",
        title="Custom",
        description="서버 관리자가 설정한 커스텀 시스템 프롬프트",
        prompt="",
    ),
}

STYLE_NAMES = tuple(STYLE_PRESETS.keys())
CONCISE_RESPONSE_GUIDE = (
    "공통 답변 지침: 기본 답변은 3~6문장 또는 짧은 목록으로 끝내라. "
    "장황한 서론, 과한 꾸밈말, 불필요한 반복은 줄이고 핵심과 다음 행동만 말하라. "
    "사용자가 '자세히', '길게', '단계별로' 요청한 경우에만 길게 답하라."
)


def is_valid_style(style: str) -> bool:
    return style in STYLE_PRESETS


def build_system_prompt(
    *,
    base_prompt: str,
    style: str,
    custom_prompt: str = "",
) -> str:
    base_prompt = (base_prompt or DEFAULT_SYSTEM_PROMPT).strip()
    style = style if is_valid_style(style) else "default"

    if style == "custom":
        custom_base = custom_prompt.strip() or base_prompt
        return append_self_manual(f"{custom_base}\n\n{CONCISE_RESPONSE_GUIDE}")

    preset = STYLE_PRESETS[style]
    if not preset.prompt:
        return append_self_manual(f"{base_prompt}\n\n{CONCISE_RESPONSE_GUIDE}")

    return append_self_manual(f"{base_prompt}\n\n{CONCISE_RESPONSE_GUIDE}\n\n응답 스타일 지침:\n{preset.prompt}")


def format_style_presets() -> str:
    lines = ["사용 가능한 AI 스타일:"]
    for preset in STYLE_PRESETS.values():
        lines.append(f"- `{preset.name}`: {preset.description}")
    return "\n".join(lines)
