import numpy as np
import pandas as pd

from bioflow_ml.microbiome_analysis import (
    alpha_diversity,
    beta_distance,
    clr_transform,
    mantel_test,
    nmds,
    pcoa,
    permanova,
    spearman_association,
    two_group_differential,
)
from bioflow_ml.metabolomics import preprocess_metabolite_table


def abundance_table() -> pd.DataFrame:
    return pd.DataFrame(
        {"taxon_a": [10, 12, 1, 2, 11, 13], "taxon_b": [2, 1, 12, 11, 3, 2], "taxon_c": [3, 2, 2, 3, 1, 2]},
        index=[f"S{i}" for i in range(1, 7)],
    )


def groups() -> pd.Series:
    return pd.Series(["control", "control", "case", "case", "control", "case"], index=[f"S{i}" for i in range(1, 7)], name="group")


def test_diversity_ordination_and_permanova():
    table = abundance_table()
    alpha = alpha_diversity(table, metrics=("observed_features", "shannon", "chao1"))
    distance = beta_distance(table, metric="braycurtis")
    pcoa_result = pcoa(distance)
    nmds_result = nmds(distance, random_state=0)
    result = permanova(distance, groups(), permutations=19, random_state=0)
    assert alpha.shape == (6, 3)
    assert np.allclose(distance, distance.T)
    assert pcoa_result.coordinates.shape == (6, 2)
    assert nmds_result.stress is not None
    assert 0 < result.p_value <= 1


def test_clr_differential_and_association():
    table = abundance_table()
    transformed = clr_transform(table)
    differential = two_group_differential(table, groups(), case="case")
    clinical = pd.DataFrame({"urea": [3.1, 3.0, 5.2, 5.0, 3.2, 5.4]}, index=table.index)
    association = spearman_association(table, clinical)
    assert np.allclose(transformed.sum(axis=1), 0)
    assert {"feature", "p_value", "q_value", "log2_fold_change"}.issubset(differential.columns)
    assert association.shape[0] == table.shape[1]


def test_mantel_and_metabolomics_preprocessing():
    table = abundance_table()
    left = beta_distance(table)
    right = beta_distance(table[["taxon_a", "taxon_c"]], metric="braycurtis")
    mantel = mantel_test(left, right, permutations=19, random_state=0)
    metabolites = pd.DataFrame({"scfa": [1.0, np.nan, 3.0], "lipid": [3.0, 2.0, 1.0]}, index=["S1", "S2", "S3"])
    processed = preprocess_metabolite_table(metabolites)
    assert 0 < mantel["p_value"] <= 1
    assert processed.notna().all().all()
