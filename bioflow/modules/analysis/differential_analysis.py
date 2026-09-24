from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Iterable as IterableABC
from collections.abc import Sequence as SequenceABC
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

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
    RunProvenance,
    TableRef,
)
from bioflow.modules.base import Module
from bioflow.modules.catalog import register

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeneResult:
    gene_id: str
    base_mean: float
    mean_a: float
    mean_b: float
    log2_fold_change: float
    pvalue: float
    padj: float
    pvalue_wilcoxon: float | None
    padj_wilcoxon: float | None
    significant: bool


@dataclass(frozen=True)
class ComparisonResult:
    group_a: str
    group_b: str
    gene_results: List[GeneResult]
    n_total: int
    n_significant: int
    n_up: int
    n_down: int
    group_sizes: Dict[str, int]


def _clean_name(value: Any) -> str:
    text = str(value).strip()
    text = re.sub(r"\s+", "_", text)
    return text


def _safe_file_part(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return text.strip("_") or "group"


class GroupResolver:
    """Resolve sample-to-group assignments for a normalized expression matrix."""

    sample_column_candidates = ("sample_id", "sample", "sampleid", "id", "sample_name")
    group_column_candidates = ("group", "condition", "treatment", "sample_group", "phenotype", "class")

    def resolve(
        self,
        sample_ids: Sequence[str],
        payload: Mapping[str, Any],
        *,
        min_samples_per_group: int = 2,
    ) -> Dict[str, str]:
        sample_ids = [str(sample_id) for sample_id in sample_ids]
        metadata_path = payload.get("metadata_path") or payload.get("metadata_file")

        if metadata_path:
            groups = self.from_metadata_file(str(metadata_path))
        else:
            groups = self.from_payload(payload)
            if not groups:
                groups = self.from_group_names(sample_ids, payload)
            if not groups:
                groups = self.auto_detect(sample_ids)

        return self.validate(sample_ids, groups, min_samples_per_group=min_samples_per_group)

    @classmethod
    def from_metadata_file(cls, path: str) -> Dict[str, str]:
        metadata_path = Path(path)
        if not metadata_path.exists():
            raise DatasetValidationError(f"metadata_path does not exist: {metadata_path}")

        separator = "\t" if metadata_path.suffix.lower() in {".tsv", ".txt"} else ","
        frame = pd.read_csv(metadata_path, sep=separator)
        if frame.empty:
            raise DatasetValidationError(f"metadata file is empty: {metadata_path}")

        normalized_columns = {str(column).strip().lower(): column for column in frame.columns}
        sample_col = cls._find_column(normalized_columns, cls.sample_column_candidates)
        group_col = cls._find_column(normalized_columns, cls.group_column_candidates)
        if sample_col is None or group_col is None:
            raise DatasetValidationError(
                "metadata file must contain a sample column and a group/condition column"
            )

        groups: Dict[str, str] = {}
        for _, row in frame[[sample_col, group_col]].dropna().iterrows():
            sample_id = str(row[sample_col]).strip()
            group = _clean_name(row[group_col])
            if sample_id and group:
                groups[sample_id] = group
        return groups

    @staticmethod
    def from_payload(payload: Mapping[str, Any]) -> Dict[str, str]:
        raw_groups = payload.get("groups")
        if not isinstance(raw_groups, Mapping):
            return {}

        groups: Dict[str, str] = {}
        for group_name, samples in raw_groups.items():
            clean_group = _clean_name(group_name)
            if isinstance(samples, str):
                sample_list: Iterable[Any] = [samples]
            elif isinstance(samples, IterableABC):
                sample_list = samples
            else:
                continue
            for sample_id in sample_list:
                groups[str(sample_id)] = clean_group
        return groups

    @staticmethod
    def from_group_names(sample_ids: Sequence[str], payload: Mapping[str, Any]) -> Dict[str, str]:
        group_a = payload.get("group_a")
        group_b = payload.get("group_b")
        if not group_a or not group_b:
            return {}

        group_a_text = _clean_name(group_a)
        group_b_text = _clean_name(group_b)
        groups: Dict[str, str] = {}
        for sample_id in sample_ids:
            sample_text = sample_id.lower()
            in_a = group_a_text.lower() in sample_text
            in_b = group_b_text.lower() in sample_text
            if in_a and not in_b:
                groups[sample_id] = group_a_text
            elif in_b and not in_a:
                groups[sample_id] = group_b_text
        return groups

    @staticmethod
    def auto_detect(sample_ids: Sequence[str]) -> Dict[str, str]:
        groups: Dict[str, str] = {}
        for sample_id in sample_ids:
            # 支持空格、下划线、点、连字符分隔
            parts = re.split(r"[\s_.-]+", str(sample_id), maxsplit=1)
            if len(parts) < 2 or not parts[0]:
                return {}
            groups[str(sample_id)] = _clean_name(parts[0])
        return groups

    @staticmethod
    def validate(
        sample_ids: Sequence[str],
        groups: Mapping[str, str],
        *,
        min_samples_per_group: int = 2,
    ) -> Dict[str, str]:
        if not groups:
            raise DatasetValidationError(
                "Could not resolve sample groups. Provide metadata_path, groups, or group_a/group_b."
            )

        resolved: Dict[str, str] = {}
        missing: List[str] = []
        for sample_id in sample_ids:
            sample_key = str(sample_id)
            if sample_key not in groups:
                missing.append(sample_key)
            else:
                resolved[sample_key] = _clean_name(groups[sample_key])
        if missing:
            raise DatasetValidationError(
                "Group assignments are missing for samples: " + ", ".join(missing[:10])
            )

        counts: Dict[str, int] = {}
        for group in resolved.values():
            counts[group] = counts.get(group, 0) + 1
        valid_groups = {group: count for group, count in counts.items() if count >= min_samples_per_group}
        if len(valid_groups) < 2:
            raise DatasetValidationError(
                f"Need at least two groups with >= {min_samples_per_group} samples each."
            )
        return resolved

    @staticmethod
    def _find_column(columns: Mapping[str, Any], candidates: Sequence[str]) -> Any | None:
        for candidate in candidates:
            if candidate.lower() in columns:
                return columns[candidate.lower()]
        return None


class DifferentialAnalyzer:
    """Pure computation layer for differential expression analysis."""

    def run_comparison(
        self,
        matrix: pd.DataFrame,
        groups: Mapping[str, str],
        comparison: Tuple[str, str],
        params: Mapping[str, Any],
    ) -> ComparisonResult:
        group_a, group_b = comparison
        samples_a = [sample for sample, group in groups.items() if group == group_a and sample in matrix.columns]
        samples_b = [sample for sample, group in groups.items() if group == group_b and sample in matrix.columns]
        min_samples = int(params.get("min_samples_per_group", 2))
        if len(samples_a) < min_samples or len(samples_b) < min_samples:
            raise DatasetValidationError(
                f"Comparison {group_a} vs {group_b} does not meet min_samples_per_group={min_samples}"
            )

        results: List[GeneResult] = []
        for gene_id, row in matrix.iterrows():
            vals_a = pd.to_numeric(row[samples_a], errors="coerce").dropna()
            vals_b = pd.to_numeric(row[samples_b], errors="coerce").dropna()
            gene_result = self._compute_gene(vals_a, vals_b, str(gene_id), params)
            if gene_result is not None:
                results.append(gene_result)

        if not results:
            raise DatasetValidationError(f"Comparison {group_a} vs {group_b} produced no testable genes.")

        results = self._multiple_test_correction(results, params)
        log2fc_threshold = float(params.get("log2fc_threshold", 1.0))
        padj_threshold = float(params.get("padj_threshold", 0.05))
        finalized: List[GeneResult] = []
        for result in results:
            significant = (
                abs(result.log2_fold_change) >= log2fc_threshold
                and math.isfinite(result.padj)
                and result.padj <= padj_threshold
            )
            finalized.append(replace(result, significant=significant))

        n_up = sum(1 for result in finalized if result.significant and result.log2_fold_change > 0)
        n_down = sum(1 for result in finalized if result.significant and result.log2_fold_change < 0)
        return ComparisonResult(
            group_a=group_a,
            group_b=group_b,
            gene_results=finalized,
            n_total=len(finalized),
            n_significant=n_up + n_down,
            n_up=n_up,
            n_down=n_down,
            group_sizes={group_a: len(samples_a), group_b: len(samples_b)},
        )

    def _compute_gene(
        self,
        vals_a: pd.Series,
        vals_b: pd.Series,
        gene_id: str,
        params: Mapping[str, Any],
    ) -> GeneResult | None:
        min_samples = int(params.get("min_samples_per_group", 2))
        if len(vals_a) < min_samples or len(vals_b) < min_samples:
            return None

        a = vals_a.astype(float).to_numpy()
        b = vals_b.astype(float).to_numpy()
        mean_a = float(np.mean(a))
        mean_b = float(np.mean(b))
        pseudocount = float(params.get("pseudocount", 1e-6))
        log2fc = float(np.log2((mean_b + pseudocount) / (mean_a + pseudocount)))
        pvalue = self._welch_pvalue(a, b)

        method = str(params.get("method", "auto")).lower()
        pvalue_wilcoxon = None
        if method in {"wilcoxon", "both"} or (method == "auto" and len(a) >= 4 and len(b) >= 4):
            pvalue_wilcoxon = self._mann_whitney_pvalue(a, b)

        return GeneResult(
            gene_id=gene_id,
            base_mean=(mean_a + mean_b) / 2,
            mean_a=mean_a,
            mean_b=mean_b,
            log2_fold_change=log2fc,
            pvalue=pvalue,
            padj=math.nan,
            pvalue_wilcoxon=pvalue_wilcoxon,
            padj_wilcoxon=None,
            significant=False,
        )

    @staticmethod
    def _welch_pvalue(vals_a: np.ndarray, vals_b: np.ndarray) -> float:
        try:
            from scipy.stats import ttest_ind  # type: ignore

            _stat, pvalue = ttest_ind(vals_a, vals_b, equal_var=False, nan_policy="omit")
            return _finite_pvalue(pvalue)
        except Exception:  # noqa: BLE001
            mean_a, mean_b = float(np.mean(vals_a)), float(np.mean(vals_b))
            var_a = float(np.var(vals_a, ddof=1)) if len(vals_a) > 1 else 0.0
            var_b = float(np.var(vals_b, ddof=1)) if len(vals_b) > 1 else 0.0
            se = math.sqrt((var_a / len(vals_a)) + (var_b / len(vals_b)))
            if se == 0:
                return 1.0 if math.isclose(mean_a, mean_b) else 0.0
            z_score = abs((mean_b - mean_a) / se)
            return _finite_pvalue(math.erfc(z_score / math.sqrt(2.0)))

    @staticmethod
    def _mann_whitney_pvalue(vals_a: np.ndarray, vals_b: np.ndarray) -> float | None:
        try:
            from scipy.stats import mannwhitneyu  # type: ignore

            _stat, pvalue = mannwhitneyu(vals_a, vals_b, alternative="two-sided")
            return _finite_pvalue(pvalue)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _multiple_test_correction(
        results: Sequence[GeneResult],
        params: Mapping[str, Any],
    ) -> List[GeneResult]:
        pvalues = [result.pvalue for result in results]
        padj_values = _benjamini_hochberg(pvalues)

        wilcoxon_pvalues = [
            result.pvalue_wilcoxon if result.pvalue_wilcoxon is not None else math.nan
            for result in results
        ]
        wilcoxon_padj = _benjamini_hochberg(wilcoxon_pvalues)
        method = str(params.get("method", "auto")).lower()

        corrected: List[GeneResult] = []
        for result, padj, padj_w in zip(results, padj_values, wilcoxon_padj):
            corrected.append(
                replace(
                    result,
                    padj=padj,
                    padj_wilcoxon=padj_w if method in {"wilcoxon", "both", "auto"} and math.isfinite(padj_w) else None,
                )
            )
        return corrected

    @staticmethod
    def to_dataframe(result: ComparisonResult) -> pd.DataFrame:
        rows = [
            {
                "gene_id": gene.gene_id,
                "baseMean": gene.base_mean,
                "mean_A": gene.mean_a,
                "mean_B": gene.mean_b,
                "log2FoldChange": gene.log2_fold_change,
                "pvalue": gene.pvalue,
                "padj": gene.padj,
                "pvalue_wilcoxon": gene.pvalue_wilcoxon,
                "padj_wilcoxon": gene.padj_wilcoxon,
                "significant": gene.significant,
            }
            for gene in result.gene_results
        ]
        frame = pd.DataFrame(rows)
        return frame.sort_values(["padj", "pvalue", "gene_id"], na_position="last").reset_index(drop=True)


@register
class DifferentialAnalysisModule(Module):
    spec = ModuleSpec(
        name="differential_analysis",
        kind="analysis",
        deps=["data_processing"],
        description="Differential expression analysis from normalized expression matrices.",
    )

    def run(self, context: RunContext, module_input: ModuleInput) -> ModuleOutput:
        upstream = module_input.deps.get("data_processing")
        if not upstream or not upstream.dataset:
            raise DatasetValidationError("differential_analysis requires data_processing output.")

        matrix_path = self._resolve_matrix_path(upstream.metrics, upstream.dataset.primary_path)
        matrix = self._load_matrix(matrix_path)

        params = self._build_params(module_input.payload)
        resolver = GroupResolver()
        groups = resolver.resolve(
            sample_ids=matrix.columns.tolist(),
            payload=module_input.payload,
            min_samples_per_group=int(params["min_samples_per_group"]),
        )
        comparisons = self._resolve_comparisons(groups, module_input.payload)

        analyzer = DifferentialAnalyzer()
        out_dir = context.artifact_path(self.spec.name)
        out_dir.mkdir(parents=True, exist_ok=True)

        tables: List[TableRef] = []
        figures: List[FigureRef] = []
        artifacts = ArtifactManifest(root=str(out_dir))
        all_results: Dict[str, ComparisonResult] = {}

        for comparison in comparisons:
            result = analyzer.run_comparison(matrix, groups, comparison, params)
            comparison_name = f"{_safe_file_part(result.group_a)}_vs_{_safe_file_part(result.group_b)}"
            table_path = out_dir / f"deg_{comparison_name}.csv"
            frame = analyzer.to_dataframe(result)
            frame.to_csv(table_path, index=False)

            volcano_path = out_dir / f"volcano_{comparison_name}.csv"
            self._write_volcano_data(frame, volcano_path)

            all_results[comparison_name] = result
            tables.append(TableRef(title=f"DEG: {result.group_a} vs {result.group_b}", path=str(table_path), rows=len(frame)))
            figures.append(
                FigureRef(
                    title=f"Volcano data: {result.group_a} vs {result.group_b}",
                    path=str(volcano_path),
                    caption="CSV data for volcano plot rendering.",
                )
            )
            artifacts.items.append(
                ArtifactRef(path=str(table_path), kind="table", description=f"DEG results for {result.group_a} vs {result.group_b}")
            )
            artifacts.items.append(
                ArtifactRef(path=str(volcano_path), kind="figure_data", description=f"Volcano plot data for {result.group_a} vs {result.group_b}")
            )

        summary_path = out_dir / "deg_summary.json"
        metrics = self._build_summary_metrics(all_results, params, matrix_path)
        summary_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts.items.append(ArtifactRef(path=str(summary_path), kind="summary", description="DEG summary metrics"))

        prov_path = self._write_provenance(out_dir, context.run_id, params, matrix_path)
        artifacts.items.append(ArtifactRef(path=str(prov_path), kind="provenance", description="Differential-analysis provenance"))

        total_significant = sum(result.n_significant for result in all_results.values())
        summary = (
            f"Differential analysis completed: {len(all_results)} comparison(s), "
            f"{total_significant} significant DEG(s)."
        )
        logger.info(summary)

        return ModuleOutput(
            module=self.spec.name,
            status=ModuleStatus.succeeded,
            summary=summary,
            artifacts=artifacts,
            dataset=upstream.dataset,
            metrics=metrics,
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
    def _load_matrix(path: Path) -> pd.DataFrame:
        frame = pd.read_csv(path, index_col=0)
        if frame.empty or frame.shape[1] < 2:
            raise DatasetValidationError("normalized matrix must contain at least two samples.")
        numeric = frame.apply(pd.to_numeric, errors="coerce")
        numeric = numeric.dropna(how="all")
        if numeric.empty:
            raise DatasetValidationError("normalized matrix contains no numeric expression values.")
        numeric.columns = [str(column) for column in numeric.columns]
        numeric.index = [str(index) for index in numeric.index]
        return numeric

    @staticmethod
    def _build_params(payload: Mapping[str, Any]) -> Dict[str, Any]:
        method = str(payload.get("method", "auto")).lower()
        valid_methods = {"auto", "ttest", "wilcoxon", "both"}
        if method not in valid_methods:
            raise DatasetValidationError(f"Unsupported differential-analysis method: {method}")

        return {
            "log2fc_threshold": float(payload.get("log2fc_threshold", 1.0)),
            "padj_threshold": float(payload.get("padj_threshold", 0.05)),
            "min_samples_per_group": int(payload.get("min_samples_per_group", 2)),
            "method": method,
            "pseudocount": float(payload.get("pseudocount", 1e-6)),
        }

    @staticmethod
    def _resolve_comparisons(
        groups: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> List[Tuple[str, str]]:
        available = sorted(set(groups.values()))
        requested = payload.get("comparisons")
        if requested:
            comparisons: List[Tuple[str, str]] = []
            for item in requested:
                if not isinstance(item, SequenceABC) or isinstance(item, str) or len(item) != 2:
                    raise DatasetValidationError("comparisons must be a list of [group_a, group_b] pairs.")
                group_a, group_b = _clean_name(item[0]), _clean_name(item[1])
                if group_a not in available or group_b not in available:
                    raise DatasetValidationError(f"Unknown comparison group: {group_a} vs {group_b}")
                if group_a != group_b:
                    comparisons.append((group_a, group_b))
            if comparisons:
                return comparisons

        if len(available) < 2:
            raise DatasetValidationError("At least two groups are required for differential analysis.")
        return [(group_a, group_b) for group_a, group_b in combinations(available, 2)]

    @staticmethod
    def _write_volcano_data(frame: pd.DataFrame, path: Path) -> None:
        volcano = frame[["gene_id", "log2FoldChange", "pvalue", "padj", "significant"]].copy()
        volcano["neg_log10_padj"] = volcano["padj"].apply(
            lambda value: -math.log10(value) if isinstance(value, (int, float)) and value > 0 else math.nan
        )
        volcano.to_csv(path, index=False)

    @staticmethod
    def _build_summary_metrics(
        results: Mapping[str, ComparisonResult],
        params: Mapping[str, Any],
        matrix_path: Path,
    ) -> Dict[str, Any]:
        comparisons = {
            name: {
                "group_a": result.group_a,
                "group_b": result.group_b,
                "deg_total_genes": result.n_total,
                "deg_significant_total": result.n_significant,
                "deg_up_regulated": result.n_up,
                "deg_down_regulated": result.n_down,
                "deg_group_sizes": result.group_sizes,
            }
            for name, result in results.items()
        }
        return {
            "deg_matrix_path": str(matrix_path),
            "deg_comparison_count": len(results),
            "deg_significant_total": sum(result.n_significant for result in results.values()),
            "deg_up_regulated": sum(result.n_up for result in results.values()),
            "deg_down_regulated": sum(result.n_down for result in results.values()),
            "deg_total_genes": sum(result.n_total for result in results.values()),
            "deg_log2fc_threshold": params["log2fc_threshold"],
            "deg_padj_threshold": params["padj_threshold"],
            "deg_method": params["method"],
            "deg_pseudocount": params["pseudocount"],
            "deg_comparisons": comparisons,
        }

    @staticmethod
    def _write_provenance(
        out_dir: Path,
        run_id: str,
        params: Mapping[str, Any],
        matrix_path: Path,
    ) -> Path:
        provenance = RunProvenance(
            runner="differential_analysis",
            pipeline="welch_ttest_bh_fdr",
            params={**dict(params), "matrix_path": str(matrix_path)},
            started_at=datetime.now(timezone.utc).isoformat(),
            ended_at=datetime.now(timezone.utc).isoformat(),
            status="success",
        )
        prov_dir = out_dir / "provenance"
        prov_dir.mkdir(parents=True, exist_ok=True)
        prov_path = prov_dir / "differential_analysis.json"
        prov_path.write_text(provenance.model_dump_json(indent=2), encoding="utf-8")
        return prov_path


def _finite_pvalue(value: Any) -> float:
    try:
        pvalue = float(value)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(pvalue):
        return 1.0
    return min(1.0, max(0.0, pvalue))


def _benjamini_hochberg(pvalues: Sequence[float]) -> List[float]:
    valid = [(index, _finite_pvalue(pvalue)) for index, pvalue in enumerate(pvalues) if math.isfinite(float(pvalue))]
    adjusted = [math.nan] * len(pvalues)
    if not valid:
        return adjusted

    valid.sort(key=lambda item: item[1], reverse=True)
    m = len(valid)
    running_min = 1.0
    for rank_from_largest, (index, pvalue) in enumerate(valid, start=1):
        rank = m - rank_from_largest + 1
        running_min = min(running_min, pvalue * m / rank)
        adjusted[index] = min(1.0, running_min)
    return adjusted