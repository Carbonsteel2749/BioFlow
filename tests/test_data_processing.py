"""Smoke-tests for the data_processing module.

Run with:  python -m pytest tests/ -v
"""

import tempfile
from pathlib import Path

import pandas as pd
import pytest

from bioflow.core.context import RunContext
from bioflow.core.models import (
    DatasetValidationError,
    ModuleInput,
    ModuleStatus,
)
from bioflow.modules.analysis.pipelines import ExpressionMatrixPipeline


# ---------------------------------------------------------------------------
#  helpers
# ---------------------------------------------------------------------------
def _make_dataset_spec(csv_path: str) -> dict:
    """Return a minimal payload that the adapter can detect."""
    return {
        "dataset_path": csv_path,
        "data_kind": "counts",
        "runner_mode": "mock",
    }


def _make_count_csv(path: Path, n_genes: int = 100, n_samples: int = 6) -> None:
    """Write a small synthetic count matrix."""
    import numpy as np

    rng = np.random.default_rng(42)
    genes = [f"gene_{i:04d}" for i in range(n_genes)]
    samples = [f"sample_{j}" for j in range(n_samples)]
    data = rng.poisson(lam=200, size=(n_genes, n_samples)).astype(float)
    # inject some missing values
    missing_rows = rng.choice(n_genes, size=5)
    missing_cols = rng.choice(n_samples, size=5)
    data[missing_rows, missing_cols] = np.nan
    df = pd.DataFrame(data, index=genes, columns=samples)
    df.index.name = "gene_id"
    df.to_csv(path)


# ---------------------------------------------------------------------------
#  tests
# ---------------------------------------------------------------------------
class TestPipeline:
    def test_qc_reduces_gene_count(self):
        """Genes with very low detection should be filtered out."""
        from bioflow.core.models import DatasetSpec, DatasetType

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "expr.csv"
            _make_count_csv(csv_path, n_genes=200, n_samples=6)

            # inject a batch of all-zero genes
            frame = pd.read_csv(csv_path, index_col=0)
            frame.iloc[:10, :] = 0.0
            frame.to_csv(csv_path)

            dataset = DatasetSpec(
                dataset_type=DatasetType.expression_matrix,
                primary_path=str(csv_path),
                format="csv",
                confidence=0.95,
                metadata={"separator": ","},
            )
            pipeline = ExpressionMatrixPipeline()
            result = pipeline.run(dataset, params={"data_kind": "counts"}, out_dir=Path(tmp))

            assert result.metrics["qc_genes_filtered_low_detect"] >= 10
            assert result.metrics["qc_genes_after_qc"] < result.metrics["qc_genes_before_qc"]

    def test_log1p_for_small_counts(self):
        """data_kind=counts with small values → log1p, not CPM."""
        from bioflow.core.models import DatasetSpec, DatasetType

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "expr.csv"
            _make_count_csv(csv_path, n_genes=50, n_samples=4)
            # scale down so max < 1000
            frame = pd.read_csv(csv_path, index_col=0)
            frame = frame.clip(upper=500)
            frame.to_csv(csv_path)

            dataset = DatasetSpec(
                dataset_type=DatasetType.expression_matrix,
                primary_path=str(csv_path),
                format="csv",
                confidence=0.95,
                metadata={"separator": ","},
            )
            pipeline = ExpressionMatrixPipeline()
            result = pipeline.run(dataset, params={"data_kind": "counts"}, out_dir=Path(tmp))
            assert result.metrics["normalization_method"] == "log1p"

    def test_non_counts_never_cpm(self):
        """data_kind=tpm must NOT trigger CPM even for large values."""
        from bioflow.core.models import DatasetSpec, DatasetType

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "tpm.csv"
            import numpy as np

            rng = np.random.default_rng(42)
            data = rng.lognormal(mean=6, sigma=2, size=(50, 4))
            df = pd.DataFrame(data, columns=[f"s{i}" for i in range(4)])
            df.index = [f"g{i}" for i in range(50)]
            df.index.name = "gene_id"
            df.to_csv(csv_path)

            dataset = DatasetSpec(
                dataset_type=DatasetType.expression_matrix,
                primary_path=str(csv_path),
                format="csv",
                confidence=0.95,
                metadata={"separator": ","},
            )
            pipeline = ExpressionMatrixPipeline()
            result = pipeline.run(dataset, params={"data_kind": "tpm"}, out_dir=Path(tmp))
            assert result.metrics["normalization_method"] != "cpm"

    def test_pca_produces_variance_ratios(self):
        """PCA should emit variance explained for each PC."""
        from bioflow.core.models import DatasetSpec, DatasetType

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "expr.csv"
            _make_count_csv(csv_path, n_genes=200, n_samples=8)

            dataset = DatasetSpec(
                dataset_type=DatasetType.expression_matrix,
                primary_path=str(csv_path),
                format="csv",
                confidence=0.95,
                metadata={"separator": ","},
            )
            pipeline = ExpressionMatrixPipeline()
            result = pipeline.run(dataset, params={"data_kind": "counts"}, out_dir=Path(tmp))
            ratios = result.metrics["pca_variance_ratio"]
            assert len(ratios) >= 2
            assert all(0 <= v <= 1 for v in ratios)
            assert abs(sum(ratios) - 1.0) < 0.01


class TestModuleIntegration:
    def test_missing_dataset_path_raises(self):
        """data_processing must complain when dataset_path is missing."""
        from bioflow.modules.analysis.data_processing import DataProcessingModule

        with tempfile.TemporaryDirectory() as tmp:
            ctx = RunContext(run_id="test-001", workspace=Path(tmp))
            module_input = ModuleInput(run_id="test-001", module="data_processing", payload={})
            mod = DataProcessingModule()
            with pytest.raises(DatasetValidationError):
                mod.run(ctx, module_input)
