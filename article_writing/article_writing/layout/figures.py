"""Embed and stage figure/table assets from upstream modules."""

from __future__ import annotations

import shutil
from pathlib import Path

from article_writing.contracts import FigureRef
from article_writing.sections.text_utils import clean_text


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}


def export_figure_filename(ref: FigureRef) -> str:
    """Stable filename under output ``figures/``."""

    suffix = Path(ref.path).suffix.lower() or ".bin"
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in ref.figure_id)
    return f"{safe_id}{suffix}"


def resolve_figure_source(ref: FigureRef, search_roots: list[Path]) -> Path | None:
    """Locate an upstream figure file on disk."""

    raw = Path(ref.path)
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(raw)
        for root in search_roots:
            candidates.append(root / raw)
            candidates.append(root / raw.name)
            # Common fixture layout: fixtures/figures/...
            candidates.append(root / "fixtures" / raw)
            if "figures" not in raw.parts:
                candidates.append(root / "fixtures" / "figures" / raw.name)
    for path in candidates:
        if path.is_file():
            return path.resolve()
    return None


def render_figure_embed(
    ref: FigureRef,
    *,
    display_index: int | None = None,
    rel_dir: str = "figures",
) -> str:
    """Markdown block that inserts a figure/table at the current position."""

    caption = clean_text(ref.caption) or ref.figure_id
    filename = export_figure_filename(ref)
    rel_path = f"{rel_dir.rstrip('/')}/{filename}"
    kind = (ref.kind or "figure").lower()
    if display_index is not None:
        label = f"Figure {display_index}" if kind != "table" else f"Table {display_index}"
    else:
        label = ref.figure_id

    lines: list[str] = []
    suffix = Path(ref.path).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        lines.append(f"![{caption}]({rel_path})")
        lines.append("")
        lines.append(f"**{label}.** {caption} (`{ref.figure_id}`)")
    else:
        lines.append(f"**{label}.** {caption} (`{ref.figure_id}`)")
        lines.append("")
        lines.append(
            f"> Upstream asset (non-image placeholder): `{rel_path}` — "
            "replace with visualization-module export when available."
        )
    lines.append("")
    return "\n".join(lines)


def stage_figures(
    figure_refs: list[FigureRef],
    output_dir: Path,
    *,
    search_roots: list[Path] | None = None,
) -> tuple[list[str], list[str]]:
    """Copy upstream assets into ``output_dir/figures``; return (copied, missing)."""

    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    roots = search_roots or [Path.cwd()]
    copied: list[str] = []
    missing: list[str] = []
    seen: set[str] = set()

    for ref in figure_refs:
        if ref.figure_id in seen:
            continue
        seen.add(ref.figure_id)
        dest = figures_dir / export_figure_filename(ref)
        source = resolve_figure_source(ref, roots)
        if source is None:
            missing.append(ref.figure_id)
            # Keep a stub so markdown links are not dangling during demos.
            if not dest.exists():
                dest.write_text(
                    f"Missing upstream asset for {ref.figure_id}: {ref.path}\n",
                    encoding="utf-8",
                )
            continue
        shutil.copy2(source, dest)
        copied.append(ref.figure_id)
    return copied, missing
