"""Conclusion: standalone closing statements (never merged into Discussion)."""

from __future__ import annotations

from article_writing.contracts import Claim, PaperState, SectionDraft, SectionId, SectionInput
from article_writing.prompts.conclusion import CONCLUSION_STRUCTURE_PROMPT
from article_writing.registry import register_section
from article_writing.sections.base import BaseSection
from article_writing.sections.text_utils import clean_text, unique_values


@register_section
class ConclusionSection(BaseSection):
    section_id = SectionId.conclusion
    title = "Conclusions"

    def run(self, section_input: SectionInput, state: PaperState | None = None) -> SectionDraft:
        prior_claims = [
            c
            for c in (state.confirmed_claims if state else [])
            if c.section
            in {
                SectionId.results.value,
                SectionId.methods.value,
                SectionId.introduction.value,
            }
        ]
        result_claims = [c for c in prior_claims if c.section == SectionId.results.value]
        focus = result_claims or prior_claims

        limitations = unique_values(
            (list(section_input.analysis.limitations) if section_input.analysis else [])
            + (list(state.limitations) if state else [])
        )
        question = clean_text(section_input.brief.research_question)
        title = clean_text(section_input.brief.title)

        lines = [
            "# Conclusions",
            "",
            "This section states **standalone conclusions** and is **not merged into Discussion**.",
            "",
            "## Principal conclusions",
            "",
        ]
        if title:
            lines.append(f'For the study "{title}":')
            lines.append("")
        if question:
            lines.append(f"Addressing the question (**{question}**), we conclude that:")
            lines.append("")
        if focus:
            for index, claim in enumerate(focus[:8], start=1):
                lines.append(f"{index}. {claim.statement}")
        else:
            lines.append("- No upstream analytical conclusions were available.")
        lines.append("")

        lines.extend(["## Scientific and practical implications", ""])
        if focus:
            lines.append(
                "These conclusions summarize analysis-supported observations suitable for "
                "downstream reporting and visualization. They should be read as evidence-bounded "
                "statements: effect sizes, thresholds, and figure references remain those reported "
                "in Results, and no additional numeric claims are introduced here."
            )
        else:
            lines.append(
                "Conclusions cannot be finalized until Methods/Results claims are available."
            )
        lines.append("")

        lines.extend(["## Limitations to keep in view", ""])
        lines.append(
            "Although limitations are discussed above, the following points remain essential "
            "closing caveats:"
        )
        lines.append("")
        if limitations:
            for item in limitations:
                lines.append(f"- {item}")
        else:
            lines.append("- No limitations were listed.")
        lines.append("")

        lines.extend(["## Closing statement", ""])
        if focus:
            lines.append(
                "In summary, the manuscript reports a contract-driven draft in which Methods, "
                "Results, Discussion, and Conclusions remain separable. Future live adapters "
                "and optional language models may enrich prose, but must preserve the factual "
                "payload supplied by analysis and literature ports."
            )
        else:
            lines.append(
                "Closing synthesis awaits upstream findings from the analysis package."
            )
        lines.append("")

        claim = Claim(
            claim_id="conclusion_summary",
            statement=(
                f"Standalone conclusions summarize {len(focus)} upstream finding claim(s) "
                f"and {len(limitations)} limitation(s)."
            ),
            evidence_ids=[c.claim_id for c in focus[:3]],
            section=self.section_id.value,
        )
        return SectionDraft(
            section=self.section_id,
            title=self.title,
            markdown="\n".join(lines),
            claims=[claim],
            warnings=[] if focus else ["no upstream claims for conclusions"],
            metadata={
                "template": "conclusions_standalone_v2",
                "n_upstream_claims": len(focus),
                "n_limitations": len(limitations),
                "draft_status": "complete" if focus else "degraded",
                "llm_instructions_extra": CONCLUSION_STRUCTURE_PROMPT,
            },
        )
