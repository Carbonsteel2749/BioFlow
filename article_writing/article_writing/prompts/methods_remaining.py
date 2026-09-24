"""Methods slots after Participants: clinical / exposure / microbiome / statistics."""

from __future__ import annotations

from article_writing.contracts import (
    AnalysisBundle,
    ClinicalScale,
    ExposureMeasurement,
    MicrobiomeSequencing,
    PaperBrief,
    StatisticalAnalysisPlan,
)
from article_writing.sections.text_utils import clean_text


def _cap(text: str) -> str:
    text = clean_text(text)
    if not text:
        return text
    return text[0].upper() + text[1:]


def resolve_clinical_scales(
    brief: PaperBrief, analysis: AnalysisBundle | None
) -> tuple[str, list[ClinicalScale]]:
    lead = clean_text(brief.clinical_assessment_lead_in)
    scales = list(brief.clinical_scales or [])
    if analysis and analysis.clinical_scales:
        scales = list(analysis.clinical_scales)
    if not lead:
        lead = (
            "We assessed symptom severity and behavioral characteristics using "
            "standardized rating scales:"
            if scales
            else ""
        )
    return lead, scales


def render_clinical_assessment_en(brief: PaperBrief, analysis: AnalysisBundle | None) -> str:
    lead, scales = resolve_clinical_scales(brief, analysis)
    if not scales:
        return (
            "Clinical/behavioral rating scales were not supplied by upstream modules. "
            "When available, report each scale's full name, domains, administrator, "
            "and scoring interpretation (do not use abbreviations alone)."
        )
    lines = [lead or "Standardized rating scales were used:", ""]
    for index, scale in enumerate(scales, start=1):
        name = clean_text(scale.name) or "[scale_name]"
        abbr = clean_text(scale.abbreviation)
        title = f"{name} ({abbr})" if abbr else name
        bits: list[str] = []
        if scale.n_items is not None:
            bits.append(f"{scale.n_items} items")
        if scale.domains:
            bits.append("covering " + ", ".join(clean_text(d) for d in scale.domains if clean_text(d)))
        if clean_text(scale.description):
            bits.append(clean_text(scale.description))
        cite = f" {clean_text(scale.citation)}" if clean_text(scale.citation) else ""
        core = "; ".join(bits) if bits else "details not fully supplied"
        admin_parts = []
        if clean_text(scale.administered_by):
            admin_parts.append(clean_text(scale.administered_by))
        if clean_text(scale.administration_method):
            admin_parts.append(clean_text(scale.administration_method))
        admin = ". ".join(_cap(p) for p in admin_parts if p)
        score = clean_text(scale.score_interpretation)
        sentence = f"{index}. **{title}**. {_cap(core)}{cite}."
        if admin:
            sentence += f" {admin}."
        if score:
            sentence += f" {_cap(score)}."
        lines.append(sentence)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_clinical_assessment_zh(brief: PaperBrief, analysis: AnalysisBundle | None) -> str:
    lead, scales = resolve_clinical_scales(brief, analysis)
    if not scales:
        return "临床量表槽位未提供（name/abbreviation/domains/administered_by/citation 等）。"
    parts = [f"总起【lead_in={lead or '未提供'}】"]
    for scale in scales:
        parts.append(
            "量表【"
            f"name={scale.name or '未提供'}; abbr={scale.abbreviation or '未提供'}; "
            f"n_items={scale.n_items if scale.n_items is not None else '未提供'}; "
            f"domains={', '.join(scale.domains) if scale.domains else '未提供'}; "
            f"by={scale.administered_by or '未提供'}; "
            f"how={scale.administration_method or '未提供'}; "
            f"score={scale.score_interpretation or '未提供'}; "
            f"cite={scale.citation or '未提供'}】"
        )
    return "；".join(parts) + "。"


