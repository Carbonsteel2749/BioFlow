"""Split a section into English, Chinese, and shared figure blocks."""

from __future__ import annotations

import re

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_CJK = re.compile(r"[\u4e00-\u9fff]")

_EN_LABELS = {
    "english",
    "abstract",
    "introduction",
    "methods",
    "results",
    "discussion",
    "conclusion",
    "conclusions",
    "back matter",
}
_ZH_LABELS = {
    "中文",
    "英文",
    "chinese",
    "摘要",
    "引言",
    "方法",
    "结果",
    "讨论",
    "结论",
    "文后",
    "文后事项",
}

_MARKER_EN = "<!-- lang:en -->"
_MARKER_ZH = "<!-- lang:zh -->"
_MARKER_SHARED = "<!-- lang:shared -->"


def _is_cjk_block(text: str) -> bool:
    visible = re.sub(r"`[^`]+`", " ", text)
    visible = re.sub(r"cat_[A-Za-z0-9_]+", " ", visible)
    return len(_CJK.findall(visible)) >= 8


def _language_label(text: str) -> str | None:
    piece = text.strip()
    if not piece or "\n" in piece:
        return None
    match = _HEADING.match(piece)
    label = match.group(2).strip().casefold() if match else piece.casefold()
    if label in _EN_LABELS:
        return "en"
    if label in _ZH_LABELS:
        return "zh"
    return None


def _is_asset(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    return all(
        line.startswith("![")
        or line.startswith("**Figure")
        or line.startswith("**Table")
        or line.startswith("> ")
        for line in lines
    )


def _join(blocks: list[str]) -> str:
    return "\n\n".join(block.strip() for block in blocks if block.strip()).strip()


def _split_marked(markdown: str) -> tuple[str, str, str]:
    english = _between(markdown, _MARKER_EN, _MARKER_ZH)
    if _MARKER_SHARED in markdown:
        chinese = _between(markdown, _MARKER_ZH, _MARKER_SHARED)
        shared = markdown.split(_MARKER_SHARED, 1)[1]
    else:
        chinese = markdown.split(_MARKER_ZH, 1)[1]
        shared = ""
    return english.strip(), chinese.strip(), shared.strip()


def _between(text: str, start: str, end: str) -> str:
    chunk = text.split(start, 1)[1]
    return chunk.split(end, 1)[0]


def _iter_pieces(text: str):
    """Yield headings and asset lines separately from the prose around them."""

    buffer: list[str] = []

    def flush():
        piece = "\n".join(buffer).strip()
        buffer.clear()
        if piece:
            yield piece

    for line in text.splitlines():
        stripped = line.strip()
        if (
            _HEADING.match(stripped)
            or _language_label(stripped) is not None
            or stripped.startswith(("![", "**Figure", "**Table", "> "))
        ):
            yield from flush()
            if stripped:
                yield stripped
            continue
        if not stripped:
            yield from flush()
            continue
        buffer.append(line.rstrip())
    yield from flush()


def split_bilingual(markdown: str) -> tuple[str, str, str]:
    """Return English prose, Chinese prose, and figure/table blocks."""

    text = (markdown or "").strip()
    if _MARKER_EN in text and _MARKER_ZH in text:
        return _split_marked(text)

    english: list[str] = []
    chinese: list[str] = []
    shared: list[str] = []
    mode: str | None = None
    for piece in _iter_pieces(text):
        label = _language_label(piece)
        if label is not None:
            mode = label
            continue
        if _is_asset(piece):
            shared.append(piece)
            continue
        if _HEADING.match(piece) and "\n" not in piece:
            english.append(piece)
            chinese.append(piece)
            mode = None
            continue
        if _is_cjk_block(piece):
            chinese.append(piece)
        elif mode == "zh":
            chinese.append(piece)
        else:
            english.append(piece)
    return _join(english), _join(chinese), _join(shared)


def join_bilingual(english: str, chinese: str, shared: str = "") -> str:
    """Store both columns so the next open restores the same split."""

    parts = [
        _MARKER_EN,
        (english or "").strip(),
        "",
        _MARKER_ZH,
        (chinese or "").strip(),
    ]
    if (shared or "").strip():
        parts.extend(["", _MARKER_SHARED, shared.strip()])
    return "\n".join(parts).strip() + "\n"


def select_language(markdown: str, language: str) -> str:
    """Keep one language and the shared figures."""

    choice = (language or "both").strip().lower()
    if choice in {"", "both", "en+zh"}:
        return markdown or ""
    english, chinese, shared = split_bilingual(markdown)
    body = english if choice == "en" else chinese
    if shared:
        body = f"{body}\n\n{shared}".strip() if body else shared
    return body.strip()
