"""UMAP non-linear dimensionality reduction.

The optional umap-learn dependency is imported only when this algorithm runs,
so the rest of the library remains usable before UMAP is installed.
"""

from __future__ import annotations

import pandas as pd

from bioflow_ml.core import AlgorithmResult, AlgorithmSpec, BaseAlgorithm, MLInput, register_algorithm


@register_algorithm
class UMAPAlgorithm(BaseAlgorithm):
    spec = AlgorithmSpec(
        name="umap",
        version="0.1.0",
        tasks=("clustering",),
        description="Uniform Manifold Approximation and Projection for non-linear visualization",
    )

    def run(self, data: MLInput) -> AlgorithmResult:
        try:
            import umap
        except ImportError as exc:
            raise ImportError("UMAP requires umap-learn. Run: python -m pip install umap-learn") from exc

        params = self.merged_params(data)
        n_samples = len(data.X)
        if n_samples < 3:
            raise ValueError("UMAP requires at least 3 samples")
        n_components = int(params.get("n_components", 2))
        if n_components < 2:
            raise ValueError("n_components for UMAP must be at least 2")
        n_neighbors = int(params.get("n_neighbors", min(15, n_samples - 1)))
        if not 2 <= n_neighbors < n_samples:
            raise ValueError(f"n_neighbors must be in [2, {n_samples - 1}], got {n_neighbors}")
        min_dist = float(params.get("min_dist", 0.1))
        if not 0 <= min_dist <= 1:
            raise ValueError("min_dist must be between 0 and 1")

        model = umap.UMAP(
            n_components=n_components,
            n_neighbors=n_neighbors,
            min_dist=min_dist,
            metric=params.get("metric", "euclidean"),
            random_state=params.get("random_state", 42),
        )
        embedding = model.fit_transform(data.X.to_numpy(dtype=float))
        columns = [f"UMAP{index + 1}" for index in range(n_components)]
        return AlgorithmResult(
            algorithm=self.spec.name,
            transformed_data=pd.DataFrame(embedding, index=data.X.index, columns=columns),
            model=model,
            metrics={"n_components": float(n_components), "n_neighbors": float(n_neighbors), "min_dist": min_dist},
            metadata={"n_samples": n_samples, "random_state": params.get("random_state", 42)},
        )
