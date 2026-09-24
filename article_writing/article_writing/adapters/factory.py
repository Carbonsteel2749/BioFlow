"""Build adapter ports. Swap mock -> live without touching sections."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from article_writing.adapters.base import AnalysisPort, BriefPort, LiteraturePort
from article_writing.adapters.live_analysis import LiveAnalysisPort
from article_writing.adapters.live_brief import LiveBriefPort
from article_writing.adapters.live_literature import LiveLiteraturePort
from article_writing.adapters.mock_analysis import MockAnalysisPort
from article_writing.adapters.mock_brief import MockBriefPort
from article_writing.adapters.mock_literature import MockLiteraturePort

AdapterMode = Literal["mock", "live"]


@dataclass(frozen=True)
class AdapterBundle:
    """Injectable ports for WritingPipeline."""

    mode: AdapterMode
    brief: BriefPort
    analysis: AnalysisPort
    literature: LiteraturePort


def build_adapters(
    mode: AdapterMode | str = "mock",
    *,
    fixtures_dir: Path | str | None = None,
    literature_url: str = "",
    literature_timeout: float = 30.0,
    literature_db: Path | str | None = None,
    analysis_bundle_path: str = "",
    analysis_run_dir: str = "",
    analysis_url: str = "",
    analysis_task_id: str = "",
    brief_path: str = "",
) -> AdapterBundle:
    """Create ports for the requested mode.

    - ``mock``: all fixtures.
    - ``live``: Article_repository for literature (keyword + get_by_doi);
      brief/analysis stay on mock unless a live source is provided.
    """
    normalized = (mode or "mock").strip().lower()
    if normalized == "mock":
        return AdapterBundle(
            mode="mock",
            brief=MockBriefPort(fixtures_dir),
            analysis=MockAnalysisPort(fixtures_dir),
            literature=MockLiteraturePort(fixtures_dir),
        )
    if normalized == "live":
        brief: BriefPort
        analysis: AnalysisPort
        if brief_path:
            brief = LiveBriefPort(brief_path=brief_path)
        else:
            brief = MockBriefPort(fixtures_dir)
        if analysis_bundle_path or analysis_run_dir or (analysis_url and analysis_task_id):
            analysis = LiveAnalysisPort(
                bundle_path=analysis_bundle_path,
                run_dir=analysis_run_dir,
                analysis_url=analysis_url,
                task_id=analysis_task_id,
            )
        else:
            analysis = MockAnalysisPort(fixtures_dir)
        return AdapterBundle(
            mode="live",
            brief=brief,
            analysis=analysis,
            literature=LiveLiteraturePort(
                base_url=literature_url,
                timeout=literature_timeout,
                db_path=literature_db,
            ),
        )
    raise ValueError(f"unknown adapter mode: {mode!r} (expected 'mock' or 'live')")
