from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from article_writing.adapters import (
    AdapterBundle,
    BioLLMPackageError,
    LiveAdapterNotReady,
    LiveAnalysisPort,
    LiveBriefPort,
    MockLiteraturePort,
    build_adapters,
    map_biollm_package,
)
from article_writing.contracts import AnalysisBundle
from article_writing.orchestrator import WritingPipeline


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
BIOLLM = FIXTURES / "biollm_metagenome"
PACKAGE = BIOLLM / "package"
BRIEF = BIOLLM / "paper_brief.json"
REPO_DB = ROOT.parent / "Article_repository" / "article_literature.sqlite3"


def test_map_biollm_package_fills_factual_bundle_slots():
    bundle = map_biollm_package(PACKAGE)
    assert isinstance(bundle, AnalysisBundle)
    assert bundle.metrics["sample_count"] == 3
    assert bundle.metrics["top_taxon"] == "Bacteroides vulgatus"
    assert bundle.metrics["top_taxon_mean_abundance_pct"] == 22.75
    assert bundle.metrics["median_raw_reads"] == 10000
    assert bundle.metrics["median_clean_reads"] == 8200
    assert bundle.metrics["mag_annotation_rows"] == 2
    assert "fastp" in bundle.methods["qc"]
    assert "Kraken2" in bundle.methods["taxonomy"]
    assert "HUMAnN" in bundle.methods["functional_annotation"]
    assert bundle.methods["mag"].startswith("enabled")
    assert bundle.microbiome_sequencing is not None
    assert "150 bp" in bundle.microbiome_sequencing.sequencing_strategy
    assert bundle.key_findings
    assert bundle.evidence_ids == ["ev_qc_reads", "ev_taxonomy_top", "ev_pathways", "ev_mag"]
    assert len(bundle.key_findings) == len(bundle.evidence_ids)
    assert {ref.figure_id for ref in bundle.figure_refs} >= {
        "tbl_read_counts",
        "tbl_species",
        "tbl_pathways",
        "fig_multiqc",
    }
    assert all(Path(ref.path).is_file() for ref in bundle.figure_refs)
    assert bundle.raw["task_id"] == "task-fixture-001"
    assert bundle.statistical_analysis is None
    assert any("no group comparison" in item for item in bundle.limitations)


def test_live_analysis_port_reads_run_dir_and_tar(tmp_path: Path):
    from_dir = LiveAnalysisPort(run_dir=str(PACKAGE)).load()
    archive = tmp_path / "task-fixture-001.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(PACKAGE, arcname="package")
    from_tar = LiveAnalysisPort(bundle_path=str(archive)).load()
    assert from_dir.metrics["sample_count"] == from_tar.metrics["sample_count"] == 3
    assert from_tar.key_findings == from_dir.key_findings


def test_live_analysis_port_accepts_analysis_bundle_json(tmp_path: Path):
    mapped = map_biollm_package(PACKAGE)
    json_path = tmp_path / "analysis_bundle.json"
    json_path.write_text(mapped.model_dump_json(indent=2), encoding="utf-8")
    loaded = LiveAnalysisPort(bundle_path=str(json_path)).load()
    assert loaded.summary == mapped.summary
    assert loaded.metrics["sample_count"] == 3


def test_live_analysis_port_downloads_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    archive = tmp_path / "remote.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(PACKAGE, arcname="package")
    payload = archive.read_bytes()

    class FakeResponse:
        def __enter__(self):
            return io.BytesIO(payload)

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=60.0):
        assert "task-fixture-001" in request.full_url
        assert timeout == 15.0
        return FakeResponse()

    monkeypatch.setattr(
        "article_writing.adapters.live_analysis.urllib.request.urlopen",
        fake_urlopen,
    )
    port = LiveAnalysisPort(
        analysis_url="http://biollm.example",
        task_id="task-fixture-001",
        timeout=15.0,
    )
    bundle = port.load()
    assert bundle.metrics["sample_count"] == 3
    assert port.package_root is not None


def test_live_analysis_port_requires_a_source():
    with pytest.raises(LiveAdapterNotReady):
        LiveAnalysisPort().load()


def test_live_brief_port_loads_and_rejects_missing(tmp_path: Path):
    brief = LiveBriefPort(brief_path=str(BRIEF)).load()
    assert "metagenomic" in brief.title.lower()
    assert brief.data_modality == "shotgun_metagenomics"
    with pytest.raises(FileNotFoundError):
        LiveBriefPort(brief_path=str(tmp_path / "missing.json")).load()


