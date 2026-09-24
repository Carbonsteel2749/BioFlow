"""RFE 特征选择算法。"""

from __future__ import annotations

from typing import Any

import pandas as pd
from sklearn.feature_selection import RFE
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
class RFEAlgorithm(BaseAlgorithm):
    """递归特征消除（RFE）特征选择。

    特征选择在交叉验证的训练折内完成，避免数据泄漏。
    使用 LogisticRegression 作为基估计器。
    """

    spec = AlgorithmSpec(
        name="rfe",
        version="0.1.0",
        tasks=("feature_selection", "classification"),
        description="Recursive Feature Elimination with Logistic Regression",
    )

    def run(self, data: MLInput) -> AlgorithmResult:
        y = data.require_labels()
        params = self.merged_params(data)

        # 构建基估计器参数
        estimator_C = float(params.get("estimator_C", 1.0))
        estimator_max_iter = int(params.get("estimator_max_iter", 1000))

        base_estimator = LogisticRegression(
            C=estimator_C,
            max_iter=estimator_max_iter,
            solver="lbfgs",
            random_state=params.get("random_state", 42),
        )

        # RFE 参数
        n_features_to_select = params.get("n_features_to_select", None)
        # step 必须是 > 1 的整数或 (0, 1) 的浮点数
        step = int(params.get("step", 2))

        # 如果 n_features_to_select 是整数但为负数或零，设置为 None
        if isinstance(n_features_to_select, int) and n_features_to_select <= 0:
            n_features_to_select = None

        rfe_selector = RFE(
            estimator=base_estimator,
            n_features_to_select=n_features_to_select,
            step=step,
        )

        # 分类器（复用相同的参数）
        classifier = LogisticRegression(
            C=estimator_C,
            max_iter=estimator_max_iter,
            solver="lbfgs",
            random_state=params.get("random_state", 42),
        )

        # 使用 Pipeline 确保 RFE 在 CV 内部执行
        pipeline = Pipeline([
            ("scaler", StandardScaler()),
            ("selector", rfe_selector),
            ("classifier", classifier),
        ])

        # 调用统一评估
        cv = int(params.get("cv", 5))
        random_state = int(params.get("random_state", 42))
        metrics, predictions, fitted_pipeline = evaluate_classifier(
            pipeline, data.X, y, cv=cv, random_state=random_state
        )

        # 从 fitted model 提取选中的特征
        fitted_selector = fitted_pipeline.named_steps["selector"]
        selected_mask = fitted_selector.support_

        # 特征排名（越小越重要）
        ranking = fitted_selector.ranking_

        feature_scores = pd.DataFrame(
            {
                "ranking": ranking,
                "selected": selected_mask,
            },
            index=data.X.columns,
        ).sort_values("ranking", ascending=True)

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
                "step": float(int(step)),
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
