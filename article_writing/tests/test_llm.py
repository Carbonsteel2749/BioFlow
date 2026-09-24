"""Tests for optional LLM polish layer."""

from __future__ import annotations

from pathlib import Path

from article_writing.contracts import SectionDraft, SectionId
from article_writing.llm import FakeLLM, LLMDisabled, build_llm_client, polish_section_draft
from article_writing.llm.polish import DEFAULT_WRITING_REQUIREMENTS, _missing_anchors
from article_writing.orchestrator import WritingPipeline


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def test_build_llm_disabled_by_default():
    client = build_llm_client(enabled=False)
    assert isinstance(client, LLMDisabled)
    assert client.enabled is False


def test_build_fake_client():
    client = build_llm_client(enabled=True, provider="fake")
    assert isinstance(client, FakeLLM)
    assert "hello" in client.generate("hello")


def test_default_requirements_cover_user_rules():
    text = DEFAULT_WRITING_REQUIREMENTS.lower()
    assert "english" in text and "chinese" in text
    assert "do not invent" in text
    assert "figure" in text


def test_polish_with_fake_preserves_claims(tmp_path: Path):
    draft = SectionDraft(
        section=SectionId.abstract,
        title="Abstract",
        markdown=(
            "# Abstract\n\n"
            "We found 128 significant genes and cite lit_001 with `fig_pca`.\n"
            "![PCA](figures/fig_pca.svg)\n"
        ),
        claims=[],
    )
    polished = polish_section_draft(draft, FakeLLM())
    assert polished.metadata["llm"]["polished"] is True
    assert polished.metadata["llm"]["bilingual"] is True
    assert "128" in polished.markdown
    assert "lit_001" in polished.markdown
    assert "`fig_pca`" in polished.markdown
    assert "![PCA](figures/fig_pca.svg)" in polished.markdown
    assert "## English" in polished.markdown
    assert "## 中文" in polished.markdown


def test_polish_rejects_when_anchors_drop():
    class BadLLM:
        provider = "bad"
        model = "bad"
        enabled = True

        def generate(self, prompt: str, system: str | None = None) -> str:
            del prompt, system
            return (
                "# Abstract\n\n## English\n\nPolished text without numbers.\n\n"
                "## 中文\n\n无数字。\n"
            )

    draft = SectionDraft(
        section=SectionId.abstract,
        title="Abstract",
        markdown="# Abstract\n\nWe report 128 genes and lit_001 plus `fig_pca`.\n",
    )
    out = polish_section_draft(draft, BadLLM())  # type: ignore[arg-type]
    assert out.metadata["llm"].get("rejected") is True
    assert out.markdown == draft.markdown
    assert any("dropped anchors" in w for w in out.warnings)


def test_missing_anchors_helper():
    missing = _missing_anchors("see lit_001 and 128 genes", "see genes only")
    assert "lit_001" in missing
    assert "128" in missing


def test_pipeline_with_fake_llm(tmp_path: Path):
    pipeline = WritingPipeline(
        fixtures_dir=FIXTURES,
        llm=FakeLLM(),
        react_enabled=False,
    )
    out = tmp_path / "llm-out"
    pipeline.run_and_export(run_id="llm-fake", output_dir=out)
    abstract = (out / "sections" / "abstract.md").read_text(encoding="utf-8")
    assert "[llm-polished]" in abstract
    assert "## English" in abstract
    assert "## 中文" in abstract
    back = (out / "sections" / "back_matter.md").read_text(encoding="utf-8")
    assert "[llm-polished]" in back
    assert "## Author contributions" in back or "Author contributions" in back
    assert (out / "manuscript.md").exists()
    assert (out / "figures" / "fig_pca.svg").exists()
    results = (out / "sections" / "results.md").read_text(encoding="utf-8")
    assert "![PCA of samples after normalization](figures/fig_pca.svg)" in results
