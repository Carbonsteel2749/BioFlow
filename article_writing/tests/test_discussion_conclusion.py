from __future__ import annotations

from pathlib import Path

from article_writing.adapters import MockAnalysisPort, MockLiteraturePort
from article_writing.contracts import (
    Claim,
    PaperBrief,
    PaperState,
    SectionId,
    SectionInput,
)
from article_writing.orchestrator import WritingPipeline
from article_writing.sections.conclusion import ConclusionSection
from article_writing.sections.discussion import DiscussionSection


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def test_discussion_has_interpretation_mechanism_limitations():
    analysis = MockAnalysisPort(FIXTURES).load()
    literature = MockLiteraturePort(FIXTURES).search("autism", top_k=3)
    state = PaperState(
        run_id="t",
        confirmed_claims=[
            Claim(
                claim_id="result_finding_1",
                statement=analysis.key_findings[0],
                evidence_ids=analysis.evidence_ids[:1],
                section=SectionId.results.value,
            )
        ],
        limitations=list(analysis.limitations),
    )
    draft = DiscussionSection().run(
        SectionInput(
            run_id="t",
            section=SectionId.discussion,
            brief=PaperBrief(),
            analysis=analysis,
            literature=literature,
        ),
        state=state,
    )
    assert "## Interpretation of principal findings" in draft.markdown
    assert "## Mechanisms and literature context" in draft.markdown
    assert "## Limitations" in draft.markdown
    assert "## Outlook" in draft.markdown
    assert draft.citations
    assert "not merged" in draft.markdown.lower() or "Standalone Conclusions" in draft.markdown
    assert "llm_instructions_extra" in draft.metadata
    assert "Opening paragraph" in draft.metadata["llm_instructions_extra"]
    assert "Do NOT over-extrapolate" in draft.metadata["llm_instructions_extra"]
    assert "CONSISTENT with prior studies" in draft.metadata["llm_instructions_extra"]


def test_conclusion_is_standalone_and_not_discussion():
    analysis = MockAnalysisPort(FIXTURES).load()
    state = PaperState(
        run_id="t",
        confirmed_claims=[
            Claim(
                claim_id="result_finding_1",
                statement="Finding A",
                evidence_ids=["ev_deg_summary"],
                section=SectionId.results.value,
            )
        ],
        limitations=list(analysis.limitations),
    )
    draft = ConclusionSection().run(
        SectionInput(
            run_id="t",
            section=SectionId.conclusion,
            brief=PaperBrief(research_question="Q?"),
            analysis=analysis,
        ),
        state=state,
    )
    assert draft.markdown.startswith("# Conclusions\n")
    assert "not merged into Discussion" in draft.markdown
    assert draft.claims[0].claim_id == "conclusion_summary"
    for item in analysis.limitations:
        assert draft.markdown.count(item) == 1
    assert "llm_instructions_extra" in draft.metadata
    assert "What did this study find" in draft.metadata["llm_instructions_extra"]
    assert "BRIEF and ACCURATE" in draft.metadata["llm_instructions_extra"]


def test_conclusion_sees_results_after_pipeline():
    state = WritingPipeline(fixtures_dir=FIXTURES).run(run_id="conclusion-pipeline")
    draft = state.drafts[SectionId.conclusion.value]
    assert draft.section is SectionId.conclusion
    assert state.drafts[SectionId.discussion.value]
    assert "not merged into Discussion" in draft.markdown
