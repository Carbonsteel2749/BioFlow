"""主成分分析（PCA）。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA

from bioflow_ml.core import (
    AlgorithmResult,
    AlgorithmSpec,
    BaseAlgorithm,
    MLInput,
    register_algorithm,
)


@register_algorithm
class PCAAlgorithm(BaseAlgorithm):
    """将高维特征投影到主成分空间。"""

    spec = AlgorithmSpec(
        name="pca",
        version="0.1.0",
        tasks=("clustering",),
        description="Principal component analysis for dimensionality reduction",
    )

    def run(self, data: MLInput) -> AlgorithmResult:
        params = self.merged_params(data)
        n_samples, n_features = data.X.shape
        max_components = min(n_samples, n_features)
        n_components = int(params.get("n_components", min(2, max_components)))
        if n_components < 1 or n_components > max_components:
            raise ValueError(
                f"n_components must be in [1, {max_components}], got {n_components}"
            )

        pca_kwargs: dict[str, Any] = {"n_components": n_components}
        if "random_state" in params:
            pca_kwargs["random_state"] = params["random_state"]
            pca_kwargs["svd_solver"] = params.get("svd_solver", "randomized")
        elif "svd_solver" in params:
            pca_kwargs["svd_solver"] = params["svd_solver"]

        model = PCA(**pca_kwargs)
        scores = model.fit_transform(data.X.to_numpy(dtype=float))
        pc_names = [f"PC{index + 1}" for index in range(n_components)]
        transformed = pd.DataFrame(scores, index=data.X.index, columns=pc_names)

        explained = model.explained_variance_ratio_
        metrics = {
            "n_components": float(n_components),
            "explained_variance_ratio_sum": float(np.sum(explained)),
        }
        for index, ratio in enumerate(explained):
            metrics[f"explained_variance_ratio_pc{index + 1}"] = float(ratio)

        loadings = pd.DataFrame(
            model.components_.T,
            index=data.X.columns,
            columns=pc_names,
        )
        loadings.insert(0, "score", np.abs(loadings.to_numpy()).sum(axis=1))
        loadings = loadings.sort_values("score", ascending=False)

        return AlgorithmResult(
            algorithm=self.spec.name,
            metrics=metrics,
            transformed_data=transformed,
            feature_scores=loadings,
            model=model,
            metadata={
                "n_components": n_components,
                "n_samples": n_samples,
                "n_features": n_features,
            },
        )
