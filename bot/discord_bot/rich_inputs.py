from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import html
from html.parser import HTMLParser
import ipaddress
import json
import mimetypes
import re
import socket
from typing import Any, Iterable
from urllib.parse import parse_qs, urljoin, urlparse

import aiohttp
import discord

from providers.base import MessageContent


MAX_ATTACHMENTS = 4
MAX_URLS = 3
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TEXT_BYTES = 512 * 1024
MAX_SPECIAL_TEXT_BYTES = 3 * 1024 * 1024
MAX_EXTRACTED_TEXT_CHARS = 5000
MAX_TRANSCRIPT_CHARS = 6000
MAX_REDIRECTS = 3
USER_AGENT = "DiscordAIAgentBot/1.0"
SUPPORTED_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}
URL_PATTERN = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)


@dataclass(frozen=True)
class RichInput:
    text: str
    content: MessageContent


@dataclass(frozen=True)
class FetchedUrl:
    url: str
    content_type: str
    data: bytes


async def build_rich_input(
    *,
    prompt: str,
    message: discord.Message | None = None,
    attachments: Iterable[discord.Attachment] = (),
) -> RichInput:
    prompt = prompt.strip()
    image_parts: list[dict[str, Any]] = []
    context_sections: list[str] = []
    skipped: list[str] = []

    attachment_list = list(message.attachments if message else [])
    attachment_list.extend(attachments)
    seen_attachment_ids: set[int] = set()
    unique_attachments: list[discord.Attachment] = []
    for attachment in attachment_list:
        attachment_id = int(getattr(attachment, "id", 0) or 0)
        if attachment_id and attachment_id in seen_attachment_ids:
            continue
        if attachment_id:
            seen_attachment_ids.add(attachment_id)
        unique_attachments.append(attachment)

    for attachment in unique_attachments[:MAX_ATTACHMENTS]:
        mime_type = _attachment_mime_type(attachment)
        if not _is_supported_image(mime_type):
            skipped.append(f"- 첨부파일 `{attachment.filename}`: 지원하는 이미지 형식이 아님")
            continue
        if attachment.size > MAX_IMAGE_BYTES:
            skipped.append(f"- 첨부 이미지 `{attachment.filename}`: 5MB 초과")
            continue

        try:
            data = await attachment.read()
        except (discord.HTTPException, OSError):
            skipped.append(f"- 첨부 이미지 `{attachment.filename}`: 다운로드 실패")
            continue

        if len(data) > MAX_IMAGE_BYTES:
            skipped.append(f"- 첨부 이미지 `{attachment.filename}`: 5MB 초과")
            continue
        image_parts.append(_image_part(data, mime_type))
        context_sections.append(f"첨부 이미지: {attachment.filename}")

    urls = _extract_urls(prompt)
    fetched_count = 0
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=20),
        headers={"User-Agent": USER_AGENT},
    ) as session:
        for url in urls[:MAX_URLS]:
            special_text = await _fetch_special_url_text(session, url)
            if special_text:
                fetched_count += 1
                context_sections.append(special_text)
                continue

            fetched = await _fetch_url(session, url)
            if fetched is None:
                skipped.append(f"- URL `{url}`: 읽을 수 없거나 안전하지 않은 주소")
                continue

            fetched_count += 1
            if _is_supported_image(fetched.content_type):
                if len(fetched.data) > MAX_IMAGE_BYTES:
                    skipped.append(f"- URL 이미지 `{fetched.url}`: 5MB 초과")
                    continue
                image_parts.append(_image_part(fetched.data, fetched.content_type))
                context_sections.append(f"URL 이미지: {fetched.url}")
                continue

            text = _extract_url_text(fetched)
            if text:
                context_sections.append(
                    f"URL 내용 ({fetched.url}, content-type: {fetched.content_type or 'unknown'}):\n{text}"
                )
            else:
                skipped.append(f"- URL `{fetched.url}`: 텍스트를 추출할 수 없는 형식")

    if len(urls) > MAX_URLS:
        skipped.append(f"- URL: 최대 {MAX_URLS}개까지만 읽음")
    if len(unique_attachments) > MAX_ATTACHMENTS:
        skipped.append(f"- 첨부파일: 최대 {MAX_ATTACHMENTS}개까지만 읽음")

    text = prompt or ("첨부된 이미지나 URL을 읽고 설명해줘." if image_parts or fetched_count else "")
    if context_sections:
        text = _append_section(text, "읽은 자료", "\n\n".join(context_sections))
    if skipped:
        text = _append_section(text, "읽지 못한 자료", "\n".join(skipped))

    if not image_parts:
        return RichInput(text=text, content=text)

    parts: list[dict[str, Any]] = []
    if text.strip():
        parts.append({"type": "text", "text": text.strip()})
    parts.extend(image_parts)
    return RichInput(text=text, content=parts)


