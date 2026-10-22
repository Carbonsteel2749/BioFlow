"""Shared text helpers for section drafting."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def clean_text(value: str) -> str:
    return " ".join((value or "").split())


def display_term(value: str) -> str:
    return clean_text(value).replace("_", " ").replace("-", " ")


def unique_values(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = clean_text(value)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            out.append(cleaned)
            seen.add(key)
    return out


def format_method_value(value: Any) -> str:
    if isinstance(value, dict):
        parts = [f"{display_term(str(k))}={v}" for k, v in value.items()]
        return "; ".join(parts)
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def truncate(value: str, limit: int = 280) -> str:
    text = clean_text(value)
    if len(text) <= limit:
        return text
    shortened = text[: limit - 1]
    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0]
    return f"{shortened.rstrip()}…"
