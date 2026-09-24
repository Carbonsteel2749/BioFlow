#!/usr/bin/env python3
"""Private helpers for visualization nodes; deliberately independent of common.py."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

class TableError(ValueError):
    pass

def read_standard_table(path: Path, required: set[str], keys: list[str], abundance: str = "abundance") -> pd.DataFrame:
    if not path.is_file():
        raise TableError(f"input TSV does not exist: {path}")
    try:
        frame = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    except Exception as exc:
        raise TableError(f"cannot read TSV: {exc}") from exc
    missing = sorted(required - set(frame.columns))
    if missing:
        raise TableError(f"missing required columns: {', '.join(missing)}")
    if frame.empty:
        return frame
    if frame.duplicated(keys).any():
        raise TableError(f"duplicate primary key rows for: {', '.join(keys)}")
    values = pd.to_numeric(frame[abundance], errors="coerce")
    if values.isna().any() or (values < 0).any() or ~np.isfinite(values).all():
        raise TableError(f"{abundance} must contain finite non-negative numbers")
    frame[abundance] = values.astype(float)
    return frame

def write_provenance(path: Path, payload: dict) -> None:
    payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

def save_figure(figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(figure)

def skip(reason: str) -> dict:
    return {"status": "skipped", "reason": reason, "outputs": []}

def top_with_other(frame: pd.DataFrame, feature: str, value: str, top_n: int, other_label: str = "Other (non-Top-N)") -> tuple[pd.DataFrame, list[str]]:
    ranked = frame.groupby(feature, as_index=True)[value].mean().sort_values(ascending=False)
    top = ranked.head(top_n).index.tolist()
    output = frame.copy()
    output[feature] = np.where(output[feature].isin(top), output[feature], other_label)
    return output.groupby([column for column in output.columns if column not in (value,)], as_index=False)[value].sum(), top

def abundance_matrix(frame: pd.DataFrame, sample: str, feature: str, value: str) -> pd.DataFrame:
    return frame.pivot(index=sample, columns=feature, values=value).fillna(0.0)
