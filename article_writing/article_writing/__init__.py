"""BioFLow article writing plate: section-wise draft generation."""

from article_writing.contracts import PaperState, SectionDraft, SectionInput
from article_writing.registry import get_registry, list_sections

__all__ = [
    "PaperState",
    "SectionDraft",
    "SectionInput",
    "get_registry",
    "list_sections",
]

__version__ = "0.1.0"
