"""Association and distance-matrix comparison utilities for matched samples."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr

from .differential import benjamini_hochberg
from .diversity import _validate_distance


def _validate_pair(left: pd.DataFrame, right: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not isinstance(left, pd.DataFrame) or not isinstance(right, pd.DataFrame) or left.empty or right.empty:
        raise ValueError("left and right must be non-empty DataFrames")
    if not left.index.equals(right.index):
        raise ValueError("left and right must have exactly matching sample IDs and order")
    if not all(pd.api.types.is_numeric_dtype(dtype) for dtype in left.dtypes) or not all(pd.api.types.is_numeric_dtype(dtype) for dtype in right.dtypes):
        raise TypeError("association tables must be numeric")
    return left.astype(float), right.astype(float)


def spearman_association(left: pd.DataFrame, right: pd.DataFrame, *, min_abs_correlation: float = 0.0) -> pd.DataFrame:
    left, right = _validate_pair(left, right)
    if not 0 <= min_abs_correlation <= 1:
        raise ValueError("min_abs_correlation must be between 0 and 1")
    rows: list[dict[str, float | str]] = []
    for left_feature in left.columns:
        for right_feature in right.columns:
            correlation, p_value = spearmanr(left[left_feature], right[right_feature])
            if not np.isfinite(correlation):
                continue
            if abs(correlation) >= min_abs_correlation:
                rows.append({"left_feature": left_feature, "right_feature": right_feature, "correlation": float(correlation), "p_value": float(p_value)})
    result = pd.DataFrame(rows)
    if result.empty:
        return pd.DataFrame(columns=["left_feature", "right_feature", "correlation", "p_value", "q_value"])
    result["q_value"] = benjamini_hochberg(result["p_value"])
    return result.sort_values(["q_value", "p_value"], ignore_index=True)


def mantel_test(left_distance: pd.DataFrame, right_distance: pd.DataFrame, *, permutations: int = 999, random_state: int = 42) -> dict[str, float | int]:
    left_distance, right_distance = _validate_distance(left_distance), _validate_distance(right_distance)
    if not left_distance.index.equals(right_distance.index):
        raise ValueError("distance matrices must have matching sample IDs and order")
    if permutations < 1:
        raise ValueError("permutations must be at least 1")
    upper = np.triu_indices(len(left_distance), k=1)
    left = left_distance.to_numpy()[upper]
    right_values = right_distance.to_numpy()
    observed = float(pearsonr(left, right_values[upper]).statistic)
    if not np.isfinite(observed):
        raise ValueError("Mantel test requires non-constant distance values")
    generator = np.random.default_rng(random_state)
    permuted = []
    for _ in range(permutations):
        order = generator.permutation(len(right_values))
        statistic = pearsonr(left, right_values[np.ix_(order, order)][upper]).statistic
        if np.isfinite(statistic):
            permuted.append(statistic)
    if not permuted:
        raise ValueError("no valid Mantel permutations were produced")
    p_value = float((np.count_nonzero(np.abs(permuted) >= abs(observed)) + 1) / (len(permuted) + 1))
    return {"statistic": observed, "p_value": p_value, "permutations": len(permuted)}
