"""Participants ethics / informed-consent slot templates and LLM prompts.

Aligned to the Calcium–ASD paper Methods §2.1 Participants ethics/consent sentences
and the Chinese writing-guide example (IRB + consent + Helsinki).
"""

from __future__ import annotations

from article_writing.contracts import PaperBrief
from article_writing.sections.text_utils import clean_text


def _cap(text: str) -> str:
    text = clean_text(text)
    if not text:
        return text
    return text[0].upper() + text[1:]


def ethics_slots_from_brief(brief: PaperBrief) -> dict[str, str]:
    return {
        "ethics_committee": clean_text(brief.ethics_committee),
        "ethics_approval_id": clean_text(brief.ethics_approval_id),
        "ethics_approval_date": clean_text(brief.ethics_approval_date),
        "informed_consent_from": clean_text(brief.informed_consent_from),
        "informed_consent_form": clean_text(brief.informed_consent_form),
        "informed_consent_process": clean_text(brief.informed_consent_process),
        "helsinki_declaration": "yes" if brief.helsinki_declaration else "",
        "helsinki_citation": clean_text(brief.helsinki_citation),
        "ethics_statement_override": clean_text(brief.ethics_statement),
    }


def missing_ethics_slots(brief: PaperBrief) -> list[str]:
    slots = ethics_slots_from_brief(brief)
    if slots["ethics_statement_override"]:
        return []
    required = [
        "ethics_committee",
        "ethics_approval_id",
        "informed_consent_from",
        "informed_consent_form",
    ]
    return [key for key in required if not slots.get(key)]


def _format_approval_id(approval: str) -> str:
    if not approval:
        return "[ethics_approval_id]"
    upper = approval.upper()
    if upper.startswith("IRB") or approval.startswith("("):
        return approval
    if approval.startswith("#"):
        return f"IRB {approval}"
    return f"IRB #{approval}"


def render_ethics_consent_paragraph(brief: PaperBrief) -> str:
    """Deterministic English paragraph from slots (no LLM).

    Style target (Calcium–ASD / writing guide):
    1) approved by {committee} ({IRB id}[, approved on {date}]).
    2) {consent form} was obtained from {consent from}.
    3) {consent from} {process}[, in accordance with Declaration of Helsinki {cite}].
    """

    slots = ethics_slots_from_brief(brief)
    if slots["ethics_statement_override"]:
        return slots["ethics_statement_override"]

    committee = slots["ethics_committee"] or "[ethics_committee]"
    approval_display = _format_approval_id(slots["ethics_approval_id"])
    date_clause = (
        f", approved on {slots['ethics_approval_date']}"
        if slots["ethics_approval_date"]
        else ""
    )
    consent_from = slots["informed_consent_from"] or "[informed_consent_from]"
    consent_form = slots["informed_consent_form"] or "[informed_consent_form]"
    process = slots["informed_consent_process"] or (
        "received detailed information on the study purposes and procedures before "
        "providing consent"
    )
    helsinki = ""
    if slots["helsinki_declaration"] or slots["helsinki_citation"]:
        cite = f" {slots['helsinki_citation']}" if slots["helsinki_citation"] else ""
        helsinki = f", in accordance with the Declaration of Helsinki{cite}"

    return (
        f"The study was approved by the {committee} "
        f"({approval_display}{date_clause}). "
        f"{_cap(consent_form)} was obtained from {consent_from}. "
        f"{_cap(consent_from)} {process}{helsinki}."
    )


def render_ethics_consent_zh_slot_line(brief: PaperBrief) -> str:
    """Chinese slot line for bilingual polish (facts only)."""

    slots = ethics_slots_from_brief(brief)
    if slots["ethics_statement_override"]:
        return slots["ethics_statement_override"]
    return (
        f"本研究经【ethics_committee={slots['ethics_committee'] or '未提供'}】批准"
        f"（【ethics_approval_id={slots['ethics_approval_id'] or '未提供'}】"
        f"{'，批准日期 ' + slots['ethics_approval_date'] if slots['ethics_approval_date'] else ''}）。"
        f"取得【informed_consent_form={slots['informed_consent_form'] or '未提供'}】，"
        f"签署方为【informed_consent_from={slots['informed_consent_from'] or '未提供'}】。"
        f"签署前【informed_consent_process="
        f"{slots['informed_consent_process'] or '充分告知研究目的与试验流程'}】"
        + (
            "，并遵循《赫尔辛基宣言》" + (slots["helsinki_citation"] or "")
            if slots["helsinki_declaration"] or slots["helsinki_citation"]
            else ""
        )
        + "。"
    )


ETHICS_CONSENT_LLM_PROMPT = """
You are polishing ONLY the Methods → Participants → Ethics and informed consent block.

REFERENCE STYLE (keep this frame; swap only slotted facts):
- Sentence 1: The study was approved by the {ethics_committee} ({ethics_approval_id}[, approved on {date}]).
- Sentence 2: {informed_consent_form} was obtained from {informed_consent_from}.
- Sentence 3: {informed_consent_from} {informed_consent_process}[, in accordance with the Declaration of Helsinki {cite}].

HARD RULES:
1. Use ONLY SLOT VALUES / DRAFT facts. Never invent committee names, IRB/ethics IDs, dates, or consent details.
2. If a value is 未提供 or [placeholder], keep an explicit placeholder or state that upstream modules did not supply it — do not fabricate.
3. Output bilingual Markdown:
   ## English
   <one short paragraph>
   ## 中文
   <factually equivalent short paragraph>
4. Do not add inclusion/exclusion criteria unless they already appear in the draft ethics text.
5. Output ONLY that Markdown block (no preamble, no code fences).
""".strip()


def build_ethics_consent_llm_user_prompt(brief: PaperBrief, draft_markdown: str) -> str:
    slots = ethics_slots_from_brief(brief)
    slot_lines = "\n".join(f"- {k}: {v or '「未提供」'}" for k, v in slots.items())
    return (
        f"{ETHICS_CONSENT_LLM_PROMPT}\n\n"
        f"SLOT VALUES:\n{slot_lines}\n\n"
        f"CURRENT DRAFT TO POLISH:\n```markdown\n{draft_markdown.rstrip()}\n```\n"
    )
