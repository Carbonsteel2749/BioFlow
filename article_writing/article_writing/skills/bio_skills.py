"""Skill-driven steps for metagenomic result packages.

These skills always run when a BioLLM material package is present. They do the
work a small model does badly: arithmetic and provenance bookkeeping.

* ``bio-fact-ledger`` computes every citable number in code.
* ``methods-from-provenance`` writes Methods from recorded versions and parameters.

The model then writes the other chapters from ledger lines and never derives
a range, mean, total or percentage itself.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from article_writing.contracts import SectionId


LEDGER_SKILL = "bio-fact-ledger"
METHODS_SKILL = "methods-from-provenance"
CORE_SKILLS = (LEDGER_SKILL, METHODS_SKILL)

_BIOFLOW_ROOT = Path(__file__).resolve().parents[3]

# Fact groups each chapter may draw on. Per-sample rows stay out of prose
# prompts; the summary facts already name the samples at each extreme.
_RESULT_BLOCKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Sequencing depth and read filtering", ("cohort", "qc_summary")),
    ("Base quality after filtering", ("read_quality",)),
    ("Host read removal", ("host",)),
    ("Species composition", ("taxonomy_cohort",)),
    ("Within-sample diversity", ("diversity",)),
    ("Functional profiles", ("function",)),
)
_HEADLINE_IDS = (
    "COHORT.sample_count", "QC.raw_reads.range", "READQ.q30_after.range",
    "QC.host_removed_pct.range", "TAX.cohort_top.1", "TAX.cohort_top.2", "TAX.cohort_top.3",
    "TAX.dominant_summary", "DIV.shannon.range", "FUNC.pathways_detected.range",
)
_SKIP_SUFFIX = (".median",)

NUMBER_RULES = (
    "NUMBER RULES: every number you write must be copied exactly from a fact line below, "
    "including its unit and the sample named with it. Never compute a range, mean, total, "
    "ratio or percentage yourself. A range is written only when a fact line gives that range. "
    "Do not mention a fact id in the prose. Reads and read pairs are different units."
)
SCOPE_RULES = (
    "SCOPE: this is a descriptive study of the listed samples. No group comparison or "
    "statistical test was run, so do not write that anything was significant, higher in a "
    "group, enriched, depleted, associated with a condition, or caused by anything."
)


def ensure_skills_path() -> None:
    root = str(_BIOFLOW_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def citation_keys(text: str) -> list[str]:
    ensure_skills_path()
    from bioflow_skills.skills.writing.claim_verifier import citation_keys as keys

    return keys(text)


def _run(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_skills_path()
    from bioflow_skills import run_skill

    result = run_skill(name, payload)
    if not getattr(result, "succeeded", False):
        raise RuntimeError(f"skill {name} failed: {getattr(result, 'error', '') or getattr(result, 'summary', '')}")
    data = dict(result.data or {})
    data["_skill"] = {"name": name, "version": getattr(result, "version", ""), "summary": getattr(result, "summary", "")}
    return data


def build_ledger(package_root: str | Path) -> dict[str, Any]:
    return _run(LEDGER_SKILL, {"package_root": str(package_root)})


def build_methods(ledger: dict[str, Any]) -> dict[str, Any]:
    return _run(METHODS_SKILL, {"ledger": ledger})


def _line(fact: dict[str, Any]) -> str:
    return f"[{fact['id']}] {fact['label']}: {fact['text']}"


def _facts(ledger: dict[str, Any], groups: tuple[str, ...]) -> list[dict[str, Any]]:
    wanted = set(groups)
    return [f for f in ledger.get("facts", [])
            if f.get("group") in wanted and not str(f["id"]).endswith(_SKIP_SUFFIX)
            and f["id"] != "COHORT.sample_ids"]


def section_facts(ledger: dict[str, Any], section: SectionId) -> str:
    """Prompt block of ledger facts for one chapter."""

    by_id = {f["id"]: f for f in ledger.get("facts", [])}
    missing = "\n".join(f"- {m['message']}" for m in ledger.get("missing", []))
    if section in {SectionId.results, SectionId.discussion}:
        blocks = []
        for title, groups in _RESULT_BLOCKS:
            lines = [_line(f) for f in _facts(ledger, groups)]
            if lines:
                blocks.append(f"Block: {title}\n" + "\n".join(lines))
        body = "\n\n".join(blocks)
        if section == SectionId.results:
            guide = ("Write one paragraph per block, in this order. Open each paragraph with what the "
                     "block shows, then give its key numbers. Skip a block only if it has no facts.")
        else:
            guide = ("Interpret these results: what they show about data quality and community "
                     "composition, and what they cannot show. Do not repeat every number.")
    elif section in {SectionId.abstract, SectionId.conclusion}:
        body = "\n".join(_line(by_id[i]) for i in _HEADLINE_IDS if i in by_id)
        guide = "Use at most four of these numbers."
    elif section == SectionId.introduction:
        body = "\n".join(_line(by_id[i]) for i in ("COHORT.sample_count",) if i in by_id)
        guide = "Do not report results numbers in the introduction."
    else:
        body = "\n".join(_line(f) for f in _facts(ledger, ("cohort",)))
        guide = ""
    parts = [NUMBER_RULES, SCOPE_RULES]
    if guide:
        parts.append(guide)
    parts.append("FACTS:\n" + body)
    if missing:
        parts.append("NOT AVAILABLE (state as limitations, never fill in):\n" + missing)
    return "\n\n".join(parts)


def ledger_catalog_item(ledger: dict[str, Any]) -> dict[str, str]:
    """A catalog entry whose excerpt holds every ledger display string.

    The number guard in compose_from_catalog searches catalog excerpts, so prose
    numbers copied from the ledger pass and any other number is reported.
    """

    lines = []
    for fact in ledger.get("facts", []):
        lines.append(f"{fact['id']} {fact['label']}: {fact['text']} {fact.get('value')}")
    return {
        "item_id": "cat_material_ledger",
        "kind": "summary",
        "relative_path": "fact_ledger",
        "path": "",
        "label": "fact ledger",
        "excerpt": "\n".join(lines),
    }


REFERENCE_SKILL = "reference-cards"
LITERATURE_CACHE = Path(__file__).resolve().parents[2] / "outputs" / "literature-cache"
REFERENCE_HEADING = "References"


def build_reference_cards(refs: list[dict[str, Any]], model: Any = None,
                          cache_dir: str | Path | None = None) -> dict[str, Any]:
    return _run(REFERENCE_SKILL, {"refs": refs, "model": model, "cache_dir": str(cache_dir or LITERATURE_CACHE)})


def refs_from_hits(hits: list[Any]) -> list[dict[str, Any]]:
    out = []
    for hit in hits or []:
        doi = str(getattr(hit, "doi", "") or "").strip()
        if doi:
            out.append({"doi": doi, "title": getattr(hit, "title", ""), "authors": getattr(hit, "authors", ""),
                        "year": getattr(hit, "year", "") or ""})
    return out


def reference_entry(card: dict[str, Any]) -> str:
    authors = ", ".join(card.get("authors", [])[:6]) + (", et al." if len(card.get("authors", [])) > 6 else "")
    parts = [p for p in (authors, card.get("title", ""), card.get("journal", ""), card.get("year", "")) if p]
    entry = ". ".join(part.rstrip(".") for part in parts) + "."
    if card.get("doi"):
        entry += f" https://doi.org/{card['doi']}"
    return entry


def references_block(cards: list[dict[str, Any]], cited_in_order: list[str]) -> str:
    """`[@key]: entry` lines in order of first citation (pandoc-like; LaTeX turns them into \\bibitem)."""

    by_key = {card["key"]: card for card in cards}
    lines = [f"[@{key}]: {reference_entry(by_key[key])}" for key in cited_in_order if key in by_key]
    return (REFERENCE_HEADING + "\n\n" + "\n\n".join(lines)) if lines else ""


def add_references(markdown: str, block: str) -> str:
    """Append the reference list to both language halves of a bilingual section."""

    if not block:
        return markdown
    marker = "\n中文\n"
    if marker in markdown:
        english, chinese = markdown.split(marker, 1)
        return f"{english.rstrip()}\n\n{block}\n{marker}\n{chinese.strip()}\n\n{block}\n"
    return f"{markdown.rstrip()}\n\n{block}\n"


def methods_markdown(methods: dict[str, Any]) -> str:
    return str(methods.get("markdown") or "")


def skills_metadata(ledger: dict[str, Any], *, methods: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = {
        LEDGER_SKILL: {"facts": len(ledger.get("facts", [])), "notes": ledger.get("notes", []),
                       "version": (ledger.get("_skill") or {}).get("version", "")},
    }
    if methods is not None:
        meta[METHODS_SKILL] = {
            "paragraphs": [{"key": p["key"], "fact_ids": p["fact_ids"]} for p in methods.get("paragraphs", [])],
            "version": (methods.get("_skill") or {}).get("version", ""),
        }
    return meta