def missing_clinical_slots(brief: PaperBrief, analysis: AnalysisBundle | None) -> list[str]:
    _, scales = resolve_clinical_scales(brief, analysis)
    if not scales:
        return ["clinical_scales"]
    missing: list[str] = []
    for index, scale in enumerate(scales, start=1):
        if not clean_text(scale.name):
            missing.append(f"clinical_scales[{index}].name")
        if not clean_text(scale.administered_by) and not clean_text(scale.administration_method):
            missing.append(f"clinical_scales[{index}].administered_by/method")
    return missing


def render_exposure_en(exposure: ExposureMeasurement | None) -> str:
    if exposure is None or not any(
        clean_text(getattr(exposure, key))
        for key in (
            "variable_name",
            "collection_site",
            "processing_workflow",
            "detection_method",
            "quality_control",
            "units",
            "grouping_criteria",
        )
    ):
        return (
            "Core exposure / key-variable measurement slots were not supplied. "
            "Upstream should provide collection site, processing, detection method, "
            "QC, units, and grouping criteria."
        )
    var = clean_text(exposure.variable_name) or "the key variable"
    sentences: list[str] = []
    if clean_text(exposure.collection_site):
        sentences.append(f"Samples for {var} were collected from {clean_text(exposure.collection_site)}.")
    if clean_text(exposure.processing_workflow):
        sentences.append(_cap(clean_text(exposure.processing_workflow)))
    if clean_text(exposure.detection_method):
        sentences.append(
            f"{_cap(var)} concentrations were measured by {clean_text(exposure.detection_method)}."
        )
    if clean_text(exposure.quality_control):
        sentences.append(f"Quality control included {clean_text(exposure.quality_control)}.")
    if clean_text(exposure.units):
        sentences.append(f"Values were expressed in {clean_text(exposure.units)}.")
    if clean_text(exposure.grouping_criteria):
        sentences.append(_cap(clean_text(exposure.grouping_criteria)))
    if clean_text(exposure.additional_notes):
        sentences.append(_cap(clean_text(exposure.additional_notes)))
    return " ".join(sentences)


def render_exposure_zh(exposure: ExposureMeasurement | None) -> str:
    if exposure is None:
        return "核心变量测量槽位未提供。"
    keys = [
        "variable_name",
        "collection_site",
        "processing_workflow",
        "detection_method",
        "quality_control",
        "units",
        "grouping_criteria",
        "additional_notes",
    ]
    return (
        "；".join(
            f"【{key}={clean_text(getattr(exposure, key)) or '未提供'}】" for key in keys
        )
        + "。"
    )


def missing_exposure_slots(exposure: ExposureMeasurement | None) -> list[str]:
    if exposure is None:
        return ["exposure_measurement"]
    required = [
        "variable_name",
        "collection_site",
        "detection_method",
        "units",
    ]
    return [k for k in required if not clean_text(getattr(exposure, k))]


def render_microbiome_en(seq: MicrobiomeSequencing | None) -> str:
    if seq is None or not any(
        clean_text(getattr(seq, key))
        for key in (
            "sample_collection",
            "sample_storage",
            "dna_extraction",
            "sequencing_platform",
            "qc_pipeline",
            "taxonomy_annotation",
            "pathway_annotation",
        )
    ):
        return (
            "Microbiome sequencing slots were not supplied. Upstream should provide "
            "fecal collection/storage, DNA extraction, platform/strategy, QC, and "
            "taxonomy/pathway annotation methods."
        )
    sentences: list[str] = []
    for key in (
        "sample_collection",
        "sample_storage",
        "dna_extraction",
        "library_prep",
        "sequencing_platform",
        "sequencing_strategy",
        "qc_pipeline",
        "taxonomy_annotation",
        "pathway_annotation",
        "post_filter_summary",
    ):
        value = clean_text(getattr(seq, key))
        if value:
            sentences.append(_cap(value))
    return " ".join(sentences)


def render_microbiome_zh(seq: MicrobiomeSequencing | None) -> str:
    if seq is None:
        return "菌群测序槽位未提供。"
    keys = [
        "sample_collection",
        "sample_storage",
        "dna_extraction",
        "library_prep",
        "sequencing_platform",
        "sequencing_strategy",
        "qc_pipeline",
        "taxonomy_annotation",
        "pathway_annotation",
        "post_filter_summary",
    ]
    return "；".join(f"【{k}={clean_text(getattr(seq, k)) or '未提供'}】" for k in keys) + "。"


