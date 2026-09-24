"""Call bioflow_skills without making the writing plate depend on it at import time."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


_BIOFLOW_ROOT = Path(__file__).resolve().parents[3]


def playbook_text(data: dict[str, Any] | None) -> str:
    """Turn a Nature skill handoff into prompt rules."""

    if not data:
        return ""
    lines: list[str] = []
    labels = (
        ("workflow", "Workflow"),
        ("constraints", "Constraints"),
        ("quality_checks", "Quality checks"),
        ("output_contract", "Expected output"),
    )
    for key, label in labels:
        items = data.get(key) or []
        if not items:
            continue
        lines.append(label + ":")
        for item in items:
            lines.append(f"- {item}")
    return "\n".join(lines).strip()


class SkillClient:
    """Thin wrapper around ``bioflow_skills.run_skill``.

    Missing the skills package, or a failed call, returns ``None`` and a warning.
    Nature skills currently return a handoff (status skipped); that payload is still usable.
    """

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = bool(enabled)
        self.available = False
        self._run_skill = None
        self.warnings: list[str] = []
        if not self.enabled:
            return
        try:
            root = str(_BIOFLOW_ROOT)
            if root not in sys.path:
                sys.path.insert(0, root)
            from bioflow_skills import run_skill

            self._run_skill = run_skill
            self.available = True
        except Exception as exc:  # noqa: BLE001 — skills are optional
            self.warnings.append(f"bioflow_skills unavailable: {exc}")

    def fetch(self, name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        if self._run_skill is None:
            return None
        try:
            result = self._run_skill(name, payload)
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"skill {name} failed: {exc}")
            return None
        data = getattr(result, "data", None)
        if not isinstance(data, dict):
            self.warnings.append(f"skill {name} returned no handoff")
            return None
        status = str(getattr(result, "status", "") or "")
        data = dict(data)
        data["skill"] = name
        data["status"] = status
        return data
