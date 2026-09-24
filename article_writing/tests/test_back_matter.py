from __future__ import annotations

from article_writing.contracts import (
    AbbreviationEntry,
    AnalysisBundle,
    ClinicalScale,
    ExposureMeasurement,
    PaperBrief,
    SectionId,
    SectionInput,
    StatisticalAnalysisPlan,
)
from article_writing.prompts.back_matter import (
    harvest_abbreviation_seeds,
    render_data_availability_en,
    render_irb_statement_en,
)
from article_writing.sections.back_matter import BackMatterSection


def test_back_matter_warns_for_missing_admin_slots():
    draft = BackMatterSection().run(
        SectionInput(run_id="t", section=SectionId.back_matter, brief=PaperBrief())
    )
    assert "## Author contributions" in draft.markdown
    assert "## Funding" in draft.markdown
    assert "## Institutional Review Board Statement" in draft.markdown
    assert "## Abbreviations" in draft.markdown
    assert "llm_instructions_extra" in draft.metadata
    assert draft.metadata["user_input_needed"]
    assert any(w.startswith("back matter slot missing:") for w in draft.warnings)


def test_back_matter_fills_from_slots_like_journal():
    brief = PaperBrief(
        authors="J.L., X.Y.",
        author_contributions=(
            "Conceptualization, J.L. and X.Y.; methodology, J.L.; "
            "All authors have read and agreed to the published version of the manuscript."
        ),
        funding="Autism Special Fund from Peking Union Medical Foundation.",
        ethics_committee="Institutional Review Board of Peking Union Medical College Hospital",
        ethics_approval_id="ZS-824",
        ethics_approval_date="12 October 2024",
        helsinki_declaration=True,
        informed_consent_form="Informed consent",
        informed_consent_from="all subjects involved in the study",
        data_repository=(
            "Genome Sequence Archive (GSA) at the National Genomics Data Center (NGDC)"
        ),
        data_accession="CRA044389",
        data_repository_url="https://ngdc.cncb.ac.cn/gsa",
        acknowledgments=(
            "The authors thank Jingjing Peng for technical assistance with the "
            "microbiology research."
        ),
        conflicts_of_interest=(
            "The authors declare no conflicts of interest. The funders had no role "
            "in the design of the study."
        ),
        abbreviations=[
            AbbreviationEntry(abbreviation="ASD", expansion="Autism Spectrum Disorder"),
            AbbreviationEntry(abbreviation="ABC", expansion="Autism Behavior Checklist"),
        ],
    )
    draft = BackMatterSection().run(
        SectionInput(run_id="t", section=SectionId.back_matter, brief=brief)
    )
    assert "ZS-824" in draft.markdown
    assert "CRA044389" in draft.markdown
    assert "Jingjing Peng" in draft.markdown
    assert "**ABC**: Autism Behavior Checklist" in draft.markdown
    assert draft.metadata["admin_slots_missing"] == []
    assert draft.metadata["abbreviations_user_confirmed"] is True
    assert "IRB #ZS-824" in render_irb_statement_en(brief)
    assert "CRA044389" in render_data_availability_en(brief)


def test_abbreviation_harvest_from_clinical_and_exposure():
    brief = PaperBrief(
        organism_or_system="children with Autism Spectrum Disorder (ASD)",
    )
    analysis = AnalysisBundle(
        clinical_scales=[
            ClinicalScale(name="Childhood Autism Rating Scale", abbreviation="CARS"),
        ],
        exposure_measurement=ExposureMeasurement(
            detection_method="inductively coupled plasma mass spectrometry (ICP-MS)",
        ),
        statistical_analysis=StatisticalAnalysisPlan(
            multiple_testing_correction="Benjamini–Hochberg FDR",
        ),
    )
    entries = harvest_abbreviation_seeds(brief, analysis)
    abbrs = {e.abbreviation.upper(): e.expansion for e in entries}
    assert abbrs["ASD"] == "Autism Spectrum Disorder"
    assert abbrs["CARS"] == "Childhood Autism Rating Scale"
    assert "ICP-MS" in abbrs
    assert abbrs["FDR"] == "False Discovery Rate"
