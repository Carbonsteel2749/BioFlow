"""Phase-4 PaperState consistency hardening."""

from __future__ import annotations

from article_writing.consistency.checks import collect_consistency_issues
from article_writing.consistency.harden import ConsistencyError, harden_paper_state, reconcile_aggregates
from article_writing.consistency.policy import ConsistencyMode
from article_writing.consistency.report import ConsistencyIssue, ConsistencyReport

__all__ = [
    "ConsistencyError",
    "ConsistencyIssue",
    "ConsistencyMode",
    "ConsistencyReport",
    "collect_consistency_issues",
    "harden_paper_state",
    "reconcile_aggregates",
]
