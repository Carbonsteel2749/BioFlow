from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

from bioflow.core.models import ModuleStatus


@dataclass
class ModuleRunRecord:
    module: str
    status: ModuleStatus = ModuleStatus.pending
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    error: Optional[str] = None

    def mark_started(self) -> None:
        self.status = ModuleStatus.running
        self.started_at = datetime.utcnow().isoformat()

    def mark_finished(self, status: ModuleStatus, error: Optional[str] = None) -> None:
        self.status = status
        self.ended_at = datetime.utcnow().isoformat()
        self.error = error


@dataclass
class RunReport:
    records: Dict[str, ModuleRunRecord] = field(default_factory=dict)

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for record in self.records.values():
            key = record.status.value
            counts[key] = counts.get(key, 0) + 1
        return counts
