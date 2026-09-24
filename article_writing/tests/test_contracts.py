from __future__ import annotations

from pathlib import Path

from article_writing.adapters.fixtures_loader import (
    load_analysis,
    load_brief,
    load_literature,
)
from article_writing.contracts import DEFAULT_SECTION_ORDER, SectionId
from article_writing.registry import get_registry


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def test_fixtures_load():
    brief = load_brief(FIXTURES)
    analysis = load_analysis(FIXTURES)
    literature = load_literature(FIXTURES)
    assert brief.title
    assert analysis.methods
    assert literature


def test_registry_has_imrad_sections():
    registry = get_registry()
    assert set(registry) == {section.value for section in DEFAULT_SECTION_ORDER}
    assert list(DEFAULT_SECTION_ORDER) == [
        SectionId.abstract,
        SectionId.introduction,
        SectionId.methods,
        SectionId.results,
        SectionId.discussion,
        SectionId.conclusion,
        SectionId.back_matter,
    ]
