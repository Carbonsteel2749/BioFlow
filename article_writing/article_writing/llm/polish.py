"""Polish template markdown with an LLM while preserving factual anchors."""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from article_writing.contracts import SectionDraft, SectionId
from article_writing.llm.base import LLMClient, LLMDisabled


DEFAULT_POLISH_SECTIONS: frozenset[SectionId] = frozenset(
    {
        SectionId.abstract,
        SectionId.introduction,
        SectionId.methods,
        SectionId.results,
        SectionId.discussion,
        SectionId.conclusion,
        SectionId.back_matter,
    }
)

# User-facing writing requirements (BioFLow manuscript plate).
DEFAULT_WRITING_REQUIREMENTS = """
OUTPUT REQUIREMENTS:
1. Produce BOTH English and Chinese for this section.
2. Use this structure exactly:
   # <Section title>
   ## English
   <journal-style English body>
   ## 中文
   <journal-style Chinese body that is factually equivalent to English>
3. Journal-style polish: connect experiments/methods with results and conclusions
   coherently (narrative continuity), without inventing new evidence.
4. Do NOT invent, alter, delete, or rescale any numbers, gene names, thresholds,
   p-values, sample sizes, metrics, conclusions, citation keys (lit_*), evidence
   ids, figure ids, or markdown image/table embeds (![…](…)).
5. Keep figure/table embeds exactly where they appear; do not move captions away
   from their images; do not change image paths.
6. Do not add scientific claims absent from the draft or from the immutable
   paper context.
7. Output ONLY Markdown. No preamble, no code fences, no explanations.
""".strip()

SYSTEM_PROMPT = f"""You are a scientific manuscript editor for bioinformatics papers.

{DEFAULT_WRITING_REQUIREMENTS}
"""


def _extract_anchors(markdown: str) -> list[str]:
    """Capture tokens that must still appear after polishing."""

    patterns = [
        r"!\[[^\]]*\]\([^)]+\)",
        r"`[a-zA-Z0-9_.:/-]+`",
        r"\blit_\d+\b",
        r"\bev_[a-zA-Z0-9_]+\b",
        r"\bfig_[a-zA-Z0-9_]+\b",
        r"\btbl_[a-zA-Z0-9_]+\b",
        r"\b\d+\.\d+\b",
        r"\b\d+\b",
    ]
    found: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, markdown):
            token = match.group(0)
            if token not in seen:
                seen.add(token)
                found.append(token)
    return found


def _missing_anchors(original: str, polished: str, *, max_report: int = 12) -> list[str]:
    missing: list[str] = []
    for token in _extract_anchors(original):
        # Skip ultra-common small integers that appear in heading numbers.
        if re.fullmatch(r"\d+", token) and int(token) < 20:
            continue
        if token not in polished:
            missing.append(token)
            if len(missing) >= max_report:
                break
    return missing


def _normalize_markdown(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:markdown|md)?\s*", "", cleaned, count=1, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip() + "\n"


def _bilingual_ok(text: str) -> bool:
    lower = text.lower()
    has_en = "## english" in lower or re.search(r"^##\s+english\b", text, re.I | re.M)
    has_zh = "## 中文" in text or "## chinese" in lower
    return bool(has_en and has_zh)


def polish_section_draft(
    draft: SectionDraft,
    client: LLMClient,
    *,
    language: str = "en+zh",
    instructions: str = "",
    paper_context: str = "",
    sections: Sequence[SectionId] | Iterable[SectionId] | None = None,
) -> SectionDraft:
    """Return a copy of ``draft`` with markdown polished when enabled."""

    allowed = DEFAULT_POLISH_SECTIONS if sections is None else frozenset(sections)
    meta = dict(draft.metadata or {})
    llm_meta = {
        "enabled": bool(getattr(client, "enabled", False)),
        "provider": getattr(client, "provider", "unknown"),
        "model": getattr(client, "model", "unknown"),
        "polished": False,
        "bilingual": False,
    }

    if not getattr(client, "enabled", False) or isinstance(client, LLMDisabled):
        meta["llm"] = llm_meta
        return draft.model_copy(update={"metadata": meta})

    if draft.section not in allowed:
        llm_meta["skipped"] = True
        llm_meta["reason"] = "section_not_in_polish_set"
        meta["llm"] = llm_meta
        return draft.model_copy(update={"metadata": meta})

    extra = (instructions or "").strip()
    context = (paper_context or "").strip()
    user_prompt = (
        f"Target languages: {language} (must include both English and Chinese).\n"
        f"Section: {draft.section.value} ({draft.title}).\n\n"
    )
    if context:
        user_prompt += (
            "Immutable paper context from upstream modules "
            "(do not invent beyond this + the draft):\n"
            f"{context}\n\n"
        )
    if extra:
        user_prompt += f"Additional author instructions:\n{extra}\n\n"
    user_prompt += (
        "Draft Markdown to polish (preserve all facts, embeds, and ids):\n"
        "```markdown\n"
        f"{draft.markdown.rstrip()}\n"
        "```\n"
    )
    # 小模型对「泛化要求」遵循度低，显式列出必须逐字保留的锚点更有效
    anchors = _extract_anchors(draft.markdown)
    if anchors:
        user_prompt += (
            "\nMUST-KEEP TOKENS — every token below must appear verbatim at least once "
            "in your output (same spelling, same backticks). Check them one by one "
            "before answering:\n"
            + ", ".join(anchors)
            + "\n"
        )

    warnings = list(draft.warnings)
    try:
        raw = client.generate(user_prompt, system=SYSTEM_PROMPT)
        polished = _normalize_markdown(raw)
        if len(polished.strip()) < 40:
            raise RuntimeError("LLM returned an empty or too-short polish")
        missing = _missing_anchors(draft.markdown, polished)
        if missing:
            warnings.append(
                "llm polish dropped anchors (kept template): " + ", ".join(missing[:8])
            )
            llm_meta["rejected"] = True
            llm_meta["missing_anchors"] = missing
            meta["llm"] = llm_meta
            return draft.model_copy(update={"metadata": meta, "warnings": warnings})

        bilingual = _bilingual_ok(polished)
        llm_meta["polished"] = True
        llm_meta["bilingual"] = bilingual
        if not bilingual:
            warnings.append(
                "llm polish missing ## English / ## 中文 bilingual structure "
                "(accepted with warning)"
            )
        meta["llm"] = llm_meta
        meta["template_markdown_chars"] = len(draft.markdown)
        return draft.model_copy(
            update={"markdown": polished, "metadata": meta, "warnings": warnings}
        )
    except Exception as error:  # noqa: BLE001 — polish must never crash the pipeline
        warnings.append(f"llm polish failed; kept template: {error}")
        llm_meta["error"] = str(error)[:300]
        meta["llm"] = llm_meta
        return draft.model_copy(update={"metadata": meta, "warnings": warnings})
