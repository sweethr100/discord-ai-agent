DISCORD_MESSAGE_LIMIT = 2000
DEFAULT_SAFE_LIMIT = 1900


def split_discord_message(
    text: str,
    *,
    limit: int = DEFAULT_SAFE_LIMIT,
) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []

    if limit <= 0 or limit > DISCORD_MESSAGE_LIMIT:
        raise ValueError("limit은 1 이상 2000 이하여야 합니다.")

    chunks: list[str] = []
    current = ""

    for segment in _split_preserving_paragraphs(text):
        if len(segment) > limit:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_hard_split(segment, limit))
            continue

        candidate = f"{current}\n\n{segment}" if current else segment
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current.strip())
            current = segment

    if current:
        chunks.append(current.strip())

    return chunks


def _split_preserving_paragraphs(text: str) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]
    if paragraphs:
        return paragraphs
    return [text]


def _hard_split(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    current = ""

    for line in text.splitlines(keepends=True):
        if len(line) > limit:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(
                line[index : index + limit].strip()
                for index in range(0, len(line), limit)
                if line[index : index + limit].strip()
            )
            continue

        if len(current) + len(line) <= limit:
            current += line
        else:
            if current:
                chunks.append(current.strip())
            current = line

    if current:
        chunks.append(current.strip())

    return chunks
