"""Abstract drafting: structured mini-paper (MDPI / journal style)."""

from __future__ import annotations

from article_writing.contracts import Claim, PaperState, SectionDraft, SectionId, SectionInput
from article_writing.registry import register_section
from article_writing.sections.base import BaseSection
from article_writing.sections.text_utils import clean_text, display_term, unique_values


@register_section
class AbstractSection(BaseSection):
    section_id = SectionId.abstract
    title = "Abstract"

    def run(self, section_input: SectionInput, state: PaperState | None = None) -> SectionDraft:
        del state
        brief = section_input.brief
        analysis = section_input.analysis
        question = clean_text(brief.research_question)
        title = clean_text(brief.title)
        system = clean_text(brief.organism_or_system)
        keywords = unique_values(brief.keywords)
        summary = clean_text(analysis.summary) if analysis else ""
        findings = [clean_text(str(f)) for f in (analysis.key_findings if analysis else [])]
        metrics = dict(analysis.metrics) if analysis else {}
        limitations = list(analysis.limitations) if analysis else []
        modality = display_term(brief.data_modality) if brief.data_modality else "omics"
        methods = dict(analysis.methods) if analysis else {}

        # Background / Objectives (common in Metabolites / Biomedicines abstracts)
        if question:
            background = (
                f"**Background/Objectives:** {title + '. ' if title else ''}"
                f"We investigated {question} "
                f"in the context of {system or 'the specified biological system'}, "
                f"focusing on {modality} profiling"
                + (f" related to {', '.join(keywords[:4])}." if keywords else ".")
            )
        else:
            background = (
                "**Background/Objectives:** The study objective is not fully specified "
                "in the paper brief."
            )

        # Methods
        method_bits: list[str] = [f"{modality} data"]
        if "qc" in methods:
            method_bits.append(f"QC ({methods['qc']})")
        if "normalization" in methods:
            method_bits.append(f"normalization ({methods['normalization']})")
        if isinstance(methods.get("differential_analysis"), dict):
            diff = methods["differential_analysis"]
            method_bits.append(
                "differential analysis with "
                f"{diff.get('method', 'specified test')}"
                + (
                    f" (|log2FC|≥{diff.get('log2fc_threshold')}, "
                    f"FDR<{diff.get('padj_threshold')})"
                    if diff.get("log2fc_threshold") is not None
                    else ""
                )
            )
        methods_para = (
            "**Methods:** "
            + (
                f"{summary} Analytical steps included: " + "; ".join(method_bits) + "."
                if summary
                else "We applied the analysis-package workflow covering "
                + "; ".join(method_bits)
                + "."
            )
        )

        # Results
        metric_bits = []
        for key in ("n_significant", "n_up", "n_down", "n_genes_tested"):
            if key in metrics:
                metric_bits.append(f"{key}={metrics[key]}")
        if findings:
            results_para = (
                "**Results:** "
                + " ".join(f"({i}) {f}." for i, f in enumerate(findings[:3], 1))
                + (
                    " Supporting quantitative metrics include "
                    + ", ".join(metric_bits)
                    + "."
                    if metric_bits
                    else ""
                )
            )
        else:
            results_para = (
                "**Results:** Key findings were not supplied in the analysis package."
            )

        # Conclusions
        if limitations:
            conclusions_para = (
                "**Conclusions:** The principal findings above provide an evidence-bound "
                "summary of the current analysis. Interpretation should remain cautious "
                f"given listed limitations (e.g., {limitations[0]}). Independent validation "
                "and richer multi-omics follow-up are warranted where applicable."
            )
        else:
            conclusions_para = (
                "**Conclusions:** The analysis supports the reported findings; broader "
                "generalization requires additional cohorts and explicit limitation review."
            )

        keyword_line = (
            f"**Keywords:** {'; '.join(keywords)}" if keywords else ""
        )

        markdown_parts = [
            "# Abstract",
            "",
            background,
            "",
            methods_para,
            "",
            results_para,
            "",
            conclusions_para,
            "",
        ]
        if keyword_line:
            markdown_parts.extend([keyword_line, ""])

        claims: list[Claim] = []
        if question:
            claims.append(
                Claim(
                    claim_id="abstract_objective",
                    statement=f"The study objective is reflected by: {question}",
                    evidence_ids=[],
                    section=self.section_id.value,
                )
            )

        warnings: list[str] = []
        if not question:
            warnings.append("paper_brief.research_question is empty")
        if analysis is None:
            warnings.append("analysis bundle missing")
        elif not findings:
            warnings.append("analysis.key_findings is empty")

        return SectionDraft(
            section=self.section_id,
            title=self.title,
            markdown="\n".join(markdown_parts),
            claims=claims,
            warnings=warnings,
            metadata={
                "template": "structured_abstract_v2_paper_aligned",
                "draft_status": "complete" if question and analysis is not None else "degraded",
            },
        )
