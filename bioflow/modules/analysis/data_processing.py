from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from bioflow.core.context import RunContext
from bioflow.core.models import (
    ArtifactManifest,
    ArtifactRef,
    DatasetSpec,
    DatasetType,
    DatasetValidationError,
    ModuleInput,
    ModuleOutput,
    ModuleSpec,
    ModuleStatus,
    RunProvenance,
)
from bioflow.modules.analysis.adapters import get_adapter_registry
from bioflow.modules.analysis.parsers import ExpressionMatrixParser
from bioflow.modules.analysis.pipelines import ExpressionMatrixPipeline
from bioflow.modules.base import Module
from bioflow.modules.catalog import register


def _validate_expression_matrix(dataset: DatasetSpec) -> List[str]:
    """Lightweight input validation for expression matrices.

    Returns a list of validation warnings (empty = clean).
    """
    warnings: List[str] = []
    if dataset.dataset_type != DatasetType.expression_matrix:
        return warnings
    path = Path(dataset.primary_path)
    if not path.exists():
        warnings.append(f"primary_path does not exist: {path}")
        return warnings
    if not path.is_file():
        warnings.append(f"primary_path is not a file: {path}")
        return warnings
    if path.stat().st_size == 0:
        warnings.append("input file is empty")
    return warnings


def _write_provenance(out_dir: Path, run_id: str, pipeline_id: str, params: Dict[str, Any]) -> Path:
    """Persist RunProvenance for the data_processing step."""
    prov = RunProvenance(
        runner="data_processing",
        pipeline=pipeline_id,
        params=params,
        started_at=datetime.now(timezone.utc).isoformat(),
        ended_at=datetime.now(timezone.utc).isoformat(),
        status="success",
    )
    prov_dir = out_dir / "provenance"
    prov_dir.mkdir(parents=True, exist_ok=True)
    prov_path = prov_dir / "data_processing.json"
    prov_path.write_text(prov.model_dump_json(indent=2), encoding="utf-8")
    return prov_path


def _merge_results(
    *,
    runner_metrics: Dict[str, Any],
    runner_tables: List[Any],
    runner_figures: List[Any],
    parser_metrics: Dict[str, Any],
    parser_tables: List[Any],
    parser_figures: List[Any],
    pipeline_metrics: Dict[str, Any],
    pipeline_tables: List[Any],
    pipeline_figures: List[Any],
    summary_path: str,
    pipeline_summary_path: str,
) -> Dict[str, Any]:
    """Merge metrics/tables/figures from all three sources.

    Priority: pipeline > parser > runner (last wins on key collision).
    """
    return {
        "metrics": {
            **runner_metrics,
            **parser_metrics,
            **pipeline_metrics,
            "summary_path": summary_path,
            "pipeline_summary_path": pipeline_summary_path,
        },
        "tables": list(runner_tables) + list(parser_tables) + list(pipeline_tables),
        "figures": list(runner_figures) + list(parser_figures) + list(pipeline_figures),
    }


def _build_summary(
    dataset: DatasetSpec,
    pipeline_id: str,
    metrics: Dict[str, Any],
) -> str:
    """Build a human-readable one-line summary for the module output."""
    norm_method = metrics.get("normalization_method", "none")
    genes_after_qc = metrics.get("qc_genes_after_qc", "?")
    samples_after_qc = metrics.get("qc_samples_after_qc", "?")
    pca_var = metrics.get("pca_variance_ratio", [])
    pca_str = f"PC1+PC2={sum(pca_var[:2]):.2%}" if len(pca_var) >= 2 else ""
    return (
        f"Detected {dataset.dataset_type.value}, "
        f"executed {pipeline_id}. "
        f"QC: {genes_after_qc} genes × {samples_after_qc} samples retained. "
        f"Normalisation: {norm_method}. "
        f"PCA: {pca_str}."
    )


