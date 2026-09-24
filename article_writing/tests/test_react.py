from __future__ import annotations

import json
from pathlib import Path

from article_writing.adapters import MockBriefPort, MockLiteraturePort
from article_writing.contracts import PaperBrief, SectionId
from article_writing.evidence import EvidenceMode
from article_writing.orchestrator import WritingPipeline
from article_writing.react import build_search_queries, run_related_work_react


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def test_build_search_queries_is_deterministic_and_deduped():
    brief = PaperBrief(
        title="Demo title",
        research_question="How does X relate to Y?",
        organism_or_system="human cells",
        data_modality="expression_matrix",
        keywords=["autism", "autism", "gut microbiota"],
    )
    queries = build_search_queries(brief, seed_query="autism gut")
    assert queries[0] == "autism gut"
    assert "How does X relate to Y?" in queries
    assert queries.count("autism") == 1


def test_react_loop_gathers_hits_and_finishes_with_trajectory():
    brief = MockBriefPort(FIXTURES).load()
    result = run_related_work_react(
        brief=brief,
        literature_port=MockLiteraturePort(FIXTURES),
        seed_query="autism gut microbiota",
        max_steps=8,
        search_top_k=3,
    )
    assert result.hits
    assert result.trajectory.finished is True
    assert result.trajectory.steps[-1].action == "finish"
    assert "search" in [s.action for s in result.trajectory.steps]


def test_pipeline_react_feeds_introduction_and_exports_trajectory(tmp_path: Path):
    out = tmp_path / "phase3"
    WritingPipeline(
        fixtures_dir=FIXTURES,
        react_enabled=True,
        evidence_mode=EvidenceMode.strict,
    ).run_and_export(run_id="phase3-react", output_dir=out)

    assert (out / "react_trajectory.json").exists()
    trajectory = json.loads((out / "react_trajectory.json").read_text(encoding="utf-8"))
    assert trajectory["finished"] is True

    bundle = json.loads((out / "writing_bundle.json").read_text(encoding="utf-8"))
    intro = next(s for s in bundle["sections"] if s["section"] == "introduction")
    assert intro["metadata"]["react"]["enabled"] is True
    assert intro["citations"]


def test_pipeline_can_disable_react(tmp_path: Path):
    out = tmp_path / "no-react"
    WritingPipeline(fixtures_dir=FIXTURES, react_enabled=False).run_and_export(
        run_id="off", output_dir=out
    )
    assert not (out / "react_trajectory.json").exists()
    bundle = json.loads((out / "writing_bundle.json").read_text(encoding="utf-8"))
    intro = next(s for s in bundle["sections"] if s["section"] == SectionId.introduction.value)
    assert intro["metadata"]["react"]["enabled"] is False
