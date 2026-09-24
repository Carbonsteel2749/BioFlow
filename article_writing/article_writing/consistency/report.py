"""Consistency report structures (stored in export metadata, not V1 fields)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from article_writing.consistency.policy import ConsistencyMode


@dataclass
class ConsistencyIssue:
    kind: str
    message: str
    section: str | None = None
    entity_id: str | None = None


@dataclass
class ConsistencyReport:
    mode: ConsistencyMode
    issues_before: list[ConsistencyIssue] = field(default_factory=list)
    issues_after: list[ConsistencyIssue] = field(default_factory=list)
    fixes_applied: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues_after

    def warning_messages(self) -> list[str]:
        return [f"consistency: {issue.message}" for issue in self.issues_after]

    def as_metadata(self) -> dict[str, Any]:
        def _dump(issues: list[ConsistencyIssue]) -> list[dict[str, Any]]:
            return [
                {
                    "kind": issue.kind,
                    "message": issue.message,
                    "section": issue.section,
                    "entity_id": issue.entity_id,
                }
                for issue in issues
            ]

        return {
            "ok": self.ok,
            "mode": self.mode.value,
            "n_issues_before": len(self.issues_before),
            "n_issues_after": len(self.issues_after),
            "fixes_applied": list(self.fixes_applied),
            "issues_before": _dump(self.issues_before),
            "issues_after": _dump(self.issues_after),
        }
