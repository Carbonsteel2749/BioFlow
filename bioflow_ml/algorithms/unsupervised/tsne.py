"""t-SNE non-linear dimensionality reduction."""

from __future__ import annotations

import inspect
from typing import Any

import pandas as pd
from sklearn.manifold import TSNE

from bioflow_ml.core import AlgorithmResult, AlgorithmSpec, BaseAlgorithm, MLInput, register_algorithm


@register_algorithm
class TSNEAlgorithm(BaseAlgorithm):
    spec = AlgorithmSpec(
        name="tsne",
        version="0.1.0",
        tasks=("clustering",),
        description="t-distributed stochastic neighbor embedding for non-linear visualization",
    )

    def run(self, data: MLInput) -> AlgorithmResult:
        params = self.merged_params(data)
        n_samples = len(data.X)
        if n_samples < 3:
            raise ValueError("t-SNE requires at least 3 samples")

        n_components = int(params.get("n_components", 2))
        if n_components not in (2, 3):
            raise ValueError("n_components for t-SNE must be 2 or 3")
        default_perplexity = min(30.0, max(1.0, (n_samples - 1) / 3))
        perplexity = float(params.get("perplexity", default_perplexity))
        if not 0 < perplexity < n_samples:
            raise ValueError(f"perplexity must be in (0, {n_samples}), got {perplexity}")

        iterations = int(params.get("n_iter", params.get("max_iter", 1000)))
        model_kwargs: dict[str, Any] = {
            "n_components": n_components,
            "perplexity": perplexity,
            "random_state": params.get("random_state", 42),
            "init": params.get("init", "pca"),
            "learning_rate": params.get("learning_rate", "auto"),
        }
        # scikit-learn 1.5 renamed n_iter to max_iter. Support both APIs.
        iteration_parameter = "max_iter" if "max_iter" in inspect.signature(TSNE).parameters else "n_iter"
        model_kwargs[iteration_parameter] = iterations
        if "metric" in params:
            model_kwargs["metric"] = params["metric"]
        model = TSNE(**model_kwargs)
        embedding = model.fit_transform(data.X.to_numpy(dtype=float))
        columns = [f"tSNE{index + 1}" for index in range(n_components)]
        transformed = pd.DataFrame(embedding, index=data.X.index, columns=columns)
        return AlgorithmResult(
            algorithm=self.spec.name,
            transformed_data=transformed,
            model=model,
            metrics={"n_components": float(n_components), "perplexity": perplexity, "kl_divergence": float(model.kl_divergence_)},
            metadata={"n_samples": n_samples, "random_state": model_kwargs["random_state"]},
        )
