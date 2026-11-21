"""LASSO 特征选择算法。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
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
class LASSOAlgorithm(BaseAlgorithm):
    """LASSO 特征选择（L1 正则化逻辑回归）。

    特征选择在交叉验证的训练折内完成，避免数据泄漏。
    """

    spec = AlgorithmSpec(
        name="lasso",
        version="0.1.0",
        tasks=("feature_selection", "classification"),
        description="LASSO feature selection with L1-regularized Logistic Regression",
    )

    def run(self, data: MLInput) -> AlgorithmResult:
        y = data.require_labels()
        params = self.merged_params(data)

        # 构建 LASSO 估计器（L1 正则化）
        C = float(params.get("C", 1.0))
        max_iter = int(params.get("max_iter", 5000))

        estimator_kwargs: dict[str, Any] = {
            "penalty": "l1",
            "C": C,
            "solver": "saga",
            "max_iter": max_iter,
            "random_state": params.get("random_state", 42),
        }

        if "class_weight" in params:
            estimator_kwargs["class_weight"] = params["class_weight"]

        lasso_estimator = LogisticRegression(**estimator_kwargs)

        # 使用 Pipeline 确保特征选择在 CV 内部执行
        pipeline = Pipeline([("scaler", StandardScaler()), ("classifier", lasso_estimator)])

        # 调用统一评估
        cv = int(params.get("cv", 5))
        random_state = int(params.get("random_state", 42))
        metrics, predictions, fitted_pipeline = evaluate_classifier(
            pipeline, data.X, y, cv=cv, random_state=random_state
        )

        # 从 fitted model 提取选中的特征（非零系数）
        fitted_model = fitted_pipeline.named_steps["classifier"]
        coef = fitted_model.coef_

        if coef.shape[0] == 1:
            # 二分类
            coef_flat = coef[0]
        else:
            # 多分类：取各类别系数的平均绝对值
            coef_flat = np.mean(np.abs(coef), axis=0)

        # 特征重要性：系数绝对值
        importance = np.abs(coef_flat)
        selected_mask = coef_flat != 0

        feature_scores = pd.DataFrame(
            {
                "importance": importance,
                "selected": selected_mask,
            },
            index=data.X.columns,
        ).sort_values("importance", ascending=False)

        # 提取选中特征列表
        selected_features = data.X.columns[selected_mask].tolist()
        n_selected = len(selected_features)

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
            metrics={
                **metrics,
                "n_selected_features": float(n_selected),
                "C": float(C),
            },
            predictions=predictions,
            feature_scores=feature_scores,
            model=fitted_pipeline,
            metadata={
                "n_samples": data.X.shape[0],
                "n_features": data.X.shape[1],
                "n_classes": y.nunique(),
                "selected_features": selected_features,
                "n_selected": n_selected,
                "cv_folds": cv,
                "fitted_model_probabilities": predictions_proba,
                "probability_scope": "in_sample_fitted_model",
            },
        )
