"""Back Matter slots: admin statements + abbreviations harvest/polish."""

from __future__ import annotations

import re

from article_writing.contracts import (
    AbbreviationEntry,
    AnalysisBundle,
    PaperBrief,
)
from article_writing.prompts.methods_participants_ethics import (
    render_ethics_consent_paragraph,
)
from article_writing.sections.text_utils import clean_text


def _cap(text: str) -> str:
    text = clean_text(text)
    if not text:
        return text
    return text[0].upper() + text[1:]


def render_author_contributions_en(brief: PaperBrief) -> str:
    text = clean_text(brief.author_contributions)
    if text:
        return text
    authors = clean_text(brief.authors)
    lead = f"Listed authors: {authors}. " if authors else ""
    return (
        f"{lead}Author contribution (CRediT) statements were not supplied. "
        "Provide role lines (e.g., Conceptualization, Methodology, Writing — "
        "original draft) and the closing sentence that all authors approved the "
        "published version. Do not invent initials or roles."
    )


def render_funding_en(brief: PaperBrief) -> str:
    text = clean_text(brief.funding)
    if text:
        return text
    return (
        "Funding information was not supplied. List grant names and numbers when "
        "available from the investigators. Do not invent funders or grant IDs."
    )


def render_irb_statement_en(brief: PaperBrief) -> str:
    """Journal-style IRB block; prefer ethics_statement override, else ethics slots."""
    override = clean_text(brief.ethics_statement)
    if override:
        return override
    committee = clean_text(brief.ethics_committee)
    approval_id = clean_text(brief.ethics_approval_id)
    date = clean_text(brief.ethics_approval_date)
    if not committee and not approval_id:
        return (
            "Institutional Review Board / ethics approval statement was not supplied. "
            "Fill ethics_committee, ethics_approval_id, and optional approval date "
            "(same slots as Methods → Participants)."
        )
    parts: list[str] = []
    if brief.helsinki_declaration:
        cite = f" {clean_text(brief.helsinki_citation)}" if clean_text(brief.helsinki_citation) else ""
        parts.append(
            f"The study was conducted in accordance with the Declaration of Helsinki{cite}."
        )
    irb_bits = []
    if committee:
        irb_bits.append(f"approved by the {committee}")
    if approval_id:
        irb_bits.append(f"(IRB #{approval_id}")
        if date:
            irb_bits[-1] += f", approved on {date}"
        irb_bits[-1] += ")"
    elif date:
        irb_bits.append(f"(approved on {date})")
    if irb_bits:
        parts.append("The study was " + " ".join(irb_bits) + ".")
    return " ".join(parts)


def render_informed_consent_statement_en(brief: PaperBrief) -> str:
    form = clean_text(brief.informed_consent_form) or "Informed consent"
    who = clean_text(brief.informed_consent_from)
    process = clean_text(brief.informed_consent_process)
    if who or process or clean_text(brief.informed_consent_form):
        # Prefer a short journal-style sentence when slots exist
        if who and not process:
            return f"{_cap(form)} was obtained from {who}."
        # Fall back to the fuller Methods ethics paragraph when process is rich
        return render_ethics_consent_paragraph(brief)
    return (
        "Informed consent statement was not supplied. "
        "Fill informed_consent_form / informed_consent_from "
        "(same slots as Methods → Participants)."
    )


def render_data_availability_en(brief: PaperBrief) -> str:
    full = clean_text(brief.data_availability)
    if full:
        return full
    accession = clean_text(brief.data_accession)
    repo = clean_text(brief.data_repository)
    url = clean_text(brief.data_repository_url)
    if accession or repo or url:
        pieces: list[str] = []
        if repo and accession:
            pieces.append(
                f"The raw sequence data generated in this study have been deposited in "
                f"the {repo} under accession number {accession}."
            )
        elif accession:
            pieces.append(
                f"Sequence data are available under accession number {accession}."
            )
        elif repo:
            pieces.append(f"Data are deposited in the {repo}.")
        if url:
            pieces.append(f"Repository URL: {url}.")
        return " ".join(pieces)
    return (
        "Data availability was not supplied. Provide repository name, accession ID, "
        "and URL (or a complete data_availability paragraph). Do not invent accessions."
    )


def render_code_availability_en(brief: PaperBrief) -> str:
    text = clean_text(brief.code_availability)
    if text:
        return text
    return (
        "Code availability was not supplied. Insert a public repository URL and "
        "environment specification when released, or state that code is available "
        "upon reasonable request."
    )


