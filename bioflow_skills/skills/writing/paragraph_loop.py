"""Plan → write → verify → revise loop shared by every planned section.

Each paragraph of a plan (from ``section-planner``) is written by ``paragraph-writer``
and checked by ``claim-verifier``; failed paragraphs are revised up to
``MAX_REVISIONS`` times, then sentences failing deterministic checks are dropped.
Figures attached to a paragraph (``paragraph['figures']``, each with an ``embed``
markdown block) are placed right after it.

Section skills added later can call :func:`write_plan` instead of re-implementing this.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .claim_verifier import citation_keys, deterministic_issues, drop_sentences, split_sentences, verify
from .paragraph_writer import SYSTEM, build_prompt, clean, last_sentence

MAX_REVISIONS = 2
PRONOUN_START = re.compile(r"^(?:It|This|These|They|That|Those|Such|Its|Their)\b")


def with_dependents(text: str, bad: set[int]) -> set[int]:
    """A sentence opening with a pronoun goes with the dropped sentence it refers back to."""

    sentences = split_sentences(text)
    out = set(bad)
    for index in range(1, len(sentences)):
        if index - 1 in out and PRONOUN_START.match(sentences[index]):
            out.add(index)
    return out


def _write(model, paragraph, section, title, previous, style_rules, draft="", issues=None) -> str:
    prompt = build_prompt(paragraph, section=section, title=title, previous=previous, draft=draft,
                          issues=issues, style_rules=style_rules)
    text = clean(model.generate(prompt, system=SYSTEM))
    if not text:
        raise ValueError("model returned an empty paragraph")
    return text


def write_plan(plan: list[dict[str, Any]], model: Any, *, section: str, title: str = "", style_rules: str = "",
               judge: bool = True, on_paragraph: Callable[[str, int, int], None] | None = None) -> dict[str, Any]:
    blocks: list[str] = []
    trace: list[dict[str, Any]] = []
    warnings: list[str] = []
    figures: list[dict[str, Any]] = []
    previous = ""
    for index, paragraph in enumerate(plan, start=1):
        if on_paragraph:
            on_paragraph(paragraph["key"], index, len(plan))
        text = _write(model, paragraph, section, title, previous, style_rules)
        check = verify(text, paragraph, model if judge else None)
        rounds = [{"issues": check["issues"], "judge": check["judge"]}]
        revisions = 0
        while not check["ok"] and revisions < MAX_REVISIONS:
            revisions += 1
            text = _write(model, paragraph, section, title, previous, style_rules, draft=text, issues=check["issues"])
            check = verify(text, paragraph, model if judge else None)
            rounds.append({"issues": check["issues"], "judge": check["judge"]})
        dropped: list[int] = []
        if not check["ok"]:
            hard, hard_bad = deterministic_issues(text, paragraph)
            if hard_bad:
                hard_bad = with_dependents(text, hard_bad)
                dropped = sorted(hard_bad)
                text = drop_sentences(text, hard_bad)
            soft = [issue for issue in check["issues"] if issue not in hard]
            if soft:
                warnings.append(f"{section}/{paragraph['key']}: please review — " + "; ".join(soft[:3]))
        if text.strip():
            blocks.append(text.strip())
            previous = last_sentence(text)
        for figure in paragraph.get("figures") or []:
            blocks.append(figure["embed"].strip())
            figures.append(figure)
        trace.append({"key": paragraph["key"], "fact_ids": paragraph.get("fact_ids", []), "revisions": revisions,
                      "rounds": rounds, "dropped_sentences": dropped, "final_ok": check["ok"],
                      "words": len(text.split()), "citations": sorted(set(citation_keys(text))),
                      "figures": [f["number"] for f in paragraph.get("figures") or []]})
    markdown = "\n\n".join(blocks) + "\n"
    return {"markdown": markdown, "paragraphs": trace, "warnings": warnings, "figures": figures,
            "citations": sorted(set(citation_keys(markdown)))}
