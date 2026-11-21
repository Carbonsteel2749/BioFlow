"""Figures from the chart module (project_figures) for the manuscript.

Only completed figures drawn from real data are accepted: a figure whose source is the
chart module's simulated example data is refused, and every PNG is checked against
the sha256 the chart module recorded. Captions are generated from the chart type and
source table (English, factual); the author can refine them in the editor.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

CAPTIONS = {
    "stacked": "Relative abundance of the most abundant taxa in each sample, shown as stacked bars",
    "pca": "Principal component analysis of the samples based on their taxonomic profiles",
    "heatmap": "Clustered heatmap of feature abundances across samples",
    "shannon": "Shannon diversity of each sample",
    "alpha_diversity": "Alpha diversity of each sample",
    "enrichment": "Enrichment of functional categories",
    "volcano": "Volcano plot of feature changes",
}


def figure_db() -> Path | None:
    """Platform database holding project_figures (the gateway's BIOFLOW_DATABASE)."""

    for name in ("ARTICLE_WRITING_FIGURE_DB", "BIOFLOW_DATABASE"):
        value = os.getenv(name, "").strip()
        if value and Path(value).is_file():
            return Path(value)
    return None


def caption_for(chart: str, source: dict[str, Any]) -> str:
    base = CAPTIONS.get(chart, f"{chart.capitalize()} chart")
    name = str(source.get("name") or "").strip()
    return f"{base} (source table: {name})." if name else base + "."


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_figures(ids: list[str], db_path: Path | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Return usable figures in the given order, and warnings for refused ones."""

    ids = [str(i).strip() for i in ids or [] if str(i).strip()]
    if not ids:
        return [], []
    db_path = db_path or figure_db()
    if db_path is None:
        return [], ["figures skipped: platform database not configured (ARTICLE_WRITING_FIGURE_DB)"]
    root = db_path.resolve().parent / "figures"
    figures, warnings = [], []
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for fid in ids:
            row = db.execute("SELECT * FROM project_figures WHERE id=?", (fid,)).fetchone()
            if row is None:
                warnings.append(f"figure {fid}: not found")
                continue
            source = json.loads(row["source_json"] or "{}")
            result = json.loads(row["result"] or "{}")
            if row["status"] != "completed":
                warnings.append(f"figure {fid}: not completed")
                continue
            if source.get("simulated") or str(source.get("id", "")).startswith("example:"):
                warnings.append(f"figure {fid}: drawn from example data, not used")
                continue
            base = (root / row["user_id"] / row["project_id"]).resolve()
            path = (base / fid / "figure.png").resolve()
            expected = ((result.get("outputs") or {}).get("png") or {}).get("sha256")
            if not path.is_relative_to(base) or not path.is_file() or (expected and _sha256(path) != expected):
                warnings.append(f"figure {fid}: PNG missing or checksum mismatch")
                continue
            chart = str(row["chart"] or "")
            params = json.loads(row["parameters"] or "{}")
            figures.append({"figure_id": f"fig_{fid[:12]}", "source_figure": fid, "path": str(path), "chart": chart,
                            "caption": caption_for(chart, source), "source_name": source.get("name", ""),
                            "task_id": source.get("task_id", ""), "title": params.get("title") or ""})
    return figures, warnings