def _attachment_mime_type(attachment: discord.Attachment) -> str:
    content_type = str(attachment.content_type or "").split(";", 1)[0].strip().lower()
    if content_type:
        return content_type
    guessed, _encoding = mimetypes.guess_type(attachment.filename or "")
    return str(guessed or "").lower()


def _extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in URL_PATTERN.finditer(text):
        url = match.group(0).rstrip(".,!?;:)]}>'\"")
        if url and url not in seen:
            urls.append(url)
            seen.add(url)
    return urls


async def _fetch_special_url_text(session: aiohttp.ClientSession, url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if host in {"youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        return await _fetch_youtube_text(session, url)
    return ""


async def _fetch_youtube_text(session: aiohttp.ClientSession, url: str) -> str:
    if not _youtube_video_id(url):
        return ""

    fetched = await _fetch_url(session, url, byte_limit=MAX_SPECIAL_TEXT_BYTES)
    if fetched is None:
        return ""

    html_text = _decode_bytes(fetched.data)
    player_response = _extract_yt_initial_player_response(html_text)
    if not player_response:
        return ""

    video_details = player_response.get("videoDetails")
    if not isinstance(video_details, dict):
        video_details = {}

    title = str(video_details.get("title") or "").strip()
    author = str(video_details.get("author") or "").strip()
    length_seconds = str(video_details.get("lengthSeconds") or "").strip()
    description = _trim_text(str(video_details.get("shortDescription") or ""))
    transcript = await _fetch_youtube_transcript(session, player_response)

    lines = [f"YouTube 영상 ({fetched.url})"]
    if title:
        lines.append(f"제목: {title}")
    if author:
        lines.append(f"채널: {author}")
    if length_seconds.isdigit():
        lines.append(f"길이: {_format_seconds(int(length_seconds))}")
    if description:
        lines.append(f"설명:\n{description}")
    if transcript:
        lines.append(f"자막:\n{transcript}")
    else:
        lines.append("자막: 공개 자막을 찾지 못해서 영상의 실제 발화 내용은 읽지 못했습니다.")

    return "\n".join(lines)


def _youtube_video_id(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if host == "youtu.be":
        return parsed.path.strip("/").split("/", 1)[0]
    if host.endswith("youtube.com"):
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [""])[0]
        if parsed.path.startswith(("/shorts/", "/embed/")):
            parts = parsed.path.strip("/").split("/")
            return parts[1] if len(parts) > 1 else ""
    return ""


def _extract_yt_initial_player_response(html_text: str) -> dict[str, Any]:
    marker = "ytInitialPlayerResponse"
    marker_index = html_text.find(marker)
    while marker_index != -1:
        equals_index = html_text.find("=", marker_index)
        brace_index = html_text.find("{", equals_index)
        if equals_index == -1 or brace_index == -1:
            return {}
        json_text = _extract_balanced_json_object(html_text, brace_index)
        if json_text:
            try:
                data = json.loads(json_text)
            except json.JSONDecodeError:
                marker_index = html_text.find(marker, brace_index + 1)
                continue
            if isinstance(data, dict):
                return data
        marker_index = html_text.find(marker, brace_index + 1)
    return {}


def _extract_balanced_json_object(text: str, start: int) -> str:
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""


async def _fetch_youtube_transcript(session: aiohttp.ClientSession, player_response: dict[str, Any]) -> str:
    captions = player_response.get("captions")
    if not isinstance(captions, dict):
        return ""
    track_list = captions.get("playerCaptionsTracklistRenderer")
    if not isinstance(track_list, dict):
        return ""
    tracks = track_list.get("captionTracks")
    if not isinstance(tracks, list):
        return ""

    track = _select_caption_track(tracks)
    base_url = str(track.get("baseUrl") or "") if track else ""
    if not base_url or not await _is_safe_public_url(base_url):
        return ""

    separator = "&" if "?" in base_url else "?"
    transcript_url = f"{base_url}{separator}fmt=json3"
    try:
        async with session.get(transcript_url, allow_redirects=True) as response:
            if response.status < 200 or response.status >= 300:
                return ""
            data = await _read_limited(response, MAX_TEXT_BYTES)
    except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError):
        return ""
    if data is None:
        return ""

    return _trim_transcript(_extract_json3_transcript(_decode_bytes(data)))


def _select_caption_track(tracks: list[Any]) -> dict[str, Any]:
    valid_tracks = [track for track in tracks if isinstance(track, dict) and track.get("baseUrl")]
    for language_code in ("ko", "en"):
        for track in valid_tracks:
            if str(track.get("languageCode") or "").casefold().startswith(language_code):
                return track
    return valid_tracks[0] if valid_tracks else {}


def _extract_json3_transcript(text: str) -> str:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ""
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return ""

    chunks: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        segs = event.get("segs")
        if not isinstance(segs, list):
            continue
        line = "".join(
            str(seg.get("utf8") or "")
            for seg in segs
            if isinstance(seg, dict)
        ).strip()
        if line:
            chunks.append(line)
    return " ".join(chunks)


def _trim_transcript(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= MAX_TRANSCRIPT_CHARS:
        return normalized
    return f"{normalized[: MAX_TRANSCRIPT_CHARS - 1]}..."


async def _fetch_url(
    session: aiohttp.ClientSession,
    url: str,
    *,
    byte_limit: int | None = None,
) -> FetchedUrl | None:
    current_url = url
    for _redirect in range(MAX_REDIRECTS + 1):
        if not await _is_safe_public_url(current_url):
            return None

        try:
            async with session.get(current_url, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        return None
                    current_url = urljoin(current_url, location)
                    continue

                if response.status < 200 or response.status >= 300:
                    return None

                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                limit = byte_limit or (MAX_IMAGE_BYTES if content_type.startswith("image/") else MAX_TEXT_BYTES)
                data = await _read_limited(response, limit)
                if data is None:
                    return None
                return FetchedUrl(url=str(response.url), content_type=content_type, data=data)
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError):
            return None

    return None


async def _read_limited(response: aiohttp.ClientResponse, limit: int) -> bytes | None:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(32 * 1024):
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def _is_safe_public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            parsed.hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        return False

    addresses = {info[4][0] for info in infos}
    if not addresses:
        return False
    return all(_is_public_ip(address) for address in addresses)


def _is_public_ip(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return bool(parsed.is_global)


def _image_part(data: bytes, mime_type: str) -> dict[str, Any]:
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime_type};base64,{encoded}",
        },
    }


