"""Compositional transformations for microbiome abundance tables."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ._validation import validate_table


def clr_transform(table: pd.DataFrame, *, pseudocount: float = 1e-6) -> pd.DataFrame:
    """Apply a zero-safe centered log-ratio transform row-wise.

    The input is expected to be samples x taxa/pathways with non-negative values.
    A pseudocount is deliberately recorded by the caller because it affects results.
    """
    table = validate_table(table)
    if pseudocount <= 0:
        raise ValueError("pseudocount must be positive")
    logged = np.log(table.to_numpy(dtype=float) + pseudocount)
    transformed = logged - logged.mean(axis=1, keepdims=True)
    return pd.DataFrame(transformed, index=table.index, columns=table.columns)
