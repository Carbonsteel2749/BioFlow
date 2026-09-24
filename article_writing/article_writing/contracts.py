"""Frozen data contracts for the writing plate and cross-plate adapters."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SectionId(str, Enum):
    """Manuscript section order aligned to IMRaD-style bioinformatics papers.

    Title/authors are rendered from ``PaperBrief`` at export time (not a section).
    Literature background is woven into Introduction / Discussion (no separate
    Related Work chapter), matching common journal practice.
    """

    abstract = "abstract"
    introduction = "introduction"
    methods = "methods"
    results = "results"
    discussion = "discussion"
    conclusion = "conclusion"
    back_matter = "back_matter"


DEFAULT_SECTION_ORDER: List[SectionId] = [
    SectionId.abstract,
    SectionId.introduction,
    SectionId.methods,
    SectionId.results,
    SectionId.discussion,
    SectionId.conclusion,
    SectionId.back_matter,
]


class Claim(BaseModel):
    """A checkable statement that must eventually bind to evidence."""

    claim_id: str
    statement: str
    evidence_ids: List[str] = Field(default_factory=list)
    section: Optional[str] = None


class Citation(BaseModel):
    cite_id: str
    title: str
    authors: str = ""
    year: Optional[int] = None
    doi: Optional[str] = None
    pmid: Optional[str] = None
    snippet: str = ""
    source: str = "fixture"


class FigureRef(BaseModel):
    figure_id: str
    path: str
    caption: str = ""
    kind: str = "figure"  # figure | table


class LiteratureHit(BaseModel):
    paper_id: str
    title: str
    abstract: str = ""
    authors: str = ""
    year: Optional[int] = None
    doi: Optional[str] = None
    pmid: Optional[str] = None
    score: float = 0.0
    tags: List[str] = Field(default_factory=list)


class AbbreviationEntry(BaseModel):
    """One manuscript abbreviation (user-confirmed or harvested seed)."""

    abbreviation: str = ""
    expansion: str = ""


class ClinicalScale(BaseModel):
    """One clinical/behavioral rating scale for Methods → Clinical assessment."""

    name: str = ""
    abbreviation: str = ""
    n_items: Optional[int] = None
    domains: List[str] = Field(default_factory=list)
    description: str = ""
    administered_by: str = ""
    administration_method: str = ""
    score_interpretation: str = ""
    citation: str = ""


class ExposureMeasurement(BaseModel):
    """Core exposure / key variable measurement (e.g., hair calcium)."""

    variable_name: str = ""
    collection_site: str = ""
    processing_workflow: str = ""
    detection_method: str = ""
    quality_control: str = ""
    units: str = ""
    grouping_criteria: str = ""
    additional_notes: str = ""


class MicrobiomeSequencing(BaseModel):
    """Sample → DNA → sequencing → annotation slots."""

    sample_collection: str = ""
    sample_storage: str = ""
    dna_extraction: str = ""
    library_prep: str = ""
    sequencing_platform: str = ""
    sequencing_strategy: str = ""
    qc_pipeline: str = ""
    taxonomy_annotation: str = ""
    pathway_annotation: str = ""
    post_filter_summary: str = ""


class StatisticalAnalysisPlan(BaseModel):
    """Statistical analysis checklist slots."""

    analysis_overview: str = ""
    group_comparison_method: str = ""
    continuous_association_method: str = ""
    multivariate_models: str = ""
    covariates: List[str] = Field(default_factory=list)
    multiple_testing_correction: str = ""
    significance_threshold: str = ""
    software: str = ""


class AnalysisBundle(BaseModel):
    """Normalized analysis package consumed by Methods / Results / Discussion."""

    summary: str = ""
    methods: Dict[str, Any] = Field(default_factory=dict)
    key_findings: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    limitations: List[str] = Field(default_factory=list)
    figure_refs: List[FigureRef] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    raw: Dict[str, Any] = Field(default_factory=dict)
    # Methods subsection slots (upstream-filled; never invent in LLM polish)
    clinical_scales: List[ClinicalScale] = Field(default_factory=list)
    exposure_measurement: Optional[ExposureMeasurement] = None
    microbiome_sequencing: Optional[MicrobiomeSequencing] = None
    statistical_analysis: Optional[StatisticalAnalysisPlan] = None


class PaperBrief(BaseModel):
    """High-level study context shared across sections."""

    title: str = ""
    research_question: str = ""
    organism_or_system: str = ""
    data_modality: str = ""
    keywords: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    # Optional front/back-matter fields (may come from UI / analysis metadata later)
    authors: str = ""
    ethics_statement: str = ""
    data_availability: str = ""
    code_availability: str = ""
    # Back Matter administrative slots (user/upstream-filled; never invent)
    author_contributions: str = ""  # CRediT narrative or role lines
    funding: str = ""
    acknowledgments: str = ""
    conflicts_of_interest: str = ""
    data_accession: str = ""  # e.g. CRA044389
    data_repository: str = ""  # e.g. Genome Sequence Archive (GSA)
    data_repository_url: str = ""  # e.g. https://ngdc.cncb.ac.cn/gsa
    # Abbreviations: user-confirmed list preferred; else harvested seeds + LLM suggest
    abbreviations: List[AbbreviationEntry] = Field(default_factory=list)
    # Methods → Participants → ethics / informed consent slots (upstream-filled; never invent)
    ethics_committee: str = ""
    ethics_approval_id: str = ""
    ethics_approval_date: str = ""
    informed_consent_from: str = ""
    informed_consent_form: str = ""  # e.g. written informed consent
    informed_consent_process: str = ""
    helsinki_declaration: bool = False
    helsinki_citation: str = ""  # e.g. "[20]" when available
    # Methods → Participants → overview / eligibility slots (upstream-filled; never invent)
    sample_source: str = ""
    study_design: str = ""
    inclusion_criteria: List[str] = Field(default_factory=list)
    exclusion_criteria: List[str] = Field(default_factory=list)
    n_participants: Optional[int] = None
    n_participants_note: str = ""  # data completeness conditions for the analytic N
    power_analysis: str = ""  # optional sensitivity/power sentence
    # Methods → Clinical assessment (optional mirror; AnalysisBundle.clinical_scales preferred)
    clinical_assessment_lead_in: str = ""
    clinical_scales: List[ClinicalScale] = Field(default_factory=list)


class SectionInput(BaseModel):
    run_id: str
    section: SectionId
    brief: PaperBrief = Field(default_factory=PaperBrief)
    analysis: Optional[AnalysisBundle] = None
    literature: List[LiteratureHit] = Field(default_factory=list)
    payload: Dict[str, Any] = Field(default_factory=dict)


class SectionDraft(BaseModel):
    section: SectionId
    title: str
    markdown: str
    claims: List[Claim] = Field(default_factory=list)
    citations: List[Citation] = Field(default_factory=list)
    figure_refs: List[FigureRef] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PaperState(BaseModel):
    """Cross-section shared state updated by the orchestrator."""

    run_id: str
    brief: PaperBrief = Field(default_factory=PaperBrief)
    confirmed_claims: List[Claim] = Field(default_factory=list)
    citations: List[Citation] = Field(default_factory=list)
    figure_refs: List[FigureRef] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    section_order: List[SectionId] = Field(default_factory=lambda: list(DEFAULT_SECTION_ORDER))
    drafts: Dict[str, SectionDraft] = Field(default_factory=dict)

    def add_draft(self, draft: SectionDraft) -> None:
        self.drafts[draft.section.value] = draft
        for claim in draft.claims:
            if claim.claim_id not in {c.claim_id for c in self.confirmed_claims}:
                self.confirmed_claims.append(claim)
        for cite in draft.citations:
            if cite.cite_id not in {c.cite_id for c in self.citations}:
                self.citations.append(cite)
        for fig in draft.figure_refs:
            if fig.figure_id not in {f.figure_id for f in self.figure_refs}:
                self.figure_refs.append(fig)


class WritingBundle(BaseModel):
    """Export package handed to the visualization plate."""

    run_id: str
    brief: PaperBrief
    sections: List[SectionDraft]
    claims: List[Claim] = Field(default_factory=list)
    citations: List[Citation] = Field(default_factory=list)
    figure_refs: List[FigureRef] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
