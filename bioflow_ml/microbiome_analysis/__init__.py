"""Microbiome abundance-table analysis (samples x features)."""

from .association import mantel_test, spearman_association
from .compositional import clr_transform
from .differential import two_group_differential
from .diversity import alpha_diversity, beta_distance, nmds, pcoa, permanova

__all__ = [
    "alpha_diversity",
    "beta_distance",
    "pcoa",
    "nmds",
    "permanova",
    "clr_transform",
    "two_group_differential",
    "spearman_association",
    "mantel_test",
]
