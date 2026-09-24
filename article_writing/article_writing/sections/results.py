"""Results: cohort metrics → primary findings (with inline figures) → tables."""

from __future__ import annotations

from article_writing.contracts import Claim, FigureRef, PaperState, SectionDraft, SectionId, SectionInput
from article_writing.layout.figures import render_figure_embed
from article_writing.prompts.results import RESULTS_NEGATIVE_CONSTRAINTS_PROMPT
from article_writing.registry import register_section
from article_writing.sections.base import BaseSection
from article_writing.sections.text_utils import clean_text, display_term


def _split_figures(figure_refs: list[FigureRef]) -> tuple[list[FigureRef], list[FigureRef]]:
    figures = [r for r in figure_refs if (r.kind or "figure").lower() != "table"]
    tables = [r for r in figure_refs if (r.kind or "figure").lower() == "table"]
    return figures, tables


@register_section
class ResultsSection(BaseSection):
    section_id = SectionId.results
    title = "Results"

    def run(self, section_input: SectionInput, state: PaperState | None = None) -> SectionDraft:
        del state
        analysis = section_input.analysis
        findings = list(analysis.key_findings) if analysis else []
        metrics = dict(analysis.metrics) if analysis else {}
        figure_refs = list(analysis.figure_refs) if analysis else []
        evidence_ids = list(analysis.evidence_ids) if analysis else []
        summary = clean_text(analysis.summary) if analysis else ""
        question = clean_text(section_input.brief.research_question)
        methods = dict(analysis.methods) if analysis else {}
        figures, tables = _split_figures(figure_refs)

        lines = ["# Results", ""]
        if question:
            lines.append(
                f"Results are organized to address the study question: **{question}**"
            )
            lines.append("")

        lines.extend(["## Study overview and quantitative metrics", ""])
        if summary:
            lines.append(f"**Analysis snapshot.** {summary}")
            lines.append("")
        if metrics:
            lines.append("Key quantitative metrics reported by the analysis package:")
            lines.append("")
            for key, value in metrics.items():
                lines.append(f"- **{display_term(str(key))}** (`{key}`): {value}")
            lines.append("")
            if "n_significant" in metrics and "n_genes_tested" in metrics:
                lines.append(
                    "These counts define the breadth of testing and the size of the "
                    "significant set used in subsequent interpretive claims."
                )
                lines.append("")
        else:
            lines.append("- No metrics were supplied in the analysis bundle.")
            lines.append("")

        # Lead figure after overview (common journal placement)
        if figures:
            lines.extend(["## Overview display", ""])
            lines.append(render_figure_embed(figures[0], display_index=1))

        lines.extend(["## Principal findings", ""])
        claims: list[Claim] = []
        diff = methods.get("differential_analysis")
        if isinstance(diff, dict) and diff.get("group_a") and diff.get("group_b"):
            lines.append(
                f"Primary contrasts compare **{diff['group_a']}** versus "
                f"**{diff['group_b']}**."
            )
            lines.append("")

        used_figure_ids = {figures[0].figure_id} if figures else set()
        fig_display_i = 2 if figures else 1
        remaining_figs = figures[1:]

        if findings:
            for index, finding in enumerate(findings, start=1):
                text = clean_text(str(finding))
                lines.append(f"### Finding {index}")
                lines.append("")
                lines.append(text)
                lines.append("")
                mapped: list[str] = []
                if evidence_ids:
                    mapped = [evidence_ids[min(index - 1, len(evidence_ids) - 1)]]
                # Place next unused figure next to this finding when available
                if remaining_figs:
                    fig = remaining_figs.pop(0)
                    lines.append(
                        render_figure_embed(fig, display_index=fig_display_i)
                    )
                    used_figure_ids.add(fig.figure_id)
                    fig_display_i += 1
                claims.append(
                    Claim(
                        claim_id=f"result_finding_{index}",
                        statement=text,
                        evidence_ids=mapped,
                        section=self.section_id.value,
                    )
                )
        else:
            lines.append("No key findings were supplied in the analysis bundle.")
            lines.append("")

        secondary_keys = [
            k for k in metrics if k not in {"n_genes_tested", "n_significant", "n_up", "n_down"}
        ]
        if secondary_keys:
            lines.extend(["## Secondary quantitative observations", ""])
            lines.append(
                "Additional package metrics that may support robustness or external "
                "agreement checks:"
            )
            lines.append("")
            for key in secondary_keys:
                lines.append(f"- **{display_term(str(key))}** (`{key}`): {metrics[key]}")
            lines.append("")

        if tables or remaining_figs:
            lines.extend(["## Figures and tables", ""])
            lines.append(
                "Remaining displays from the analysis/visualization package are inserted below. "
                "Captions and paths are upstream-owned and must not be altered by polishing."
            )
            lines.append("")
            table_i = 1
            for ref in remaining_figs:
                lines.append(render_figure_embed(ref, display_index=fig_display_i))
                fig_display_i += 1
            for ref in tables:
                lines.append(render_figure_embed(ref, display_index=table_i))
                table_i += 1
        elif not figure_refs:
            lines.extend(["## Figures and tables", ""])
            lines.append("- No figure/table references were supplied.")
            lines.append("")

        warnings: list[str] = []
        if analysis is None:
            warnings.append("analysis bundle missing")
        else:
            if not findings:
                warnings.append("analysis.key_findings is empty")
            if not metrics:
                warnings.append("analysis.metrics is empty")
            if findings and evidence_ids and len(findings) != len(evidence_ids):
                warnings.append(
                    "analysis.evidence_ids count does not match analysis.key_findings count"
                )

        return SectionDraft(
            section=self.section_id,
            title=self.title,
            markdown="\n".join(lines),
            claims=claims,
            figure_refs=figure_refs,
            warnings=warnings,
            metadata={
                "template": "results_paper_aligned_v3_inline_figures",
                "n_findings": len(findings),
                "n_metrics": len(metrics),
                "n_figures": len(figure_refs),
                "draft_status": "complete" if findings else "degraded",
                "llm_instructions_extra": RESULTS_NEGATIVE_CONSTRAINTS_PROMPT,
            },
        )
