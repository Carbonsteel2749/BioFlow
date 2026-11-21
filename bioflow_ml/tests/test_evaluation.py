from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression

from bioflow_ml.evaluation import evaluate_classifier
from bioflow_ml.fixtures import make_classification_fixture


def test_evaluate_classifier_accepts_array_like_labels() -> None:
    X, y = make_classification_fixture(n_samples=50, n_features=12, random_state=0)

    metrics, predicted, model = evaluate_classifier(
        LogisticRegression(max_iter=500),
        X,
        y.to_numpy(),
        cv=5,
        random_state=0,
    )

    assert predicted.index.equals(X.index)
    assert predicted.name == "prediction"
    assert set(predicted.unique()).issubset({0, 1})
    assert "accuracy" in metrics
    assert "roc_auc" in metrics
    assert model is not None


def test_evaluate_classifier_aligns_series_by_sample_id() -> None:
    X, y = make_classification_fixture(n_samples=40, n_features=10, random_state=1)
    shuffled_y = y.sample(frac=1.0, random_state=2)

    metrics, predicted, _ = evaluate_classifier(
        LogisticRegression(max_iter=500),
        X,
        shuffled_y,
        cv=4,
        random_state=1,
    )

    assert predicted.index.equals(X.index)
    assert metrics["cv_folds"] == 4.0


def test_evaluate_classifier_rejects_missing_labels() -> None:
    X, y = make_classification_fixture(n_samples=30, n_features=8, random_state=3)
    y_with_nan = y.astype(float)
    y_with_nan.iloc[0] = np.nan

    with pytest.raises(ValueError, match="missing values"):
        evaluate_classifier(LogisticRegression(max_iter=500), X, y_with_nan)


def test_evaluate_classifier_accepts_string_labels() -> None:
    X, y = make_classification_fixture(n_samples=40, n_features=10, random_state=4)
    groups = y.map({0: "control", 1: "case"})
    metrics, predicted, _ = evaluate_classifier(LogisticRegression(max_iter=500), X, groups, cv=4)
    assert predicted.index.equals(X.index)
    assert set(predicted.unique()).issubset({"control", "case"})
    assert "f1" in metrics
