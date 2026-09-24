"""Random Forest 分类算法。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

from bioflow_ml.core import (
    AlgorithmResult,
    AlgorithmSpec,
    BaseAlgorithm,
    MLInput,
    register_algorithm,
)
from bioflow_ml.evaluation import evaluate_classifier


@register_algorithm
class RandomForestAlgorithm(BaseAlgorithm):
    """随机森林分类器。"""

    spec = AlgorithmSpec(
        name="random_forest",
        version="0.1.0",
        tasks=("classification",),
        description="Random Forest classifier",
    )

    def run(self, data: MLInput) -> AlgorithmResult:
        y = data.require_labels()
        params = self.merged_params(data)

        # 构建估计器参数
        estimator_kwargs: dict[str, Any] = {
            "random_state": params.get("random_state", 42),
            "n_jobs": params.get("n_jobs", -1),
        }

        # 可选参数
        if "n_estimators" in params:
            estimator_kwargs["n_estimators"] = int(params["n_estimators"])
        if "max_depth" in params:
            estimator_kwargs["max_depth"] = int(params["max_depth"])
        if "min_samples_split" in params:
            estimator_kwargs["min_samples_split"] = int(params["min_samples_split"])
        if "min_samples_leaf" in params:
            estimator_kwargs["min_samples_leaf"] = int(params["min_samples_leaf"])
        if "max_features" in params:
            estimator_kwargs["max_features"] = params["max_features"]
        if "class_weight" in params:
            estimator_kwargs["class_weight"] = params["class_weight"]

        estimator = RandomForestClassifier(**estimator_kwargs)

        # 调用统一评估
        cv = int(params.get("cv", 5))
        random_state = int(params.get("random_state", 42))
        metrics, predictions, fitted_model = evaluate_classifier(
            estimator, data.X, y, cv=cv, random_state=random_state
        )

        # 提取特征重要性（Gini importance）
        feature_scores = pd.DataFrame(
            {"importance": fitted_model.feature_importances_},
            index=data.X.columns,
        ).sort_values("importance", ascending=False)

        # 二分类时获取预测概率
        predictions_proba = None
        if y.nunique() == 2:
            predictions_proba = pd.Series(
                fitted_model.predict_proba(data.X)[:, 1],
                index=data.X.index,
                name="probability",
            )

        return AlgorithmResult(
            algorithm=self.spec.name,
            metrics=metrics,
            predictions=predictions,
            feature_scores=feature_scores,
            model=fitted_model,
            metadata={
                "n_samples": data.X.shape[0],
                "n_features": data.X.shape[1],
                "n_classes": y.nunique(),
                "n_estimators": fitted_model.n_estimators,
                "cv_folds": cv,
                "fitted_model_probabilities": predictions_proba,
                "probability_scope": "in_sample_fitted_model",
            },
        )
