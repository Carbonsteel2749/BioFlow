"""Methods aligned to journal Materials and Methods subsections."""

from __future__ import annotations

from article_writing.contracts import Claim, PaperState, SectionDraft, SectionId, SectionInput
from article_writing.prompts.methods_participants_ethics import (
    ETHICS_CONSENT_LLM_PROMPT,
    missing_ethics_slots,
    render_ethics_consent_paragraph,
    render_ethics_consent_zh_slot_line,
)
from article_writing.prompts.methods_participants_overview import (
    OVERVIEW_ELIGIBILITY_LLM_PROMPT,
    PARTICIPANTS_COMBINED_LLM_PROMPT,
    missing_eligibility_slots,
    missing_overview_slots,
    render_eligibility_block,
    render_eligibility_zh_slot_line,
    render_overview_paragraph,
    render_overview_zh_slot_line,
)
from article_writing.prompts.methods_remaining import (
    METHODS_REMAINING_LLM_PROMPT,
    missing_clinical_slots,
    missing_exposure_slots,
    missing_microbiome_slots,
    missing_statistics_slots,
    render_clinical_assessment_en,
    render_clinical_assessment_zh,
    render_exposure_en,
    render_exposure_zh,
    render_microbiome_en,
    render_microbiome_zh,
    render_statistics_en,
    render_statistics_zh,
)
from article_writing.registry import register_section
from article_writing.sections.base import BaseSection
from article_writing.sections.text_utils import clean_text, display_term, format_method_value


