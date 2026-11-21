"""Plan → write → verify → revise, one paragraph at a time.

Every step is a skill from ``bioflow_skills``:

* ``section-planner``  splits the chapter and fixes facts and allowed readings,
* ``paragraph-writer`` writes (and later revises) one paragraph with the model,
* ``claim-verifier``   checks numbers, wording and support sentence by sentence.

The loop itself lives in ``bioflow_skills.skills.writing.paragraph_loop`` so that
library section skills reuse exactly the same behaviour.
"""

from __future__ import annotations

from typing import Any, Callable

from article_writing.contracts import FigureRef, SectionId
from article_writing.skills.bio_skills import _run, ensure_skills_path


PLANNER_SKILL = "section-planner"
WRITER_SKILL = "paragraph-writer"
VERIFIER_SKILL = "claim-verifier"
PLANNED_SECTIONS = frozenset({SectionId.introduction, SectionId.results, SectionId.discussion,
                              SectionId.abstract, SectionId.conclusion})


def _brief_payload(brief: Any) -> dict[str, str]:
    return {"title": str(getattr(brief, "title", "") or ""),
            "research_question": str(getattr(brief, "research_question", "") or "")}


def figure_refs(figures: list[dict[str, Any]]) -> list[FigureRef]:
    return [FigureRef(figure_id=f["figure_id"], path=f["path"], caption=f["caption"], kind="figure") for f in figures]


def write_planned_section(
    section: SectionId,
    ledger: dict[str, Any],
    model: Any,
    brief: Any,
    *,
    judge: bool = True,
    style_rules: str = "",
    cards: list[dict[str, Any]] | None = None,
    figures: list[dict[str, Any]] | None = None,
    on_paragraph: Callable[[str, int, int], None] | None = None,
) -> tuple[str, dict[str, Any], list[str]]:
    ensure_skills_path()
    from bioflow_skills.skills.writing.paragraph_loop import write_plan

    plan = _run(PLANNER_SKILL, {"ledger": ledger, "section": section.value, "brief": _brief_payload(brief),
                                "cards": cards or [], "figures": figures or []})
    result = write_plan(plan.get("paragraphs", []), model, section=section.value,
                        title=_brief_payload(brief)["title"], style_rules=style_rules, judge=judge,
                        on_paragraph=on_paragraph)
    meta = {"source": "planned_paragraphs", "skills": [PLANNER_SKILL, WRITER_SKILL, VERIFIER_SKILL],
            "paragraphs": result["paragraphs"], "citations": result["citations"],
            "figure_refs": [{"figure_id": f["figure_id"], "number": f["number"]} for f in result["figures"]]}
    meta["_figure_refs"] = figure_refs(result["figures"])
    return result["markdown"], meta, result["warnings"]
