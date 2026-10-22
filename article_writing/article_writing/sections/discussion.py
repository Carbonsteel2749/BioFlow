"""Discussion: summary → literature comparison → mechanisms → limitations → outlook."""

from __future__ import annotations

from article_writing.contracts import (
    Citation,
    Claim,
    LiteratureHit,
    PaperState,
    SectionDraft,
    SectionId,
    SectionInput,
)
from article_writing.prompts.discussion import DISCUSSION_STRUCTURE_PROMPT
from article_writing.registry import register_section
from article_writing.sections.base import BaseSection
from article_writing.sections.text_utils import clean_text, truncate, unique_values


@register_section
class DiscussionSection(BaseSection):
    section_id = SectionId.discussion
    title = "Discussion"

    def run(self, section_input: SectionInput, state: PaperState | None = None) -> SectionDraft:
        analysis = section_input.analysis
        findings = list(analysis.key_findings) if analysis else []
        evidence_ids = list(analysis.evidence_ids) if analysis else []
        hits = [h for h in section_input.literature if clean_text(h.paper_id)]
        limitations = unique_values(
            (list(analysis.limitations) if analysis else [])
            + (list(state.limitations) if state else [])
        )
        question = clean_text(section_input.brief.research_question)
        system = clean_text(section_input.brief.organism_or_system)

        result_claims = []
        if state:
            result_claims = [
                c for c in state.confirmed_claims if c.section == SectionId.results.value
            ]

        lines = ["# Discussion", ""]
        claims: list[Claim] = []

        # Opening summary paragraph (common journal move)
        lines.extend(["## Interpretation of principal findings", ""])
        if question:
            lines.append(
                f"Relative to the study question (**{question}**), the analysis package "
                "supports the following evidence-bound observations."
            )
            lines.append("")
        if result_claims or findings:
            lines.append(
                "We first restate the principal findings and then interpret their scope "
                "without introducing numeric claims absent from Results:"
            )
            lines.append("")
            source_claims = result_claims
            if not source_claims:
                for index, finding in enumerate(findings, start=1):
                    mapped = (
                        [evidence_ids[min(index - 1, len(evidence_ids) - 1)]]
                        if evidence_ids
                        else []
                    )
                    source_claims.append(
                        Claim(
                            claim_id=f"result_finding_{index}",
                            statement=clean_text(str(finding)),
                            evidence_ids=mapped,
                            section=SectionId.results.value,
                        )
                    )
            for claim in source_claims:
                lines.append(f"- {claim.statement}")
                claims.append(
                    Claim(
                        claim_id=f"disc_interpret_{claim.claim_id}",
                        statement=(
                            f"The finding “{claim.statement}” is interpreted as an "
                            "analysis-supported observation rather than a causal proof."
                        ),
                        evidence_ids=list(claim.evidence_ids) or evidence_ids[:1],
                        section=self.section_id.value,
                    )
                )
            lines.append("")
            lines.append(
                "Taken together, these results delineate a measurable signal in the "
                f"studied system{f' ({system})' if system else ''} while remaining bounded "
                "by the analytical definitions encoded in Methods."
            )
        else:
            lines.append("- No upstream results were available for interpretation.")
        lines.append("")

        # Literature comparison / mechanisms
        lines.extend(["## Mechanisms and literature context", ""])
        citations = [self._citation(hit) for hit in hits[:5]]
        if hits:
            lines.append(
                "Comparison with prior literature helps situate whether the observed "
                "patterns are consistent with known biological or methodological themes. "
                "The following records were supplied for contextualization:"
            )
            lines.append("")
            for hit in hits[:5]:
                cite = clean_text(hit.paper_id)
                year = str(hit.year) if hit.year is not None else "n.d."
                lines.append(
                    f"- [{cite}] {clean_text(hit.title)} ({year}). "
                    f"{truncate(hit.abstract, 240) or 'No abstract supplied.'}"
                )
                claims.append(
                    Claim(
                        claim_id=f"disc_lit_{cite}",
                        statement=(
                            f"Literature context from {clean_text(hit.title)}: "
                            f"{truncate(hit.abstract) or 'metadata-only'}"
                        ),
                        evidence_ids=[cite],
                        section=self.section_id.value,
                    )
                )
            lines.append("")
            lines.append(
                "Where themes overlap (for example immune, barrier, or metabolic motifs "
                "appearing both in literature tags and in the present study keywords), "
                "they should be treated as hypotheses for follow-up rather than as "
                "demonstrated mechanisms unless directly supported by the analysis package."
            )
        else:
            lines.append(
                "No literature records were supplied; mechanistic discussion is limited "
                "to the analysis package narrative and should be expanded once LiteraturePort "
                "returns curated hits."
            )
        lines.append("")

        # Limitations
        lines.extend(["## Limitations", ""])
        lines.append(
            "Several limitations qualify the present draft and mirror constraints commonly "
            "acknowledged in observational multi-omics studies:"
        )
        lines.append("")
        if limitations:
            for item in limitations:
                lines.append(f"- {item}")
        else:
            lines.append("- No limitations were listed in the analysis package or PaperState.")
        lines.append(
            "- Template-generated prose does not replace expert clinical or statistical review."
        )
        lines.append("")

        # Outlook (short; detailed conclusions stay in Conclusions)
        lines.extend(["## Outlook", ""])
        lines.append(
            "Future work should prioritize independent cohort validation, richer phenotype "
            "linkage, and tighter synchronization between analysis provenance and manuscript "
            "claims. Standalone Conclusions follow this Discussion and are not merged here."
        )
        lines.append("")

        warnings: list[str] = []
        if analysis is None:
            warnings.append("analysis bundle missing")
        if not findings and not result_claims:
            warnings.append("no findings available for discussion")
        if not hits:
            warnings.append("section_input.literature is empty for discussion context")
        if not limitations:
            warnings.append("no limitations supplied")

        return SectionDraft(
            section=self.section_id,
            title=self.title,
            markdown="\n".join(lines),
            claims=claims,
            citations=citations,
            warnings=warnings,
            metadata={
                "template": "discussion_paper_aligned_v2",
                "n_literature": len(hits),
                "n_limitations": len(limitations),
                "draft_status": "complete" if (findings or result_claims) else "degraded",
                "llm_instructions_extra": DISCUSSION_STRUCTURE_PROMPT,
            },
        )

    @staticmethod
    def _citation(hit: LiteratureHit) -> Citation:
        return Citation(
            cite_id=clean_text(hit.paper_id),
            title=clean_text(hit.title),
            authors=clean_text(hit.authors),
            year=hit.year,
            doi=clean_text(hit.doi or "") or None,
            pmid=clean_text(hit.pmid or "") or None,
            snippet=truncate(hit.abstract),
            source="fixture" if hit.paper_id.startswith("lit_") else "adapter",
        )
