"""Manuscript layout helpers (figures + compiled manuscript)."""

from __future__ import annotations

from article_writing.layout.figures import render_figure_embed, stage_figures
from article_writing.layout.manuscript import build_manuscript_markdown

__all__ = [
    "build_manuscript_markdown",
    "render_figure_embed",
    "stage_figures",
]
