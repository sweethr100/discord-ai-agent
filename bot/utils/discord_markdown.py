from __future__ import annotations

import re


TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


def normalize_discord_markdown(text: str) -> str:
    """Convert common Markdown that Discord does not render well."""
    text = (text or "").strip()
    if not text:
        return ""

    lines = text.splitlines()
    converted: list[str] = []
    index = 0
    in_code_block = False

    while index < len(lines):
        line = lines[index]
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            converted.append(line.rstrip())
            index += 1
            continue

        if in_code_block:
            converted.append(line.rstrip())
            index += 1
            continue

        table_block = _collect_table(lines, index)
        if table_block:
            converted.extend(_table_to_bullets(table_block))
            index += len(table_block)
            continue

        normalized = _normalize_line(line)
        if normalized is not None:
            converted.append(normalized)
        index += 1

    return _collapse_blank_lines("\n".join(converted)).strip()


def _normalize_line(line: str) -> str | None:
    stripped = line.strip()
    if stripped in {"---", "***", "___"}:
        return None

    heading = re.match(r"^(#{4,})\s+(.+)$", line)
    if heading:
        return f"**{heading.group(2).strip()}**"

    return line.rstrip()


def _collect_table(lines: list[str], start: int) -> list[str]:
    if start + 1 >= len(lines):
        return []

    first = lines[start]
    second = lines[start + 1]
    if not _looks_like_table_row(first) or not TABLE_SEPARATOR_RE.match(second):
        return []

    table = [first, second]
    index = start + 2
    while index < len(lines) and _looks_like_table_row(lines[index]):
        table.append(lines[index])
        index += 1
    return table


def _looks_like_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.count("|") >= 2 and not stripped.startswith("```")


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


def _table_to_bullets(table: list[str]) -> list[str]:
    headers = _split_table_row(table[0])
    rows = [_split_table_row(row) for row in table[2:]]
    output: list[str] = []

    for row in rows:
        if not any(row):
            continue
        title = row[0] if row else ""
        details = []
        for header, cell in zip(headers[1:], row[1:]):
            if cell:
                details.append(f"{header}: {cell}")
        if title and details:
            output.append(f"- **{title}**: {', '.join(details)}")
        elif title:
            output.append(f"- {title}")
        elif details:
            output.append(f"- {', '.join(details)}")

    return output or [" ".join(headers)]


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text)
