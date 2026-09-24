from __future__ import annotations

from pathlib import Path

from article_writing.adapters import MockBriefPort, MockLiteraturePort
from article_writing.contracts import PaperBrief, SectionId, SectionInput
from article_writing.sections.introduction import IntroductionSection


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def test_introduction_has_background_gap_objective():
    brief = MockBriefPort(FIXTURES).load()
    literature = MockLiteraturePort(FIXTURES).search("autism", top_k=3)
    draft = IntroductionSection().run(
        SectionInput(
            run_id="t",
            section=SectionId.introduction,
            brief=brief,
            literature=literature,
        )
    )
    assert "## Background" in draft.markdown
    assert "## Gap" in draft.markdown
    assert "## Objective" in draft.markdown
    assert brief.research_question in draft.markdown
    assert draft.citations
    assert draft.metadata["draft_status"] == "complete"


def test_introduction_warns_without_question():
    draft = IntroductionSection().run(
        SectionInput(
            run_id="t",
            section=SectionId.introduction,
            brief=PaperBrief(title="X"),
        )
    )
    assert "paper_brief.research_question is empty" in draft.warnings
    assert draft.metadata["draft_status"] == "degraded"
