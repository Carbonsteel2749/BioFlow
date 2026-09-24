from __future__ import annotations

from pathlib import Path

from article_writing.adapters import MockAnalysisPort, MockBriefPort
from article_writing.contracts import (
    AnalysisBundle,
    ClinicalScale,
    ExposureMeasurement,
    MicrobiomeSequencing,
    PaperBrief,
    SectionId,
    SectionInput,
    StatisticalAnalysisPlan,
)
from article_writing.prompts.methods_participants_ethics import render_ethics_consent_paragraph
from article_writing.prompts.methods_participants_overview import (
    render_eligibility_block,
    render_overview_paragraph,
)
from article_writing.prompts.methods_remaining import (
    render_clinical_assessment_en,
    render_exposure_en,
    render_microbiome_en,
    render_statistics_en,
)
from article_writing.sections.methods import MethodsSection
from article_writing.sections.results import ResultsSection


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def test_methods_has_required_subsections():
    brief = MockBriefPort(FIXTURES).load()
    analysis = MockAnalysisPort(FIXTURES).load()
    draft = MethodsSection().run(
        SectionInput(
            run_id="t",
            section=SectionId.methods,
            brief=brief,
            analysis=analysis,
        )
    )
    assert "## Participants" in draft.markdown
    assert "### Study overview" in draft.markdown
    assert "### Eligibility criteria" in draft.markdown
    assert "### Ethics approval and informed consent" in draft.markdown
    assert "## Clinical assessment" in draft.markdown
    assert "## Exposure measurement" in draft.markdown
    assert "## Microbiome sequencing" in draft.markdown
    assert "## Statistical analysis" in draft.markdown
    assert draft.claims
    assert draft.metadata["draft_status"] == "complete"
    assert "llm_instructions_extra" in draft.metadata
    assert "Clinical assessment" in draft.metadata["llm_instructions_extra"]


def _calcium_style_brief() -> PaperBrief:
    return PaperBrief(
        organism_or_system="children with ASD",
        study_design="cross-sectional study",
        sample_source="an existing single-center cohort of children with ASD",
        n_participants=183,
        n_participants_note=(
            "complete core phenotypic data, available hair calcium measurements, and "
            "fecal shotgun metagenomic sequencing data that passed quality-control requirements"
        ),
        power_analysis=(
            "Using Fisher's z transformation for a two-sided α = 0.05 test, this sample "
            "size provides approximately 80% power to detect a correlation coefficient "
            "of about 0.21, supporting adequate sensitivity for the main calcium-behavior "
            "association analyses."
        ),
        inclusion_criteria=[
            "ASD confirmed by experienced psychiatrists according to DSM-V (2013) criteria",
            "no antibiotics, prebiotics, or probiotics for at least four weeks before sampling",
            "primary caregivers able to complete assessment scales",
            "written informed consent from a parent or legal guardian",
        ],
        exclusion_criteria=[
            "other comorbid neurological or psychiatric disorders confirmed by experienced clinicians"
        ],
        ethics_committee="Institutional Review Board of Peking Union Medical College Hospital",
        ethics_approval_id="ZS-824",
        ethics_approval_date="12 October 2024",
        informed_consent_form="written informed consent",
        informed_consent_from="a parent or legal guardian",
        informed_consent_process=(
            "received detailed information on the study purposes and procedures before "
            "providing consent"
        ),
        helsinki_declaration=True,
        helsinki_citation="[20]",
        clinical_assessment_lead_in=(
            "This study used standardized rating scales to assess the severity of "
            "symptoms and behavioral characteristics of Autism Spectrum Disorder (ASD):"
        ),
        clinical_scales=[
            ClinicalScale(
                name="Autism Behavior Checklist",
                abbreviation="ABC",
                n_items=57,
                domains=[
                    "sensation",
                    "social interaction",
                    "body and object use",
                    "language",
                    "social and self-care ability",
                ],
                administered_by="the child's parents or primary caregivers",
                administration_method="based on the child's daily behavior",
                citation="[21]",
            ),
        ],
    )


