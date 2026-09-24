from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

import pandas as pd

from bioflow.analysis.rag import RagClient, build_rag_query_terms
from bioflow.core.context import RunContext
from bioflow.core.models import (
    AnalysisConclusion,
    AnalysisData,
    ArtifactManifest,
    ArtifactRef,
    ComparisonItem,
    EvidenceRef,
    FindingItem,
    IntegrationReport,
    ModuleInput,
    ModuleOutput,
    ModuleSpec,
    ModuleStatus,
    TableRef,
)
from bioflow.modules.base import Module
from bioflow.modules.catalog import register


_ALLOWED_DIRECTIONS = {"consistent", "divergent", "not_found"}


@register
class ResultAnalysisModule(Module):
    spec = ModuleSpec(
        name="result_analysis",
        kind="analysis",
        deps=["differential_analysis"],
        description="Interpret differential-expression results and optionally compare them with RAG literature.",
    )

    def run(self, context: RunContext, module_input: ModuleInput) -> ModuleOutput:
        upstream = module_input.deps.get("differential_analysis")
        report_lang = self._normalize_report_lang(module_input.payload.get("report_lang"))

        analysis_data = self._load_upstream_artifacts(context, upstream)
        conclusion = self._generate_conclusion(context, analysis_data, report_lang)
        integration_report = self._literature_comparison(context, conclusion, analysis_data, report_lang)

        return self._persist_and_output(
            context=context,
            analysis_data=analysis_data,
            conclusion=conclusion,
            integration_report=integration_report,
            report_lang=report_lang,
        )

    # ------------------------------------------------------------------
    #  loading layer
    # ------------------------------------------------------------------
    def _load_upstream_artifacts(self, context: RunContext, upstream: ModuleOutput | None) -> AnalysisData:
        data_type = "unknown"
        if upstream and upstream.dataset:
            data_type = upstream.dataset.dataset_type.value

        analysis_data = AnalysisData(data_type=data_type)
        diff_path = self._find_diff_expr_path(context, upstream)
        if diff_path:
            analysis_data.diff_expr = self._read_diff_expr(diff_path)
            analysis_data.detected_types.append("differential_expression")
        else:
            analysis_data.available = False
            analysis_data.data_quality = "missing_primary_input"
            analysis_data.warnings.append("differential_expression.csv not found")

        data_processing_output = context.module_outputs.get("data_processing")
        if data_processing_output is None and upstream:
            data_processing_output = getattr(upstream, "deps", {}).get("data_processing") if hasattr(upstream, "deps") else None
        self._merge_data_processing_metrics(analysis_data, data_processing_output)
        return analysis_data

    def _find_diff_expr_path(self, context: RunContext, upstream: ModuleOutput | None) -> Path | None:
        candidates: List[Path] = []
        if upstream:
            for item in upstream.artifacts.items:
                path = self._resolve_artifact_path(upstream.artifacts.root, item.path)
                if path.name == "differential_expression.csv":
                    candidates.append(path)
            for table in upstream.tables:
                path = self._resolve_artifact_path(upstream.artifacts.root, table.path)
                if path.suffix.lower() == ".csv":
                    candidates.append(path)

        candidates.extend(
            [
                context.artifacts_dir / "analysis" / "differential_expression.csv",
                context.artifacts_dir / "differential_analysis" / "differential_expression.csv",
            ]
        )
        diff_dir = context.artifacts_dir / "differential_analysis"
        if diff_dir.exists():
            candidates.extend(sorted(diff_dir.glob("deg_*.csv")))

        for path in candidates:
            if path.exists() and path.is_file():
                return path
        return None

    @staticmethod
    def _resolve_artifact_path(root: str, path: str) -> Path:
        artifact_path = Path(path)
        if artifact_path.is_absolute():
            return artifact_path
        return Path(root) / artifact_path

    def _read_diff_expr(self, path: Path) -> Dict[str, Any]:
        frame = pd.read_csv(path)
        required = {"gene_id", "baseMean", "log2FoldChange", "pvalue", "padj"}
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise ValueError(f"differential expression CSV is missing columns: {', '.join(missing)}")

        work = frame.copy()
        for column in ["baseMean", "log2FoldChange", "pvalue", "padj"]:
            work[column] = pd.to_numeric(work[column], errors="coerce")

        if "significant" in work.columns:
            significant = work["significant"].astype(str).str.lower().isin({"true", "1", "yes"})
        else:
            significant = (work["padj"] <= 0.05) & (work["log2FoldChange"].abs() >= 1.0)
        significant = significant.fillna(False)

        up = work[significant & (work["log2FoldChange"] > 0)].copy()
        down = work[significant & (work["log2FoldChange"] < 0)].copy()
        total_genes = int(len(work))
        significant_count = int(significant.sum())

        return {
            "total_genes": total_genes,
            "significant_count": significant_count,
            "significant_ratio": float(significant_count / total_genes) if total_genes else 0.0,
            "up_regulated_count": int(len(up)),
            "down_regulated_count": int(len(down)),
            "top_up_genes": self._top_genes(up.sort_values("log2FoldChange", ascending=False)),
            "top_down_genes": self._top_genes(down.sort_values("log2FoldChange", ascending=True)),
            "pvalue_median": self._safe_float(work["pvalue"].median()),
            "min_padj": self._safe_float(work["padj"].min()),
            "max_abs_log2_fold_change": self._safe_float(work["log2FoldChange"].abs().max()),
            "source_path": str(path),
        }

    @staticmethod
    def _top_genes(frame: pd.DataFrame, limit: int = 10) -> List[Dict[str, Any]]:
        genes: List[Dict[str, Any]] = []
        for _, row in frame.head(limit).iterrows():
            genes.append(
                {
                    "gene_id": str(row["gene_id"]),
                    "log2FoldChange": ResultAnalysisModule._safe_float(row["log2FoldChange"]),
                    "padj": ResultAnalysisModule._safe_float(row["padj"]),
                }
            )
        return genes

    def _read_pipeline_summary(self, path: Path) -> Dict[str, Any]:
        return self._read_summary_metrics(path)

    def _read_raw_matrix_summary(self, path: Path) -> Dict[str, Any]:
        return self._read_summary_metrics(path)

    @staticmethod
    def _read_summary_metrics(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("metrics"), dict):
            return dict(data["metrics"])
        return data if isinstance(data, dict) else {}

    def _merge_data_processing_metrics(self, data: AnalysisData, output: Any) -> None:
        metrics = getattr(output, "metrics", {}) if output is not None else {}
        if not isinstance(metrics, Mapping):
            return

        summary_path = metrics.get("summary_path")
        pipeline_summary_path = metrics.get("pipeline_summary_path")
        raw_metrics = self._read_raw_matrix_summary(Path(summary_path)) if summary_path else {}
        pipeline_metrics = self._read_pipeline_summary(Path(pipeline_summary_path)) if pipeline_summary_path else {}
        merged = {**dict(metrics), **raw_metrics, **pipeline_metrics}

        matrix_info = self._pick_keys(
            merged,
            [
                "raw_dataset_type",
                "raw_matrix_shape",
                "raw_feature_count",
                "raw_sample_count",
                "raw_non_missing_ratio",
                "raw_non_zero_ratio",
                "norm_shape",
            ],
        )
        qc_info = {key: value for key, value in merged.items() if str(key).startswith("qc_")}
        normalization_info = self._pick_keys(
            merged,
            [
                "normalization_method",
                "norm_matrix_path",
                "norm_shape",
                "norm_sample_mean_range",
                "norm_top_variable_features",
            ],
        )
        pca_info = {key: value for key, value in merged.items() if str(key).startswith("pca_")}

        if matrix_info:
            data.matrix_info = matrix_info
            data.detected_types.append("matrix")
        if qc_info:
            data.qc_info = qc_info
            data.detected_types.append("qc")
        if normalization_info:
            data.normalization_info = normalization_info
            data.detected_types.append("normalization")
        if pca_info:
            data.extra_analyses["pca"] = pca_info
            data.detected_types.append("pca")

    @staticmethod
    def _pick_keys(data: Mapping[str, Any], keys: Iterable[str]) -> Dict[str, Any]:
        return {key: data[key] for key in keys if key in data}

    # ------------------------------------------------------------------
    #  independent conclusion layer
    # ------------------------------------------------------------------
    def _generate_conclusion(
        self,
        context: RunContext,
        analysis_data: AnalysisData,
        report_lang: str = "zh",
    ) -> AnalysisConclusion:
        llm_payload = self._call_llm_conclusion(context, analysis_data, report_lang)
        if llm_payload:
            parsed = self._parse_llm_json(llm_payload)
            conclusion = self._validate_conclusion_json(parsed, analysis_data) if parsed else None
            if conclusion:
                return conclusion
        return self._fallback_conclusion(analysis_data, report_lang)

    def _call_llm_conclusion(self, context: RunContext, analysis_data: AnalysisData, report_lang: str) -> str | None:
        router = getattr(context, "model_router", None)
        if router is None:
            return None
        lang_hint = "Chinese" if report_lang == "zh" else "English"
        prompt = (
            f"Generate a {lang_hint} JSON conclusion from the current analysis data only. "
            "Return keys: summary, key_findings [{statement,data_basis,verified}], "
            "methodology_notes, limitations. Do not use literature claims.\n\n"
            f"{json.dumps(analysis_data.model_dump(mode='json'), ensure_ascii=False)}"
        )
        try:
            result = router.generate(prompt, system="You are a cautious bioinformatics result analyst.")
        except Exception:  # noqa: BLE001
            return None
        return self._stringify_llm_result(result)

    def _rule_based_summary(self, analysis_data: AnalysisData, report_lang: str = "zh") -> str:
        diff = analysis_data.diff_expr or {}
        if not diff:
            return (
                "未发现可解析的差异表达结果，当前报告仅能说明上游数据缺失。"
                if report_lang == "zh"
                else "No parseable differential-expression result was found; the report is limited to upstream-data availability."
            )
        if report_lang == "en":
            return (
                f"Differential expression analysis covered {diff.get('total_genes', 0)} genes, "
                f"with {diff.get('significant_count', 0)} significant genes "
                f"({diff.get('up_regulated_count', 0)} up-regulated, "
                f"{diff.get('down_regulated_count', 0)} down-regulated)."
            )
        return (
            f"差异表达分析共覆盖 {diff.get('total_genes', 0)} 个基因，"
            f"检出 {diff.get('significant_count', 0)} 个显著差异基因，"
            f"其中上调 {diff.get('up_regulated_count', 0)} 个、下调 {diff.get('down_regulated_count', 0)} 个。"
        )

    def _fallback_conclusion(self, analysis_data: AnalysisData, report_lang: str = "zh") -> AnalysisConclusion:
        diff = analysis_data.diff_expr or {}
        findings: List[FindingItem] = []
        if diff:
            if report_lang == "en":
                findings.extend(
                    [
                        FindingItem(
                            statement=f"{diff.get('significant_count', 0)} significant genes were detected.",
                            data_basis="diff_expr.significant_count",
                        ),
                        FindingItem(
                            statement=(
                                f"Up-regulated genes: {diff.get('up_regulated_count', 0)}; "
                                f"down-regulated genes: {diff.get('down_regulated_count', 0)}."
                            ),
                            data_basis="diff_expr.up_regulated_count; diff_expr.down_regulated_count",
                        ),
                        FindingItem(
                            statement=f"The largest absolute log2 fold-change was {diff.get('max_abs_log2_fold_change', 0)}.",
                            data_basis="diff_expr.max_abs_log2_fold_change",
                        ),
                    ]
                )
            else:
                findings.extend(
                    [
                        FindingItem(
                            statement=f"检出 {diff.get('significant_count', 0)} 个显著差异基因。",
                            data_basis="diff_expr.significant_count",
                        ),
                        FindingItem(
                            statement=(
                                f"上调基因 {diff.get('up_regulated_count', 0)} 个，"
                                f"下调基因 {diff.get('down_regulated_count', 0)} 个。"
                            ),
                            data_basis="diff_expr.up_regulated_count; diff_expr.down_regulated_count",
                        ),
                        FindingItem(
                            statement=f"最大绝对 log2 倍数变化为 {diff.get('max_abs_log2_fold_change', 0)}。",
                            data_basis="diff_expr.max_abs_log2_fold_change",
                        ),
                    ]
                )

        methodology_notes = (
            ["Conclusion generated by deterministic rules because no valid LLM JSON was available."]
            if report_lang == "en"
            else ["由于没有可用的 LLM JSON 输出，本结论由规则逻辑生成。"]
        )
        limitations = list(analysis_data.warnings)
        if not analysis_data.normalization_info:
            limitations.append(
                "缺少 data_processing 背景信息。" if report_lang == "zh" else "data_processing background is unavailable."
            )
        conclusion = AnalysisConclusion(
            summary=self._rule_based_summary(analysis_data, report_lang),
            key_findings=findings,
            methodology_notes=methodology_notes,
            limitations=limitations,
        )
        return self._verify_findings(conclusion, analysis_data)

    def _parse_llm_json(self, text: str | None) -> Dict[str, Any] | None:
        if not text:
            return None
        cleaned = text.strip()
        fence_match = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
        if fence_match:
            cleaned = fence_match.group(1).strip()
        try:
            data = json.loads(cleaned)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(cleaned[start : end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
        return None

    def _validate_conclusion_json(
        self,
        payload: Mapping[str, Any] | None,
        analysis_data: AnalysisData,
    ) -> AnalysisConclusion | None:
        if not payload or not isinstance(payload.get("summary"), str):
            return None
        findings: List[FindingItem] = []
        for item in payload.get("key_findings", []) or []:
            if not isinstance(item, Mapping):
                continue
            statement = str(item.get("statement", "")).strip()
            data_basis = str(item.get("data_basis", "")).strip()
            if statement and data_basis:
                findings.append(FindingItem(statement=statement, data_basis=data_basis, verified=bool(item.get("verified", True))))
        conclusion = AnalysisConclusion(
            summary=str(payload["summary"]).strip(),
            key_findings=findings,
            methodology_notes=[str(x) for x in payload.get("methodology_notes", []) or []],
            limitations=[str(x) for x in payload.get("limitations", []) or []],
        )
        return self._verify_findings(conclusion, analysis_data)

    def _verify_findings(self, conclusion: AnalysisConclusion, analysis_data: AnalysisData) -> AnalysisConclusion:
        valid_fields = set(self._flatten_analysis_keys(analysis_data.model_dump(mode="json")))
        verified_findings: List[FindingItem] = []
        for finding in conclusion.key_findings:
            basis = finding.data_basis.lower()
            verified = any(field.lower() in basis for field in valid_fields)
            verified_findings.append(finding.model_copy(update={"verified": verified}))
        return conclusion.model_copy(update={"key_findings": verified_findings})

    def _flatten_analysis_keys(self, value: Any, prefix: str = "") -> List[str]:
        keys: List[str] = []
        if isinstance(value, Mapping):
            for key, nested in value.items():
                full = f"{prefix}.{key}" if prefix else str(key)
                keys.append(full)
                keys.extend(self._flatten_analysis_keys(nested, full))
        return keys

    # ------------------------------------------------------------------
    #  literature integration layer
    # ------------------------------------------------------------------
    def _literature_comparison(
        self,
        context: RunContext,
        conclusion: AnalysisConclusion,
        analysis_data: AnalysisData,
        report_lang: str = "zh",
    ) -> IntegrationReport:
        query = build_rag_query_terms(self._build_rag_query_text(conclusion, analysis_data))
        rag_result = self._query_rag(context, query)
        if rag_result is None:
            return self._no_literature_report(conclusion, query, report_lang)

        llm_payload = self._call_llm_comparison(context, conclusion, rag_result, query, report_lang)
        if llm_payload:
            parsed = self._parse_llm_json(llm_payload)
            report = self._build_integration_report_from_llm(parsed, conclusion, query, rag_result)
            if report:
                return report
        return self._fallback_literature_report(conclusion, query, rag_result, report_lang)

    def _query_rag(self, context: RunContext, query: str) -> Any | None:
        rag_url = getattr(context, "rag_url", "")
        if not rag_url:
            rag_url = os.getenv("BIOFLOW_RAG_URL", "")
        if not rag_url or not query:
            return None
        try:
            timeout = getattr(context, "rag_timeout", 60)
            return RagClient(rag_url, timeout=timeout).query(query)
        except Exception:  # noqa: BLE001
            return None

    def _call_llm_comparison(
        self,
        context: RunContext,
        conclusion: AnalysisConclusion,
        rag_result: Any,
        query: str,
        report_lang: str,
    ) -> str | None:
        router = getattr(context, "model_router", None)
        if router is None:
            return None
        lang_hint = "Chinese" if report_lang == "zh" else "English"
        prompt = (
            f"Compare our conclusion with the RAG literature evidence in {lang_hint}. "
            "Return JSON keys: comparisons, consistent_points, divergent_points, "
            "not_found_points, overall_assessment, agreement_score. "
            "Each comparison must contain topic, our_finding, literature_finding, "
            "direction, source, snippet, confidence. direction must be consistent, divergent, or not_found.\n\n"
            f"RAG query: {query}\n"
            f"Our conclusion: {conclusion.model_dump_json()}\n"
            f"RAG answer: {getattr(rag_result, 'answer', '')}\n"
            f"RAG sources: {json.dumps(self._rag_sources(rag_result), ensure_ascii=False)}"
        )
        try:
            result = router.generate(prompt, system="You compare current data-backed findings with retrieved literature.")
        except Exception:  # noqa: BLE001
            return None
        return self._stringify_llm_result(result)

    def _build_integration_report_from_llm(
        self,
        payload: Mapping[str, Any] | None,
        conclusion: AnalysisConclusion,
        query: str,
        rag_result: Any,
    ) -> IntegrationReport | None:
        if not payload:
            return None
        comparisons = []
        for item in payload.get("comparisons", []) or []:
            comparison = self._validate_comparison_item(item)
            if comparison:
                comparisons.append(comparison)
        score = self._clamp_float(payload.get("agreement_score", 0.5), default=0.5)
        return IntegrationReport(
            conclusion=conclusion,
            comparisons=comparisons,
            consistent_points=[str(x) for x in payload.get("consistent_points", []) or []],
            divergent_points=[str(x) for x in payload.get("divergent_points", []) or []],
            not_found_points=[str(x) for x in payload.get("not_found_points", []) or []],
            overall_assessment=str(payload.get("overall_assessment", "")),
            agreement_score=score,
            rag_sources=self._rag_sources(rag_result),
            rag_query=query,
        )

    def _fallback_literature_report(
        self,
        conclusion: AnalysisConclusion,
        query: str,
        rag_result: Any,
        report_lang: str = "zh",
    ) -> IntegrationReport:
        answer = str(getattr(rag_result, "answer", "") or "")
        snippet = answer[:400]
        sources = self._rag_sources(rag_result)
        comparisons = []
        for finding in conclusion.key_findings[:5]:
            comparisons.append(
                ComparisonItem(
                    topic=finding.statement[:80],
                    our_finding=finding.statement,
                    literature_finding=snippet or ("RAG returned sources but no answer." if report_lang == "en" else "RAG 返回了来源但没有答案。"),
                    direction="not_found" if not answer else "consistent",
                    source=sources[0] if sources else "RAG",
                    snippet=snippet,
                    confidence=0.5 if answer else 0.3,
                )
            )
        overall = (
            "RAG evidence was retrieved, but no valid LLM comparison JSON was available; a conservative fallback report was generated."
            if report_lang == "en"
            else "已检索到 RAG 证据，但没有可用的 LLM 对比 JSON，因此生成保守的降级整合报告。"
        )
        return IntegrationReport(
            conclusion=conclusion,
            comparisons=comparisons,
            consistent_points=[item.our_finding for item in comparisons if item.direction == "consistent"],
            not_found_points=[item.our_finding for item in comparisons if item.direction == "not_found"],
            overall_assessment=overall,
            agreement_score=0.5 if answer else 0.3,
            rag_sources=sources,
            rag_query=query,
        )

    def _no_literature_report(
        self,
        conclusion: AnalysisConclusion,
        query: str,
        report_lang: str = "zh",
    ) -> IntegrationReport:
        points = [finding.statement for finding in conclusion.key_findings] or [conclusion.summary]
        assessment = (
            "No RAG endpoint or retrievable literature evidence was available; literature comparison is marked as not found."
            if report_lang == "en"
            else "未配置 RAG 端点或未检索到可用文献证据，文献对比标记为未找到。"
        )
        return IntegrationReport(
            conclusion=conclusion,
            comparisons=[
                ComparisonItem(
                    topic=point[:80],
                    our_finding=point,
                    literature_finding="No retrieved literature evidence." if report_lang == "en" else "未检索到文献证据。",
                    direction="not_found",
                    source="RAG",
                    snippet="",
                    confidence=0.2,
                )
                for point in points[:5]
            ],
            not_found_points=points,
            overall_assessment=assessment,
            agreement_score=0.0,
            rag_sources=[],
            rag_query=query,
        )

    def _validate_comparison_item(self, item: Any) -> ComparisonItem | None:
        if not isinstance(item, Mapping):
            return None
        direction = str(item.get("direction", "not_found")).strip().lower()
        if direction == "novel":
            direction = "not_found"
        if direction not in _ALLOWED_DIRECTIONS:
            direction = "not_found"
        return ComparisonItem(
            topic=str(item.get("topic", "")).strip() or "comparison",
            our_finding=str(item.get("our_finding", "")).strip(),
            literature_finding=str(item.get("literature_finding", "")).strip(),
            direction=direction,
            source=str(item.get("source", "RAG")).strip() or "RAG",
            snippet=str(item.get("snippet", "")).strip(),
            confidence=self._clamp_float(item.get("confidence", 0.5), default=0.5),
        )

    # ------------------------------------------------------------------
    #  output layer
    # ------------------------------------------------------------------
    def _persist_and_output(
        self,
        *,
        context: RunContext,
        analysis_data: AnalysisData,
        conclusion: AnalysisConclusion,
        integration_report: IntegrationReport,
        report_lang: str,
    ) -> ModuleOutput:
        artifact_dir = context.artifacts_dir / "result_analysis"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        conclusion_path = artifact_dir / "conclusion.json"
        integration_path = artifact_dir / "integration_report.json"
        report_path = artifact_dir / "report.md"

        conclusion_path.write_text(
            json.dumps(conclusion.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        integration_path.write_text(
            json.dumps(integration_report.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report_path.write_text(
            self._render_report_md(analysis_data, conclusion, integration_report, report_lang),
            encoding="utf-8",
        )

        artifacts = ArtifactManifest(root=str(artifact_dir))
        artifacts.items.extend(
            [
                ArtifactRef(path=str(conclusion_path), kind="json", description="Independent result-analysis conclusion"),
                ArtifactRef(path=str(integration_path), kind="json", description="RAG literature integration report"),
                ArtifactRef(path=str(report_path), kind="report", description="Markdown result-analysis report"),
            ]
        )

        tables = [TableRef(title="result_analysis_report", path=str(report_path), rows=None)]
        evidence = self._build_evidence(conclusion, integration_report)
        metrics = {
            "analysis_data": analysis_data.model_dump(mode="json"),
            "conclusion": conclusion.model_dump(mode="json"),
            "integration_report": integration_report.model_dump(mode="json"),
            "rag_query": integration_report.rag_query,
            "rag_sources": integration_report.rag_sources,
        }

        return ModuleOutput(
            module=self.spec.name,
            status=ModuleStatus.succeeded,
            summary=conclusion.summary,
            artifacts=artifacts,
            evidence=evidence,
            warnings=list(analysis_data.warnings),
            metrics=metrics,
            tables=tables,
        )

    def _build_evidence(
        self,
        conclusion: AnalysisConclusion,
        integration_report: IntegrationReport,
    ) -> List[EvidenceRef]:
        evidence: List[EvidenceRef] = []
        for index, finding in enumerate(conclusion.key_findings, start=1):
            evidence.append(
                EvidenceRef(
                    evidence_id=f"result_analysis:finding:{index}",
                    title="Result-analysis finding",
                    source="result_analysis",
                    locator=finding.data_basis,
                    snippet=finding.statement,
                    score=1.0 if finding.verified else 0.5,
                    tags=["finding", "verified" if finding.verified else "unverified"],
                )
            )
        for index, comparison in enumerate(integration_report.comparisons, start=1):
            evidence.append(
                EvidenceRef(
                    evidence_id=f"result_analysis:literature:{index}",
                    title=comparison.topic,
                    source=comparison.source,
                    locator=comparison.direction,
                    snippet=comparison.snippet or comparison.literature_finding,
                    score=comparison.confidence,
                    tags=["literature", comparison.direction],
                )
            )
        return evidence

    def _render_report_md(
        self,
        analysis_data: AnalysisData,
        conclusion: AnalysisConclusion,
        integration_report: IntegrationReport,
        report_lang: str,
    ) -> str:
        if report_lang == "en":
            return self._render_report_en(analysis_data, conclusion, integration_report)
        return self._render_report_zh(analysis_data, conclusion, integration_report)

    def _render_report_zh(
        self,
        analysis_data: AnalysisData,
        conclusion: AnalysisConclusion,
        integration_report: IntegrationReport,
    ) -> str:
        lines = [
            "# 结果分析与文献整合报告",
            "",
            "## 独立结论",
            "",
            conclusion.summary,
            "",
            "## 关键发现",
            "",
        ]
        lines.extend(self._finding_lines(conclusion))
        lines.extend(
            [
                "",
                "## 差异表达摘要",
                "",
                self._diff_summary_line(analysis_data, "zh"),
                "",
                "## 文献对比",
                "",
            ]
        )
        lines.extend(self._comparison_lines(integration_report))
        lines.extend(
            [
                "",
                "## 整体评估",
                "",
                integration_report.overall_assessment or "未形成额外评估。",
                "",
                f"- RAG 查询: {integration_report.rag_query or '无'}",
                f"- 一致性评分: {integration_report.agreement_score:.2f}",
            ]
        )
        if conclusion.limitations:
            lines.extend(["", "## 局限性", ""])
            lines.extend(f"- {item}" for item in conclusion.limitations)
        return "\n".join(lines).rstrip() + "\n"

    def _render_report_en(
        self,
        analysis_data: AnalysisData,
        conclusion: AnalysisConclusion,
        integration_report: IntegrationReport,
    ) -> str:
        lines = [
            "# Result Analysis and Literature Integration",
            "",
            "## Independent Conclusion",
            "",
            conclusion.summary,
            "",
            "## Key Findings",
            "",
        ]
        lines.extend(self._finding_lines(conclusion))
        lines.extend(
            [
                "",
                "## Differential Expression Summary",
                "",
                self._diff_summary_line(analysis_data, "en"),
                "",
                "## Literature Comparison",
                "",
            ]
        )
        lines.extend(self._comparison_lines(integration_report))
        lines.extend(
            [
                "",
                "## Overall Assessment",
                "",
                integration_report.overall_assessment or "No additional assessment was generated.",
                "",
                f"- RAG query: {integration_report.rag_query or 'none'}",
                f"- Agreement score: {integration_report.agreement_score:.2f}",
            ]
        )
        if conclusion.limitations:
            lines.extend(["", "## Limitations", ""])
            lines.extend(f"- {item}" for item in conclusion.limitations)
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _finding_lines(conclusion: AnalysisConclusion) -> List[str]:
        if not conclusion.key_findings:
            return ["- No data-backed finding was generated."]
        return [
            f"- {finding.statement} (basis: {finding.data_basis}; verified: {finding.verified})"
            for finding in conclusion.key_findings
        ]

    @staticmethod
    def _comparison_lines(integration_report: IntegrationReport) -> List[str]:
        if not integration_report.comparisons:
            return ["- No comparison item was generated."]
        return [
            (
                f"- [{item.direction}] {item.topic}: {item.our_finding} | "
                f"literature: {item.literature_finding} | source: {item.source}"
            )
            for item in integration_report.comparisons
        ]

    @staticmethod
    def _diff_summary_line(analysis_data: AnalysisData, report_lang: str) -> str:
        diff = analysis_data.diff_expr or {}
        if not diff:
            return "未找到差异表达摘要。" if report_lang == "zh" else "No differential-expression summary was found."
        if report_lang == "en":
            return (
                f"{diff.get('total_genes', 0)} genes analyzed; "
                f"{diff.get('significant_count', 0)} significant; "
                f"{diff.get('up_regulated_count', 0)} up-regulated; "
                f"{diff.get('down_regulated_count', 0)} down-regulated."
            )
        return (
            f"共分析 {diff.get('total_genes', 0)} 个基因；"
            f"显著差异 {diff.get('significant_count', 0)} 个；"
            f"上调 {diff.get('up_regulated_count', 0)} 个；"
            f"下调 {diff.get('down_regulated_count', 0)} 个。"
        )

    @staticmethod
    def _normalize_report_lang(value: Any) -> str:
        lang = str(value or "zh").strip().lower()
        if lang in {"en", "eng", "english"}:
            return "en"
        return "zh"

    # ------------------------------------------------------------------
    #  small helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _build_rag_query_text(conclusion: AnalysisConclusion, analysis_data: AnalysisData) -> str:
        parts = [conclusion.summary]
        parts.extend(finding.statement for finding in conclusion.key_findings)
        diff = analysis_data.diff_expr or {}
        for key in ["top_up_genes", "top_down_genes"]:
            for item in diff.get(key, []) or []:
                if isinstance(item, Mapping):
                    parts.append(str(item.get("gene_id", "")))
        return " ".join(parts)

    @staticmethod
    def _rag_sources(rag_result: Any) -> List[str]:
        sources = list(getattr(rag_result, "sources", []) or [])
        references = list(getattr(rag_result, "references", []) or [])
        combined = [str(item) for item in sources + references if item]
        return list(dict.fromkeys(combined))

    @staticmethod
    def _stringify_llm_result(result: Any) -> str:
        if isinstance(result, str):
            return result
        for attr in ("content", "text", "answer"):
            value = getattr(result, attr, None)
            if isinstance(value, str):
                return value
        return str(result)

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(numeric):
            return None
        return numeric

    @staticmethod
    def _clamp_float(value: Any, default: float = 0.5) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return default
        if not math.isfinite(numeric):
            return default
        return min(1.0, max(0.0, numeric))
