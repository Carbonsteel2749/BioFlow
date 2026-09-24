"""所有监督分类算法共用的评估入口。"""

from __future__ import annotations

from typing import Any

import pandas as pd
from sklearn.base import clone
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict


def _normalize_labels(X: pd.DataFrame, y: Any) -> pd.Series:
    """Normalize labels into a Series aligned to X index."""

    if isinstance(y, pd.DataFrame):
        if y.shape[1] != 1:
            raise ValueError("y DataFrame must contain exactly one label column")
        labels = y.iloc[:, 0]
    elif isinstance(y, pd.Series):
        labels = y.copy()
    else:
        labels = pd.Series(y, name="label")

    if len(labels) != len(X):
        raise ValueError("X and y must contain the same number of samples")

    if labels.index.equals(X.index):
        aligned = labels
    elif labels.index.is_unique and X.index.is_unique and set(labels.index) == set(X.index):
        aligned = labels.loc[X.index]
    else:
        aligned = labels.copy()
        aligned.index = X.index

    if aligned.isna().any():
        raise ValueError("classification labels must not contain missing values")

    if aligned.name is None:
        aligned.name = "label"
    return aligned


def evaluate_classifier(
    estimator: Any,
    X: pd.DataFrame,
    y: Any,
    *,
    cv: int = 5,
    random_state: int = 42,
) -> tuple[dict[str, float], pd.Series, Any]:
    """返回交叉验证指标、折外预测和全量拟合模型。"""

    labels = _normalize_labels(X, y)
    class_counts = labels.value_counts()
    folds = min(cv, int(class_counts.min()))
    if labels.nunique() < 2 or folds < 2:
        raise ValueError("classification needs at least two classes and two samples per class")
    splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=random_state)
    predicted = cross_val_predict(clone(estimator), X, labels, cv=splitter, method="predict")
    average = "binary" if labels.nunique() == 2 else "macro"
    metric_kwargs: dict[str, Any] = {"average": average, "zero_division": 0}
    if average == "binary":
        # sklearn defaults to pos_label=1, which fails for biological labels
        # such as "case"/"control" or "NHEK"/"UT-SCC".
        metric_kwargs["pos_label"] = labels.drop_duplicates().iloc[-1]
    metrics = {
        "accuracy": float(accuracy_score(labels, predicted)),
        "f1": float(f1_score(labels, predicted, **metric_kwargs)),
        "precision": float(precision_score(labels, predicted, **metric_kwargs)),
        "recall": float(recall_score(labels, predicted, **metric_kwargs)),
        "cv_folds": float(folds),
    }
    if labels.nunique() == 2:
        try:
            probabilities = cross_val_predict(clone(estimator), X, labels, cv=splitter, method="predict_proba")
            metrics["roc_auc"] = float(roc_auc_score(labels, probabilities[:, 1]))
        except (AttributeError, ValueError):
            pass
    fitted = clone(estimator).fit(X, labels)
    return metrics, pd.Series(predicted, index=X.index, name="prediction"), fitted