def _calcium_style_analysis() -> AnalysisBundle:
    return AnalysisBundle(
        methods={"qc": "ok", "differential_analysis": {"test": "Wilcoxon", "fdr": "BH"}},
        clinical_scales=[
            ClinicalScale(
                name="Autism Behavior Checklist",
                abbreviation="ABC",
                n_items=57,
                domains=[
                    "sensation",
                    "social interaction",
                    "body and object use",
                    "language",
                    "social and self-care ability",
                ],
                administered_by="the child's parents or primary caregivers",
                administration_method="based on the child's daily behavior",
                citation="[21]",
            ),
            ClinicalScale(
                name="Autism Treatment Evaluation Checklist",
                abbreviation="ATEC",
                domains=[
                    "speech/language/communication",
                    "sociability",
                    "sensory/cognitive awareness",
                    "health/physical/behavior",
                ],
                score_interpretation=(
                    "lower scores represent less impairment in behavior and function"
                ),
                administered_by="parents or caregivers",
                citation="[22]",
            ),
            ClinicalScale(
                name="Childhood Autism Rating Scale",
                abbreviation="CARS",
                administered_by="trained clinical assessors",
                administration_method=(
                    "direct behavioral observation combined with caregiver interviews"
                ),
                description=(
                    "widely used to assess symptom severity and assist clinical grading"
                ),
                citation="[23]",
            ),
        ],
        exposure_measurement=ExposureMeasurement(
            variable_name="hair calcium",
            collection_site=(
                "the proximal end of hair close to the scalp using stainless steel scissors"
            ),
            processing_workflow=(
                "Samples were cut short, mixed, washed repeatedly with non-ionic detergent, "
                "acetone, and ultrapure water, then dried and weighed; nitric acid-assisted "
                "microwave digestion was performed and samples were diluted with ultrapure "
                "water after adding an internal standard."
            ),
            detection_method="inductively coupled plasma mass spectrometry (ICP-MS)",
            quality_control=(
                "calibration standards, certified hair reference materials, blank samples, "
                "laboratory control samples, and spiked hair samples"
            ),
            units="μg/g",
            grouping_criteria=(
                "Subjects were ranked by concentration; the low-calcium group was the "
                "lowest quartile (Q1 ≤ 227.5 μg/g, n=46) and the high-calcium group was "
                "the highest quartile (Q4 ≥ 341.0 μg/g, n=46)."
            ),
            additional_notes="Clinical data showed no subjects were taking calcium supplements.",
        ),
        microbiome_sequencing=MicrobiomeSequencing(
            sample_collection="Fresh fecal samples were collected at home using sterile kits.",
            sample_storage="Samples were transported on ice and stored at −80 °C until DNA extraction.",
            dna_extraction="Total DNA was extracted with a commercial kit following the manufacturer protocol.",
            sequencing_platform="Illumina NovaSeq",
            sequencing_strategy="shotgun metagenomic sequencing",
            qc_pipeline="Adapter trimming and host-read removal were applied before assembly.",
            taxonomy_annotation="Taxonomic profiles were generated with MetaPhlAn.",
            pathway_annotation="Functional pathways were annotated with HUMAnN against MetaCyc/KEGG.",
        ),
        statistical_analysis=StatisticalAnalysisPlan(
            analysis_overview=(
                "Analyses compared high- versus low-calcium groups and tested continuous "
                "associations between calcium, taxa, pathways, and behavioral scores."
            ),
            group_comparison_method="Wilcoxon rank-sum tests for continuous variables",
            continuous_association_method="Spearman correlation",
            multivariate_models="MaAsLin2 multivariable models relating taxa/pathways to behavior",
            covariates=["age", "sex", "BMI"],
            multiple_testing_correction="Benjamini–Hochberg FDR",
            significance_threshold="q < 0.05",
            software="R 4.x",
        ),
    )


