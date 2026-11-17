"""Plan → write → verify → revise, one paragraph at a time.

Every step is a skill from ``bioflow_skills``:

* ``section-planner``  splits the chapter and fixes facts and allowed readings,
* ``paragraph-writer`` writes (and later revises) one paragraph with the model,
* ``claim-verifier``   checks numbers, wording and support sentence by sentence.

After ``MAX_REVISIONS`` failed rounds only the sentences that fail the
deterministic checks are removed; model-judged problems are kept as warnings
so the author can decide.
"""

from __future__ import annotations

from typing import Any, Callable

from article_writing.contracts import SectionId
from article_writing.skills.bio_skills import _run


PLANNER_SKILL = "section-planner"
WRITER_SKILL = "paragraph-writer"
VERIFIER_SKILL = "claim-verifier"
PLANNED_SECTIONS = frozenset({SectionId.introduction, SectionId.results, SectionId.discussion,
                              SectionId.abstract, SectionId.conclusion})
MAX_REVISIONS = 2


def _brief_payload(brief: Any) -> dict[str, str]:
    return {"title": str(getattr(brief, "title", "") or ""),
            "research_question": str(getattr(brief, "research_question", "") or "")}


def write_planned_section(
    section: SectionId,
    ledger: dict[str, Any],
    model: Any,
    brief: Any,
    *,
    judge: bool = True,
    style_rules: str = "",
    cards: list[dict[str, Any]] | None = None,
    on_paragraph: Callable[[str, int, int], None] | None = None,
) -> tuple[str, dict[str, Any], list[str]]:
    from bioflow_skills.skills.writing.claim_verifier import citation_keys, deterministic_issues, drop_sentences
    from bioflow_skills.skills.writing.paragraph_writer import last_sentence

    plan = _run(PLANNER_SKILL, {"ledger": ledger, "section": section.value, "brief": _brief_payload(brief),
                                "cards": cards or []})
    title = _brief_payload(brief)["title"]
    texts: list[str] = []
    trace: list[dict[str, Any]] = []
    warnings: list[str] = []
    previous = ""
    paragraphs = plan.get("paragraphs", [])
    for index, paragraph in enumerate(paragraphs, start=1):
        if on_paragraph:
            on_paragraph(paragraph["key"], index, len(paragraphs))
        text = _run(WRITER_SKILL, {"model": model, "paragraph": paragraph, "section": section.value,
                                   "title": title, "previous": previous, "style_rules": style_rules})["text"]
        check = _run(VERIFIER_SKILL, {"text": text, "paragraph": paragraph, "model": model if judge else None})
        rounds: list[dict[str, Any]] = [{"issues": check["issues"], "judge": check["judge"]}]
        revisions = 0
        while not check["ok"] and revisions < MAX_REVISIONS:
            revisions += 1
            text = _run(WRITER_SKILL, {"model": model, "paragraph": paragraph, "section": section.value,
                                       "title": title, "previous": previous, "draft": text,
                                       "issues": check["issues"], "style_rules": style_rules})["text"]
            check = _run(VERIFIER_SKILL, {"text": text, "paragraph": paragraph, "model": model if judge else None})
            rounds.append({"issues": check["issues"], "judge": check["judge"]})
        dropped: list[int] = []
        if not check["ok"]:
            hard, hard_bad = deterministic_issues(text, paragraph)
            if hard_bad:
                dropped = sorted(hard_bad)
                text = drop_sentences(text, hard_bad)
            soft = [issue for issue in check["issues"] if issue not in hard]
            if soft:
                warnings.append(f"{section.value}/{paragraph['key']}: please review — " + "; ".join(soft[:3]))
        if text.strip():
            texts.append(text.strip())
            previous = last_sentence(text)
        trace.append({"key": paragraph["key"], "fact_ids": paragraph["fact_ids"], "revisions": revisions,
                      "rounds": rounds, "dropped_sentences": dropped, "final_ok": check["ok"],
                      "words": len(text.split()), "citations": sorted(set(citation_keys(text)))})
    markdown = "\n\n".join(texts) + "\n"
    meta = {"source": "planned_paragraphs", "skills": [PLANNER_SKILL, WRITER_SKILL, VERIFIER_SKILL],
            "paragraphs": trace, "citations": sorted(set(citation_keys(markdown)))}
    return markdown, meta, warnings
