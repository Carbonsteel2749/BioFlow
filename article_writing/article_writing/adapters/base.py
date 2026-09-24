"""Stable ports for upstream plates. Sections must depend on these ABCs only."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from article_writing.contracts import AnalysisBundle, LiteratureHit, PaperBrief


class LiteraturePort(ABC):
    """Literature plate boundary. Live impl must map external JSON -> LiteratureHit."""

    @abstractmethod
    def search(self, query: str, top_k: int = 5) -> List[LiteratureHit]:
        raise NotImplementedError

    @abstractmethod
    def get(self, paper_id: str) -> Optional[LiteratureHit]:
        raise NotImplementedError


class AnalysisPort(ABC):
    """Analysis plate boundary. Live impl must map external artifacts -> AnalysisBundle."""

    @abstractmethod
    def load(self) -> AnalysisBundle:
        raise NotImplementedError


class BriefPort(ABC):
    """Study brief boundary (may later come from UI / config / analysis metadata)."""

    @abstractmethod
    def load(self) -> PaperBrief:
        raise NotImplementedError
