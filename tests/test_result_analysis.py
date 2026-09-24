import json
import tempfile
from pathlib import Path

import pandas as pd

from bioflow.core.context import RunContext
from bioflow.core.models import (
    AnalysisConclusion,
    AnalysisData,
    ArtifactManifest,
    FindingItem,
    ModuleInput,
    ModuleOutput,
    ModuleStatus,
    TableRef,
)
from bioflow.modules.analysis.result_analysis import ResultAnalysisModule


def _write_diff_expr(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(
        {
            "gene_id": ["gene_up", "gene_down", "gene_flat"],
            "baseMean": [100.0, 80.0, 20.0],
            "log2FoldChange": [2.5, -1.8, 0.1],
            "pvalue": [0.001, 0.002, 0.8],
            "padj": [0.01, 0.02, 0.9],
        }
    )
    frame.to_csv(path, index=False)


def test_parse_llm_json_accepts_fenced_payload():
    mod = ResultAnalysisModule()
    payload = mod._parse_llm_json(
        """```json
        {"summary": "ok", "key_findings": []}
        ```"""
    )

    assert payload == {"summary": "ok", "key_findings": []}


def test_read_diff_expr_builds_secondary_summary():
    with tempfile.TemporaryDirectory() as tmp:
        diff_path = Path(tmp) / "differential_expression.csv"
        _write_diff_expr(diff_path)

        summary = ResultAnalysisModule()._read_diff_expr(diff_path)

        assert summary["total_genes"] == 3
        assert summary["significant_count"] == 2
        assert summary["up_regulated_count"] == 1
        assert summary["down_regulated_count"] == 1
        assert summary["top_up_genes"][0]["gene_id"] == "gene_up"
        assert summary["top_down_genes"][0]["gene_id"] == "gene_down"


def test_verify_findings_uses_real_analysis_fields():
    mod = ResultAnalysisModule()
    analysis_data = AnalysisData(diff_expr={"significant_count": 2})
    conclusion = AnalysisConclusion(
        summary="manual",
        key_findings=[
            FindingItem(statement="valid", data_basis="diff_expr.significant_count"),
            FindingItem(statement="invalid", data_basis="unknown.metric"),
        ],
    )

    verified = mod._verify_findings(conclusion, analysis_data=analysis_data)

    assert verified.key_findings[0].verified is True
    assert verified.key_findings[1].verified is False


def test_validate_comparison_item_maps_novel_to_not_found():
    item = ResultAnalysisModule()._validate_comparison_item(
        {
            "topic": "gene_up",
            "our_finding": "gene_up increased",
            "literature_finding": "not retrieved",
            "direction": "novel",
            "source": "paper",
            "snippet": "snippet",
            "confidence": 1.5,
        }
    )

    assert item.direction == "not_found"
    assert item.confidence == 1.0
    assert item.source == "paper"
    assert item.snippet == "snippet"


def test_run_without_llm_or_rag_writes_default_chinese_report(monkeypatch):
    monkeypatch.delenv("BIOFLOW_RAG_URL", raising=False)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        context = RunContext(run_id="result-zh", workspace=tmp_path)
        diff_path = context.artifacts_dir / "analysis" / "differential_expression.csv"
        _write_diff_expr(diff_path)
        upstream = ModuleOutput(
            module="differential_analysis",
            status=ModuleStatus.succeeded,
            artifacts=ArtifactManifest(root=str(diff_path.parent)),
            tables=[TableRef(title="DEG", path=str(diff_path), rows=3)],
        )

        output = ResultAnalysisModule().run(
            context,
            ModuleInput(run_id="result-zh", module="result_analysis", deps={"differential_analysis": upstream}),
        )

        artifact_dir = context.artifacts_dir / "result_analysis"
        rendered_report = (artifact_dir / "report.md").read_text(encoding="utf-8")
        assert output.status == ModuleStatus.succeeded
        assert (artifact_dir / "conclusion.json").exists()
        assert (artifact_dir / "integration_report.json").exists()
        assert (artifact_dir / "report.md").exists()
        assert rendered_report.startswith("# 结果分析与文献整合报告")
        assert output.metrics["integration_report"]["comparisons"][0]["direction"] == "not_found"


def test_run_supports_english_report(monkeypatch):
    monkeypatch.delenv("BIOFLOW_RAG_URL", raising=False)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        context = RunContext(run_id="result-en", workspace=tmp_path)
        diff_path = context.artifacts_dir / "analysis" / "differential_expression.csv"
        _write_diff_expr(diff_path)

        output = ResultAnalysisModule().run(
            context,
            ModuleInput(
                run_id="result-en",
                module="result_analysis",
                payload={"report_lang": "english"},
                deps={},
            ),
        )

        artifact_dir = context.artifacts_dir / "result_analysis"
        rendered_report = (artifact_dir / "report.md").read_text(encoding="utf-8")
        conclusion_json = json.loads((artifact_dir / "conclusion.json").read_text(encoding="utf-8"))
        assert output.status == ModuleStatus.succeeded
        assert rendered_report.startswith("# Result Analysis and Literature Integration")
        assert conclusion_json["summary"].startswith("Differential expression analysis")


def test_non_json_llm_response_falls_back(monkeypatch):
    class BadRouter:
        def generate(self, prompt, system=None):
            return "this is not json"

    monkeypatch.delenv("BIOFLOW_RAG_URL", raising=False)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        context = RunContext(run_id="result-bad-llm", workspace=tmp_path)
        context.model_router = BadRouter()
        diff_path = context.artifacts_dir / "analysis" / "differential_expression.csv"
        _write_diff_expr(diff_path)

        output = ResultAnalysisModule().run(
            context,
            ModuleInput(run_id="result-bad-llm", module="result_analysis", deps={}),
        )

        assert output.status == ModuleStatus.succeeded
        assert "规则逻辑生成" in output.metrics["conclusion"]["methodology_notes"][0]


def test_run_uses_context_rag_url_before_env(monkeypatch):
    monkeypatch.delenv("BIOFLOW_RAG_URL", raising=False)

    seen = {}

    class FakeRagResult:
        answer = "literature support"
        sources = ["paper-1"]
        references = ["ref-1"]

    class FakeRagClient:
        def __init__(self, url, timeout=60):
            seen["url"] = url
            seen["timeout"] = timeout

        def query(self, query):
            seen["query"] = query
            return FakeRagResult()

    monkeypatch.setattr("bioflow.modules.analysis.result_analysis.RagClient", FakeRagClient)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        context = RunContext(run_id="result-rag", workspace=tmp_path)
        context.rag_url = "http://localhost:8000/rag/query"
        context.rag_timeout = 12
        diff_path = context.artifacts_dir / "analysis" / "differential_expression.csv"
        _write_diff_expr(diff_path)
        upstream = ModuleOutput(
            module="differential_analysis",
            status=ModuleStatus.succeeded,
            artifacts=ArtifactManifest(root=str(diff_path.parent)),
            tables=[TableRef(title="DEG", path=str(diff_path), rows=3)],
        )

        output = ResultAnalysisModule().run(
            context,
            ModuleInput(run_id="result-rag", module="result_analysis", deps={"differential_analysis": upstream}),
        )

        assert seen["url"] == "http://localhost:8000/rag/query"
        assert seen["timeout"] == 12
        assert output.metrics["integration_report"]["comparisons"][0]["direction"] == "consistent"
