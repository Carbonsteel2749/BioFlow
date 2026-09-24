"""Attach Nature writing skills to draft, polish, review, and response steps."""

from __future__ import annotations

from typing import Any

from article_writing.contracts import AnalysisBundle, PaperBrief, PaperState, SectionDraft, SectionId
from article_writing.llm.base import LLMClient, LLMDisabled
from article_writing.skills.client import SkillClient, playbook_text


WRITING_SKILL = "nature-writing"
POLISH_SKILL = "nature-polishing"
REVIEW_SKILL = "nature-reviewer"
RESPONSE_SKILL = "nature-response"

WRITING_SECTIONS = frozenset(
    {SectionId.methods, SectionId.results, SectionId.discussion}
)


def _task_context(brief: PaperBrief, section: str) -> str:
    return (
        f"Section: {section}. Title: {brief.title}. "
        f"Question: {brief.research_question}. "
        f"System: {brief.organism_or_system}. Modality: {brief.data_modality}."
    ).strip()


def _evidence_package(analysis: AnalysisBundle | None) -> str:
    if analysis is None:
        return ""
    parts = [analysis.summary or ""]
    parts.extend(analysis.key_findings or [])
    if analysis.metrics:
        parts.append(
            "metrics: " + ", ".join(f"{k}={v}" for k, v in analysis.metrics.items())
        )
    if analysis.limitations:
        parts.append("limitations: " + " | ".join(analysis.limitations))
    return "\n".join(p for p in parts if p)


def writing_instructions(
    client: SkillClient,
    *,
    section: SectionId,
    brief: PaperBrief,
    analysis: AnalysisBundle | None,
) -> tuple[str, dict[str, Any] | None]:
    if section not in WRITING_SECTIONS:
        return "", None
    handoff = client.fetch(
        WRITING_SKILL,
        {
            "task_context": _task_context(brief, section.value),
            "target_section": section.value,
            "paper_type": "research",
            "evidence_package": _evidence_package(analysis),
            "language": "en",
        },
    )
    text = playbook_text(handoff)
    if not text:
        return "", handoff
    return (
        "Follow this nature-writing playbook. Do not invent numbers, figures, "
        "or citations beyond the draft and immutable context.\n" + text,
        handoff,
    )


def polish_instructions(client: SkillClient, *, section: SectionId, draft_text: str) -> tuple[str, dict[str, Any] | None]:
    handoff = client.fetch(
        POLISH_SKILL,
        {
            "task_context": f"Polish or translate section {section.value} without changing facts.",
            "draft_text": draft_text[:4000],
            "target_section": section.value,
            "language": "en+zh",
        },
    )
    text = playbook_text(handoff)
    if not text:
        return "", handoff
    return (
        "Follow this nature-polishing playbook. Do not add conclusions, data, or citations.\n"
        + text,
        handoff,
    )


def _llm_text(llm: LLMClient | None, prompt: str, system: str) -> str | None:
    if llm is None or not getattr(llm, "enabled", False) or isinstance(llm, LLMDisabled):
        return None
    try:
        return llm.generate(prompt, system=system).strip()
    except Exception as exc:  # noqa: BLE001
        return f"(model call failed; checklist only) {exc}"


def review_markdown(
    client: SkillClient,
    *,
    state: PaperState,
    manuscript: str,
    analysis: AnalysisBundle | None,
    llm: LLMClient | None,
) -> str | None:
    handoff = client.fetch(
        REVIEW_SKILL,
        {
            "task_context": _task_context(state.brief, "full_manuscript"),
            "manuscript": manuscript[:8000],
            "evidence_package": _evidence_package(analysis),
            "review_scope": "full_manuscript",
        },
    )
    # Evidence package is on the brief/findings already in manuscript; pass findings via task.
    if handoff is None and not client.enabled:
        return None
    rules = playbook_text(handoff) or "No skill handoff."
    system = (
        "You are simulating a pre-submission peer review. "
        "List concrete issues. Every issue must point at a claim in the manuscript. "
        "Do not invent experiments, figure numbers, or citations. "
        "This is not an editorial decision."
    )
    prompt = (
        f"{rules}\n\nManuscript:\n{manuscript[:12000]}\n\n"
        "Write a markdown review with Major and Minor issues."
    )
    generated = _llm_text(llm, prompt, system)
    body = generated or (
        "# Pre-submission review checklist\n\n"
        "Model execution was off. Apply these skill checks manually:\n\n"
        f"{rules}\n"
    )
    return body if body.strip() else None


def response_markdown(
    client: SkillClient,
    *,
    state: PaperState,
    manuscript: str,
    editor_letter: str,
    reviewer_comments: str,
    llm: LLMClient | None,
) -> str | None:
    if not (editor_letter.strip() or reviewer_comments.strip()):
        return None
    handoff = client.fetch(
        RESPONSE_SKILL,
        {
            "task_context": "Draft a point-by-point response. Do not invent completed experiments.",
            "editor_letter": editor_letter[:4000],
            "reviewer_comments": reviewer_comments[:8000],
            "manuscript": manuscript[:4000],
        },
    )
    rules = playbook_text(handoff)
    system = (
        "Draft a point-by-point revision response. "
        "If a requested change is not present in the manuscript, mark AUTHOR_INPUT_NEEDED. "
        "Do not invent experiments, line numbers, or completed edits."
    )
    prompt = (
        f"{rules}\n\nEditor letter:\n{editor_letter}\n\n"
        f"Reviewer comments:\n{reviewer_comments}\n\n"
        f"Current manuscript excerpt:\n{manuscript[:8000]}\n"
    )
    generated = _llm_text(llm, prompt, system)
    if generated:
        return generated
    return (
        "# Revision response checklist\n\n"
        "Model execution was off. Use this skill playbook with the supplied comments:\n\n"
        f"{rules}\n"
    )
