from __future__ import annotations

from pathlib import Path

from article_writing.adapters import MockAnalysisPort, MockBriefPort, MockLiteraturePort
from article_writing.contracts import AnalysisBundle, PaperBrief, SectionId, SectionInput
from article_writing.evidence import EvidenceMode, apply_evidence_binding, claims_missing_evidence
from article_writing.orchestrator import WritingPipeline
from article_writing.sections.abstract import AbstractSection
from article_writing.sections.introduction import IntroductionSection
from article_writing.sections.results import ResultsSection


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def test_fixture_pipeline_binds_cleanly_in_warn_mode():
    state = WritingPipeline(fixtures_dir=FIXTURES).run(run_id="phase2-ok")
    for section_id in state.section_order:
        draft = state.drafts[section_id.value]
        binding = draft.metadata["evidence_binding"]
        assert binding["ok"] is True
        assert not any(w.startswith("evidence binding:") for w in draft.warnings)


def test_introduction_may_omit_objective_evidence():
    brief = MockBriefPort(FIXTURES).load()
    draft = IntroductionSection().run(
        SectionInput(
            run_id="t",
            section=SectionId.introduction,
            brief=brief,
            literature=MockLiteraturePort(FIXTURES).search("autism", top_k=3),
        )
    )
    # Literature-backed claims have evidence; objective claim may be empty — policy allows.
    bound = apply_evidence_binding(
        draft,
        section_input=SectionInput(
            run_id="t",
            section=SectionId.introduction,
            brief=brief,
            literature=MockLiteraturePort(FIXTURES).search("autism", top_k=3),
        ),
        mode=EvidenceMode.warn,
    )
    assert bound.metadata["evidence_binding"]["ok"] is True


def test_abstract_degrades_without_question():
    draft = AbstractSection().run(
        SectionInput(run_id="t", section=SectionId.abstract, brief=PaperBrief())
    )
    assert "paper_brief.research_question is empty" in draft.warnings
    assert draft.metadata["draft_status"] == "degraded"


def test_results_missing_evidence_warns_and_degrades():
    analysis = AnalysisBundle(
        key_findings=["finding without evidence"],
        metrics={"n_significant": 1},
        evidence_ids=[],
    )
    draft = ResultsSection().run(
        SectionInput(run_id="t", section=SectionId.results, brief=PaperBrief(), analysis=analysis)
    )
    assert claims_missing_evidence(draft)
    bound = apply_evidence_binding(
        draft,
        section_input=SectionInput(
            run_id="t", section=SectionId.results, brief=PaperBrief(), analysis=analysis
        ),
        mode=EvidenceMode.warn,
    )
    assert bound.metadata["evidence_binding"]["ok"] is False
    assert bound.metadata["draft_status"] == "degraded"


def test_pipeline_strict_mode_passes_on_fixtures():
    state = WritingPipeline(
        fixtures_dir=FIXTURES,
        evidence_mode=EvidenceMode.strict,
    ).run(run_id="phase2-strict-ok")
    assert len(state.drafts) == 7