@register_section
class MethodsSection(BaseSection):
    section_id = SectionId.methods
    title = "Methods"

    def run(self, section_input: SectionInput, state: PaperState | None = None) -> SectionDraft:
        del state
        brief = section_input.brief
        analysis = section_input.analysis
        methods = dict(analysis.methods) if analysis else {}
        evidence_ids = list(analysis.evidence_ids) if analysis else []
        summary = clean_text(analysis.summary) if analysis else ""
        figures = list(analysis.figure_refs) if analysis else []

        exposure = analysis.exposure_measurement if analysis else None
        microbiome = analysis.microbiome_sequencing if analysis else None
        stats_plan = analysis.statistical_analysis if analysis else None

        lines = ["# Methods", ""]

        # Participants (journal §2.1): overview + eligibility + ethics/consent
        lines.extend(["## Participants", ""])
        question = clean_text(brief.research_question)
        if question:
            lines.append(f"The methods are oriented toward answering: **{question}**.")
            lines.append("")

        lines.extend(["### Study overview", ""])
        lines.append("#### English")
        lines.append("")
        lines.append(render_overview_paragraph(brief))
        lines.append("")
        lines.append("#### 中文（槽位稿，供润色）")
        lines.append("")
        lines.append(render_overview_zh_slot_line(brief))
        lines.append("")
        missing_overview = missing_overview_slots(brief)

        lines.extend(["### Eligibility criteria", ""])
        lines.append("#### English")
        lines.append("")
        lines.append(render_eligibility_block(brief))
        lines.append("")
        lines.append("#### 中文（槽位稿，供润色）")
        lines.append("")
        lines.append(render_eligibility_zh_slot_line(brief))
        lines.append("")
        missing_eligibility = missing_eligibility_slots(brief)

        lines.extend(["### Ethics approval and informed consent", ""])
        ethics_en = render_ethics_consent_paragraph(brief)
        ethics_zh = render_ethics_consent_zh_slot_line(brief)
        lines.append("#### English")
        lines.append("")
        lines.append(ethics_en)
        lines.append("")
        lines.append("#### 中文（槽位稿，供润色）")
        lines.append("")
        lines.append(ethics_zh)
        lines.append("")
        missing_ethics = missing_ethics_slots(brief)

        # Clinical assessment (§4.4.2 / journal §2.2)
        lines.extend(["## Clinical assessment", ""])
        lines.append("#### English")
        lines.append("")
        lines.append(render_clinical_assessment_en(brief, analysis))
        lines.append("")
        lines.append("#### 中文（槽位稿，供润色）")
        lines.append("")
        lines.append(render_clinical_assessment_zh(brief, analysis))
        lines.append("")
        missing_clinical = missing_clinical_slots(brief, analysis)

        # Exposure / core variable measurement (§4.4.3)
        lines.extend(["## Exposure measurement", ""])
        lines.append("#### English")
        lines.append("")
        lines.append(render_exposure_en(exposure))
        lines.append("")
        lines.append("#### 中文（槽位稿，供润色）")
        lines.append("")
        lines.append(render_exposure_zh(exposure))
        lines.append("")
        missing_exposure = missing_exposure_slots(exposure)

        # Microbiome sequencing (§4.4.4)
        lines.extend(["## Microbiome sequencing", ""])
        lines.append("#### English")
        lines.append("")
        lines.append(render_microbiome_en(microbiome))
        lines.append("")
        lines.append("#### 中文（槽位稿，供润色）")
        lines.append("")
        lines.append(render_microbiome_zh(microbiome))
        lines.append("")
        missing_microbiome = missing_microbiome_slots(microbiome)

        # Optional pipeline provenance (legacy methods dict + summary)
        if summary or methods:
            lines.extend(["## Analytical pipeline notes", ""])
            if summary:
                lines.append(f"**Pipeline overview.** {summary}")
                lines.append("")
            if methods:
                lines.append(
                    "Additional procedures reconstructed from the analysis package "
                    "`methods` block (supplement to the slots above):"
                )
                lines.append("")
                preferred = ["qc", "normalization", "differential_analysis"]
                ordered_keys = [k for k in preferred if k in methods] + [
                    k for k in methods if k not in preferred
                ]
                for key in ordered_keys:
                    value = methods[key]
                    label = display_term(key)
                    if key.casefold() == "qc":
                        lines.append(
                            f"- **Quality control ({label})**: {format_method_value(value)}"
                        )
                    elif key.casefold() == "normalization":
                        lines.append(
                            f"- **Normalization ({label})**: {format_method_value(value)}"
                        )
                    elif key.casefold() == "differential_analysis":
                        lines.append(
                            f"- **Differential / association analysis ({label})**: "
                            f"{format_method_value(value)}"
                        )
                    else:
                        lines.append(f"- **{label}**: {format_method_value(value)}")
                lines.append("")
            if figures:
                lines.append(
                    "Intermediate visual diagnostics referenced by the pipeline include: "
                    + ", ".join(f"`{fig.figure_id}`" for fig in figures)
                    + "."
                )
                lines.append("")

        # Statistical analysis (§4.4.5)
        lines.extend(["## Statistical analysis", ""])
        stats_en = render_statistics_en(stats_plan)
        lines.append("#### English")
        lines.append("")
        if stats_en:
            lines.append(stats_en)
        else:
            # Legacy fallback from methods.differential_analysis
            diff = methods.get("differential_analysis")
            if isinstance(diff, dict) and diff:
                lines.append(
                    "Inferential settings for the primary contrast were taken from "
                    "`differential_analysis` (structured statistical slots not supplied):"
                )
                lines.append("")
                for key, value in diff.items():
                    lines.append(f"- **{display_term(str(key))}**: {value}")
                lines.append("")
            else:
                lines.append(
                    "Statistical analysis slots were not supplied. Upstream should provide "
                    "group comparison methods, continuous association methods, models, "
                    "covariates, multiple-testing correction, and significance thresholds."
                )
                lines.append("")
        lines.append("#### 中文（槽位稿，供润色）")
        lines.append("")
        lines.append(render_statistics_zh(stats_plan))
        lines.append("")
        missing_stats = missing_statistics_slots(stats_plan)

        lines.extend(["## Ethics cross-reference", ""])
        lines.append(
            "The Participants subsection above is the primary ethics/consent narrative. "
            "Back Matter may repeat a short institutional statement for journal checklists."
        )
        lines.append("")

        claims: list[Claim] = []
        if methods:
            claims.append(
                Claim(
                    claim_id="methods_pipeline",
                    statement=(
                        "Analysis followed the methods encoded in the analysis bundle: "
                        + ", ".join(display_term(k) for k in methods)
                        + "."
                    ),
                    evidence_ids=evidence_ids[:1],
                    section=self.section_id.value,
                )
            )
        if analysis and (
            analysis.clinical_scales
            or analysis.exposure_measurement
            or analysis.microbiome_sequencing
            or analysis.statistical_analysis
        ):
            claims.append(
                Claim(
                    claim_id="methods_structured_slots",
                    statement=(
                        "Clinical, exposure, microbiome, and/or statistical Methods "
                        "subsections were filled from structured AnalysisBundle slots."
                    ),
                    evidence_ids=evidence_ids[:1],
                    section=self.section_id.value,
                )
            )

        warnings: list[str] = []
        if analysis is None:
            warnings.append("analysis bundle missing")
        elif not methods and not any(
            [
                analysis.clinical_scales,
                analysis.exposure_measurement,
                analysis.microbiome_sequencing,
                analysis.statistical_analysis,
            ]
        ):
            warnings.append("analysis.methods is empty")
        if not clean_text(brief.organism_or_system):
            warnings.append("paper_brief.organism_or_system is empty")
        for key in missing_overview:
            warnings.append(f"participants overview slot missing: {key}")
        for key in missing_eligibility:
            warnings.append(f"participants eligibility slot missing: {key}")
        for key in missing_ethics:
            warnings.append(f"participants ethics slot missing: {key}")
        for key in missing_clinical:
            warnings.append(f"clinical assessment slot missing: {key}")
        for key in missing_exposure:
            warnings.append(f"exposure measurement slot missing: {key}")
        for key in missing_microbiome:
            warnings.append(f"microbiome sequencing slot missing: {key}")
        for key in missing_stats:
            warnings.append(f"statistical analysis slot missing: {key}")

        structured_ok = bool(
            analysis
            and (
                analysis.clinical_scales
                or analysis.exposure_measurement
                or analysis.microbiome_sequencing
                or analysis.statistical_analysis
                or methods
            )
        )

        return SectionDraft(
            section=self.section_id,
            title=self.title,
            markdown="\n".join(lines),
            claims=claims,
            figure_refs=figures,
            warnings=warnings,
            metadata={
                "template": "methods_full_slots_v1",
                "n_methods": len(methods),
                "overview_slots_missing": missing_overview,
                "eligibility_slots_missing": missing_eligibility,
                "ethics_slots_missing": missing_ethics,
                "clinical_slots_missing": missing_clinical,
                "exposure_slots_missing": missing_exposure,
                "microbiome_slots_missing": missing_microbiome,
                "statistics_slots_missing": missing_stats,
                "llm_instructions_extra": "\n\n".join(
                    [
                        PARTICIPANTS_COMBINED_LLM_PROMPT,
                        OVERVIEW_ELIGIBILITY_LLM_PROMPT,
                        ETHICS_CONSENT_LLM_PROMPT,
                        METHODS_REMAINING_LLM_PROMPT,
                    ]
                ),
                "draft_status": "complete" if structured_ok else "degraded",
            },
        )
