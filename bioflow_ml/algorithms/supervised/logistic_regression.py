"""Logistic Regression 分类算法。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression as SklearnLogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from bioflow_ml.core import (
    AlgorithmResult,
    AlgorithmSpec,
    BaseAlgorithm,
    MLInput,
    register_algorithm,
)
from bioflow_ml.evaluation import evaluate_classifier


@register_algorithm
class LogisticRegressionAlgorithm(BaseAlgorithm):
    """Logistic Regression 分类器，支持 L1/L2 正则化。"""

    spec = AlgorithmSpec(
        name="logistic_regression",
        version="0.1.0",
        tasks=("classification",),
        description="Logistic Regression classifier with L1/L2 regularization",
    )

    def run(self, data: MLInput) -> AlgorithmResult:
        y = data.require_labels()
        params = self.merged_params(data)

        # 构建估计器参数
        estimator_kwargs: dict[str, Any] = {
            "max_iter": int(params.get("max_iter", 1000)),
            "random_state": params.get("random_state", 42),
        }

        # 可选参数
        if "C" in params:
            estimator_kwargs["C"] = float(params["C"])
        if "penalty" in params:
            estimator_kwargs["penalty"] = params["penalty"]
            if params["penalty"] == "l1":
                estimator_kwargs["solver"] = "saga"
        if "solver" in params:
            estimator_kwargs["solver"] = params["solver"]
        if "class_weight" in params:
            estimator_kwargs["class_weight"] = params["class_weight"]

        estimator = SklearnLogisticRegression(**estimator_kwargs)

        # 调用统一评估
        cv = int(params.get("cv", 5))
        random_state = int(params.get("random_state", 42))
        pipeline = Pipeline([("scaler", StandardScaler()), ("classifier", estimator)])
        metrics, predictions, fitted_pipeline = evaluate_classifier(
            pipeline, data.X, y, cv=cv, random_state=random_state
        )
        fitted_model = fitted_pipeline.named_steps["classifier"]

        # 提取特征重要性（系数绝对值）
        if fitted_model.coef_.shape[0] == 1:
            # 二分类
            importance = np.abs(fitted_model.coef_[0])
        else:
            # 多分类：取各类别系数的平均绝对值
            importance = np.mean(np.abs(fitted_model.coef_), axis=0)

        feature_scores = pd.DataFrame(
            {"importance": importance},
            index=data.X.columns,
        ).sort_values("importance", ascending=False)

        # 二分类时获取预测概率
        predictions_proba = None
        if y.nunique() == 2:
            predictions_proba = pd.Series(
                fitted_pipeline.predict_proba(data.X)[:, 1],
                index=data.X.index,
                name="probability",
            )

        return AlgorithmResult(
            algorithm=self.spec.name,
            metrics=metrics,
            predictions=predictions,
            feature_scores=feature_scores,
            model=fitted_pipeline,
            metadata={
                "n_samples": data.X.shape[0],
                "n_features": data.X.shape[1],
                "n_classes": y.nunique(),
                "cv_folds": cv,
                "fitted_model_probabilities": predictions_proba,
                "probability_scope": "in_sample_fitted_model",
            },
        )
