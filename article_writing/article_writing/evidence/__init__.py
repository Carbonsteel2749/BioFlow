"""Evidence helpers and Phase-2 claim↔evidence binding."""

from __future__ import annotations

from article_writing.evidence.binding import (
    BindingIssue,
    BindingReport,
    EvidenceBindingError,
    aggregate_binding_issues,
    apply_evidence_binding,
    claims_missing_evidence,
    collect_available_evidence_ids,
    summarize_binding,
    validate_draft_evidence,
)
from article_writing.evidence.policy import (
    EvidenceMode,
    SECTION_EVIDENCE_POLICY,
    SectionEvidencePolicy,
    policy_for,
)

__all__ = [
    "BindingIssue",
    "BindingReport",
    "EvidenceBindingError",
    "EvidenceMode",
    "SECTION_EVIDENCE_POLICY",
    "SectionEvidencePolicy",
    "aggregate_binding_issues",
    "apply_evidence_binding",
    "claims_missing_evidence",
    "collect_available_evidence_ids",
    "policy_for",
    "summarize_binding",
    "validate_draft_evidence",
]
