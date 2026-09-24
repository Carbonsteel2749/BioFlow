from __future__ import annotations

from pathlib import Path

from article_writing.llm.base import FakeLLM
from article_writing.orchestrator import WritingPipeline
from article_writing.skills.client import SkillClient, playbook_text


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def test_skill_client_fetches_nature_writing_handoff():
    client = SkillClient(enabled=True)
    assert client.available
    data = client.fetch(
        "nature-writing",
        {"task_context": "Draft the results section from the evidence package."},
    )
    assert data is not None
    assert data.get("skill") == "nature-writing"
    text = playbook_text(data)
    assert "Constraints" in text
    assert "不得虚构" in text or "Workflow" in text


def test_pipeline_skills_off_skips_review(tmp_path: Path):
    pipeline = WritingPipeline(
        fixtures_dir=FIXTURES,
        react_enabled=False,
        llm_enabled=False,
        skills_enabled=False,
    )
    out = tmp_path / "out"
    pipeline.run_and_export(run_id="no-skills", output_dir=out)
    assert not (out / "review.md").exists()


def test_pipeline_skills_writes_review_and_response(tmp_path: Path):
    pipeline = WritingPipeline(
        fixtures_dir=FIXTURES,
        react_enabled=False,
        llm=FakeLLM(),
        skills_enabled=True,
        editor_letter="Please address the sample size.",
        reviewer_comments="The DEG threshold is not justified.",
    )
    out = tmp_path / "out"
    pipeline.run_and_export(run_id="skills", output_dir=out)
    assert (out / "review.md").is_file()
    assert (out / "response.md").is_file()
    methods = pipeline.run(run_id="skills-meta")
    meta = methods.drafts["methods"].metadata.get("skills") or {}
    assert meta.get("enabled") is True
    assert meta.get("writing") is True
