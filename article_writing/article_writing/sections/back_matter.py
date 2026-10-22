"""Back matter: admin slots, abbreviations, references, supplements."""

from __future__ import annotations

from article_writing.contracts import PaperState, SectionDraft, SectionId, SectionInput
from article_writing.prompts.back_matter import (
    ABBREVIATIONS_LLM_PROMPT,
    BACK_MATTER_ADMIN_LLM_PROMPT,
    harvest_abbreviation_seeds,
    missing_back_matter_admin_slots,
    render_abbreviations_en,
    render_abbreviations_zh,
    render_acknowledgments_en,
    render_author_contributions_en,
    render_back_matter_admin_zh,
    render_code_availability_en,
    render_conflicts_en,
    render_data_availability_en,
    render_funding_en,
    render_informed_consent_statement_en,
    render_irb_statement_en,
)
from article_writing.registry import register_section
from article_writing.sections.base import BaseSection
from article_writing.sections.text_utils import clean_text


@register_section
class BackMatterSection(BaseSection):
    section_id = SectionId.back_matter
    title = "Back Matter"

    def run(self, section_input: SectionInput, state: PaperState | None = None) -> SectionDraft:
        brief = section_input.brief
        analysis = section_input.analysis
        citations = list(state.citations) if state else []
        figures = list(state.figure_refs) if state else []

        ref_lines: list[str] = []
        if citations:
            for cite in citations:
                year = str(cite.year) if cite.year is not None else "n.d."
                doi = f" doi:{cite.doi}" if cite.doi else ""
                pmid = f" PMID:{cite.pmid}" if cite.pmid else ""
                ref_lines.append(
                    f"- [{cite.cite_id}] {cite.authors + '. ' if cite.authors else ''}"
                    f"{cite.title} ({year}).{doi}{pmid}"
                )
        else:
            for hit in section_input.literature:
                pid = clean_text(hit.paper_id)
                if not pid:
                    continue
                year = str(hit.year) if hit.year is not None else "n.d."
                ref_lines.append(
                    f"- [{pid}] {clean_text(hit.authors) + '. ' if hit.authors else ''}"
                    f"{clean_text(hit.title)} ({year})."
                )

        abbr_entries = harvest_abbreviation_seeds(brief, analysis)
        missing_admin = missing_back_matter_admin_slots(brief)
        user_confirmed_abbr = bool(brief.abbreviations)

        lines = [
            "# Back Matter",
            "",
            "## Author contributions",
            "",
            "#### English",
            "",
            render_author_contributions_en(brief),
            "",
            "## Funding",
            "",
            "#### English",
            "",
            render_funding_en(brief),
            "",
            "## Institutional Review Board Statement",
            "",
            "#### English",
            "",
            render_irb_statement_en(brief),
            "",
            "## Informed Consent Statement",
            "",
            "#### English",
            "",
            render_informed_consent_statement_en(brief),
            "",
            "## Data availability",
            "",
            "#### English",
            "",
            render_data_availability_en(brief),
            "",
            "## Code availability",
            "",
            "#### English",
            "",
            render_code_availability_en(brief),
            "",
            "## Acknowledgments",
            "",
            "#### English",
            "",
            render_acknowledgments_en(brief),
            "",
            "## Conflicts of Interest",
            "",
            "#### English",
            "",
            render_conflicts_en(brief),
            "",
            "## Abbreviations",
            "",
            "#### English",
            "",
            render_abbreviations_en(abbr_entries),
            "",
            "#### 中文（槽位稿 / 缩写种子，供润色）",
            "",
            render_abbreviations_zh(abbr_entries),
            "",
            "#### 中文（行政槽位稿，供润色与向用户索要）",
            "",
            render_back_matter_admin_zh(brief),
            "",
            "## References",
            "",
        ]
        if ref_lines:
            lines.extend(ref_lines)
        else:
            lines.append(
                "- No references were accumulated from Introduction/Discussion citations."
            )

        lines.extend(["", "## Supplementary materials", ""])
        if figures:
            lines.append(
                "Supplementary displays currently linked from the analysis package "
                "(paths may be placeholders until visualization exports finals):"
            )
            lines.append("")
            for fig in figures:
                lines.append(
                    f"- `{fig.figure_id}` ({fig.kind}): "
                    f"{fig.caption or 'no caption'} — `{fig.path}`"
                )
        else:
            lines.append("- No supplementary figure/table paths were listed in PaperState.")
        lines.append("")
        lines.append(
            "Additional supplementary tables (full differential feature lists, "
            "sensitivity analyses) should be attached by the analysis/visualization modules."
        )
        lines.append("")

        warnings: list[str] = []
        if not clean_text(brief.authors) and not clean_text(brief.author_contributions):
            warnings.append("paper_brief.authors is empty")
        for key in missing_admin:
            warnings.append(f"back matter slot missing: {key}")
        if not abbr_entries:
            warnings.append(
                "abbreviations empty — provide PaperBrief.abbreviations or fill "
                "clinical/exposure/stats slots; LLM may suggest from manuscript only"
            )
        elif not user_confirmed_abbr:
            warnings.append(
                "abbreviations harvested as seeds (not user-confirmed); review before publish"
            )
        if not ref_lines:
            warnings.append("no references available for back matter")

        admin_ok = len(missing_admin) <= 3  # allow partial drafts
        return SectionDraft(
            section=self.section_id,
            title=self.title,
            markdown="\n".join(lines),
            citations=citations,
            figure_refs=figures,
            warnings=warnings,
            metadata={
                "template": "back_matter_slots_v1",
                "n_references": len(ref_lines),
                "admin_slots_missing": missing_admin,
                "n_abbreviations": len(abbr_entries),
                "abbreviations_user_confirmed": user_confirmed_abbr,
                "user_input_needed": missing_admin,
                "draft_status": "complete" if (admin_ok and ref_lines) else "degraded",
                "llm_instructions_extra": "\n\n".join(
                    [BACK_MATTER_ADMIN_LLM_PROMPT, ABBREVIATIONS_LLM_PROMPT]
                ),
            },
        )
