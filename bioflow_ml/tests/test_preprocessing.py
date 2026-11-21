"""文件读取和统一表达矩阵预处理测试。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from bioflow_ml.core import MLInput
from bioflow_ml.preprocessing import load_table, preprocess_expression_matrix, preprocess_file


def _write_counts_files(tmp_path: Path) -> tuple[Path, Path]:
    expression = pd.DataFrame(
        {
            "gene_id": ["g1", "g2", "g3"],
            "case_1": [100, 0, 20],
            "case_2": [120, 0, 18],
            "control_1": [5, 0, 10],
            "control_2": [6, 0, 12],
        }
    )
    metadata = pd.DataFrame(
        {
            "sample_id": ["case_1", "case_2", "control_1", "control_2"],
            "group": ["case", "case", "control", "control"],
        }
    )
    expression_path = tmp_path / "counts.csv"
    metadata_path = tmp_path / "metadata.csv"
    expression.to_csv(expression_path, index=False)
    metadata.to_csv(metadata_path, index=False)
    return expression_path, metadata_path


def test_preprocess_counts_file_returns_mldata(tmp_path: Path) -> None:
    expression_path, metadata_path = _write_counts_files(tmp_path)
    result = preprocess_file(
        expression_path,
        metadata_path,
        data_kind="counts",
        task="classification",
    )
    assert isinstance(result, MLInput)
    assert result.X.shape == (4, 2)  # g2 is removed as an undetected feature
    assert result.y is not None and result.y.index.equals(result.X.index)
    assert result.metadata["normalization_method"] == "cpm_log1p"
    assert result.X.index.tolist() == ["case_1", "case_2", "control_1", "control_2"]


def test_normalized_data_skips_count_normalization() -> None:
    frame = pd.DataFrame(
        {"Symbol": ["g1", "g2"], "s1": [0.4, 1.2], "s2": [0.9, 2.1]}
    )
    result = preprocess_expression_matrix(frame, data_kind="normalized", min_detection_fraction=0)
    assert result.metadata["normalization_method"] == "skipped_already_normalized"
    assert result.X.loc["s1", "g1"] == pytest.approx(0.4)


def test_metadata_sample_mismatch_is_rejected(tmp_path: Path) -> None:
    expression_path, metadata_path = _write_counts_files(tmp_path)
    metadata = pd.read_csv(metadata_path)
    metadata.loc[0, "sample_id"] = "unknown_sample"
    metadata.to_csv(metadata_path, index=False)
    with pytest.raises(ValueError, match="sample IDs"):
        preprocess_file(expression_path, metadata_path, data_kind="counts")


def test_csv_loader_reads_table(tmp_path: Path) -> None:
    path, _ = _write_counts_files(tmp_path)
    loaded = load_table(path)
    assert loaded.shape == (3, 5)
