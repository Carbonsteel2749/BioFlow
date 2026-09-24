from __future__ import annotations

from pathlib import Path

from article_writing.adapters.base import BriefPort
from article_writing.adapters.fixtures_loader import load_brief
from article_writing.contracts import PaperBrief


class MockBriefPort(BriefPort):
    def __init__(self, fixtures_dir: Path | str | None = None) -> None:
        self._brief = load_brief(fixtures_dir)

    def load(self) -> PaperBrief:
        return self._brief