def test_methods_participants_overview_and_eligibility_slots():
    brief = _calcium_style_brief()
    overview = render_overview_paragraph(brief)
    assert "cross-sectional study" in overview
    assert "single-center cohort" in overview
    assert "183" in overview
    assert "80% power" in overview

    eligibility = render_eligibility_block(brief)
    assert "Inclusion criteria" in eligibility
    assert "DSM-V" in eligibility
    assert "Exclusion criteria" in eligibility

    draft = MethodsSection().run(
        SectionInput(
            run_id="t",
            section=SectionId.methods,
            brief=brief,
            analysis=AnalysisBundle(methods={"qc": "ok"}),
        )
    )
    assert "### Study overview" in draft.markdown
    assert "183" in draft.markdown
    assert draft.metadata["overview_slots_missing"] == []
    assert draft.metadata["eligibility_slots_missing"] == []
    assert draft.metadata["ethics_slots_missing"] == []


def test_methods_ethics_slots_render_like_journal_example():
    brief = _calcium_style_brief()
    paragraph = render_ethics_consent_paragraph(brief)
    assert "Institutional Review Board of Peking Union Medical College Hospital" in paragraph
    assert "IRB #ZS-824" in paragraph
    assert "Declaration of Helsinki" in paragraph


def test_methods_remaining_four_sections_slot_fill():
    brief = _calcium_style_brief()
    analysis = _calcium_style_analysis()

    clinical = render_clinical_assessment_en(brief, analysis)
    assert "Autism Behavior Checklist (ABC)" in clinical
    assert "57 items" in clinical
    assert "CARS" in clinical

    exposure = render_exposure_en(analysis.exposure_measurement)
    assert "ICP-MS" in exposure
    assert "μg/g" in exposure
    assert "Q1" in exposure

    microbiome = render_microbiome_en(analysis.microbiome_sequencing)
    assert "NovaSeq" in microbiome
    assert "MetaPhlAn" in microbiome

    stats = render_statistics_en(analysis.statistical_analysis)
    assert "Wilcoxon" in stats
    assert "Benjamini–Hochberg" in stats
    assert "age" in stats

    draft = MethodsSection().run(
        SectionInput(
            run_id="t",
            section=SectionId.methods,
            brief=brief,
            analysis=analysis,
        )
    )
    assert "## Clinical assessment" in draft.markdown
    assert "## Exposure measurement" in draft.markdown
    assert "## Microbiome sequencing" in draft.markdown
    assert "ATEC" in draft.markdown
    assert "hair calcium" in draft.markdown
    assert draft.metadata["clinical_slots_missing"] == []
    assert draft.metadata["exposure_slots_missing"] == []
    assert draft.metadata["microbiome_slots_missing"] == []
    assert draft.metadata["statistics_slots_missing"] == []


def test_methods_warns_without_analysis():
    draft = MethodsSection().run(
        SectionInput(run_id="t", section=SectionId.methods, brief=PaperBrief())
    )
    assert "analysis bundle missing" in draft.warnings
    assert "paper_brief.organism_or_system is empty" in draft.warnings
    assert any(w.startswith("participants overview slot missing:") for w in draft.warnings)
    assert any(w.startswith("participants eligibility slot missing:") for w in draft.warnings)
    assert any(w.startswith("participants ethics slot missing:") for w in draft.warnings)
    assert any(w.startswith("clinical assessment slot missing:") for w in draft.warnings)
    assert any(w.startswith("exposure measurement slot missing:") for w in draft.warnings)
    assert any(w.startswith("microbiome sequencing slot missing:") for w in draft.warnings)
    assert any(w.startswith("statistical analysis slot missing:") for w in draft.warnings)
    assert draft.claims == []


def test_results_uses_findings_metrics_figures():
    analysis = MockAnalysisPort(FIXTURES).load()
    draft = ResultsSection().run(
        SectionInput(
            run_id="t",
            section=SectionId.results,
            brief=PaperBrief(research_question="Q?"),
            analysis=analysis,
        )
    )
    assert "## Principal findings" in draft.markdown
    assert "## Overview display" in draft.markdown
    assert "figures/fig_pca.svg" in draft.markdown
    assert "figures/tbl_deg.svg" in draft.markdown
    assert len(draft.claims) == len(analysis.key_findings)
    assert draft.figure_refs
    assert "llm_instructions_extra" in draft.metadata
    assert "Do NOT write long background" in draft.metadata["llm_instructions_extra"]
    assert "Do NOT restate every item from figures" in draft.metadata["llm_instructions_extra"]
