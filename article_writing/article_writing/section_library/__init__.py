"""Section library and article-type templates (JSON, editable without code changes).

``sections.json`` lists every section a manuscript may contain; ``article_types.json``
gives default section lists per article type. ``contracts.SectionId`` is generated
from the library, so a new section only needs a library entry (and, to be written by
a model, a registered skill that follows SECTION_SKILLS.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
LEGACY_IDS = ("abstract", "introduction", "methods", "results", "discussion", "conclusion", "back_matter")
MODES = {"builtin", "skill", "author_input"}
PARTS = {"front", "body", "back"}


@dataclass(frozen=True)
class SectionSpec:
    id: str
    title_en: str
    title_zh: str
    part: str
    mode: str
    skill: str | None
    write_rank: int
    purpose: str
    depends_on: tuple[str, ...] = ()
    needs_citations: bool = False
    index: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title_en": self.title_en, "title_zh": self.title_zh, "part": self.part,
                "mode": self.mode, "skill": self.skill, "write_rank": self.write_rank, "purpose": self.purpose,
                "depends_on": list(self.depends_on), "needs_citations": self.needs_citations}


@dataclass(frozen=True)
class ArticleType:
    id: str
    title_en: str
    title_zh: str
    sections: tuple[str, ...]
    examples: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title_en": self.title_en, "title_zh": self.title_zh,
                "sections": list(self.sections), "examples": self.examples}


def _read(name: str) -> dict[str, Any]:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def sections() -> dict[str, SectionSpec]:
    out: dict[str, SectionSpec] = {}
    for index, row in enumerate(_read("sections.json")["sections"]):
        spec = SectionSpec(
            id=str(row["id"]), title_en=str(row["title_en"]), title_zh=str(row["title_zh"]),
            part=str(row["part"]), mode=str(row["mode"]), skill=row.get("skill") or None,
            write_rank=int(row["write_rank"]), purpose=str(row.get("purpose") or ""),
            depends_on=tuple(row.get("depends_on") or ()), needs_citations=bool(row.get("needs_citations")),
            index=index)
        if spec.id in out:
            raise ValueError(f"duplicate section id in library: {spec.id}")
        if spec.mode not in MODES or spec.part not in PARTS:
            raise ValueError(f"section {spec.id}: bad mode/part")
        out[spec.id] = spec
    missing = [i for i in LEGACY_IDS if i not in out]
    if missing:
        raise ValueError(f"library must keep the legacy sections: {missing}")
    return out


def section_ids() -> list[str]:
    """Legacy ids first (stable enum order), then library order."""

    lib = sections()
    return list(LEGACY_IDS) + [i for i in lib if i not in LEGACY_IDS]


def get(section_id: str) -> SectionSpec:
    try:
        return sections()[str(getattr(section_id, "value", section_id))]
    except KeyError as error:
        raise KeyError(f"unknown section: {section_id}") from error


@lru_cache(maxsize=1)
def article_types() -> dict[str, ArticleType]:
    data = _read("article_types.json")
    out = {}
    for row in data["types"]:
        kind = ArticleType(id=row["id"], title_en=row["title_en"], title_zh=row["title_zh"],
                           sections=tuple(row["sections"]), examples=row.get("examples", ""))
        unknown = [s for s in kind.sections if s not in sections()]
        if unknown:
            raise ValueError(f"article type {kind.id} uses unknown sections: {unknown}")
        out[kind.id] = kind
    return out


def default_article_type() -> str:
    return _read("article_types.json").get("default", "research_article")


def resolve_sections(article_type: str = "", chosen: Iterable[str] | None = None) -> list[str]:
    """User choice wins; otherwise the article type's default; otherwise the legacy order."""

    chosen = [str(getattr(s, "value", s)).strip() for s in (chosen or []) if str(getattr(s, "value", s)).strip()]
    if not chosen:
        kind = article_types().get(article_type or default_article_type())
        if article_type and kind is None:
            raise ValueError(f"unknown article type: {article_type}")
        chosen = list(kind.sections) if kind else list(LEGACY_IDS)
    unknown = [s for s in chosen if s not in sections()]
    if unknown:
        raise ValueError(f"unknown sections: {unknown}")
    duplicates = sorted({s for s in chosen if chosen.count(s) > 1})
    if duplicates:
        raise ValueError(f"duplicate sections: {duplicates}")
    return chosen


def writing_order(display_order: Iterable[str]) -> list[str]:
    """Write data-bearing sections first and summaries last, whatever the display order."""

    lib = sections()
    ids = [str(getattr(s, "value", s)) for s in display_order]
    return sorted(ids, key=lambda s: (lib[s].write_rank, lib[s].index))


def catalogue(registered_skills: Iterable[str] = ()) -> dict[str, Any]:
    """Library listing for the API, with whether each section's skill is available."""

    registered = set(registered_skills)
    rows = []
    for spec in sections().values():
        row = spec.as_dict()
        if spec.mode == "builtin":
            row["status"] = "ready"
        elif spec.mode == "author_input":
            row["status"] = "author_input"
        else:
            row["status"] = "ready" if spec.skill in registered else "skill_missing"
        rows.append(row)
    return {"sections": rows, "article_types": [t.as_dict() for t in article_types().values()],
            "default_article_type": default_article_type()}
