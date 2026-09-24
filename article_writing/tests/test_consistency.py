from __future__ import annotations

import json
from pathlib import Path

import pytest

from article_writing.consistency import (
    ConsistencyError,
    ConsistencyMode,
    collect_consistency_issues,
    harden_paper_state,
)
from article_writing.contracts import (
    Claim,
    PaperBrief,
    PaperState,
    SectionDraft,
    SectionId,
)
from article_writing.orchestrator import WritingPipeline


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def _draft(section: SectionId, *, claims: list[Claim] | None = None) -> SectionDraft:
    return SectionDraft(
        section=section,
        title=section.value,
        markdown=f"# {section.value}\n",
        claims=claims or [],
        metadata={"draft_status": "complete"},
    )


def test_fixture_pipeline_is_consistent_and_exports_report(tmp_path: Path):
    out = tmp_path / "phase4"
    pipeline = WritingPipeline(
        fixtures_dir=FIXTURES,
        consistency_mode=ConsistencyMode.strict,
    )
    pipeline.run_and_export(run_id="phase4-ok", output_dir=out)
    report = json.loads((out / "consistency_report.json").read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert pipeline.last_consistency_report.ok is True


def test_reconcile_fixes_aggregate_drift_and_duplicate_limitations():
    state = PaperState(
        run_id="drift",
        brief=PaperBrief(),
        section_order=[SectionId.introduction, SectionId.results],
        drafts={
            SectionId.introduction.value: _draft(
                SectionId.introduction,
                claims=[
                    Claim(
                        claim_id="intro_a",
                        statement="Intro claim",
                        section=SectionId.introduction.value,
                    )
                ],
            ),
            SectionId.results.value: _draft(
                SectionId.results,
                claims=[
                    Claim(
                        claim_id="result_a",
                        statement="Result claim",
                        evidence_ids=["ev_1"],
                        section=SectionId.results.value,
                    )
                ],
            ),
        },
        confirmed_claims=[],
        limitations=["Same limit", "same limit", ""],
    )
    before = collect_consistency_issues(state)
    assert any(issue.kind == "claim_aggregate_drift" for issue in before)
    hardened, report = harden_paper_state(state, mode=ConsistencyMode.warn)
    assert report.ok is True
    assert [c.claim_id for c in hardened.confirmed_claims] == ["intro_a", "result_a"]
    assert hardened.limitations == ["Same limit"]


def test_missing_section_remains_an_issue_after_harden():
    state = PaperState(
        run_id="missing",
        section_order=[SectionId.introduction, SectionId.conclusion],
        drafts={SectionId.introduction.value: _draft(SectionId.introduction)},
    )
    _, report = harden_paper_state(state, mode=ConsistencyMode.warn)
    assert any(issue.kind == "missing_section" for issue in report.issues_after)


def test_strict_mode_raises_on_residual_issues():
    state = PaperState(
        run_id="strict",
        section_order=[SectionId.introduction, SectionId.conclusion],
        drafts={SectionId.introduction.value: _draft(SectionId.introduction)},
    )
    with pytest.raises(ConsistencyError):
        harden_paper_state(state, mode=ConsistencyMode.strict)
