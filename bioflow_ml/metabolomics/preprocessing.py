"""Transparent baseline preprocessing for metabolite-abundance matrices."""

from __future__ import annotations

import numpy as np
import pandas as pd


def preprocess_metabolite_table(table: pd.DataFrame, *, imputation: str = "half_minimum", normalization: str = "total_sum", log_transform: bool = True) -> pd.DataFrame:
    """Impute missing values, normalize sample intensity and optionally log1p-transform.

    Batch correction is deliberately not implicit: it needs an explicit batch vector
    and should be added as a separately audited operation.
    """
    if not isinstance(table, pd.DataFrame) or table.empty:
        raise ValueError("table must be a non-empty pandas DataFrame")
    if not all(pd.api.types.is_numeric_dtype(dtype) for dtype in table.dtypes):
        raise TypeError("all metabolite columns must be numeric")
    values = table.astype(float).copy()
    if (values < 0).any().any():
        raise ValueError("metabolite abundance must not contain negative values")
    if imputation != "half_minimum":
        raise ValueError("only half_minimum imputation is currently supported")
    for column in values.columns:
        observed = values[column].dropna()
        if observed.empty:
            raise ValueError(f"metabolite '{column}' is entirely missing")
        positive = observed[observed > 0]
        fill_value = float(positive.min() / 2) if not positive.empty else 0.0
        values[column] = values[column].fillna(fill_value)
    if normalization == "total_sum":
        totals = values.sum(axis=1)
        if (totals <= 0).any():
            raise ValueError("each sample needs positive total metabolite intensity")
        values = values.div(totals, axis=0)
    elif normalization != "none":
        raise ValueError("normalization must be total_sum or none")
    return np.log1p(values) if log_transform else values
