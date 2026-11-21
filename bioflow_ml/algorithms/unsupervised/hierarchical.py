"""层次聚类（凝聚式）。"""

from __future__ import annotations

from typing import Any

import pandas as pd
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics import silhouette_score

from bioflow_ml.core import (
    AlgorithmResult,
    AlgorithmSpec,
    BaseAlgorithm,
    MLInput,
    register_algorithm,
)


@register_algorithm
class HierarchicalAlgorithm(BaseAlgorithm):
    """凝聚式层次聚类。"""

    spec = AlgorithmSpec(
        name="hierarchical",
        version="0.1.0",
        tasks=("clustering",),
        description="Agglomerative hierarchical clustering",
    )

    def run(self, data: MLInput) -> AlgorithmResult:
        params = self.merged_params(data)
        n_samples = data.X.shape[0]
        n_clusters = int(params.get("n_clusters", 3))
        if n_clusters < 2 or n_clusters > n_samples:
            raise ValueError(
                f"n_clusters must be in [2, {n_samples}], got {n_clusters}"
            )

        linkage = str(params.get("linkage", "ward"))
        metric = str(params.get("metric", params.get("affinity", "euclidean")))
        if linkage == "ward" and metric != "euclidean":
            raise ValueError("ward linkage only supports euclidean metric")

        cluster_kwargs: dict[str, Any] = {
            "n_clusters": n_clusters,
            "linkage": linkage,
            "metric": metric,
        }

        model = AgglomerativeClustering(**cluster_kwargs)
        cluster_ids = model.fit_predict(data.X.to_numpy(dtype=float))
        labels = pd.Series(cluster_ids, index=data.X.index, name="cluster")

        metrics: dict[str, float] = {"n_clusters": float(n_clusters)}
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
                "linkage": linkage,
                "metric": metric,
                "n_samples": n_samples,
            },
        )
