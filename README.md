# Discord AI Agent Bot

Python 3.11+, `discord.py`, `python-dotenv`, `aiohttp` 기반 Discord AI 에이전트 봇입니다.

지원 입력:

- `/ai message:<내용>` 슬래시 명령어
- 봇을 멘션한 일반 메시지
- `/autochannel add`로 등록한 채널의 일반 메시지

봇은 자체 사용 설명서를 시스템 컨텍스트로 참고하므로, `/ai message:너 명령어 어떻게 써?` 또는 `@봇 자동 응답 채널 설정법 알려줘`처럼 물어보면 slash command, 권한, provider 설정, 실행법을 자세히 설명할 수 있습니다.

지원 provider:

- `openai`
- `gemini`
- `anthropic`
- `local` (`Ollama`, `LM Studio`, `vLLM` 같은 OpenAI-compatible Chat Completions API)

## 프로젝트 구조

```text
bot/
  main.py
  config.py
  discord_bot/
    client.py
    commands.py
    handlers.py
    agent_actions.py
  agent/
    agent.py
    system_prompt.py
    tools/
      __init__.py
  providers/
    base.py
    openai_provider.py
    gemini_provider.py
    anthropic_provider.py
    local_provider.py
  utils/
    split_message.py
    logger.py
requirements.txt
.env.example
README.md
```

## Discord 봇 만들기