def _is_supported_image(mime_type: str) -> bool:
    return mime_type in SUPPORTED_IMAGE_MIME_TYPES


def _extract_url_text(fetched: FetchedUrl) -> str:
    content_type = fetched.content_type
    if content_type in {"text/html", "application/xhtml+xml"}:
        html_text = _decode_bytes(fetched.data)
        parser = _NamuWikiTextParser() if _is_namuwiki_url(fetched.url) else _VisibleTextParser()
        parser.feed(html_text)
        return _trim_text(parser.text)

    if content_type.startswith("text/") or content_type in {
        "application/json",
        "application/xml",
        "application/rss+xml",
        "application/atom+xml",
    }:
        return _trim_text(_decode_bytes(fetched.data))

    return ""


def _is_namuwiki_url(url: str) -> bool:
    return (urlparse(url).hostname or "").casefold().endswith("namu.wiki")


def _decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8", "cp949", "euc-kr", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _trim_text(text: str) -> str:
    normalized = html.unescape(re.sub(r"\s+", " ", text)).strip()
    if len(normalized) <= MAX_EXTRACTED_TEXT_CHARS:
        return normalized
    return f"{normalized[: MAX_EXTRACTED_TEXT_CHARS - 1]}..."


def _format_seconds(seconds: int) -> str:
    minutes, second = divmod(seconds, 60)
    hour, minute = divmod(minutes, 60)
    if hour:
        return f"{hour}:{minute:02d}:{second:02d}"
    return f"{minute}:{second:02d}"


def _append_section(base: str, title: str, body: str) -> str:
    base = base.strip()
    section = f"[{title}]\n{body.strip()}"
    return f"{base}\n\n{section}" if base else section


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._chunks: list[str] = []

    @property
    def text(self) -> str:
        return " ".join(self._chunks)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self._chunks.append(text)


class _NamuWikiTextParser(_VisibleTextParser):
    _ignored_exact = {
        "최근 변경",
        "최근 토론",
        "특수 기능",
        "토론",
        "역사",
        "편집",
        "편집 요청",
        "닫기",
        "분류",
    }
    _ignored_prefixes = (
        "편집 권한이 부족합니다",
        "해당 문서의 ACL",
        "편집 요청 도움말",
    )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"head", "script", "style", "noscript", "svg", "nav", "button"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"head", "script", "style", "noscript", "svg", "nav", "button"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if not text or text in self._ignored_exact:
            return
        if any(text.startswith(prefix) for prefix in self._ignored_prefixes):
            return
        self._chunks.append(text)
