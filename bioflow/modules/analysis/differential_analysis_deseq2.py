"""Python-based differential expression analysis using PyDESeq2.

This module provides a Python implementation of DESeq2-like analysis
using the pydeseq2 library, which replicates DESeq2's statistical methods.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping

import numpy as np
import pandas as pd

from bioflow.core.context import RunContext
from bioflow.core.models import (
    ArtifactManifest,
    ArtifactRef,
    DatasetValidationError,
    FigureRef,
    ModuleInput,
    ModuleOutput,
    ModuleSpec,
    ModuleStatus,
    TableRef,
)
from bioflow.modules.base import Module
from bioflow.modules.catalog import register

logger = logging.getLogger(__name__)


@register
class DifferentialAnalysisDESeq2Module(Module):
    """Differential expression analysis using PyDESeq2 (DESeq2 in Python)."""

    spec = ModuleSpec(
        name="differential_analysis_deseq2",
        kind="analysis",
        deps=["data_processing"],
        description="Differential expression analysis using PyDESeq2 (DESeq2-like workflow for raw counts).",
    )

    def run(self, context: RunContext, module_input: ModuleInput) -> ModuleOutput:
        upstream = module_input.deps.get("data_processing")
        if not upstream or not upstream.dataset:
            raise DatasetValidationError("differential_analysis_deseq2 requires data_processing output.")

        # 解析参数
        params = self._build_params(module_input.payload)
        matrix_path = self._resolve_matrix_path(upstream.metrics, upstream.dataset.primary_path)
        metadata_path = self._resolve_metadata_path(module_input.payload)

        # 创建输出目录
        out_dir = context.artifact_path(self.spec.name)
        out_dir.mkdir(parents=True, exist_ok=True)
        output_prefix = str(out_dir / "deg_deseq2")

        # 运行 DESeq2 分析
        results = self._run_deseq2_analysis(matrix_path, metadata_path, output_prefix, params)

        # 构建输出
        tables = [
            TableRef(
                title="DEG Results (PyDESeq2)",
                path=str(results["deg_path"].relative_to(context.workspace)),
                rows=results["deg_count"],
            ),
            TableRef(
                title="Normalized Counts",
                path=str(results["normalized_path"].relative_to(context.workspace)),
                rows=results["normalized_count"],
            ),
        ]
        figures = [
            FigureRef(
                title="Volcano Plot Data",
                path=str(results["volcano_path"].relative_to(context.workspace)),
                caption="Volcano plot data from PyDESeq2 analysis.",
            )
        ]
        artifacts = ArtifactManifest(root=str(out_dir))
        artifacts.items.extend([
            ArtifactRef(path=str(results["deg_path"]), kind="table", description="DEG results from PyDESeq2"),
            ArtifactRef(path=str(results["volcano_path"]), kind="figure_data", description="Volcano plot data"),
            ArtifactRef(path=str(results["normalized_path"]), kind="table", description="Normalized counts"),
            ArtifactRef(path=str(results["summary_path"]), kind="summary", description="DEG summary"),
        ])

        summary = (
            f"Differential analysis with PyDESeq2 completed: {results['metrics']['deg_comparison_count']} comparison(s), "
            f"{results['metrics']['deg_significant_total']} significant DEG(s)."
        )
        logger.info(summary)

        return ModuleOutput(
            module=self.spec.name,
            status=ModuleStatus.succeeded,
            summary=summary,
            artifacts=artifacts,
            dataset=upstream.dataset,
            metrics=results["metrics"],
            tables=tables,
            figures=figures,
            evidence=[],
        )

    @staticmethod
    def _resolve_matrix_path(metrics: Mapping[str, Any], fallback_path: str) -> Path:
        matrix_path = metrics.get("norm_matrix_path") or fallback_path
        path = Path(str(matrix_path))
        if not path.exists():
            raise DatasetValidationError(f"matrix path does not exist: {path}")
        return path

    @staticmethod
    def _resolve_metadata_path(payload: Mapping[str, Any]) -> Path:
        metadata_path = payload.get("metadata_path") or payload.get("metadata_file")
        if not metadata_path:
            raise DatasetValidationError("metadata_path is required for DESeq2 analysis")
        path = Path(str(metadata_path))
        if not path.exists():
            raise DatasetValidationError(f"metadata_path does not exist: {path}")
        return path

    @staticmethod
    def _build_params(payload: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "log2fc_threshold": float(payload.get("log2fc_threshold", 1.0)),
            "padj_threshold": float(payload.get("padj_threshold", 0.05)),
            "min_reads": int(payload.get("min_reads", 10)),
            "min_samples": int(payload.get("min_samples", 3)),
        }

    def _run_deseq2_analysis(
        self,
        matrix_path: Path,
        metadata_path: Path,
        output_prefix: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run DESeq2-like analysis using PyDESeq2."""
        try:
            from pydeseq2.dds import DeseqDataSet
            from pydeseq2.ds import DeseqStats
        except ImportError:
            raise DatasetValidationError(
                "PyDESeq2 not installed. Install with: pip install pydeseq2"
            )

        # 读取数据
        counts_df = pd.read_csv(matrix_path, index_col=0)
        metadata = pd.read_csv(metadata_path, index_col=0)

        # 确保样本顺序一致
        common_samples = counts_df.columns.intersection(metadata.index)
        if len(common_samples) == 0:
            raise DatasetValidationError("No common samples between counts and metadata")
        counts_df = counts_df[common_samples]
        metadata = metadata.loc[common_samples]

        # 过滤低表达基因
        min_reads = params["min_reads"]
        min_samples = params["min_samples"]
        keep = (counts_df >= min_reads).sum(axis=1) >= min_samples
        counts_df = counts_df[keep]

        # 确保 counts 是整数
        counts_df = counts_df.round().astype(int)

        # 创建 DESeq2 对象
        logger.info("Creating DESeqDataSet...")
        # PyDESeq2 要求 metadata 索引为样本名，行数等于样本数
        dds = DeseqDataSet(
            counts=counts_df.T,  # 转置：行为样本，列为基因
            metadata=metadata,
            design_factors=["group"],
        )

        # 运行 DESeq2 分析
        logger.info("Running DESeq2 analysis...")
        dds.deseq2()

        # 获取统计结果
        groups = metadata["group"].unique()
        # PyDESeq2 需要三元组: (factor_name, treatment, control)
        contrast = ("group", str(groups[1]), str(groups[0]))  # (因子名, 处理组, 对照组)
        logger.info(f"Creating DeseqStats with contrast: {contrast}")
        stat_res = DeseqStats(dds, contrast=contrast)
        stat_res.summary()

        # 获取结果
        results_df = stat_res.results_df
        results_df = results_df.reset_index().rename(columns={"index": "gene_id"})

        # 添加显著标记
        log2fc_thresh = params["log2fc_threshold"]
        padj_thresh = params["padj_threshold"]
        results_df["significant"] = (
            (abs(results_df["log2FoldChange"]) >= log2fc_thresh) &
            (results_df["padj"] <= padj_thresh) &
            results_df["padj"].notna()
        )

        # 统计上调/下调基因
        n_up = (results_df["significant"] & (results_df["log2FoldChange"] > 0)).sum()
        n_down = (results_df["significant"] & (results_df["log2FoldChange"] < 0)).sum()

        # 保存结果
        groups = metadata["group"].unique()
        deg_path = Path(f"{output_prefix}_deg_results.csv")
        results_df.to_csv(deg_path, index=False)

        # 火山图数据
        volcano_df = results_df[["gene_id", "log2FoldChange", "pvalue", "padj", "significant"]].copy()
        volcano_df["neg_log10_padj"] = -np.log10(volcano_df["padj"].fillna(1))
        volcano_path = Path(f"{output_prefix}_volcano.csv")
        volcano_df.to_csv(volcano_path, index=False)

        # 归一化后的表达矩阵（需要转置回来，因为之前转置了输入）
        normalized_counts = dds.layers["normed_counts"].T  # 转回基因×样本
        normalized_df = pd.DataFrame(normalized_counts, index=counts_df.index, columns=counts_df.columns)
        normalized_path = Path(f"{output_prefix}_normalized_counts.csv")
        normalized_df.to_csv(normalized_path)

        # 汇总 JSON
        summary = {
            "deg_total_genes": len(results_df),
            "deg_significant_total": int(n_up + n_down),
            "deg_up_regulated": int(n_up),
            "deg_down_regulated": int(n_down),
            "deg_log2fc_threshold": log2fc_thresh,
            "deg_padj_threshold": padj_thresh,
            "groups": list(groups),
            "group_sizes": {str(g): int((metadata["group"] == g).sum()) for g in groups},
            "method": "PyDESeq2",
        }
        summary_path = Path(f"{output_prefix}_summary.json")
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

        # 构建 metrics
        metrics = {
            "deg_matrix_path": str(deg_path),
            "deg_comparison_count": 1,
            "deg_significant_total": int(n_up + n_down),
            "deg_up_regulated": int(n_up),
            "deg_down_regulated": int(n_down),
            "deg_total_genes": len(results_df),
            "deg_log2fc_threshold": log2fc_thresh,
            "deg_padj_threshold": padj_thresh,
            "deg_method": "PyDESeq2",
            "deg_groups": list(groups),
            "deg_group_sizes": {str(g): int((metadata["group"] == g).sum()) for g in groups},
        }

        return {
            "deg_path": deg_path,
            "volcano_path": volcano_path,
            "normalized_path": normalized_path,
            "summary_path": summary_path,
            "deg_count": len(results_df),
            "normalized_count": len(normalized_df),
            "metrics": metrics,
        }