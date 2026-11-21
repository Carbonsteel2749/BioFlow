"""监督分类算法测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bioflow_ml.algorithms import supervised  # noqa: F401  # trigger registration
from bioflow_ml.core import MLInput, registry
from bioflow_ml.fixtures import make_classification_fixture


@pytest.fixture
def classification_data() -> MLInput:
    X, y = make_classification_fixture(n_samples=60, n_features=20, random_state=42)
    return MLInput(X=X, y=y, task="classification")


class TestLogisticRegression:
    def test_normal_run(self, classification_data: MLInput) -> None:
        result = registry.create("logistic_regression", random_state=0).run(classification_data)
        assert result.algorithm == "logistic_regression"
        assert result.predictions is not None
        assert result.predictions.index.equals(classification_data.X.index)
        assert "accuracy" in result.metrics
        assert "f1" in result.metrics
        assert result.metrics["accuracy"] >= 0.0
        assert result.feature_scores is not None
        assert result.feature_scores.shape[0] == classification_data.X.shape[1]
        assert result.model is not None

    def test_probability_output(self, classification_data: MLInput) -> None:
        result = registry.create("logistic_regression", random_state=0).run(classification_data)
        assert result.metadata["fitted_model_probabilities"] is not None
        proba = result.metadata["fitted_model_probabilities"]
        assert proba.index.equals(classification_data.X.index)
        assert proba.name == "probability"
        assert proba.between(0, 1).all()

    def test_custom_params(self, classification_data: MLInput) -> None:
        result = registry.create("logistic_regression", C=0.5, max_iter=500, random_state=0).run(
            classification_data
        )
        assert result.algorithm == "logistic_regression"
        assert "accuracy" in result.metrics

    def test_reproducibility(self, classification_data: MLInput) -> None:
        first = registry.create("logistic_regression", random_state=7).run(classification_data)
        second = registry.create("logistic_regression", random_state=7).run(classification_data)
        assert first.predictions is not None
        assert second.predictions is not None
        pd.testing.assert_series_equal(first.predictions, second.predictions)


class TestSVM:
    def test_normal_run(self, classification_data: MLInput) -> None:
        result = registry.create("svm", kernel="rbf", random_state=0).run(classification_data)
        assert result.algorithm == "svm"
        assert result.predictions is not None
        assert result.predictions.index.equals(classification_data.X.index)
        assert "accuracy" in result.metrics
        assert "roc_auc" in result.metrics
        assert result.model is not None

    def test_probability_output(self, classification_data: MLInput) -> None:
        result = registry.create("svm", random_state=0).run(classification_data)
        assert result.metadata["fitted_model_probabilities"] is not None
        proba = result.metadata["fitted_model_probabilities"]
        assert proba.between(0, 1).all()

    def test_linear_kernel_feature_importance(self, classification_data: MLInput) -> None:
        result = registry.create("svm", kernel="linear", random_state=0).run(classification_data)
        assert result.feature_scores is not None
        assert result.feature_scores.shape[0] == classification_data.X.shape[1]

    def test_reproducibility(self, classification_data: MLInput) -> None:
        first = registry.create("svm", kernel="rbf", random_state=7).run(classification_data)
        second = registry.create("svm", kernel="rbf", random_state=7).run(classification_data)
        assert first.predictions is not None
        assert second.predictions is not None
        pd.testing.assert_series_equal(first.predictions, second.predictions)


class TestRandomForest:
    def test_normal_run(self, classification_data: MLInput) -> None:
        result = registry.create("random_forest", n_estimators=50, random_state=0).run(
            classification_data
        )
        assert result.algorithm == "random_forest"
        assert result.predictions is not None
        assert result.predictions.index.equals(classification_data.X.index)
        assert "accuracy" in result.metrics
        assert result.feature_scores is not None
        assert result.feature_scores.shape[0] == classification_data.X.shape[1]
        assert result.model is not None

    def test_probability_output(self, classification_data: MLInput) -> None:
        result = registry.create("random_forest", n_estimators=20, random_state=0).run(
            classification_data
        )
        assert result.metadata["fitted_model_probabilities"] is not None
        proba = result.metadata["fitted_model_probabilities"]
        assert proba.between(0, 1).all()

    def test_feature_importance(self, classification_data: MLInput) -> None:
        result = registry.create("random_forest", n_estimators=50, random_state=0).run(
            classification_data
        )
        assert result.feature_scores is not None
        assert "importance" in result.feature_scores.columns
        assert result.feature_scores["importance"].sum() > 0

    def test_reproducibility(self, classification_data: MLInput) -> None:
        first = registry.create("random_forest", n_estimators=30, random_state=7).run(
            classification_data
        )
        second = registry.create("random_forest", n_estimators=30, random_state=7).run(
            classification_data
        )
        assert first.predictions is not None
        assert second.predictions is not None
        pd.testing.assert_series_equal(first.predictions, second.predictions)


def test_supervised_algorithms_registered() -> None:
    names = {spec.name for spec in registry.list_specs()}
    assert {"logistic_regression", "svm", "random_forest"}.issubset(names)
