"""Harden PaperState aggregates and apply Phase-4 consistency policy."""

from __future__ import annotations

from article_writing.consistency.checks import collect_consistency_issues
from article_writing.consistency.policy import ConsistencyMode
from article_writing.consistency.report import ConsistencyReport
from article_writing.contracts import Claim, Citation, FigureRef, PaperState, SectionId


class ConsistencyError(ValueError):
    """Raised when consistency_mode=strict and residual issues remain."""


def _unique_limitations(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join((value or "").split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            out.append(cleaned)
            seen.add(key)
    return out


def reconcile_aggregates(state: PaperState) -> tuple[PaperState, list[str]]:
    """Rebuild claims/citations/figures from drafts in section_order."""

    fixes: list[str] = []
    claims: list[Claim] = []
    citations: list[Citation] = []
    figures: list[FigureRef] = []
    seen_claims: set[str] = set()
    seen_cites: set[str] = set()
    seen_figs: set[str] = set()

    for section_id in state.section_order:
        draft = state.drafts.get(section_id.value)
        if draft is None:
            continue
        for claim in draft.claims:
            if claim.claim_id not in seen_claims:
                claims.append(claim)
                seen_claims.add(claim.claim_id)
        for cite in draft.citations:
            if cite.cite_id not in seen_cites:
                citations.append(cite)
                seen_cites.add(cite.cite_id)
        for fig in draft.figure_refs:
            if fig.figure_id not in seen_figs:
                figures.append(fig)
                seen_figs.add(fig.figure_id)

    if [c.claim_id for c in state.confirmed_claims] != [c.claim_id for c in claims]:
        fixes.append("reconciled confirmed_claims from drafts")
    if [c.cite_id for c in state.citations] != [c.cite_id for c in citations]:
        fixes.append("reconciled citations from drafts")
    if [f.figure_id for f in state.figure_refs] != [f.figure_id for f in figures]:
        fixes.append("reconciled figure_refs from drafts")

    limitations = _unique_limitations(list(state.limitations))
    if limitations != list(state.limitations):
        fixes.append("deduplicated limitations")

    updated = state.model_copy(
        update={
            "confirmed_claims": claims,
            "citations": citations,
            "figure_refs": figures,
            "limitations": limitations,
        }
    )
    return updated, fixes


def _attach_report_to_conclusion(
    state: PaperState,
    report: ConsistencyReport,
) -> PaperState:
    conclusion = state.drafts.get(SectionId.conclusion.value)
    if conclusion is None:
        return state

    metadata = dict(conclusion.metadata)
    metadata["consistency"] = report.as_metadata()
    warnings = list(conclusion.warnings)
    for message in report.warning_messages():
        if message not in warnings:
            warnings.append(message)

    if not report.ok and metadata.get("draft_status") == "complete":
        metadata["draft_status"] = "degraded"

    new_draft = conclusion.model_copy(update={"metadata": metadata, "warnings": warnings})
    drafts = dict(state.drafts)
    drafts[SectionId.conclusion.value] = new_draft
    return state.model_copy(update={"drafts": drafts})


def harden_paper_state(
    state: PaperState,
    *,
    mode: ConsistencyMode | str = ConsistencyMode.warn,
) -> tuple[PaperState, ConsistencyReport]:
    """Reconcile aggregates, re-check, and optionally fail in strict mode."""

    consistency_mode = mode if isinstance(mode, ConsistencyMode) else ConsistencyMode(mode)
    before = collect_consistency_issues(state)
    hardened, fixes = reconcile_aggregates(state)
    after = collect_consistency_issues(hardened)

    report = ConsistencyReport(
        mode=consistency_mode,
        issues_before=before,
        issues_after=after,
        fixes_applied=fixes,
    )
    hardened = _attach_report_to_conclusion(hardened, report)

    if not report.ok and consistency_mode is ConsistencyMode.strict:
        raise ConsistencyError(
            "PaperState consistency check failed: "
            + "; ".join(issue.message for issue in report.issues_after)
        )
    return hardened, report