def test_build_adapters_live_wires_biollm_sources():
    adapters = build_adapters(
        "live",
        fixtures_dir=FIXTURES,
        analysis_run_dir=str(PACKAGE),
        brief_path=str(BRIEF),
    )
    analysis = adapters.analysis.load()
    brief = adapters.brief.load()
    assert analysis.metrics["sample_count"] == 3
    assert brief.data_modality == "shotgun_metagenomics"


def test_pipeline_writes_methods_from_biollm_package(tmp_path: Path):
    adapters = AdapterBundle(
        mode="live",
        brief=LiveBriefPort(brief_path=str(BRIEF)),
        analysis=LiveAnalysisPort(run_dir=str(PACKAGE)),
        literature=MockLiteraturePort(FIXTURES),
    )
    pipeline = WritingPipeline(
        adapters=adapters,
        literature_query="gut microbiota",
        react_enabled=False,
        llm_enabled=False,
    )
    out = tmp_path / "out"
    path = pipeline.run_and_export(run_id="biollm-live", output_dir=out)
    bundle = json.loads(path.read_text(encoding="utf-8"))
    methods = (out / "sections" / "methods.md").read_text(encoding="utf-8")
    results = (out / "sections" / "results.md").read_text(encoding="utf-8")
    assert bundle["brief"]["data_modality"] == "shotgun_metagenomics"
    assert "Kraken2" in methods or "kraken" in methods.lower()
    assert "Bacteroides vulgatus" in results
    assert "10000" in results or "10,000" in results
    assert any(ref["figure_id"] == "tbl_species" for ref in bundle["figure_refs"])


@pytest.mark.skipif(not REPO_DB.is_file(), reason="Article_repository sqlite missing")
def test_live_literature_and_analysis_reach_one_strict_writing_run(tmp_path: Path):
    adapters = build_adapters(
        "live",
        fixtures_dir=FIXTURES,
        literature_db=REPO_DB,
        analysis_run_dir=str(PACKAGE),
        brief_path=str(BRIEF),
    )
    pipeline = WritingPipeline(
        adapters=adapters,
        literature_query="autism",
        evidence_mode="strict",
        consistency_mode="strict",
        react_enabled=False,
        llm_enabled=False,
    )

    output_dir = tmp_path / "live-integration"
    bundle_path = pipeline.run_and_export(
        run_id="live-literature-analysis",
        output_dir=output_dir,
    )
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    citations = bundle["citations"]
    assert citations
    assert all(item["source"] == "adapter" for item in citations)
    assert "ev_qc_reads" in {
        evidence_id
        for claim in bundle["claims"]
        for evidence_id in claim["evidence_ids"]
    }
    assert any(item["doi"] for item in citations)
    assert len({item["cite_id"] for item in citations}) == len(citations)
    assert "Bacteroides vulgatus" in (
        output_dir / "sections" / "results.md"
    ).read_text(encoding="utf-8")
    assert all(
        "evidence" not in warning.lower() and "consistency" not in warning.lower()
        for warning in bundle["warnings"]
    )


def test_frontend_session_can_start_from_biollm_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from frontend.session_store import SessionStore

    monkeypatch.chdir(ROOT)
    store = SessionStore(tmp_path / "sessions")
    session = store.create_from_pipeline(
        use_llm=False,
        analysis_run_dir=str(PACKAGE),
        brief_path=str(BRIEF),
    )
    assert session.title.startswith("Shotgun metagenomic")
    results = session.sections["results"].markdown
    assert "Bacteroides vulgatus" in results
    assert (Path(session.output_dir) / "pipeline" / "writing_bundle.json").is_file()
    assert session.source["adapter_mode"] == "live"
    summaries = store.list_summaries()
    assert summaries[0]["session_id"] == session.session_id
    assert "manuscript" not in summaries[0]


def test_reject_unsafe_archive(tmp_path: Path):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        info = tarfile.TarInfo(name="../escape.txt")
        payload = b"nope"
        info.size = len(payload)
        handle.addfile(info, io.BytesIO(payload))
    with pytest.raises(BioLLMPackageError):
        LiveAnalysisPort(bundle_path=str(archive)).load()
