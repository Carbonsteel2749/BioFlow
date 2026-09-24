"""Alpha/beta diversity, ordination and PERMANOVA for abundance tables."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.spatial.distance import pdist, squareform
from sklearn.manifold import MDS

from ._validation import validate_groups, validate_table
from .compositional import clr_transform


@dataclass
class OrdinationResult:
    coordinates: pd.DataFrame
    eigenvalues: pd.Series | None = None
    stress: float | None = None


@dataclass
class PermanovaResult:
    statistic: float
    p_value: float
    permutations: int
    groups: int


def alpha_diversity(table: pd.DataFrame, *, metrics: tuple[str, ...] = ("observed_features", "shannon", "simpson")) -> pd.DataFrame:
    table = validate_table(table)
    values = table.to_numpy(dtype=float)
    totals = values.sum(axis=1)
    if (totals <= 0).any():
        raise ValueError("each sample must have positive total abundance")
    proportions = values / totals[:, None]
    nonzero = proportions > 0
    available = {"observed_features", "shannon", "simpson", "chao1"}
    unknown = set(metrics) - available
    if unknown:
        raise ValueError(f"unsupported alpha-diversity metrics: {sorted(unknown)}")

    result: dict[str, np.ndarray] = {}
    if "observed_features" in metrics:
        result["observed_features"] = nonzero.sum(axis=1)
    if "shannon" in metrics:
        result["shannon"] = -(np.where(nonzero, proportions * np.log(np.where(nonzero, proportions, 1)), 0)).sum(axis=1)
    if "simpson" in metrics:
        result["simpson"] = 1 - np.square(proportions).sum(axis=1)
    if "chao1" in metrics:
        singleton = (values == 1).sum(axis=1)
        doubleton = (values == 2).sum(axis=1)
        observed = nonzero.sum(axis=1)
        chao_extra = singleton * (singleton - 1) / 2
        valid_doubletons = doubleton > 0
        chao_extra[valid_doubletons] = singleton[valid_doubletons] ** 2 / (2 * doubleton[valid_doubletons])
        result["chao1"] = observed + chao_extra
    return pd.DataFrame(result, index=table.index)


def beta_distance(table: pd.DataFrame, *, metric: str = "braycurtis", pseudocount: float = 1e-6) -> pd.DataFrame:
    table = validate_table(table)
    if metric == "braycurtis":
        distances = pdist(table.to_numpy(), metric="braycurtis")
    elif metric == "jaccard":
        distances = pdist(table.to_numpy() > 0, metric="jaccard")
    elif metric == "aitchison":
        distances = pdist(clr_transform(table, pseudocount=pseudocount).to_numpy(), metric="euclidean")
    else:
        raise ValueError("metric must be braycurtis, jaccard or aitchison")
    return pd.DataFrame(squareform(distances), index=table.index, columns=table.index)


def _validate_distance(distance: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(distance, pd.DataFrame) or distance.empty or not distance.index.equals(distance.columns):
        raise ValueError("distance must be a non-empty square DataFrame with matching IDs")
    values = distance.to_numpy(dtype=float)
    if not np.isfinite(values).all() or not np.allclose(values, values.T) or not np.allclose(np.diag(values), 0):
        raise ValueError("distance matrix must be finite, symmetric and have a zero diagonal")
    return distance.astype(float)


def pcoa(distance: pd.DataFrame, *, n_components: int = 2) -> OrdinationResult:
    distance = _validate_distance(distance)
    n_samples = len(distance)
    if not 1 <= n_components < n_samples:
        raise ValueError(f"n_components must be in [1, {n_samples - 1}]")
    d2 = np.square(distance.to_numpy())
    centering = np.eye(n_samples) - np.ones((n_samples, n_samples)) / n_samples
    centered = -0.5 * centering @ d2 @ centering
    eigenvalues, eigenvectors = np.linalg.eigh(centered)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues, eigenvectors = eigenvalues[order], eigenvectors[:, order]
    positive = np.clip(eigenvalues[:n_components], a_min=0, a_max=None)
    coordinates = eigenvectors[:, :n_components] * np.sqrt(positive)
    columns = [f"PCoA{number + 1}" for number in range(n_components)]
    return OrdinationResult(
        coordinates=pd.DataFrame(coordinates, index=distance.index, columns=columns),
        eigenvalues=pd.Series(eigenvalues, name="eigenvalue"),
    )


def nmds(distance: pd.DataFrame, *, n_components: int = 2, random_state: int = 42, n_init: int = 4, max_iter: int = 300) -> OrdinationResult:
    distance = _validate_distance(distance)
    if not 1 <= n_components < len(distance):
        raise ValueError("n_components must be smaller than the number of samples")
    model = MDS(n_components=n_components, metric=False, dissimilarity="precomputed", random_state=random_state, n_init=n_init, max_iter=max_iter)
    coordinates = model.fit_transform(distance.to_numpy())
    return OrdinationResult(
        coordinates=pd.DataFrame(coordinates, index=distance.index, columns=[f"NMDS{number + 1}" for number in range(n_components)]),
        stress=float(model.stress_),
    )


def _pseudo_f(distance_values: np.ndarray, groups: np.ndarray) -> float:
    n_samples = len(groups)
    unique = np.unique(groups)
    if len(unique) < 2 or len(unique) >= n_samples:
        raise ValueError("PERMANOVA requires 2 to n-1 groups")
    total_ss = np.square(distance_values).sum() / (2 * n_samples)
    within_ss = 0.0
    for group in unique:
        positions = np.flatnonzero(groups == group)
        within_ss += np.square(distance_values[np.ix_(positions, positions)]).sum() / (2 * len(positions))
    between_ss = total_ss - within_ss
    df_between, df_within = len(unique) - 1, n_samples - len(unique)
    if within_ss == 0:
        return float("inf")
    return (between_ss / df_between) / (within_ss / df_within)


def permanova(distance: pd.DataFrame, groups: pd.Series | list[str], *, permutations: int = 999, random_state: int = 42) -> PermanovaResult:
    distance = _validate_distance(distance)
    if permutations < 1:
        raise ValueError("permutations must be at least 1")
    labels = validate_groups(groups, distance.index).to_numpy()
    values = distance.to_numpy(dtype=float)
    statistic = _pseudo_f(values, labels)
    generator = np.random.default_rng(random_state)
    permuted = np.array([_pseudo_f(values, generator.permutation(labels)) for _ in range(permutations)])
    p_value = float((np.count_nonzero(permuted >= statistic) + 1) / (permutations + 1))
    return PermanovaResult(statistic=float(statistic), p_value=p_value, permutations=permutations, groups=len(np.unique(labels)))
