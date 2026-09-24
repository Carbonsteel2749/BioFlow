"""K-means 聚类。"""

from __future__ import annotations

from typing import Any

import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

from bioflow_ml.core import (
    AlgorithmResult,
    AlgorithmSpec,
    BaseAlgorithm,
    MLInput,
    register_algorithm,
)


@register_algorithm
class KMeansAlgorithm(BaseAlgorithm):
    """基于距离的划分式聚类。"""

    spec = AlgorithmSpec(
        name="kmeans",
        version="0.1.0",
        tasks=("clustering",),
        description="K-means clustering",
    )

    def run(self, data: MLInput) -> AlgorithmResult:
        params = self.merged_params(data)
        n_samples = data.X.shape[0]
        n_clusters = int(params.get("n_clusters", 3))
        if n_clusters < 2 or n_clusters > n_samples:
            raise ValueError(
                f"n_clusters must be in [2, {n_samples}], got {n_clusters}"
            )

        kmeans_kwargs: dict[str, Any] = {
            "n_clusters": n_clusters,
            "n_init": int(params.get("n_init", 10)),
            "random_state": params.get("random_state", 42),
        }
        if "max_iter" in params:
            kmeans_kwargs["max_iter"] = int(params["max_iter"])
        if "tol" in params:
            kmeans_kwargs["tol"] = float(params["tol"])

        model = KMeans(**kmeans_kwargs)
        cluster_ids = model.fit_predict(data.X.to_numpy(dtype=float))
        labels = pd.Series(cluster_ids, index=data.X.index, name="cluster")

        metrics: dict[str, float] = {
            "n_clusters": float(n_clusters),
            "inertia": float(model.inertia_),
        }
        unique_labels = set(cluster_ids.tolist())
        if len(unique_labels) >= 2 and len(unique_labels) < n_samples:
            metrics["silhouette"] = float(
                silhouette_score(data.X.to_numpy(dtype=float), cluster_ids)
            )

        return AlgorithmResult(
            algorithm=self.spec.name,
            metrics=metrics,
            labels=labels,
            model=model,
            metadata={
                "n_clusters": n_clusters,
                "n_samples": n_samples,
                "random_state": kmeans_kwargs["random_state"],
            },
        )
