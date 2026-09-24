from __future__ import annotations

from dataclasses import dataclass
import csv
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from bioflow.core.models import DatasetSpec, DatasetType, PipelineSpec


@dataclass
class AdapterDetection:
    dataset: DatasetSpec
    pipeline: PipelineSpec


class AnalysisAdapter:
    dataset_type: DatasetType = DatasetType.unknown

    def can_handle(self, path: Path, payload: Dict) -> float:
        return 0.0

    def detect(self, path: Path, payload: Dict) -> DatasetSpec:
        raise NotImplementedError

    def build_pipeline(self, dataset: DatasetSpec, payload: Dict) -> PipelineSpec:
        raise NotImplementedError


class ExpressionMatrixAdapter(AnalysisAdapter):
    dataset_type = DatasetType.expression_matrix

    def _infer_separator(self, path: Path, payload: Dict) -> str:
        sep = payload.get("sep")
        if sep:
            return str(sep)

        suffix = path.suffix.lower()
        if suffix == ".tsv":
            return "\t"
        if suffix == ".csv":
            return ","

        try:
            sample = path.read_text(encoding=payload.get("encoding", "utf-8"), errors="ignore").splitlines()
            sample = "\n".join(sample[:5])
            dialect = csv.Sniffer().sniff(sample, delimiters=["\t", ",", ";", "|"])
            return dialect.delimiter
        except Exception:
            return "\t"

    def _read_frame(self, path: Path, payload: Dict) -> pd.DataFrame:
        suffix = path.suffix.lower()
        if suffix in {".xlsx", ".xls"}:
            sheet_name = payload.get("sheet_name")
            return pd.read_excel(path, sheet_name=sheet_name)
        sep = self._infer_separator(path, payload)
        return pd.read_csv(path, sep=sep)

    def can_handle(self, path: Path, payload: Dict) -> float:
        if not path.exists() or not path.is_file():
            return 0.0
        if path.suffix.lower() in {".csv", ".tsv", ".txt", ".xlsx", ".xls"}:
            return 0.8
        return 0.0

    def detect(self, path: Path, payload: Dict) -> DatasetSpec:
        frame = self._read_frame(path, payload)
        if frame.empty or frame.shape[0] < 2 or frame.shape[1] < 2:
            raise ValueError("expression matrix requires at least 2 rows and 2 columns")

        numeric_ratio = frame.apply(pd.to_numeric, errors="coerce").notna().mean(axis=0)
        numeric_columns = int((numeric_ratio >= 0.5).sum())
        if numeric_columns < max(1, frame.shape[1] - 1):
            raise ValueError("file does not look like an expression matrix")

        dataset = DatasetSpec(
            dataset_type=DatasetType.expression_matrix,
            primary_path=str(path),
            source_paths=[str(path)],
            format=path.suffix.lstrip(".").lower(),
            confidence=0.95,
            shape=[int(frame.shape[0]), int(frame.shape[1])],
            feature_axis=payload.get("feature_axis", "rows"),
            sample_axis=payload.get("sample_axis", "columns"),
            metadata={
                "columns": [str(column) for column in frame.columns.tolist()[:20]],
                "row_count": int(frame.shape[0]),
                "column_count": int(frame.shape[1]),
                "separator": self._infer_separator(path, payload),
                "sheet_name": payload.get("sheet_name"),
            },
        )
        return dataset

    def build_pipeline(self, dataset: DatasetSpec, payload: Dict) -> PipelineSpec:
        params = {
            "input": dataset.primary_path,
            "format": dataset.format,
            "feature_axis": dataset.feature_axis or "rows",
            "sample_axis": dataset.sample_axis or "columns",
            "sep": dataset.metadata.get("separator") if isinstance(dataset.metadata, dict) else payload.get("sep"),
            "sheet_name": payload.get("sheet_name"),
        }
        params = {key: value for key, value in params.items() if value is not None and value != ""}
        return PipelineSpec(
            pipeline_id="expression_matrix_qc",
            dataset_type=dataset.dataset_type,
            runner_mode=payload.get("runner_mode", "mock"),
            params=params,
            description="Quality control and summarization for expression matrix data.",
        )


class AdapterRegistry:
    def __init__(self, adapters: Optional[Iterable[AnalysisAdapter]] = None) -> None:
        self._adapters: List[AnalysisAdapter] = list(adapters or [ExpressionMatrixAdapter()])

    def add(self, adapter: AnalysisAdapter) -> None:
        self._adapters.append(adapter)

    def detect(self, dataset_path: str, payload: Dict) -> AdapterDetection:
        path = Path(dataset_path)
        scores = sorted(
            ((adapter.can_handle(path, payload), adapter) for adapter in self._adapters),
            key=lambda item: item[0],
            reverse=True,
        )
        for score, adapter in scores:
            if score <= 0:
                continue
            dataset = adapter.detect(path, payload)
            pipeline = adapter.build_pipeline(dataset, payload)
            return AdapterDetection(dataset=dataset, pipeline=pipeline)
        raise ValueError(f"unsupported dataset: {dataset_path}")


_registry = AdapterRegistry()


def get_adapter_registry() -> AdapterRegistry:
    return _registry
