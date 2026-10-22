"""Evidence requirements aligned to the IMRaD-style manuscript framework."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from article_writing.contracts import SectionId


class EvidenceMode(str, Enum):
    warn = "warn"
    strict = "strict"


@dataclass(frozen=True)
class SectionEvidencePolicy:
    evidence_required: bool
    allow_empty_claims: bool = True
    notes: str = ""


SECTION_EVIDENCE_POLICY: dict[SectionId, SectionEvidencePolicy] = {
    SectionId.abstract: SectionEvidencePolicy(
        evidence_required=False,
        notes="Abstract summarizes study scope; evidence optional",
    ),
    SectionId.introduction: SectionEvidencePolicy(
        evidence_required=False,
        notes="Background may cite literature when available; empty evidence allowed",
    ),
    SectionId.methods: SectionEvidencePolicy(
        evidence_required=True,
        notes="Method claims bind to AnalysisBundle.evidence_ids when present",
    ),
    SectionId.results: SectionEvidencePolicy(
        evidence_required=True,
        notes="Result claims must bind to analysis evidence_ids",
    ),
    SectionId.discussion: SectionEvidencePolicy(
        evidence_required=True,
        notes="Discussion interpretive claims should cite literature or analysis evidence",
    ),
    SectionId.conclusion: SectionEvidencePolicy(
        evidence_required=True,
        notes="Conclusion claims reference upstream claim_ids",
    ),
    SectionId.back_matter: SectionEvidencePolicy(
        evidence_required=False,
        notes="Administrative back matter",
    ),
}


def policy_for(section: SectionId) -> SectionEvidencePolicy:
    return SECTION_EVIDENCE_POLICY[section]
