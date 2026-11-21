"""Numbered citations for Markdown / Word / PDF export.

Drafts keep pandoc-style markers ``[@Key]`` and a reference block of ``[@Key]: entry``
lines. For export, keys are numbered in order of first citation, markers become
``[1]`` / ``[1, 2]`` and the block becomes a numbered list. Entries that are never
cited are dropped; repeated reference blocks (one per language half) are merged.
"""

from __future__ import annotations

import re

_MARKER = re.compile(r"\[(@[^\]]+)\]")
_KEY = re.compile(r"@([A-Za-z0-9_:.-]+)")
_REF_LINE = re.compile(r"^\[@([A-Za-z0-9_:.-]+)\]:\s*(.+?)\s*$", re.M)
_HEADING = re.compile(r"^\s*(?:#+\s*)?(References|参考文献)\s*$", re.M)


def number_citations(markdown: str, heading: str | None = None) -> str:
    text = markdown or ""
    entries: dict[str, str] = {}
    for match in _REF_LINE.finditer(text):
        entries.setdefault(match.group(1), match.group(2))
    body = _REF_LINE.sub("", text)
    first_heading = _HEADING.search(body)
    if entries:
        body = _HEADING.sub("", body)
    order: list[str] = []
    for match in _MARKER.finditer(body):
        for key in _KEY.findall(match.group(1)):
            if key not in order:
                order.append(key)
    if not order:
        return re.sub(r"\n{3,}", "\n\n", body).rstrip() + "\n" if entries else text
    number = {key: n for n, key in enumerate(order, 1)}

    def replace(match: re.Match[str]) -> str:
        keys = _KEY.findall(match.group(1))
        if not keys:
            return match.group(0)
        return "[" + ", ".join(str(number[k]) for k in keys) + "]"

    body = _MARKER.sub(replace, body)
    body = re.sub(r"\n{3,}", "\n\n", body).rstrip()
    heading = heading or (first_heading.group(1) if first_heading else "References")
    listed = [f"{number[key]}. {entries[key]}" for key in order if key in entries]
    missing = [key for key in order if key not in entries]
    if missing:
        listed += [f"{number[key]}. [reference details missing for {key}]" for key in missing]
    return f"{body}\n\n# {heading}\n\n" + "\n".join(listed) + "\n"