def missing_microbiome_slots(seq: MicrobiomeSequencing | None) -> list[str]:
    if seq is None:
        return ["microbiome_sequencing"]
    required = [
        "sample_collection",
        "sample_storage",
        "dna_extraction",
        "sequencing_platform",
        "qc_pipeline",
        "taxonomy_annotation",
    ]
    return [k for k in required if not clean_text(getattr(seq, k))]


def render_statistics_en(plan: StatisticalAnalysisPlan | None) -> str:
    if plan is None or not any(
        [
            clean_text(plan.analysis_overview),
            clean_text(plan.group_comparison_method),
            clean_text(plan.continuous_association_method),
            clean_text(plan.multivariate_models),
            plan.covariates,
            clean_text(plan.multiple_testing_correction),
            clean_text(plan.significance_threshold),
            clean_text(plan.software),
        ]
    ):
        # Legacy fallback to methods.differential_analysis bullets handled by caller
        return ""
    lines: list[str] = []
    if clean_text(plan.analysis_overview):
        lines.append(_cap(clean_text(plan.analysis_overview)))
        lines.append("")
    checklist = [
        ("Group comparisons", plan.group_comparison_method),
        ("Continuous associations", plan.continuous_association_method),
        ("Multivariate / integrative models", plan.multivariate_models),
        (
            "Covariates",
            ", ".join(clean_text(c) for c in (plan.covariates or []) if clean_text(c)),
        ),
        ("Multiple-testing correction", plan.multiple_testing_correction),
        ("Significance threshold", plan.significance_threshold),
        ("Software", plan.software),
    ]
    lines.append("Key statistical settings:")
    lines.append("")
    for label, value in checklist:
        value = clean_text(value) if isinstance(value, str) else value
        lines.append(f"- **{label}**: {value or '[not supplied]'}")
    return "\n".join(lines) + "\n"


def render_statistics_zh(plan: StatisticalAnalysisPlan | None) -> str:
    if plan is None:
        return "统计分析槽位未提供。"
    return (
        f"【overview={plan.analysis_overview or '未提供'}】；"
        f"【group_comparison={plan.group_comparison_method or '未提供'}】；"
        f"【continuous_association={plan.continuous_association_method or '未提供'}】；"
        f"【models={plan.multivariate_models or '未提供'}】；"
        f"【covariates={', '.join(plan.covariates) if plan.covariates else '未提供'}】；"
        f"【multiple_testing={plan.multiple_testing_correction or '未提供'}】；"
        f"【threshold={plan.significance_threshold or '未提供'}】；"
        f"【software={plan.software or '未提供'}】。"
    )


def missing_statistics_slots(plan: StatisticalAnalysisPlan | None) -> list[str]:
    if plan is None:
        return ["statistical_analysis"]
    required = [
        "group_comparison_method",
        "continuous_association_method",
        "multiple_testing_correction",
        "significance_threshold",
    ]
    return [k for k in required if not clean_text(getattr(plan, k))]


METHODS_REMAINING_LLM_PROMPT = """
You are polishing Methods subsections AFTER Participants:
1) Clinical assessment
2) Exposure measurement
3) Microbiome sequencing
4) Statistical analysis

Keep subsection headings. Use journal style from the Calcium–ASD paper / writing guide.
For clinical scales: lead-in + numbered items with full name (abbr), domains/items,
who administered, how, score meaning, citation — never abbreviations alone.
For exposure: collection → processing → detection → QC → units → grouping.
For microbiome: collection/storage → DNA → platform/strategy → QC → taxonomy/pathway.
For statistics: cover group tests, continuous associations, models, covariates,
multiple-testing correction, and significance threshold.

HARD RULES: use ONLY supplied slot facts; never invent kit names, platforms, q-value
cutoffs, covariate lists, or scale item counts. Missing slots stay as placeholders.
Output bilingual English + 中文 under each subsection. Output ONLY Markdown.
""".strip()
