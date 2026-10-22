from __future__ import annotations

from pathlib import Path

from article_writing.adapters.base import AnalysisPort
from article_writing.adapters.fixtures_loader import load_analysis
from article_writing.contracts import AnalysisBundle


class MockAnalysisPort(AnalysisPort):
    def __init__(self, fixtures_dir: Path | str | None = None) -> None:
        self._bundle = load_analysis(fixtures_dir)

    def load(self) -> AnalysisBundle:
        return self._bundle
