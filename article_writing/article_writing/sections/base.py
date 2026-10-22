"""Base section interface for Phase-1 shells."""

from __future__ import annotations

from abc import ABC, abstractmethod

from article_writing.contracts import PaperState, SectionDraft, SectionId, SectionInput


class BaseSection(ABC):
    section_id: SectionId
    title: str

    @abstractmethod
    def run(self, section_input: SectionInput, state: PaperState | None = None) -> SectionDraft:
        raise NotImplementedError
