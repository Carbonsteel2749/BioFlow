"""SVM 分类算法。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from bioflow_ml.core import (
    AlgorithmResult,
    AlgorithmSpec,
    BaseAlgorithm,
    MLInput,
    register_algorithm,
)
from bioflow_ml.evaluation import evaluate_classifier


@register_algorithm
class SVMAlgorithm(BaseAlgorithm):
    """支持向量机分类器。"""

    spec = AlgorithmSpec(
        name="svm",
        version="0.1.0",
        tasks=("classification",),
        description="Support Vector Machine classifier",
    )

    def run(self, data: MLInput) -> AlgorithmResult:
        y = data.require_labels()
        params = self.merged_params(data)

        # 构建估计器参数
        estimator_kwargs: dict[str, Any] = {
            "probability": True,  # 启用概率输出
            "random_state": params.get("random_state", 42),
        }

        # 可选参数
        if "C" in params:
            estimator_kwargs["C"] = float(params["C"])
        if "kernel" in params:
            estimator_kwargs["kernel"] = params["kernel"]
        if "gamma" in params:
            estimator_kwargs["gamma"] = params["gamma"]
        if "degree" in params:
            estimator_kwargs["degree"] = int(params["degree"])
        if "class_weight" in params:
            estimator_kwargs["class_weight"] = params["class_weight"]

        estimator = SVC(**estimator_kwargs)

        # 调用统一评估
        cv = int(params.get("cv", 5))
        random_state = int(params.get("random_state", 42))
        pipeline = Pipeline([("scaler", StandardScaler()), ("classifier", estimator)])
        metrics, predictions, fitted_pipeline = evaluate_classifier(
            pipeline, data.X, y, cv=cv, random_state=random_state
        )
        fitted_model = fitted_pipeline.named_steps["classifier"]

        # 线性核时可提取特征重要性
        feature_scores = None
        if fitted_model.kernel == "linear" and hasattr(fitted_model, "coef_"):
            if fitted_model.coef_.shape[0] == 1:
                importance = np.abs(fitted_model.coef_[0])
            else:
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
                "kernel": fitted_model.kernel,
                "cv_folds": cv,
                "fitted_model_probabilities": predictions_proba,
                "probability_scope": "in_sample_fitted_model",
            },
        )
