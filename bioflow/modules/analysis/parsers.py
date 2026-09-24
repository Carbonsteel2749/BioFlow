from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from bioflow.core.models import DatasetSpec, DatasetType


@dataclass
class ExpressionMatrixParseResult:
    metrics: Dict[str, Any]
    tables: List[Dict[str, Any]]
    figures: List[Dict[str, Any]]
    notes: List[str]
    summary_path: str


class ExpressionMatrixParser:
    def _load_frame(self, dataset: DatasetSpec) -> pd.DataFrame:
        path = Path(dataset.primary_path)
        suffix = path.suffix.lower()
        meta = dataset.metadata if isinstance(dataset.metadata, dict) else {}
        separator = meta.get("separator")
        sheet_name = meta.get("sheet_name")
        if suffix in {".xlsx", ".xls"}:
            frame = pd.read_excel(path, sheet_name=sheet_name)
        else:
            frame = pd.read_csv(path, sep=separator or "\t")

        # set gene names as row index (consistent with Pipeline._load_numeric_frame)
        feature_col = frame.columns[0]
        features = frame[feature_col].astype(str)
        numeric_frame = frame.drop(columns=[feature_col], errors="ignore")
        numeric_frame = numeric_frame.apply(pd.to_numeric, errors="coerce")
        numeric_frame.index = features
        return numeric_frame

    def parse(self, dataset: DatasetSpec) -> ExpressionMatrixParseResult:
        if dataset.dataset_type != DatasetType.expression_matrix:
            raise ValueError("expression matrix parser only supports expression_matrix datasets")

        numeric_frame = self._load_frame(dataset)
        if numeric_frame.empty:
            raise ValueError("expression matrix is empty")

        row_count, column_count = int(numeric_frame.shape[0]), int(numeric_frame.shape[1])

        non_missing_ratio = float(1.0 - numeric_frame.isna().mean().mean())
        non_zero_ratio = float((numeric_frame.fillna(0) != 0).mean().mean())
        flat_values = pd.to_numeric(pd.Series(numeric_frame.to_numpy().ravel()), errors="coerce").dropna()
        mean_signal = float(flat_values.mean()) if not flat_values.empty else 0.0
        median_signal = float(flat_values.median()) if not flat_values.empty else 0.0
        raw_sample_means = numeric_frame.mean(axis=0).fillna(0.0)
        raw_variable_features = (
            numeric_frame.var(axis=1, ddof=1)
            .fillna(0.0)
            .sort_values(ascending=False)
            .head(10)
        )

        # raw-matrix descriptive metrics (stage ``raw_*``)
        metrics: Dict[str, Any] = {
            "raw_dataset_type": dataset.dataset_type.value,
            "raw_matrix_shape": [row_count, column_count],
            "raw_feature_count": row_count,
            "raw_sample_count": column_count,
            "raw_feature_axis": dataset.feature_axis or "rows",
            "raw_sample_axis": dataset.sample_axis or "columns",
            "raw_format": dataset.format,
            "raw_non_missing_ratio": round(non_missing_ratio, 4),
            "raw_non_zero_ratio": round(non_zero_ratio, 4),
            "raw_mean_signal": round(mean_signal, 4),
            "raw_median_signal": round(median_signal, 4),
            "raw_sample_mean_range": [round(float(raw_sample_means.min()), 4), round(float(raw_sample_means.max()), 4)] if not raw_sample_means.empty else [0.0, 0.0],
            "raw_top_variable_features": [str(idx) for idx in raw_variable_features.index.tolist()],
        }

        tables = [
            {
                "title": "expression_matrix_raw_summary",
                "path": "tables/expression_matrix_raw_summary.json",
                "rows": 1,
            }
        ]
        figures = [
            {
                "title": "raw_sample_mean_distribution",
                "path": "figures/raw_sample_mean_distribution.txt",
                "caption": "Text summary of raw sample mean distribution.",
            }
        ]
        notes = [
            f"Raw matrix: {row_count} features × {column_count} samples.",
        ]
        return ExpressionMatrixParseResult(
            metrics=metrics,
            tables=tables,
            figures=figures,
            notes=notes,
            summary_path="summary/expression_matrix_raw_summary.json",
        )

    def write_summary(self, out_dir: Path, result: ExpressionMatrixParseResult) -> Path:
        summary_dir = out_dir / "summary"
        summary_dir.mkdir(parents=True, exist_ok=True)
        summary_path = summary_dir / "expression_matrix_summary.json"
        payload = {
            "metrics": result.metrics,
            "tables": result.tables,
            "figures": result.figures,
            "notes": result.notes,
        }
        summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary_path