1. [Discord Developer Portal](https://discord.com/developers/applications)에서 **New Application**을 만듭니다.
2. 왼쪽 **Bot** 메뉴에서 봇을 생성하고 **Reset Token** 또는 **Copy Token**으로 토큰을 복사합니다.
3. **Bot > Privileged Gateway Intents**에서 필요한 intent를 켭니다.
   - **Message Content Intent**: 멘션 응답, 자동 응답 채널, 채팅방 문맥 읽기에 필요
   - **Server Members Intent**: 멤버를 멘션/ID 없이 별명이나 표시 이름으로 찾을 때 필요
4. **OAuth2 > URL Generator**에서 아래 scope를 선택합니다.
   - `bot`
   - `applications.commands`
5. Bot Permissions는 최소한 아래 권한을 권장합니다.
   - View Channels
   - Send Messages
   - Read Message History
   - Use Slash Commands
   - Manage Channels: 자연어 채널 설정 변경과 자동 응답 채널 관리에 필요
   - Manage Roles: 자연어 역할 생성/수정/삭제/추가/제거에 필요
   - Create Expressions, Manage Expressions: 이모지, 스티커, 사운드 생성/수정/삭제에 필요
   - Manage Webhooks: 웹훅 생성/삭제에 필요
   - View Audit Log: 감사 로그 조회에 필요
   - Manage Server: 서버 이름/설명 변경에 필요
   - Create Invite: 초대 링크 생성에 필요
   - Kick Members, Ban Members, Moderate Members, Manage Nicknames: 멤버 제재와 별명 관리에 필요
   - Manage Messages, Pin Messages: 메시지 삭제와 고정 관리에 필요
   - Create Public Threads, Manage Threads: 스레드 생성/수정/삭제에 필요
   - Create Events, Manage Events: 이벤트 생성/삭제에 필요
   - Move Members, Mute Members, Deafen Members: 음성 채널 멤버 관리에 필요
   - 서버 관리 전반을 자연어로 맡기려면 Discord의 **Administrator** 권한을 줄 수도 있지만, 강력한 권한이므로 신중히 사용하세요.
6. 생성된 초대 URL로 서버에 봇을 초대합니다.

## 설치와 실행

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
python bot/main.py
```

`.env`를 열어 `DISCORD_TOKEN`과 사용할 provider 설정을 채운 뒤 다시 실행하세요.

## Slash command 등록

봇이 시작될 때 아래 slash command를 자동으로 sync합니다.

- `/ai`
- `/autochannel add`
- `/autochannel remove`
- `/autochannel list`
- `/autochannel mode`
- `/style set`
- `/style show`
- `/style presets`
- `/style custom`
- `/style add`
- `/style modify`

- `DISCORD_GUILD_ID`를 넣으면 해당 서버에 즉시 등록됩니다. 개발 중에는 이 방식을 추천합니다.
- `DISCORD_GUILD_ID`를 비우면 global command로 등록되며 Discord 반영에 시간이 걸릴 수 있습니다.

서버 ID는 Discord 개발자 모드를 켠 뒤 서버 우클릭으로 복사할 수 있습니다.

## 명령어

### AI에게 질문

```text
/ai message:<내용> style:<선택>
```

- 모든 사용자가 사용할 수 있습니다.
- `style`은 선택값입니다.
- `style`을 넣으면 해당 요청에만 임시 적용되고, 서버 기본 스타일은 바뀌지 않습니다.

예시:

```text
/ai message:오늘 회의 안건 정리해줘 style:teacher
```

### 자동 응답 채널 관리

자동 응답 채널에서는 멘션이나 `/ai` 없이도 일반 메시지에 AI가 응답합니다.

```text
/autochannel add channel:<채널> mode:<always|question_only|keyword> keywords:<선택>
/autochannel remove channel:<채널>
/autochannel list
/autochannel mode channel:<채널> mode:<always|question_only|keyword> keywords:<선택>
```

권한:

- 관리자 또는 **Manage Channels** 권한이 필요합니다.

모드:

- `always`: 등록된 채널의 일반 메시지에 항상 응답합니다.
- `question_only`: 질문처럼 보이는 메시지에만 응답합니다. 물음표와 기본적인 한국어 질문 표현을 감지합니다.
- `keyword`: 지정한 키워드가 들어간 메시지에만 응답합니다. `keywords`는 쉼표로 구분합니다.

예시:

```text
/autochannel add channel:#ai-chat mode:always
/autochannel add channel:#help mode:keyword keywords:질문,도와줘,에러
/autochannel mode channel:#help mode:question_only
/autochannel remove channel:#ai-chat
```

봇 자신이 보낸 메시지와 다른 봇이 보낸 메시지에는 응답하지 않습니다.

### 채팅방 문맥 읽기

일반 AI 답변은 같은 채널의 최근 메시지를 참고할 수 있습니다. 기본값은 최근 20개 메시지, 최대 6000자입니다.

```env
CHANNEL_CONTEXT_MESSAGES=20
CHANNEL_CONTEXT_CHAR_LIMIT=6000
```

- `CHANNEL_CONTEXT_MESSAGES=0` 또는 `CHANNEL_CONTEXT_CHAR_LIMIT=0`으로 설정하면 문맥 읽기를 끌 수 있습니다.
- `/ai`, 봇 멘션, 자동 응답 채널 답변에 모두 적용됩니다.
- 서버 관리 도구 호출도 최근 채널 문맥을 참고할 수 있습니다. 다만 과거 메시지만으로 새 작업을 실행하지 않고 현재 요청에 실행 의도가 있어야 합니다.
- 봇에게 해당 채널의 **View Channel** 및 **Read Message History** 권한이 필요합니다.
- 메시지 내용을 읽으려면 Discord Developer Portal에서 **Message Content Intent**가 켜져 있어야 합니다.

### AI 스타일 관리

```text
/style set style:<default|grok|serious|teacher|coder|korean_friend|custom|서버_커스텀_스타일>
/style show
/style presets
/style custom prompt:<내용>
/style add name:<이름> description:<간단한 설명> prompt:<시스템 프롬프트>
/style modify name:<스타일 이름> description:<선택> prompt:<선택>
```

권한:

- `/style set`, `/style custom`, `/style add`, `/style modify`: 관리자 또는 **Manage Guild** 권한이 필요합니다.
- `/style show`, `/style presets`: 모든 사용자가 볼 수 있습니다.

스타일:

- `default`: 기본 친절한 Discord AI 에이전트
- `grok`: 재치 있고 직설적이지만 무례하지 않은 답변
- `serious`: 차분하고 전문적인 답변
- `teacher`: 개념을 단계별로 설명하는 선생님 스타일
- `coder`: 개발자에게 유용한 코드 중심 답변
- `korean_friend`: 한국어로 편하게 말해주는 친구 스타일
- `custom`: 서버 관리자가 설정한 커스텀 시스템 프롬프트
- `/style add`로 추가한 스타일: 해당 서버에서만 사용할 수 있는 커스텀 스타일

`/style presets`는 각 스타일의 설명과 시스템 프롬프트를 함께 보여줍니다.
`/style add`로 추가한 스타일은 다른 서버에는 보이지 않고, `/ai style:<이름>` 또는 `/style set style:<이름>`에서 autocomplete로 선택할 수 있습니다.

예시:

```text
/style presets
/style custom prompt:너는 우리 서버에서 한국어로 짧고 실용적으로 답하는 AI 도우미다.
/style set style:custom
/style add name:news description:뉴스를 짧게 요약하는 스타일 prompt:핵심 사실, 배경, 영향 순서로 짧게 답하라.
/style modify name:news description:뉴스 요약 특화 prompt:핵심 사실 3개와 다음 확인할 점 1개만 답하라.
/style show
```

서버별 자동 응답 채널과 AI 스타일 설정은 `data/guild_settings.json`에 저장됩니다.

### AI 에이전트 자연어 실행 도구

별도 관리자 slash command를 만들지 않고, `/ai` 또는 봇 멘션으로 자연어 요청을 보내면 AI 에이전트가 실행 가능한 작업인지 판단한 뒤 권한을 확인하고 실행합니다.
작업을 실행할 때는 기존 “생각 중...” 메시지가 `채널 생성 중...` → `채널 생성했습니다.`처럼 먼저 실시간으로 바뀐 뒤 최종 결과가 표시됩니다.

자체 설정 예시:

```text
@봇 #ai-chat 채널을 자동 응답 채널로 등록해줘. 모드는 always
/ai message:#help 자동응답을 keyword 모드로 바꾸고 키워드는 질문,도와줘로 해줘
@봇 서버 기본 AI 스타일을 coder로 바꿔줘
@봇 custom 스타일 프롬프트를 "한국어로 짧게 답해"로 저장해줘
```

서버 관리 예시:

```text
@봇 현재 채널 슬로우모드 5초로 바꿔줘
@봇 ai-chat 텍스트 채널 만들어줘
@봇 #old-channel 삭제해줘
/ai message:#general 채널 주제를 공지와 잡담으로 바꿔줘
@봇 AI Helper 역할 만들어줘. 색상은 #5865F2
@봇 @AI Helper 역할 이름을 Support로 바꿔줘
@봇 @user에게 @AI Helper 역할 추가해줘
@봇 @user에게서 @AI Helper 역할 제거해줘
@봇 sweet 10분 타임아웃 해줘
@봇 이 이미지로 party 이모지 만들어줘
@봇 이 파일로 cheer 사운드 추가해줘
@봇 최근 감사 로그 5개 보여줘
@봇 #news 공지 채널을 #announcements로 팔로우해줘
@봇 #forum에 help 태그 만들어줘
@봇 서버 환영 화면을 켜고 설명을 "처음 오신 분은 안내 채널을 확인해 주세요"로 바꿔줘
@봇 차단 목록 10명 보여줘
```

실행 가능한 작업:

- 자동 응답 채널 추가/제거/목록/모드 변경
- AI 스타일 set/show/presets/custom
- 채널 생성/수정/삭제: 텍스트, 음성, 스테이지, 카테고리, 포럼, 미디어 채널
- 채널 위치, 슬로우모드, 포럼 기본 레이아웃/정렬/태그 요구, 음성 RTC 지역/영상 품질 같은 세부 설정 변경
- 채널 복제, 공지 채널 팔로우, 채널 고정 메시지 조회
- 채널별 역할/멤버 권한 덮어쓰기 설정 또는 제거
- 역할 생성/수정/삭제, 역할 permission/색상/아이콘/위치 수정, 멤버에게 역할 추가/제거
- 이모지 생성/수정/삭제, 스티커 생성/수정/삭제, 사운드보드 사운드 생성/수정/삭제
- 웹훅 생성/목록/삭제, 초대 링크 생성/목록/삭제, 감사 로그 조회
- 서버 이름/설명/아이콘/배너/스플래시/시스템 채널/규칙 채널/업데이트 채널/초대 비활성화 설정 변경
- 환영 화면, 서버 위젯, 서버 온보딩 기본 채널 설정 변경
- 서버 템플릿 생성/목록/동기화/삭제
- AutoMod 키워드 규칙 생성/목록/수정/삭제
- 서버 통합 목록/삭제, 커스텀 초대 URL 조회
- 차단 목록 조회, 사용자 ID/멘션 목록 대량 차단
- 멤버 추방, 차단, 차단 해제, 타임아웃, 별명 변경
- 비활동 멤버 정리(prune)
- 음성 채널 멤버 이동, 서버 음소거, 서버 헤드셋 음소거, 음성 연결 끊기
- 메시지 대량 삭제, 메시지 고정/고정 해제
- 스레드 생성/수정/삭제, 이벤트 생성/수정/취소/삭제
- 포럼 태그 생성/목록/수정/삭제

권한:

- 자동 응답 채널 관리와 채널 작업: 관리자 또는 **Manage Channels** 권한이 필요합니다. 봇에게도 **Manage Channels** 권한이 있어야 합니다.
- AI 스타일 set/custom: 관리자 또는 **Manage Guild** 권한이 필요합니다.
- 역할 생성/수정/삭제/추가/제거: 관리자 또는 **Manage Roles** 권한이 필요합니다. 봇에게도 **Manage Roles** 권한이 있어야 합니다.
- 이모지/스티커/사운드: **Create Expressions** 또는 **Manage Expressions** 권한이 필요합니다. 첨부파일 또는 URL이 필요합니다.
- 웹훅/공지 채널 팔로우: **Manage Webhooks**, 감사 로그: **View Audit Log**, 서버 설정/환영 화면/위젯/온보딩/통합/커스텀 초대: **Manage Server**, 초대: **Create Invite** 권한이 필요합니다.
- AutoMod와 서버 템플릿: **Manage Server** 권한이 필요합니다.
- 멤버 제재/관리: 작업별로 **Kick Members**, **Ban Members**, **Moderate Members**, **Manage Nicknames**, **Move Members**, **Mute Members**, **Deafen Members** 권한이 필요합니다. 대량 차단은 **Ban Members**와 **Manage Server**가 모두 필요합니다.
- 메시지/스레드/이벤트: 작업별로 **Manage Messages**, **Pin Messages**, **Create Public Threads**, **Manage Threads**, **Create Events**, **Manage Events** 권한이 필요합니다.
- 역할/멤버 관리 작업은 Discord 역할 순서 제한을 따릅니다. 봇과 실행 사용자의 가장 높은 역할이 대상보다 높아야 합니다.
- 요청이 설명인지 실행인지 애매하면 실행하지 않고 일반 AI 답변으로 처리합니다.
- 서버 설정 변경, 멤버 제재, 역할/채널 수정 같은 실행 작업은 바로 실행하지 않고 채팅에 작업 내용을 먼저 표시합니다. 요청자가 **수락** 버튼을 눌러야 실행되고, **거절** 또는 시간 초과 시 취소됩니다.
- 멤버 대상은 멘션이나 ID가 가장 정확하지만, 별명/표시 이름/유저명만 알려줘도 가능한 경우 자동으로 찾습니다. 동명이인이 있으면 찾지 못한 것으로 처리해 안전하게 멈춥니다.

### 다른 봇의 slash command 호출

Discord의 application command는 사용자가 Discord 클라이언트에서 호출하면 해당 앱이 interaction을 받는 구조입니다. 일반 Bot API로 이 봇이 다른 봇의 slash command를 대신 실행할 수는 없습니다.

가능한 대안:

- 이 봇 자신의 기능은 `/ai` 또는 멘션으로 들어온 자연어 요청을 내부 도구로 연결해 실행할 수 있습니다.
- 다른 봇이 공식 HTTP API, webhook, 또는 메시지 기반 명령을 제공한다면 별도 adapter로 연동할 수 있습니다.
- 사용자는 Discord 클라이언트에서 다른 봇의 slash command를 직접 실행해야 합니다.

## Provider 설정 예시

### OpenAI

```env
AI_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

### Gemini

```env
AI_PROVIDER=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-1.5-flash
```

### Anthropic Claude

```env
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-3-5-sonnet-latest
```

### Local: Ollama

Ollama의 OpenAI-compatible endpoint를 사용합니다.

```powershell
ollama pull llama3.1
ollama serve
```

```env
AI_PROVIDER=local
LOCAL_BASE_URL=http://localhost:11434/v1
LOCAL_MODEL=llama3.1
LOCAL_API_KEY=
```

`LOCAL_API_KEY`는 Ollama처럼 키가 필요 없는 서버에서는 비워 둬도 됩니다.

### Local: LM Studio

```env
AI_PROVIDER=local
LOCAL_BASE_URL=http://localhost:1234/v1
LOCAL_MODEL=local-model
LOCAL_API_KEY=
```

### Local: vLLM

```env
AI_PROVIDER=local
LOCAL_BASE_URL=http://localhost:8000/v1
LOCAL_MODEL=your-served-model
LOCAL_API_KEY=
```

## 시스템 프롬프트

기본값:

```text
너는 디스코드 서버에서 동작하는 친절하고 유용한 AI 에이전트다. 기본적으로 한국어로 짧지만 실속 있게 답하라. 사용자가 자세히 요청할 때만 길게 설명하라. Discord에서 지원되는 Markdown만 사용하라.
```

`.env`에서 바꿀 수 있습니다.

```env
SYSTEM_PROMPT=너는 한국어로 짧지만 실속 있게 답하는 Discord AI 도우미다.
```

서버별 스타일은 `/style` 명령어로 별도 관리합니다. `SYSTEM_PROMPT`는 기본 바탕 프롬프트이고, `custom` 스타일은 `/style custom`으로 설정한 프롬프트를 사용합니다. `/style add`로 추가한 스타일의 시스템 프롬프트도 이 기본 바탕 프롬프트 위에 스타일 지침으로 적용됩니다.

## Discord Markdown 제한

봇은 Discord에서 안정적으로 보이는 Markdown만 사용하도록 지시하고, 전송 전에 한 번 더 정리합니다.

- 제목은 `#`, `##`, `###`까지만 사용합니다.
- `####` 이상 제목은 굵은 글씨로 바꿉니다.
- `---`, `***`, `___` 같은 수평선은 제거합니다.
- `|`로 만든 Markdown 표는 bullet 목록으로 바꿉니다.
- 코드 블록 안의 내용은 그대로 유지합니다.

## 응답 길이 설정

기본값으로는 앱에서 `AI_MAX_TOKENS` 제한을 걸지 않습니다. OpenAI, Gemini, local OpenAI-compatible provider는 이 값이 비어 있으면 max token 파라미터를 보내지 않고 provider/model 기본 한도를 따릅니다.

```env
AI_MAX_TOKENS=
```

특정 길이로 제한하고 싶을 때만 숫자를 넣으세요.

```env
AI_MAX_TOKENS=4096
```

Claude/Anthropic은 API가 `max_tokens` 값을 필수로 요구하므로, `AI_MAX_TOKENS`가 비어 있으면 내부 호환값을 사용합니다. Discord 메시지 2000자 제한은 봇이 자동으로 여러 메시지로 나누어 전송합니다.

## 확장 포인트

- provider 추가: `bot/providers/base.py`의 `AIProvider` 인터페이스를 구현하고 `bot/providers/__init__.py`의 `create_provider`에 연결합니다.
- tool calling 추가: `bot/agent/tools/`의 `ToolRegistry`에 도구 정의를 등록하고 `AIAgent`에서 provider별 tool 호출 흐름을 확장하면 됩니다.
- 대화 기록 추가: 현재는 단발성 대화만 보냅니다. `AIAgent._build_messages()`에 채널별/유저별 저장소를 연결하면 기존 provider 인터페이스를 유지한 채 확장할 수 있습니다.
- 자동 응답 정책 확장: `bot/discord_bot/settings_store.py`에 설정 필드를 추가하고 `bot/discord_bot/handlers.py`의 자동 응답 판단 로직을 확장하면 됩니다.
- 자체 사용 설명서 업데이트: 명령어, 권한, 실행법이 바뀌면 `bot/agent/self_manual.py`의 `SELF_USAGE_MANUAL`도 함께 업데이트하세요.

## 문제 해결

- 멘션이나 자동 응답 채널이 작동하지 않으면 Discord Developer Portal에서 **Message Content Intent**가 켜져 있는지 확인하세요.
- 별명만으로 멤버를 찾지 못하면 **Server Members Intent**가 켜져 있는지, 봇이 재시작됐는지 확인하세요.
- 채팅방 문맥을 못 읽는 것 같으면 봇 역할에 **View Channel** 및 **Read Message History** 권한이 있는지 확인하세요.
- `/ai`가 보이지 않으면 `DISCORD_GUILD_ID`를 넣고 봇을 재시작해 보세요.
- 답변이 `### 서버 운영`처럼 중간에 끊기면 provider/model 출력 한도에 걸린 것입니다. `.env`에 `AI_MAX_TOKENS`를 낮게 설정했다면 값을 비우거나 더 크게 조정하고 봇을 재시작하세요.
- provider 설정 오류는 실행 시 콘솔에 자세히 출력됩니다. API 키는 `.env`에만 넣고 코드에 하드코딩하지 마세요.
- Gemini에서 `429 RESOURCE_EXHAUSTED` 또는 `Your prepayment credits are depleted`가 나오면 Google AI Studio 프로젝트의 결제/크레딧을 확인하거나 `AI_PROVIDER`를 `openai`, `anthropic`, `local` 중 하나로 바꿔 실행하세요.

