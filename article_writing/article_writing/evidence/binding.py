"""Evidence binding: require/resolve evidence_ids without changing V1 fields."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from article_writing.contracts import (
    Claim,
    PaperState,
    SectionDraft,
    SectionId,
    SectionInput,
)
from article_writing.evidence.policy import EvidenceMode, policy_for


class EvidenceBindingError(ValueError):
    """Raised when evidence_mode=strict and binding issues are found."""


@dataclass
class BindingIssue:
    kind: str  # missing_evidence | unresolved_evidence
    claim_id: str
    message: str
    evidence_id: str | None = None


@dataclass
class BindingReport:
    section: SectionId
    mode: EvidenceMode
    evidence_required: bool
    issues: list[BindingIssue] = field(default_factory=list)
    available_evidence_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def n_missing(self) -> int:
        return sum(1 for issue in self.issues if issue.kind == "missing_evidence")

    @property
    def n_unresolved(self) -> int:
        return sum(1 for issue in self.issues if issue.kind == "unresolved_evidence")

    def warning_messages(self) -> list[str]:
        return [issue.message for issue in self.issues]

    def as_metadata(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "mode": self.mode.value,
            "evidence_required": self.evidence_required,
            "n_issues": len(self.issues),
            "n_missing_evidence": self.n_missing,
            "n_unresolved_evidence": self.n_unresolved,
            "available_evidence_count": len(self.available_evidence_ids),
            "issues": [
                {
                    "kind": issue.kind,
                    "claim_id": issue.claim_id,
                    "evidence_id": issue.evidence_id,
                    "message": issue.message,
                }
                for issue in self.issues
            ],
        }


def collect_available_evidence_ids(
    section_input: SectionInput,
    state: PaperState | None = None,
    draft: SectionDraft | None = None,
) -> set[str]:
    """Build the id pool a claim.evidence_ids entry may resolve against."""

    available: set[str] = set()

    for hit in section_input.literature:
        if hit.paper_id:
            available.add(hit.paper_id)

    if section_input.analysis:
        for evidence_id in section_input.analysis.evidence_ids:
            if evidence_id:
                available.add(evidence_id)
        for fig in section_input.analysis.figure_refs:
            if fig.figure_id:
                available.add(fig.figure_id)

    if draft is not None:
        for cite in draft.citations:
            if cite.cite_id:
                available.add(cite.cite_id)
        for fig in draft.figure_refs:
            if fig.figure_id:
                available.add(fig.figure_id)

    if state is not None:
        for cite in state.citations:
            if cite.cite_id:
                available.add(cite.cite_id)
        for fig in state.figure_refs:
            if fig.figure_id:
                available.add(fig.figure_id)
        for claim in state.confirmed_claims:
            if claim.claim_id:
                available.add(claim.claim_id)

    return available


def validate_draft_evidence(
    draft: SectionDraft,
    *,
    section_input: SectionInput,
    state: PaperState | None = None,
    mode: EvidenceMode | str = EvidenceMode.warn,
) -> BindingReport:
    """Check missing / unresolved evidence_ids for one section draft."""

    evidence_mode = mode if isinstance(mode, EvidenceMode) else EvidenceMode(mode)
    policy = policy_for(draft.section)
    available = collect_available_evidence_ids(section_input, state=state, draft=draft)
    issues: list[BindingIssue] = []

    for claim in draft.claims:
        evidence_ids = [eid for eid in claim.evidence_ids if eid]
        if not evidence_ids:
            if policy.evidence_required:
                issues.append(
                    BindingIssue(
                        kind="missing_evidence",
                        claim_id=claim.claim_id,
                        message=(
                            f"evidence binding: claim {claim.claim_id!r} in "
                            f"{draft.section.value} has empty evidence_ids"
                        ),
                    )
                )
            continue

        for evidence_id in evidence_ids:
            if evidence_id not in available:
                issues.append(
                    BindingIssue(
                        kind="unresolved_evidence",
                        claim_id=claim.claim_id,
                        evidence_id=evidence_id,
                        message=(
                            f"evidence binding: claim {claim.claim_id!r} references "
                            f"unknown evidence_id {evidence_id!r}"
                        ),
                    )
                )

    return BindingReport(
        section=draft.section,
        mode=evidence_mode,
        evidence_required=policy.evidence_required,
        issues=issues,
        available_evidence_ids=sorted(available),
    )


def claims_missing_evidence(draft: SectionDraft) -> list[Claim]:
    return [claim for claim in draft.claims if not claim.evidence_ids]


def summarize_binding(draft: SectionDraft) -> dict[str, int]:
    missing = claims_missing_evidence(draft)
    return {
        "n_claims": len(draft.claims),
        "n_with_evidence": len(draft.claims) - len(missing),
        "n_missing_evidence": len(missing),
    }


def apply_evidence_binding(
    draft: SectionDraft,
    *,
    section_input: SectionInput,
    state: PaperState | None = None,
    mode: EvidenceMode | str = EvidenceMode.warn,
) -> SectionDraft:
    """Attach binding warnings/metadata; raise in strict mode on issues."""

    report = validate_draft_evidence(
        draft,
        section_input=section_input,
        state=state,
        mode=mode,
    )
    evidence_mode = report.mode

    if not report.ok and evidence_mode is EvidenceMode.strict:
        raise EvidenceBindingError(
            f"{draft.section.value}: "
            + "; ".join(report.warning_messages())
        )

    warnings = list(draft.warnings)
    for message in report.warning_messages():
        if message not in warnings:
            warnings.append(message)

    metadata = dict(draft.metadata)
    binding_meta = report.as_metadata()
    claim_summary = summarize_binding(draft)
    binding_meta.update(claim_summary)
    metadata["evidence_binding"] = binding_meta

    if not report.ok and metadata.get("draft_status") == "complete":
        metadata["draft_status"] = "degraded"

    return draft.model_copy(update={"warnings": warnings, "metadata": metadata})


def aggregate_binding_issues(drafts: Iterable[SectionDraft]) -> list[str]:
    messages: list[str] = []
    for draft in drafts:
        binding = draft.metadata.get("evidence_binding") or {}
        for issue in binding.get("issues") or []:
            message = issue.get("message")
            if message:
                messages.append(message)
    return messages
