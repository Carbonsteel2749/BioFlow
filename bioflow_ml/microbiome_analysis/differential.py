"""Two-group univariate differential analysis with Benjamini-Hochberg FDR."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu, ttest_ind

from ._validation import validate_groups, validate_table


def benjamini_hochberg(p_values: pd.Series | np.ndarray) -> np.ndarray:
    values = np.asarray(p_values, dtype=float)
    if values.ndim != 1 or not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError("p_values must be a finite one-dimensional array in [0, 1]")
    count = len(values)
    order = np.argsort(values)
    ranked = values[order] * count / np.arange(1, count + 1)
    adjusted = np.minimum.accumulate(ranked[::-1])[::-1]
    result = np.empty(count)
    result[order] = np.minimum(adjusted, 1.0)
    return result


def two_group_differential(table: pd.DataFrame, groups: pd.Series | list[str], *, case: str, test: str = "mannwhitney", pseudocount: float = 1e-6) -> pd.DataFrame:
    table = validate_table(table)
    labels = validate_groups(groups, table.index)
    levels = list(labels.unique())
    if len(levels) != 2 or case not in levels:
        raise ValueError("two_group_differential requires exactly two groups and a valid case label")
    control = next(level for level in levels if level != case)
    case_values = table.loc[labels == case]
    control_values = table.loc[labels == control]
    if len(case_values) < 2 or len(control_values) < 2:
        raise ValueError("each group needs at least two samples")
    if test not in {"mannwhitney", "welch_t"}:
        raise ValueError("test must be mannwhitney or welch_t")

    rows: list[dict[str, float | str]] = []
    for feature in table.columns:
        left, right = case_values[feature].to_numpy(), control_values[feature].to_numpy()
        p_value = mannwhitneyu(left, right, alternative="two-sided").pvalue if test == "mannwhitney" else ttest_ind(left, right, equal_var=False).pvalue
        case_mean, control_mean = float(left.mean()), float(right.mean())
        rows.append({"feature": feature, "case_mean": case_mean, "control_mean": control_mean, "mean_difference": case_mean - control_mean, "log2_fold_change": float(np.log2((case_mean + pseudocount) / (control_mean + pseudocount))), "p_value": float(p_value)})
    result = pd.DataFrame(rows)
    result["q_value"] = benjamini_hochberg(result["p_value"])
    return result.sort_values(["q_value", "p_value", "feature"], ignore_index=True)
