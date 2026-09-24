"""Layout: figure staging and manuscript assembly."""

from __future__ import annotations

from pathlib import Path

from article_writing.contracts import FigureRef
from article_writing.layout.figures import render_figure_embed, stage_figures
from article_writing.orchestrator import WritingPipeline


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def test_render_figure_embed_uses_markdown_image():
    ref = FigureRef(
        figure_id="fig_pca",
        path="fixtures/figures/pca_placeholder.svg",
        caption="PCA of samples after normalization",
        kind="figure",
    )
    block = render_figure_embed(ref, display_index=1)
    assert "![PCA of samples after normalization](figures/fig_pca.svg)" in block
    assert "Figure 1" in block


def test_stage_figures_copies_svg(tmp_path: Path):
    refs = [
        FigureRef(
            figure_id="fig_pca",
            path="fixtures/figures/pca_placeholder.svg",
            caption="PCA",
            kind="figure",
        )
    ]
    copied, missing = stage_figures(refs, tmp_path, search_roots=[ROOT, FIXTURES])
    assert copied == ["fig_pca"]
    assert missing == []
    assert (tmp_path / "figures" / "fig_pca.svg").is_file()


def test_pipeline_exports_manuscript_and_figures(tmp_path: Path):
    pipeline = WritingPipeline(fixtures_dir=FIXTURES, react_enabled=False)
    out = tmp_path / "layout-out"
    pipeline.run_and_export(run_id="layout", output_dir=out)
    manuscript = (out / "manuscript.md").read_text(encoding="utf-8")
    assert "Results / 结果" in manuscript
    assert (out / "figures" / "fig_pca.svg").exists()
    assert (out / "figures" / "tbl_deg.svg").exists()
    assert (out / "layout_report.json").exists()
    results = (out / "sections" / "results.md").read_text(encoding="utf-8")
    assert "figures/fig_pca.svg" in results
    assert "figures/tbl_deg.svg" in results
