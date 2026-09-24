"""R-based differential expression analysis using limma.

This module provides a Python wrapper to call R's limma package,
which is the standard tool for microarray and normalized RNA-seq analysis.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping

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
class DifferentialAnalysisRModule(Module):
    """Differential expression analysis using R's limma package."""

    spec = ModuleSpec(
        name="differential_analysis_r",
        kind="analysis",
        deps=["data_processing"],
        description="Differential expression analysis using R's limma package (standard bioinformatics workflow).",
    )

    # 从项目根目录计算脚本路径
    R_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "run_deg_with_r.R"

    def run(self, context: RunContext, module_input: ModuleInput) -> ModuleOutput:
        upstream = module_input.deps.get("data_processing")
        if not upstream or not upstream.dataset:
            raise DatasetValidationError("differential_analysis_r requires data_processing output.")

        # 解析参数
        params = self._build_params(module_input.payload)
        matrix_path = self._resolve_matrix_path(upstream.metrics, upstream.dataset.primary_path)
        metadata_path = self._resolve_metadata_path(module_input.payload)

        # 创建输出目录
        out_dir = context.artifact_path(self.spec.name)
        out_dir.mkdir(parents=True, exist_ok=True)
        output_prefix = str(out_dir / "deg_limma")

        # 调用 R 脚本
        self._run_r_script(matrix_path, metadata_path, output_prefix, params)

        # 读取结果
        results = self._load_results(out_dir)

        # 构建输出
        tables = [
            TableRef(
                title="DEG Results (limma)",
                path=str(results["deg_path"].relative_to(context.workspace)),
                rows=results["deg_count"],
            )
        ]
        figures = [
            FigureRef(
                title="Volcano Plot Data",
                path=str(results["volcano_path"].relative_to(context.workspace)),
                caption="Volcano plot data from limma analysis.",
            )
        ]
        artifacts = ArtifactManifest(root=str(out_dir))
        artifacts.items.extend([
            ArtifactRef(path=str(results["deg_path"]), kind="table", description="DEG results from limma"),
            ArtifactRef(path=str(results["volcano_path"]), kind="figure_data", description="Volcano plot data"),
            ArtifactRef(path=str(results["summary_path"]), kind="summary", description="DEG summary"),
        ])

        summary = (
            f"Differential analysis with limma completed: {results['metrics']['deg_comparison_count']} comparison(s), "
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
            raise DatasetValidationError(f"normalized matrix path does not exist: {path}")
        return path

    @staticmethod
    def _resolve_metadata_path(payload: Mapping[str, Any]) -> Path:
        metadata_path = payload.get("metadata_path") or payload.get("metadata_file")
        if not metadata_path:
            raise DatasetValidationError("metadata_path is required for R-based analysis")
        path = Path(str(metadata_path))
        if not path.exists():
            raise DatasetValidationError(f"metadata_path does not exist: {path}")
        return path

    @staticmethod
    def _build_params(payload: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "log2fc_threshold": float(payload.get("log2fc_threshold", 1.0)),
            "padj_threshold": float(payload.get("padj_threshold", 0.05)),
        }

    def _run_r_script(
        self,
        matrix_path: Path,
        metadata_path: Path,
        output_prefix: str,
        params: Dict[str, Any],
    ) -> None:
        """Call the R script to run limma analysis."""
        if not self.R_SCRIPT_PATH.exists():
            raise DatasetValidationError(f"R script not found: {self.R_SCRIPT_PATH}")

        cmd = [
            "Rscript",
            str(self.R_SCRIPT_PATH),
            str(matrix_path),
            str(metadata_path),
            output_prefix,
            str(params["log2fc_threshold"]),
            str(params["padj_threshold"]),
        ]

        logger.info(f"Running R script: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )

        if result.stdout:
            logger.info(f"R script output:\n{result.stdout}")
        if result.stderr:
            logger.warning(f"R script warnings:\n{result.stderr}")

    def _load_results(self, out_dir: Path) -> Dict[str, Any]:
        """Load results generated by the R script."""
        deg_path = out_dir / "deg_limma_deg_results.csv"
        volcano_path = out_dir / "deg_limma_volcano.csv"
        summary_path = out_dir / "deg_limma_summary.json"

        if not deg_path.exists():
            raise DatasetValidationError(f"DEG results not found: {deg_path}")
        if not summary_path.exists():
            raise DatasetValidationError(f"Summary not found: {summary_path}")

        deg_df = pd.read_csv(deg_path)
        with open(summary_path, "r") as f:
            summary = json.load(f)

        # 构建 metrics
        metrics = {
            "deg_matrix_path": str(deg_path),
            "deg_comparison_count": 1,
            "deg_significant_total": summary["deg_significant_total"],
            "deg_up_regulated": summary["deg_up_regulated"],
            "deg_down_regulated": summary["deg_down_regulated"],
            "deg_total_genes": summary["deg_total_genes"],
            "deg_log2fc_threshold": summary["deg_log2fc_threshold"],
            "deg_padj_threshold": summary["deg_padj_threshold"],
            "deg_method": "limma",
            "deg_groups": summary["groups"],
            "deg_group_sizes": summary["group_sizes"],
        }

        return {
            "deg_path": deg_path,
            "volcano_path": volcano_path,
            "summary_path": summary_path,
            "deg_count": len(deg_df),
            "metrics": metrics,
        }