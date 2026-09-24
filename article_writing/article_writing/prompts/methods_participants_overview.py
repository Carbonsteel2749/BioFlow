"""Participants overview + eligibility slot templates and LLM prompts.

Aligned to Calcium–ASD paper Methods §2.1 Participants first paragraph
(design / cohort source / analytic N / optional power) and checklist items:
样本来源、研究设计类型、纳入标准、排除标准、最终样本量.
"""

from __future__ import annotations

from article_writing.contracts import PaperBrief
from article_writing.sections.text_utils import clean_text


def _cap(text: str) -> str:
    text = clean_text(text)
    if not text:
        return text
    return text[0].upper() + text[1:]


def overview_slots_from_brief(brief: PaperBrief) -> dict[str, object]:
    return {
        "sample_source": clean_text(brief.sample_source),
        "study_design": clean_text(brief.study_design),
        "n_participants": brief.n_participants,
        "n_participants_note": clean_text(brief.n_participants_note),
        "power_analysis": clean_text(brief.power_analysis),
        "organism_or_system": clean_text(brief.organism_or_system),
    }


def eligibility_slots_from_brief(brief: PaperBrief) -> dict[str, list[str]]:
    inclusion = [clean_text(x) for x in (brief.inclusion_criteria or []) if clean_text(x)]
    exclusion = [clean_text(x) for x in (brief.exclusion_criteria or []) if clean_text(x)]
    return {"inclusion_criteria": inclusion, "exclusion_criteria": exclusion}


def missing_overview_slots(brief: PaperBrief) -> list[str]:
    slots = overview_slots_from_brief(brief)
    required = ["study_design", "sample_source", "n_participants"]
    missing: list[str] = []
    for key in required:
        value = slots.get(key)
        if value is None or value == "":
            missing.append(key)
    return missing


def missing_eligibility_slots(brief: PaperBrief) -> list[str]:
    slots = eligibility_slots_from_brief(brief)
    missing: list[str] = []
    if not slots["inclusion_criteria"]:
        missing.append("inclusion_criteria")
    if not slots["exclusion_criteria"]:
        missing.append("exclusion_criteria")
    return missing


def render_overview_paragraph(brief: PaperBrief) -> str:
    """Deterministic English overview paragraph (Calcium–ASD first-paragraph frame)."""

    slots = overview_slots_from_brief(brief)
    design = slots["study_design"] or "[study_design]"
    source = slots["sample_source"] or "[sample_source]"
    n = slots["n_participants"]
    n_display = str(n) if n is not None else "[n_participants]"
    note = slots["n_participants_note"]
    if note:
        n_clause = f"{n_display} participants with {note}"
    else:
        system = slots["organism_or_system"]
        tail = f" ({system})" if system else ""
        n_clause = f"{n_display} participants{tail}"

    sentences = [
        f"This {design} used {source}.",
        f"The final analysis included {n_clause}.",
    ]
    power = slots["power_analysis"]
    if power:
        sentences.append(_cap(str(power)))
    return " ".join(sentences)


def render_overview_zh_slot_line(brief: PaperBrief) -> str:
    slots = overview_slots_from_brief(brief)
    n = slots["n_participants"]
    return (
        f"本研究为【study_design={slots['study_design'] or '未提供'}】，"
        f"样本来源【sample_source={slots['sample_source'] or '未提供'}】。"
        f"最终分析纳入【n_participants={n if n is not None else '未提供'}】名受试者"
        f"{'（' + slots['n_participants_note'] + '）' if slots['n_participants_note'] else ''}。"
        + (
            f"效能说明【power_analysis={slots['power_analysis']}】。"
            if slots["power_analysis"]
            else ""
        )
    )


def render_eligibility_block(brief: PaperBrief) -> str:
    """English inclusion/exclusion block (list form for clear slot binding)."""

    slots = eligibility_slots_from_brief(brief)
    lines = ["**Inclusion criteria**", ""]
    if slots["inclusion_criteria"]:
        for item in slots["inclusion_criteria"]:
            lines.append(f"- {item}")
    else:
        lines.append("- [inclusion_criteria not supplied]")
    lines.extend(["", "**Exclusion criteria**", ""])
    if slots["exclusion_criteria"]:
        for item in slots["exclusion_criteria"]:
            lines.append(f"- {item}")
    else:
        lines.append("- [exclusion_criteria not supplied]")
    return "\n".join(lines)


def render_eligibility_zh_slot_line(brief: PaperBrief) -> str:
    slots = eligibility_slots_from_brief(brief)
    inc = "；".join(slots["inclusion_criteria"]) if slots["inclusion_criteria"] else "未提供"
    exc = "；".join(slots["exclusion_criteria"]) if slots["exclusion_criteria"] else "未提供"
    return f"纳入标准【inclusion_criteria={inc}】。排除标准【exclusion_criteria={exc}】。"


OVERVIEW_ELIGIBILITY_LLM_PROMPT = """
You are polishing ONLY the Methods → Participants → Study overview and Eligibility blocks.

REFERENCE STYLE (Calcium–ASD Participants first paragraph + eligibility lists):
Overview paragraph frame:
1) This {study_design} used {sample_source}.
2) The final analysis included {n_participants} participants with {n_participants_note}.
3) Optional: power/sensitivity sentence from power_analysis only if supplied.

Eligibility:
- Keep inclusion and exclusion as clear lists OR weave them into journal prose
  ONLY using the listed criteria text — do not add new criteria.

HARD RULES:
1. Use ONLY SLOT VALUES / DRAFT facts. Never invent N, design type, cohort source, or criteria.
2. If a value is 未提供 or [placeholder], keep an explicit placeholder — do not fabricate.
3. Output bilingual Markdown:
   ### Study overview
   #### English
   <paragraph>
   #### 中文
   <equivalent>
   ### Eligibility criteria
   #### English
   <lists or prose>
   #### 中文
   <equivalent>
4. Output ONLY that Markdown (no preamble, no code fences).
""".strip()


PARTICIPANTS_COMBINED_LLM_PROMPT = """
You are polishing the full Methods → Participants section (overview + eligibility + ethics/consent).

Keep three subsections in order:
1) Study overview
2) Eligibility criteria
3) Ethics approval and informed consent

Use the journal frame from the Calcium–ASD paper / writing guide.
HARD RULES: never invent sample size, IRB IDs, committee names, inclusion/exclusion items,
consent facts, or power numbers. Missing slots stay as placeholders or “not supplied”.
Output bilingual English + 中文 under each subsection. Output ONLY Markdown.
""".strip()


def build_overview_eligibility_llm_user_prompt(brief: PaperBrief, draft_markdown: str) -> str:
    overview = overview_slots_from_brief(brief)
    eligibility = eligibility_slots_from_brief(brief)
    slot_lines = "\n".join(
        [f"- {k}: {v if v not in (None, '') else '「未提供」'}" for k, v in overview.items()]
        + [
            f"- inclusion_criteria: {eligibility['inclusion_criteria'] or '「未提供」'}",
            f"- exclusion_criteria: {eligibility['exclusion_criteria'] or '「未提供」'}",
        ]
    )
    return (
        f"{OVERVIEW_ELIGIBILITY_LLM_PROMPT}\n\n"
        f"SLOT VALUES:\n{slot_lines}\n\n"
        f"CURRENT DRAFT TO POLISH:\n```markdown\n{draft_markdown.rstrip()}\n```\n"
    )