def render_acknowledgments_en(brief: PaperBrief) -> str:
    text = clean_text(brief.acknowledgments)
    if text:
        return text
    return (
        "Acknowledgments were not supplied. Name individuals or facilities and the "
        "assistance provided when the investigators approve the wording."
    )


def render_conflicts_en(brief: PaperBrief) -> str:
    text = clean_text(brief.conflicts_of_interest)
    if text:
        return text
    return (
        "Conflicts of interest statement was not supplied. Typical wording declares "
        "no conflicts and that funders had no role in design, analysis, writing, or "
        "publication decisions — only after investigators confirm."
    )


def render_back_matter_admin_zh(brief: PaperBrief) -> str:
    return (
        f"【author_contributions={brief.author_contributions or '未提供'}】；"
        f"【funding={brief.funding or '未提供'}】；"
        f"【ethics_committee={brief.ethics_committee or '未提供'}】；"
        f"【ethics_approval_id={brief.ethics_approval_id or '未提供'}】；"
        f"【informed_consent_from={brief.informed_consent_from or '未提供'}】；"
        f"【data_availability={brief.data_availability or '未提供'}】；"
        f"【data_accession={brief.data_accession or '未提供'}】；"
        f"【data_repository={brief.data_repository or '未提供'}】；"
        f"【data_repository_url={brief.data_repository_url or '未提供'}】；"
        f"【code_availability={brief.code_availability or '未提供'}】；"
        f"【acknowledgments={brief.acknowledgments or '未提供'}】；"
        f"【conflicts_of_interest={brief.conflicts_of_interest or '未提供'}】。"
    )


def missing_back_matter_admin_slots(brief: PaperBrief) -> list[str]:
    missing: list[str] = []
    if not clean_text(brief.author_contributions):
        missing.append("author_contributions")
    if not clean_text(brief.funding):
        missing.append("funding")
    has_ethics = bool(
        clean_text(brief.ethics_statement)
        or clean_text(brief.ethics_committee)
        or clean_text(brief.ethics_approval_id)
    )
    if not has_ethics:
        missing.append("ethics_committee/ethics_approval_id (or ethics_statement)")
    has_consent = bool(
        clean_text(brief.informed_consent_form)
        or clean_text(brief.informed_consent_from)
        or clean_text(brief.ethics_statement)
    )
    if not has_consent:
        missing.append("informed_consent_form/from")
    has_data = bool(
        clean_text(brief.data_availability)
        or clean_text(brief.data_accession)
        or clean_text(brief.data_repository)
    )
    if not has_data:
        missing.append("data_availability (or data_accession/repository)")
    if not clean_text(brief.acknowledgments):
        missing.append("acknowledgments")
    if not clean_text(brief.conflicts_of_interest):
        missing.append("conflicts_of_interest")
    return missing


def _add_abbr(
    bag: dict[str, str], abbreviation: str, expansion: str
) -> None:
    abbr = clean_text(abbreviation)
    expansion = clean_text(expansion)
    if not abbr or not expansion:
        return
    key = abbr.upper()
    if key not in bag:
        bag[key] = expansion


