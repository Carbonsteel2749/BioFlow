"""Cross-section PaperState consistency checks."""

from __future__ import annotations

from article_writing.consistency.report import ConsistencyIssue
from article_writing.contracts import PaperState, SectionId


def _unique_preserve(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join((value or "").split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            out.append(cleaned)
            seen.add(key)
    return out


def collect_consistency_issues(state: PaperState) -> list[ConsistencyIssue]:
    """Inspect PaperState for cross-section completeness and aggregate drift."""

    issues: list[ConsistencyIssue] = []
    order = list(state.section_order)
    draft_keys = set(state.drafts)

    for section_id in order:
        if section_id.value not in state.drafts:
            issues.append(
                ConsistencyIssue(
                    kind="missing_section",
                    section=section_id.value,
                    message=f"section_order includes {section_id.value!r} but no draft exists",
                )
            )

    for key in sorted(draft_keys):
        if key not in {section.value for section in order}:
            issues.append(
                ConsistencyIssue(
                    kind="unexpected_draft",
                    section=key,
                    message=f"draft {key!r} is not listed in section_order",
                )
            )

    # Claim id conflicts across drafts (same id, different statement).
    claim_statements: dict[str, tuple[str, str]] = {}
    for section_id in order:
        draft = state.drafts.get(section_id.value)
        if draft is None:
            continue
        for claim in draft.claims:
            if claim.claim_id in claim_statements:
                prev_section, prev_statement = claim_statements[claim.claim_id]
                if prev_statement != claim.statement:
                    issues.append(
                        ConsistencyIssue(
                            kind="claim_id_conflict",
                            section=section_id.value,
                            entity_id=claim.claim_id,
                            message=(
                                f"claim_id {claim.claim_id!r} has conflicting statements in "
                                f"{prev_section!r} and {section_id.value!r}"
                            ),
                        )
                    )
            else:
                claim_statements[claim.claim_id] = (section_id.value, claim.statement)

    # Aggregates should match the union of drafts in section_order.
    expected_claim_ids: list[str] = []
    expected_cite_ids: list[str] = []
    expected_figure_ids: list[str] = []
    seen_c: set[str] = set()
    seen_cite: set[str] = set()
    seen_fig: set[str] = set()
    for section_id in order:
        draft = state.drafts.get(section_id.value)
        if draft is None:
            continue
        for claim in draft.claims:
            if claim.claim_id not in seen_c:
                expected_claim_ids.append(claim.claim_id)
                seen_c.add(claim.claim_id)
        for cite in draft.citations:
            if cite.cite_id not in seen_cite:
                expected_cite_ids.append(cite.cite_id)
                seen_cite.add(cite.cite_id)
        for fig in draft.figure_refs:
            if fig.figure_id not in seen_fig:
                expected_figure_ids.append(fig.figure_id)
                seen_fig.add(fig.figure_id)

    actual_claim_ids = [claim.claim_id for claim in state.confirmed_claims]
    actual_cite_ids = [cite.cite_id for cite in state.citations]
    actual_figure_ids = [fig.figure_id for fig in state.figure_refs]

    if actual_claim_ids != expected_claim_ids:
        issues.append(
            ConsistencyIssue(
                kind="claim_aggregate_drift",
                message=(
                    "confirmed_claims does not match draft claim order/union "
                    f"(expected {len(expected_claim_ids)}, actual {len(actual_claim_ids)})"
                ),
            )
        )
    if actual_cite_ids != expected_cite_ids:
        issues.append(
            ConsistencyIssue(
                kind="citation_aggregate_drift",
                message=(
                    "citations does not match draft citation order/union "
                    f"(expected {len(expected_cite_ids)}, actual {len(actual_cite_ids)})"
                ),
            )
        )
    if actual_figure_ids != expected_figure_ids:
        issues.append(
            ConsistencyIssue(
                kind="figure_aggregate_drift",
                message=(
                    "figure_refs does not match draft figure order/union "
                    f"(expected {len(expected_figure_ids)}, actual {len(actual_figure_ids)})"
                ),
            )
        )

    cleaned_raw = [
        " ".join(item.split()) for item in state.limitations if " ".join((item or "").split())
    ]
    deduped_limitations = _unique_preserve(list(state.limitations))
    if cleaned_raw != deduped_limitations:
        issues.append(
            ConsistencyIssue(
                kind="duplicate_limitations",
                message=(
                    "limitations contain duplicates or blank entries "
                    f"(unique={len(deduped_limitations)}, raw={len(state.limitations)})"
                ),
            )
        )

    # Conclusion evidence should point at upstream claim ids when present.
    conclusion = state.drafts.get(SectionId.conclusion.value)
    if conclusion is not None:
        upstream_ids = {
            claim.claim_id
            for sid, draft in state.drafts.items()
            if sid != SectionId.conclusion.value
            for claim in draft.claims
        }
        for claim in conclusion.claims:
            for evidence_id in claim.evidence_ids:
                if evidence_id and evidence_id not in upstream_ids:
                    issues.append(
                        ConsistencyIssue(
                            kind="orphan_conclusion_evidence",
                            section=SectionId.conclusion.value,
                            entity_id=evidence_id,
                            message=(
                                f"conclusion claim {claim.claim_id!r} references unknown "
                                f"upstream claim_id {evidence_id!r}"
                            ),
                        )
                    )

    # Discussion / Introduction citations should cover claim evidence ids that are paper ids.
    for section_key in (SectionId.discussion.value, SectionId.introduction.value):
        draft = state.drafts.get(section_key)
        if draft is None:
            continue
        cite_ids = {cite.cite_id for cite in draft.citations}
        for claim in draft.claims:
            for evidence_id in claim.evidence_ids:
                if not evidence_id:
                    continue
                # Skip analysis-style evidence ids; only enforce paper-like citations.
                if evidence_id.startswith("ev_"):
                    continue
                if evidence_id not in cite_ids and evidence_id not in {
                    c.claim_id for c in state.confirmed_claims
                }:
                    issues.append(
                        ConsistencyIssue(
                            kind="literature_evidence_without_citation",
                            section=section_key,
                            entity_id=evidence_id,
                            message=(
                                f"{section_key} claim {claim.claim_id!r} evidence_id "
                                f"{evidence_id!r} has no matching citation"
                            ),
                        )
                    )

    return issues
