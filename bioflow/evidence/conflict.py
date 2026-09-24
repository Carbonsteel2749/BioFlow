from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass
class ConflictAssessment:
    severity: str
    template: str
    score: float


@dataclass
class ConflictPolicy:
    low_threshold: float = 0.6
    high_threshold: float = 0.85
    templates: Dict[str, str] = None

    def __post_init__(self) -> None:
        if self.templates is None:
            self.templates = {
                "low": "Prior studies suggest {prior}, while this cohort shows {current}. This may indicate context-specific effects.",
                "medium": "Prior literature reports {prior}, yet this analysis observed {current}. The discrepancy warrants cautious interpretation.",
                "high": "Findings largely align with prior literature: {prior}.",
            }


class ConflictDetector:
    def __init__(self, policy: ConflictPolicy | None = None) -> None:
        self.policy = policy or ConflictPolicy()

    def assess(self, agreement_score: float) -> ConflictAssessment:
        if agreement_score >= self.policy.high_threshold:
            severity = "high"
        elif agreement_score >= self.policy.low_threshold:
            severity = "medium"
        else:
            severity = "low"
        template = self.policy.templates.get(severity, "")
        return ConflictAssessment(severity=severity, template=template, score=agreement_score)