@register
class DataProcessingModule(Module):
    spec = ModuleSpec(
        name="data_processing",
        kind="analysis",
        deps=[],
        description="QC, normalisation and PCA for expression-matrix data.",
    )

    # ------------------------------------------------------------------
    #  main entry point
    # ------------------------------------------------------------------
    def run(self, context: RunContext, module_input: ModuleInput) -> ModuleOutput:
        dataset_path = module_input.payload.get("dataset_path", "")
        if not dataset_path:
            raise DatasetValidationError("dataset_path is required for data processing")

        # ---- detect & validate ----
        registry = get_adapter_registry()
        detection = registry.detect(dataset_path, module_input.payload)
        dataset = detection.dataset
        pipeline_spec = detection.pipeline
        params = pipeline_spec.params

        validation_warnings = _validate_expression_matrix(dataset)

        runner_mode = pipeline_spec.runner_mode
        runner = context.runners.get(runner_mode)
        if runner is None:
            raise ValueError(f"runner not configured: {runner_mode}")

        work_dir = context.cache_dir / pipeline_spec.pipeline_id
        out_dir = context.artifacts_dir / "data_processing"
        work_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)

        # ---- 1) Runner (mock / nextflow) ----
        result_bundle = runner.run(pipeline_spec.pipeline_id, params, work_dir, out_dir)

        # ---- 2) Pipeline – QC + normalisation + PCA ----
        pipeline_params = {
            **params,
            "normalization_method": module_input.payload.get("normalization_method"),
            "data_kind": module_input.payload.get("data_kind"),
        }
        pipeline = ExpressionMatrixPipeline()
        pipeline_result = pipeline.run(
            dataset=dataset,
            params=pipeline_params,
            out_dir=out_dir,
        )
        pipeline_summary_path = pipeline.write_summary(out_dir, pipeline_result)

        # ---- 3) Parser – raw-matrix descriptive summary ----
        parser = ExpressionMatrixParser()
        parsed_result = parser.parse(dataset)
        summary_path = parser.write_summary(out_dir, parsed_result)

        # ---- assemble artifacts ----
        artifacts = ArtifactManifest(root=str(out_dir))
        artifacts.items.append(
            ArtifactRef(
                path=str(Path(dataset.primary_path)),
                kind="input",
                description=f"Detected {dataset.dataset_type.value} input",
            )
        )
        artifacts.items.append(
            ArtifactRef(path=str(summary_path), kind="summary", description="Raw matrix summary")
        )
        artifacts.items.append(
            ArtifactRef(path=str(pipeline_summary_path), kind="summary",
                       description="QC + normalisation + PCA pipeline summary")
        )

        # ---- provenance ----
        prov_path = _write_provenance(out_dir, context.run_id, pipeline_spec.pipeline_id, pipeline_params)
        artifacts.items.append(
            ArtifactRef(path=str(prov_path), kind="provenance", description="Run provenance")
        )

        # ---- merge ----
        merged = _merge_results(
            runner_metrics=result_bundle.metrics,
            runner_tables=result_bundle.tables,
            runner_figures=result_bundle.figures,
            parser_metrics=parsed_result.metrics,
            parser_tables=parsed_result.tables,
            parser_figures=parsed_result.figures,
            pipeline_metrics=pipeline_result.metrics,
            pipeline_tables=pipeline_result.tables,
            pipeline_figures=pipeline_result.figures,
            summary_path=str(summary_path),
            pipeline_summary_path=str(pipeline_summary_path),
        )

        # ---- warnings & summary ----
        warnings = [f"dataset confidence={dataset.confidence:.2f}"]
        warnings.extend(validation_warnings)
        warnings.extend(parsed_result.notes)
        warnings.extend(pipeline_result.notes)

        summary = _build_summary(dataset, pipeline_spec.pipeline_id, merged["metrics"])

        return ModuleOutput(
            module=self.spec.name,
            status=ModuleStatus.succeeded,
            summary=summary,
            artifacts=artifacts,
            dataset=dataset,
            metrics=merged["metrics"],
            tables=merged["tables"],
            figures=merged["figures"],
            warnings=warnings,
            evidence=[],
        )
