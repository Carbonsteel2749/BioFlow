"""Introduction: Background → Gap → Objective (literature woven as in journal intros)."""

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
from article_writing.registry import register_section
from article_writing.sections.base import BaseSection
from article_writing.sections.text_utils import clean_text, display_term, truncate, unique_values


@register_section
class IntroductionSection(BaseSection):
    section_id = SectionId.introduction
    title = "Introduction"

    def run(self, section_input: SectionInput, state: PaperState | None = None) -> SectionDraft:
        del state
        brief = section_input.brief
        hits = [h for h in section_input.literature if clean_text(h.paper_id)]
        title = clean_text(brief.title)
        question = clean_text(brief.research_question)
        system = clean_text(brief.organism_or_system)
        modality = display_term(brief.data_modality) if brief.data_modality else ""
        keywords = unique_values(brief.keywords)
        notes = unique_values(brief.notes)
        citations = [self._citation(hit) for hit in hits[:5]]
        claims: list[Claim] = []
        react_meta = section_input.payload.get("react") or {}

        lines: list[str] = ["# Introduction", "", "## Background", ""]

        # Paragraph 1: clinical / biological framing (ASD-style intros)
        theme = ", ".join(keywords[:3]) if keywords else "the target phenotype"
        if system:
            lines.append(
                f"Research on {theme} increasingly emphasizes host–environment and "
                f"molecular interactions within {system}. "
                "Understanding how measurable molecular profiles relate to phenotype "
                "remains a central challenge for mechanism-oriented and translational studies."
            )
        else:
            lines.append(
                f"Research on {theme} increasingly emphasizes molecular and ecological "
                "determinants of phenotype. Connecting measurable molecular profiles to "
                "clinical or biological outcomes remains an open challenge."
            )
        lines.append("")

        # Paragraph 2: data modality / analytical opportunity
        if modality:
            lines.append(
                f"High-throughput {modality} profiling enables systematic comparison across "
                "conditions and can surface candidate markers, pathways, or community-level "
                "shifts. However, raw measurement tables alone do not constitute an "
                "interpretable scientific narrative: quality control, normalization, "
                "inferential testing, and evidence-linked reporting are required."
            )
            lines.append("")

        # Paragraph 3: prior literature (woven, not a separate Related Work chapter)
        if hits:
            lines.append(
                "Prior studies provide contextual evidence for the biological and "
                "methodological setting of this work:"
            )
            lines.append("")
            for hit in hits[:5]:
                cite = clean_text(hit.paper_id)
                year = str(hit.year) if hit.year is not None else "n.d."
                authors = clean_text(hit.authors) or "et al."
                abstract = truncate(hit.abstract, 220) or "No abstract was supplied."
                lines.append(
                    f"- **{clean_text(hit.title)}** ({authors}, {year}; [{cite}]). {abstract}"
                )
                claims.append(
                    Claim(
                        claim_id=f"intro_bg_{cite}",
                        statement=(
                            f"{clean_text(hit.title)}: "
                            f"{truncate(hit.abstract) or 'metadata-only record'}"
                        ),
                        evidence_ids=[cite],
                        section=self.section_id.value,
                    )
                )
            lines.append("")
            lines.append(
                "Collectively, these reports motivate an analysis that is both "
                "computationally explicit and tightly linked to traceable evidence."
            )
        else:
            lines.append(
                "No literature records were supplied for background synthesis; the "
                "introduction therefore relies on the paper brief alone."
            )
        lines.append("")

        if title:
            lines.append(
                f'The present manuscript develops this agenda for the study titled "{title}".'
            )
            lines.append("")

        # Gap
        lines.extend(["## Gap", ""])
        if question:
            lines.append(
                "Despite accumulating related observations, several gaps persist. First, "
                "many reports emphasize either descriptive microbial/molecular shifts or "
                "clinical association in isolation, without a single pipeline-facing account "
                "that simultaneously documents preprocessing choices, inferential thresholds, "
                "and figure-backed findings. Second, cross-study heterogeneity in cohorts, "
                "assays, and analytic definitions makes it difficult to reuse results without "
                "an explicit methods–results contract. Third, for the focal question—"
                f"**{question}**—an integrated narrative that connects {modality or 'the available data modality'}, "
                f"{system or 'the study system'}, and evidence-linked conclusions is still incomplete "
                "in the supplied package."
            )
        else:
            lines.append(
                "The research gap cannot be stated precisely because "
                "`paper_brief.research_question` is empty."
            )
        if notes:
            lines.append("")
            lines.append("Additional brief notes retained for transparency:")
            for note in notes:
                lines.append(f"- {note}")
        lines.append("")

        # Objective
        lines.extend(["## Objective", ""])
        if question:
            lines.append(
                "Accordingly, the objective of this study is to address the following "
                f"question: **{question}**"
            )
            lines.append("")
            lines.append("Operationally, we aim to:")
            lines.append(
                "1. Document the study system, data modality, and analytical/statistical procedures;"
            )
            lines.append(
                "2. Report principal quantitative findings with metrics and figure/table references;"
            )
            lines.append(
                "3. Interpret results against prior literature, state limitations, and provide "
                "standalone conclusions that are not merged into the Discussion."
            )
            claims.append(
                Claim(
                    claim_id="intro_objective",
                    statement=f"The study objective is: {question}",
                    evidence_ids=[],
                    section=self.section_id.value,
                )
            )
        else:
            lines.append(
                "The study objective will be finalized once a research question is supplied."
            )
        lines.append("")

        warnings: list[str] = []
        if not title:
            warnings.append("paper_brief.title is empty")
        if not question:
            warnings.append("paper_brief.research_question is empty")
        if not hits:
            warnings.append("section_input.literature is empty for introduction background")

        return SectionDraft(
            section=self.section_id,
            title=self.title,
            markdown="\n".join(lines),
            claims=claims,
            citations=citations,
            warnings=warnings,
            metadata={
                "template": "intro_background_gap_objective_v2",
                "n_literature": len(hits),
                "draft_status": "complete" if question else "degraded",
                "react": react_meta,
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
