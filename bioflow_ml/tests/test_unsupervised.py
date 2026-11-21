"""无监督算法（PCA / k-means / 层次聚类）测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bioflow_ml.algorithms import unsupervised  # noqa: F401  # trigger registration
from bioflow_ml.core import MLInput, registry
from bioflow_ml.fixtures import make_clustering_fixture


@pytest.fixture
def clustering_data() -> MLInput:
    return MLInput(X=make_clustering_fixture(), task="clustering")


class TestPCA:
    def test_normal_run(self, clustering_data: MLInput) -> None:
        result = registry.create("pca", n_components=3, random_state=0).run(
            clustering_data
        )
        assert result.algorithm == "pca"
        assert result.transformed_data is not None
        assert result.transformed_data.shape == (clustering_data.X.shape[0], 3)
        assert result.transformed_data.index.equals(clustering_data.X.index)
        assert list(result.transformed_data.columns) == ["PC1", "PC2", "PC3"]
        assert "explained_variance_ratio_sum" in result.metrics
        assert 0.0 < result.metrics["explained_variance_ratio_sum"] <= 1.0
        assert result.feature_scores is not None
        assert result.feature_scores.shape[0] == clustering_data.X.shape[1]
        assert result.model is not None

    def test_invalid_n_components(self, clustering_data: MLInput) -> None:
        algo = registry.create("pca", n_components=clustering_data.X.shape[1] + 10)
        with pytest.raises(ValueError, match="n_components"):
            algo.run(clustering_data)

    def test_reproducibility(self, clustering_data: MLInput) -> None:
        first = registry.create("pca", n_components=2, random_state=7).run(
            clustering_data
        )
        second = registry.create("pca", n_components=2, random_state=7).run(
            clustering_data
        )
        assert first.transformed_data is not None
        assert second.transformed_data is not None
        np.testing.assert_allclose(
            first.transformed_data.to_numpy(),
            second.transformed_data.to_numpy(),
        )


class TestKMeans:
    def test_normal_run(self, clustering_data: MLInput) -> None:
        result = registry.create(
            "kmeans", n_clusters=3, random_state=42, n_init=10
        ).run(clustering_data)
        assert result.algorithm == "kmeans"
        assert result.labels is not None
        assert result.labels.index.equals(clustering_data.X.index)
        assert result.labels.name == "cluster"
        assert set(result.labels.unique()).issubset({0, 1, 2})
        assert result.labels.nunique() == 3
        assert "inertia" in result.metrics
        assert result.metrics["inertia"] >= 0.0
        assert "silhouette" in result.metrics
        assert -1.0 <= result.metrics["silhouette"] <= 1.0
        assert result.model is not None

    def test_invalid_n_clusters(self, clustering_data: MLInput) -> None:
        algo = registry.create("kmeans", n_clusters=1)
        with pytest.raises(ValueError, match="n_clusters"):
            algo.run(clustering_data)

    def test_reproducibility(self, clustering_data: MLInput) -> None:
        first = registry.create("kmeans", n_clusters=3, random_state=0).run(
            clustering_data
        )
        second = registry.create("kmeans", n_clusters=3, random_state=0).run(
            clustering_data
        )
        assert first.labels is not None
        assert second.labels is not None
        pd.testing.assert_series_equal(first.labels, second.labels)
        assert first.metrics["inertia"] == pytest.approx(second.metrics["inertia"])


class TestHierarchical:
    def test_normal_run(self, clustering_data: MLInput) -> None:
        result = registry.create(
            "hierarchical", n_clusters=3, linkage="ward"
        ).run(clustering_data)
        assert result.algorithm == "hierarchical"
        assert result.labels is not None
        assert result.labels.index.equals(clustering_data.X.index)
        assert result.labels.nunique() == 3
        assert "silhouette" in result.metrics
        assert -1.0 <= result.metrics["silhouette"] <= 1.0
        assert result.metadata["linkage"] == "ward"
        assert result.model is not None

    def test_invalid_params(self, clustering_data: MLInput) -> None:
        too_many = registry.create("hierarchical", n_clusters=10_000)
        with pytest.raises(ValueError, match="n_clusters"):
            too_many.run(clustering_data)

        bad_metric = registry.create(
            "hierarchical", n_clusters=3, linkage="ward", metric="cosine"
        )
        with pytest.raises(ValueError, match="ward linkage"):
            bad_metric.run(clustering_data)

    def test_reproducibility(self, clustering_data: MLInput) -> None:
        first = registry.create(
            "hierarchical", n_clusters=3, linkage="average", metric="euclidean"
        ).run(clustering_data)
        second = registry.create(
            "hierarchical", n_clusters=3, linkage="average", metric="euclidean"
        ).run(clustering_data)
        assert first.labels is not None
        assert second.labels is not None
        pd.testing.assert_series_equal(first.labels, second.labels)


def test_algorithms_registered() -> None:
    names = {spec.name for spec in registry.list_specs()}
    assert {"pca", "kmeans", "hierarchical", "tsne", "umap"}.issubset(names)


def test_tsne_normal_run(clustering_data: MLInput) -> None:
    result = registry.create("tsne", n_components=2, perplexity=3, random_state=0).run(clustering_data)
    assert result.transformed_data is not None
    assert result.transformed_data.shape == (len(clustering_data.X), 2)
    assert result.metrics["perplexity"] == 3.0


def test_params_from_mlinput_override_constructor() -> None:
    X = make_clustering_fixture()
    data = MLInput(X=X, task="clustering", params={"n_clusters": 2})
    result = registry.create("kmeans", n_clusters=5, random_state=1).run(data)
    assert result.labels is not None
    assert result.labels.nunique() == 2
    assert result.metrics["n_clusters"] == 2.0
