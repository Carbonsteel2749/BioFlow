from __future__ import annotations

import json
from pathlib import Path

import pytest

import article_writing.orchestrator.pipeline as pipeline_module
from article_writing.contracts import Claim, SectionDraft, SectionId
from article_writing.orchestrator import WritingPipeline
from article_writing.orchestrator.pipeline import DEFAULT_ORDER


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
SECTION_NAMES = [section.value for section in DEFAULT_ORDER]


def test_pipeline_exports_complete_bundle(tmp_path: Path):
    pipeline = WritingPipeline(fixtures_dir=FIXTURES)
    out = tmp_path / "out"
    bundle_path = pipeline.run_and_export(run_id="test-run", output_dir=out)

    assert bundle_path == out / "writing_bundle.json"
    assert (out / "paper_state.json").exists()
    assert (out / "title_page.md").exists()
    assert (out / "react_trajectory.json").exists()
    assert (out / "consistency_report.json").exists()

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["run_id"] == "test-run"
    assert [section["section"] for section in bundle["sections"]] == SECTION_NAMES
    assert bundle["claims"]
    assert bundle["citations"]
    assert bundle["figure_refs"]

    for section in bundle["sections"]:
        markdown_path = out / "sections" / f"{section['section']}.md"
        assert markdown_path.read_text(encoding="utf-8") == section["markdown"]


def test_pipeline_passes_shared_state_between_sections(monkeypatch: pytest.MonkeyPatch):
    observations: list[dict[str, object]] = []

    class RecordingSection:
        def __init__(self, section_id: SectionId) -> None:
            self.section_id = section_id

        def run(self, section_input, state=None) -> SectionDraft:
            assert state is not None
            assert section_input.section == self.section_id
            observations.append(
                {
                    "section": self.section_id.value,
                    "drafts": list(state.drafts),
                    "claims": [claim.claim_id for claim in state.confirmed_claims],
                }
            )
            return SectionDraft(
                section=self.section_id,
                title=self.section_id.value,
                markdown=f"# {self.section_id.value}\n",
                claims=[
                    Claim(
                        claim_id=f"{self.section_id.value}_claim",
                        statement=f"Claim from {self.section_id.value}",
                        evidence_ids=(
                            ["ev_deg_summary"]
                            if self.section_id
                            in {SectionId.methods, SectionId.results, SectionId.discussion}
                            else (
                                ["intro_claim_placeholder"]
                                if self.section_id is SectionId.conclusion
                                else []
                            )
                        ),
                        section=self.section_id.value,
                    )
                ],
            )

    monkeypatch.setattr(
        pipeline_module,
        "create_section",
        lambda section_id: RecordingSection(section_id),
    )

    # Avoid evidence strict failures from placeholder evidence in recording mode.
    state = WritingPipeline(fixtures_dir=FIXTURES, evidence_mode="warn").run(
        run_id="state-handoff"
    )

    assert list(state.drafts) == SECTION_NAMES
    for index, observation in enumerate(observations):
        previous_sections = SECTION_NAMES[:index]
        assert observation["section"] == SECTION_NAMES[index]
        assert observation["drafts"] == previous_sections
        assert observation["claims"] == [f"{name}_claim" for name in previous_sections]


def test_pipeline_exports_complete_custom_order(tmp_path: Path):
    custom_order = list(reversed(DEFAULT_ORDER))
    bundle_path = WritingPipeline(
        fixtures_dir=FIXTURES,
        section_order=custom_order,
    ).run_and_export(run_id="custom-order", output_dir=tmp_path / "custom")

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert [section["section"] for section in bundle["sections"]] == [
        section.value for section in custom_order
    ]
    assert {path.stem for path in (bundle_path.parent / "sections").glob("*.md")} == set(
        SECTION_NAMES
    )


@pytest.mark.parametrize(
    "section_order",
    [
        [],
        DEFAULT_ORDER[:-1],
        [
            SectionId.abstract,
            SectionId.abstract,
            SectionId.methods,
            SectionId.results,
            SectionId.discussion,
            SectionId.conclusion,
            SectionId.back_matter,
        ],
    ],
)
def test_pipeline_rejects_incomplete_or_duplicate_order(section_order):
    with pytest.raises(ValueError, match="each manuscript section exactly once"):
        WritingPipeline(fixtures_dir=FIXTURES, section_order=section_order)
