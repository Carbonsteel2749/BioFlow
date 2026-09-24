"""统一表达矩阵预处理，并输出算法库使用的 MLInput。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from bioflow_ml.core import MLInput
from bioflow_ml.preprocessing.data_loader import load_table

DataKind = Literal["auto", "counts", "normalized", "unknown"]


def _infer_data_kind(matrix: pd.DataFrame) -> tuple[str, float, list[str]]:
    """仅提供保守的数值建议；生产流程应优先显式传入 data_kind。"""

    values = matrix.to_numpy(dtype=float)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        raise ValueError("expression matrix contains no finite numeric values")
    nonnegative = bool((finite >= 0).all())
    integer_ratio = float(np.isclose(finite, np.round(finite)).mean())
    if nonnegative and integer_ratio >= 0.995:
        return "counts", 0.85, [f"{integer_ratio:.1%} values are non-negative integers"]
    if nonnegative:
        return "normalized", 0.55, ["values are non-negative but not predominantly integers"]
    return "unknown", 0.20, ["matrix contains negative values or mixed-scale values"]


def _align_labels(labels: object, sample_ids: pd.Index) -> pd.Series:
    if isinstance(labels, pd.DataFrame):
        if {"sample_id", "group"}.issubset(labels.columns):
            series = labels.set_index("sample_id")["group"]
        elif labels.shape[1] == 1:
            series = labels.iloc[:, 0]
        else:
            raise ValueError("metadata must contain sample_id/group columns or exactly one label column")
    elif isinstance(labels, pd.Series):
        series = labels.copy()
    else:
        series = pd.Series(labels, index=sample_ids, name="label")

    if len(series) != len(sample_ids):
        raise ValueError("matrix and labels must contain the same number of samples")
    if series.index.equals(sample_ids):
        aligned = series
    elif series.index.is_unique and set(series.index) == set(sample_ids):
        aligned = series.loc[sample_ids]
    else:
        raise ValueError("matrix sample IDs and metadata sample IDs do not match")
    if aligned.isna().any():
        raise ValueError("labels must not contain missing values")
    return aligned.rename("label")


def preprocess_expression_matrix(
    frame: pd.DataFrame,
    labels: object | None = None,
    *,
    data_kind: DataKind = "auto",
    feature_column: str | None = None,
    min_detection_fraction: float = 0.1,
    task: str = "clustering",
) -> MLInput:
    """将常见的“特征 × 样本”表达矩阵转换为安全的样本 × 特征 MLInput。

    原始 counts 会执行低检出特征过滤、CPM 和 log1p；已归一化数据不再
    重复归一化。这里不执行 z-score，避免在全量数据上缩放造成交叉验证泄漏。
    """

    if not 0 <= min_detection_fraction <= 1:
        raise ValueError("min_detection_fraction must be between 0 and 1")
    if frame.shape[1] < 2:
        raise ValueError("expression table needs one feature column and at least one sample column")

    feature_column = feature_column or str(frame.columns[0])
    if feature_column not in frame.columns:
        raise ValueError(f"feature column '{feature_column}' not found")
    features = frame[feature_column].astype(str)
    if not features.is_unique:
        raise ValueError("feature IDs must be unique")

    matrix = frame.drop(columns=[feature_column]).apply(pd.to_numeric, errors="raise")
    matrix.index = features
    matrix = matrix.dropna(axis=0, how="all")
    if matrix.isna().any().any():
        # Feature-wise median imputation is deterministic and keeps MLInput finite.
        matrix = matrix.T.apply(lambda column: column.fillna(column.median())).T
        matrix = matrix.dropna(axis=0, how="any")
    if matrix.empty or not np.isfinite(matrix.to_numpy(dtype=float)).all():
        raise ValueError("expression matrix has no complete finite features after cleaning")

    inferred_kind, confidence, reasons = _infer_data_kind(matrix)
    effective_kind = inferred_kind if data_kind == "auto" else data_kind
    warnings: list[str] = []
    if data_kind == "auto":
        warnings.append("data kind is inferred; pass data_kind explicitly for production runs")
    if effective_kind not in {"counts", "normalized", "unknown"}:
        raise ValueError("data_kind must be auto, counts, normalized, or unknown")

    detection = (matrix != 0).mean(axis=1)
    matrix = matrix.loc[detection >= min_detection_fraction]
    if matrix.empty:
        raise ValueError("all features were removed by min_detection_fraction")

    normalization_method = "not_applied"
    if effective_kind == "counts":
        if (matrix < 0).any().any():
            raise ValueError("counts matrix must not contain negative values")
        library_sizes = matrix.sum(axis=0)
        if (library_sizes <= 0).any():
            bad_samples = library_sizes.index[library_sizes <= 0].tolist()
            raise ValueError(f"counts matrix contains zero-library samples: {bad_samples}")
        matrix = np.log1p(matrix.div(library_sizes, axis=1) * 1_000_000)
        normalization_method = "cpm_log1p"
    elif effective_kind == "normalized":
        normalization_method = "skipped_already_normalized"
    else:
        warnings.append("normalization skipped because data status is unknown")

    X = matrix.T
    # Remove constant features after normalization, as no algorithm can use them.
    X = X.loc[:, X.nunique(dropna=False) > 1]
    if X.empty:
        raise ValueError("no variable features remain after preprocessing")

    y = _align_labels(labels, X.index) if labels is not None else None
    return MLInput(
        X=X,
        y=y,
        task=task,
        metadata={
            "input_orientation": "features_by_samples",
            "inferred_data_kind": inferred_kind,
            "inference_confidence": confidence,
            "inference_reasons": reasons,
            "effective_data_kind": effective_kind,
            "normalization_method": normalization_method,
            "warnings": warnings,
        },
    )


def preprocess_file(
    expression_path: str | Path,
    metadata_path: str | Path | None = None,
    **kwargs: object,
) -> MLInput:
    """从表达矩阵文件和可选 metadata 文件创建 MLInput。"""

    frame = load_table(expression_path, sheet_name=kwargs.pop("sheet_name", 0))
    labels = load_table(metadata_path) if metadata_path is not None else None
    return preprocess_expression_matrix(frame, labels, **kwargs)


# Backward-compatible name used by early team drafts.
preprocess_data = preprocess_expression_matrix