def harvest_abbreviation_seeds(
    brief: PaperBrief, analysis: AnalysisBundle | None
) -> list[AbbreviationEntry]:
    """Deterministic seeds from structured slots (never invent expansions)."""
    bag: dict[str, str] = {}

    # User-confirmed list wins as base
    for entry in brief.abbreviations or []:
        _add_abbr(bag, entry.abbreviation, entry.expansion)

    scales = list(brief.clinical_scales or [])
    if analysis and analysis.clinical_scales:
        scales = list(analysis.clinical_scales)
    for scale in scales:
        _add_abbr(bag, scale.abbreviation, scale.name)

    if analysis and analysis.exposure_measurement:
        det = clean_text(analysis.exposure_measurement.detection_method)
        match = re.search(
            r"(.+?)\s*\(([A-Z][A-Z0-9/-]{1,20})\)\s*$",
            det,
        )
        if match:
            _add_abbr(bag, match.group(2), match.group(1).strip())

    if analysis and analysis.statistical_analysis:
        plan = analysis.statistical_analysis
        for blob in (
            clean_text(plan.multiple_testing_correction),
            clean_text(plan.group_comparison_method),
            clean_text(plan.analysis_overview),
            clean_text(plan.multivariate_models),
        ):
            for m in re.finditer(
                r"([A-Za-z][A-Za-z0-9 /,-]{2,80}?)\s*\(([A-Z][A-Z0-9/-]{1,20})\)",
                blob,
            ):
                _add_abbr(bag, m.group(2), m.group(1).strip())
            if re.search(r"\bFDR\b", blob) and "FDR" not in bag:
                if "false discovery" in blob.casefold() or "benjamini" in blob.casefold():
                    _add_abbr(bag, "FDR", "False Discovery Rate")

    if analysis and analysis.microbiome_sequencing:
        seq = analysis.microbiome_sequencing
        blob = " ".join(
            clean_text(getattr(seq, key))
            for key in (
                "pathway_annotation",
                "taxonomy_annotation",
                "sequencing_strategy",
                "qc_pipeline",
                "sample_collection",
            )
        )
        for m in re.finditer(
            r"([A-Za-z][A-Za-z0-9 /,-]{2,80}?)\s*\(([A-Z][A-Z0-9/-]{1,20})\)",
            blob,
        ):
            _add_abbr(bag, m.group(2), m.group(1).strip())
        if "MetaCyc" in blob and "METACYC" not in bag:
            # Keep only if expansion already present nearby; otherwise skip inventing.
            if "metabolic pathway" in blob.casefold():
                _add_abbr(bag, "MetaCyc", "Metabolic pathway database")

    # Common organism/system acronyms only when expansion is in brief text
    system = clean_text(brief.organism_or_system)
    title = clean_text(brief.title)
    question = clean_text(brief.research_question)
    for blob in (system, title, question, " ".join(brief.keywords or [])):
        m = re.search(
            r"(Autism Spectrum Disorder)\s*\(\s*(ASD)\s*\)",
            blob,
            flags=re.I,
        )
        if m:
            _add_abbr(bag, m.group(2), m.group(1))
        m2 = re.search(
            r"\b(ASD)\b.*\b(Autism Spectrum Disorder)\b",
            blob,
            flags=re.I,
        )
        if m2 and "ASD" not in bag:
            _add_abbr(bag, "ASD", "Autism Spectrum Disorder")

    # Sort alphabetically by abbreviation
    return [
        AbbreviationEntry(abbreviation=k, expansion=bag[k])
        for k in sorted(bag.keys(), key=str.casefold)
    ]


def render_abbreviations_en(entries: list[AbbreviationEntry]) -> str:
    if not entries:
        return (
            "The following abbreviations are used in this manuscript:\n\n"
            "- *(none harvested yet — confirm a list or enable LLM extraction from "
            "the polished manuscript; do not invent expansions)*\n"
        )
    lines = [
        "The following abbreviations are used in this manuscript:",
        "",
    ]
    for entry in entries:
        lines.append(f"- **{entry.abbreviation}**: {entry.expansion}")
    return "\n".join(lines) + "\n"


def render_abbreviations_zh(entries: list[AbbreviationEntry]) -> str:
    if not entries:
        return "缩写表【未提供 / 未从槽位收获】。"
    parts = [f"{e.abbreviation}={e.expansion}" for e in entries]
    return "缩写表【" + "；".join(parts) + "】。"


BACK_MATTER_ADMIN_LLM_PROMPT = """
You are polishing ONLY the Back Matter administrative blocks:
Author Contributions, Funding, Institutional Review Board Statement,
Informed Consent Statement, Data Availability, Code Availability,
Acknowledgments, Conflicts of Interest.

HARD RULES:
- Use ONLY supplied slot facts. Never invent author initials, CRediT roles,
  grant numbers, IRB IDs, accessions, URLs, acknowledgments, or conflict claims.
- If a slot is missing, keep a clear placeholder; do not fill from "typical papers".
- Ethics/consent wording must stay consistent with Methods Participants slots.
- Output bilingual English + 中文. Output ONLY Markdown for these subsections.
""".strip()


ABBREVIATIONS_LLM_PROMPT = """
You are building or polishing the Abbreviations list for this manuscript.

Preferred workflow:
1) Start from any harvested/confirmed abbreviation seeds in the draft.
2) You may ADD an abbreviation ONLY if it appears in the manuscript draft AND
   its expansion is already written in that draft (e.g. "False Discovery Rate (FDR)").
3) Sort alphabetically by abbreviation.
4) Format:
   The following abbreviations are used in this manuscript:
   - **ABBR**: Expansion

HARD RULES:
- Do NOT invent expansions from domain knowledge alone.
- Do NOT add abbreviations that never appear in the draft.
- Mark the list as suggested for author confirmation when newly extracted.
- Output bilingual English + 中文. Output ONLY Markdown for Abbreviations.
""".strip()
