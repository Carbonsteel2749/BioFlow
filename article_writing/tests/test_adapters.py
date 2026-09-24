from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from article_writing.adapters import (
    LiveLiteraturePort,
    MockLiteraturePort,
    build_adapters,
    normalize_doi_paper_id,
    repository_row_to_hit,
    validate_fixtures_dir,
)
from article_writing.orchestrator import WritingPipeline


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
REPO_DB = Path(__file__).resolve().parents[2] / "Article_repository" / "article_literature.sqlite3"


def test_validate_fixtures_dir():
    summary = validate_fixtures_dir(FIXTURES)
    assert summary["n_literature"] >= 4
    assert summary["n_findings"] >= 1


def test_mock_literature_token_search_ranks_relevant_first():
    port = MockLiteraturePort(FIXTURES)
    hits = port.search("autism microbiota", top_k=3)
    assert hits
    assert any("autism" in (h.title + h.abstract + " ".join(h.tags)).lower() for h in hits)
    top_blob = f"{hits[0].title} {hits[0].abstract} {' '.join(hits[0].tags)}".lower()
    assert "autism" in top_blob or "microbiota" in top_blob or "microbiome" in top_blob


def test_mock_literature_get():
    port = MockLiteraturePort(FIXTURES)
    hit = port.get("lit_001")
    assert hit is not None
    assert hit.paper_id == "lit_001"
    assert port.get("missing") is None


def test_build_adapters_mock_pipeline():
    adapters = build_adapters("mock", fixtures_dir=FIXTURES)
    assert adapters.mode == "mock"
    brief = adapters.brief.load()
    analysis = adapters.analysis.load()
    assert brief.title
    assert analysis.key_findings


def test_repository_row_to_hit_mapping():
    hit = repository_row_to_hit(
        {
            "doi": "10.1000/test.doi",
            "title": "Test title",
            "abstract": "Abs",
            "authors": [{"name": "Alice"}, {"name": "Bob"}],
            "publish_time": datetime(2022, 5, 1),
            "keywords": ["autism"],
            "llm_tags": {"species": ["Bifidobacterium"]},
            "citation_count": 3,
        }
    )
    assert hit.paper_id == "10.1000/test.doi"
    assert hit.doi == "10.1000/test.doi"
    assert hit.authors == "Alice; Bob"
    assert hit.year == 2022
    assert "autism" in hit.tags
    assert any(t.startswith("species:") for t in hit.tags)
    assert hit.score == 3.0
    assert normalize_doi_paper_id("doi:10.1000/test.doi") == "10.1000/test.doi"


def test_build_adapters_live_uses_repository_literature_keeps_mock_brief_analysis():
    adapters = build_adapters("live", fixtures_dir=FIXTURES, literature_db=REPO_DB)
    assert adapters.mode == "live"
    assert isinstance(adapters.literature, LiveLiteraturePort)
    brief = adapters.brief.load()
    analysis = adapters.analysis.load()
    assert brief.title
    assert analysis.key_findings
    with pytest.raises(FileNotFoundError):
        build_adapters(
            "live",
            fixtures_dir=FIXTURES,
            brief_path="/tmp/missing-brief.json",
            literature_db=REPO_DB,
        ).brief.load()


@pytest.mark.skipif(not REPO_DB.is_file(), reason="Article_repository sqlite missing")
def test_live_literature_search_and_get_by_doi():
    port = LiveLiteraturePort(db_path=REPO_DB)
    hits = port.search("autism", top_k=3)
    assert hits, "expected hits from article_literature.sqlite3"
    assert all(h.paper_id and h.doi for h in hits)
    first = hits[0]
    got = port.get(first.doi)
    assert got is not None
    assert got.paper_id == first.doi
    assert port.get("doi:" + first.doi) is not None
    assert port.get("not-a-real-doi-xxx") is None


@pytest.mark.skipif(not REPO_DB.is_file(), reason="Article_repository sqlite missing")
def test_pipeline_live_literature_with_mock_upstream(tmp_path: Path):
    adapters = build_adapters("live", fixtures_dir=FIXTURES, literature_db=REPO_DB)
    pipeline = WritingPipeline(
        adapters=adapters,
        literature_query="gut microbiota",
        react_enabled=False,
        llm_enabled=False,
    )
    out = tmp_path / "out"
    path = pipeline.run_and_export(run_id="live-lit", output_dir=out)
    assert path.exists()
    assert (out / "sections" / "introduction.md").is_file()
    assert pipeline.adapters.mode == "live"
    hits = pipeline.literature_port.search("gut microbiota", top_k=2)
    assert hits
    assert pipeline.literature_port.get(hits[0].paper_id) is not None


def test_pipeline_accepts_injected_adapters(tmp_path: Path):
    adapters = build_adapters("mock", fixtures_dir=FIXTURES)
    pipeline = WritingPipeline(adapters=adapters, literature_query="keratinocyte", llm_enabled=False)
    out = tmp_path / "out"
    path = pipeline.run_and_export(run_id="b-test", output_dir=out)
    assert path.exists()
    assert pipeline.adapters.mode == "mock"
