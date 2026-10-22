"""Load and validate fixture JSON into V1 contracts.

Fixtures are Phase-1 stand-ins only. Live adapters must return the same
Pydantic models; they must not invent fields outside CONTRACTS_V1.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from article_writing.contracts import AnalysisBundle, LiteratureHit, PaperBrief

DEFAULT_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

BRIEF_FILE = "paper_brief.json"
ANALYSIS_FILE = "analysis_bundle.json"
LITERATURE_FILE = "literature_hits.json"


class FixtureLoadError(ValueError):
    """Raised when fixture files are missing or fail V1 validation."""


def resolve_fixtures_dir(fixtures_dir: Path | str | None = None) -> Path:
    root = Path(fixtures_dir) if fixtures_dir else DEFAULT_FIXTURES
    return root.resolve()


def _read_json(path: Path) -> dict | list:
    if not path.is_file():
        raise FixtureLoadError(f"fixture file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FixtureLoadError(f"invalid JSON in {path}: {exc}") from exc


def load_brief(fixtures_dir: Path | str | None = None) -> PaperBrief:
    root = resolve_fixtures_dir(fixtures_dir)
    try:
        return PaperBrief.model_validate(_read_json(root / BRIEF_FILE))
    except Exception as exc:  # noqa: BLE001 - surface as fixture error
        if isinstance(exc, FixtureLoadError):
            raise
        raise FixtureLoadError(f"paper_brief failed V1 validation: {exc}") from exc


def load_analysis(fixtures_dir: Path | str | None = None) -> AnalysisBundle:
    root = resolve_fixtures_dir(fixtures_dir)
    try:
        return AnalysisBundle.model_validate(_read_json(root / ANALYSIS_FILE))
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, FixtureLoadError):
            raise
        raise FixtureLoadError(f"analysis_bundle failed V1 validation: {exc}") from exc


def load_literature(fixtures_dir: Path | str | None = None) -> List[LiteratureHit]:
    root = resolve_fixtures_dir(fixtures_dir)
    payload = _read_json(root / LITERATURE_FILE)
    if not isinstance(payload, list):
        raise FixtureLoadError(f"{LITERATURE_FILE} must be a JSON list")
    try:
        return [LiteratureHit.model_validate(item) for item in payload]
    except Exception as exc:  # noqa: BLE001
        raise FixtureLoadError(f"literature_hits failed V1 validation: {exc}") from exc


def validate_fixtures_dir(fixtures_dir: Path | str | None = None) -> dict[str, int | str]:
    """Eagerly load all fixtures; useful for CI / member-B smoke checks."""
    root = resolve_fixtures_dir(fixtures_dir)
    brief = load_brief(root)
    analysis = load_analysis(root)
    literature = load_literature(root)
    return {
        "fixtures_dir": str(root),
        "brief_title": brief.title,
        "n_findings": len(analysis.key_findings),
        "n_literature": len(literature),
        "n_figure_refs": len(analysis.figure_refs),
        "n_evidence_ids": len(analysis.evidence_ids),
    }
