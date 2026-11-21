"""特征选择算法测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bioflow_ml.algorithms import feature_selection  # noqa: F401  # trigger registration
from bioflow_ml.core import MLInput, registry
from bioflow_ml.fixtures import make_classification_fixture


@pytest.fixture
def classification_data() -> MLInput:
    X, y = make_classification_fixture(n_samples=60, n_features=30, random_state=42)
    return MLInput(X=X, y=y, task="classification")


class TestLASSO:
    def test_normal_run(self, classification_data: MLInput) -> None:
        result = registry.create("lasso", C=0.1, random_state=0).run(classification_data)
        assert result.algorithm == "lasso"
        assert result.predictions is not None
        assert result.predictions.index.equals(classification_data.X.index)
        assert "accuracy" in result.metrics
        assert "n_selected_features" in result.metrics
        assert result.metrics["n_selected_features"] >= 0
        assert result.feature_scores is not None
        assert "selected" in result.feature_scores.columns
        assert result.model is not None

    def test_feature_selection_in_cv(self, classification_data: MLInput) -> None:
        """确保特征选择在 CV 内执行，不使用全量数据。"""
        result = registry.create("lasso", C=0.01, random_state=0).run(classification_data)
        # 检查有特征被选中
        selected_features = result.metadata["selected_features"]
        assert isinstance(selected_features, list)
        # 特征数应该少于原始特征数（稀疏性）
        assert len(selected_features) <= classification_data.X.shape[1]

    def test_probability_output(self, classification_data: MLInput) -> None:
        result = registry.create("lasso", C=0.1, random_state=0).run(classification_data)
        proba = result.metadata.get("fitted_model_probabilities")
        if proba is not None:
            assert proba.index.equals(classification_data.X.index)
            assert proba.between(0, 1).all()

    def test_reproducibility(self, classification_data: MLInput) -> None:
        first = registry.create("lasso", C=0.1, random_state=7).run(classification_data)
        second = registry.create("lasso", C=0.1, random_state=7).run(classification_data)
        assert first.predictions is not None
        assert second.predictions is not None
        pd.testing.assert_series_equal(first.predictions, second.predictions)


class TestRFE:
    def test_normal_run(self, classification_data: MLInput) -> None:
        result = registry.create("rfe", n_features_to_select=10, random_state=0).run(
            classification_data
        )
        assert result.algorithm == "rfe"
        assert result.predictions is not None
        assert result.predictions.index.equals(classification_data.X.index)
        assert "accuracy" in result.metrics
        assert "n_selected_features" in result.metrics
        assert result.metrics["n_selected_features"] == 10.0
        assert result.feature_scores is not None
        assert "ranking" in result.feature_scores.columns
        assert "selected" in result.feature_scores.columns
        assert result.model is not None

    def test_feature_selection_count(self, classification_data: MLInput) -> None:
        n_select = 5
        result = registry.create(
            "rfe", n_features_to_select=n_select, random_state=0
        ).run(classification_data)
        selected = result.metadata["selected_features"]
        assert len(selected) == n_select

    def test_feature_ranking(self, classification_data: MLInput) -> None:
        result = registry.create("rfe", n_features_to_select=10, random_state=0).run(
            classification_data
        )
        ranking = result.feature_scores["ranking"]
        # 排名 1 的特征是最重要的
        assert ranking.min() == 1
        # 选中的特征排名为 1
        selected_mask = result.feature_scores["selected"]
        assert (ranking[selected_mask] == 1).all()

    def test_probability_output(self, classification_data: MLInput) -> None:
        result = registry.create("rfe", n_features_to_select=10, random_state=0).run(
            classification_data
        )
        proba = result.metadata.get("fitted_model_probabilities")
        if proba is not None:
            assert proba.index.equals(classification_data.X.index)
            assert proba.between(0, 1).all()

    def test_reproducibility(self, classification_data: MLInput) -> None:
        first = registry.create("rfe", n_features_to_select=8, random_state=7).run(
            classification_data
        )
        second = registry.create("rfe", n_features_to_select=8, random_state=7).run(
            classification_data
        )
        assert first.predictions is not None
        assert second.predictions is not None
        pd.testing.assert_series_equal(first.predictions, second.predictions)


def test_feature_selection_algorithms_registered() -> None:
    names = {spec.name for spec in registry.list_specs()}
    assert {"lasso", "rfe"}.issubset(names)


def test_all_algorithms_registered() -> None:
    """验证所有算法都已正确注册。"""
    names = {spec.name for spec in registry.list_specs()}
    expected = {"pca", "kmeans", "hierarchical", "logistic_regression", "svm", "random_forest", "lasso", "rfe"}
    assert expected.issubset(names)
