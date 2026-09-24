"""Internal validation shared by abundance-table analysis functions."""

from __future__ import annotations

import numpy as np
import pandas as pd


def validate_table(table: pd.DataFrame, *, nonnegative: bool = True) -> pd.DataFrame:
    if not isinstance(table, pd.DataFrame) or table.empty:
        raise ValueError("table must be a non-empty pandas DataFrame (samples x features)")
    if not table.index.is_unique or not table.columns.is_unique:
        raise ValueError("sample IDs and feature IDs must be unique")
    if not all(pd.api.types.is_numeric_dtype(dtype) for dtype in table.dtypes):
        raise TypeError("all abundance-table columns must be numeric")
    values = table.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("table must not contain missing or infinite values")
    if nonnegative and (values < 0).any():
        raise ValueError("abundance table must not contain negative values")
    return table.astype(float)


def validate_groups(groups: pd.Series | list[str], index: pd.Index) -> pd.Series:
    series = groups if isinstance(groups, pd.Series) else pd.Series(groups, index=index, name="group")
    if not series.index.equals(index):
        raise ValueError("group labels must exactly match sample IDs and order")
    if series.isna().any() or series.nunique() < 2:
        raise ValueError("group labels must contain at least two non-missing groups")
    return series
