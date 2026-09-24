from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from bioflow.core.models import DatasetSpec, DatasetType, DataKind, DataKindDetection, NormalizationMethod, QCReport


def _flatten_numeric_values(frame: pd.DataFrame) -> pd.Series:
    """Return all matrix values as a 1-D numeric series without missing values."""
    values = pd.Series(frame.to_numpy().ravel())
    return pd.to_numeric(values, errors="coerce").dropna()


@dataclass
class PipelineResult:
    metrics: Dict[str, Any] = field(default_factory=dict)
    tables: List[Dict[str, Any]] = field(default_factory=list)
    figures: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    normalized_frame: pd.DataFrame | None = None
    pca_coords: pd.DataFrame | None = None


class ExpressionMatrixPipeline:
    """Run QC + normalization + PCA on an expression matrix in pure Python."""

    # ------------------------------------------------------------------
    #  data-kind detection
    # ------------------------------------------------------------------
    @staticmethod
    def detect_data_kind(frame: pd.DataFrame, file_path: str | Path = "") -> DataKindDetection:
        """Auto-detect data kind and pre-normalization status from file name + numeric features.

        Detection strategy (two layers):
          1. File-name heuristics: keywords like "Normalized", "Raw", "Counts" etc.
          2. Numeric heuristics: integer vs float, value range, negative values, etc.

        Returns a DataKindDetection with kind, pre_normalized flag, confidence and reasons.
        """
        reasons: list[str] = []
        file_path = Path(file_path) if file_path else Path()
        file_stem = file_path.stem.lower() if file_path.name else ""

        # --- Layer 1: file-name heuristics ---
        name_hints: dict[str, str] = {}  # hint → source

        if "normalized" in file_stem or "norm" in file_stem:
            name_hints["pre_normalized"] = "文件名含 'normalized/norm'"
        if "raw" in file_stem or "counts" in file_stem:
            name_hints["raw_counts"] = "文件名含 'raw/counts'"
        if "tpm" in file_stem:
            name_hints["tpm"] = "文件名含 'tpm'"
        if "fpkm" in file_stem:
            name_hints["fpkm"] = "文件名含 'fpkm'"
        if "rma" in file_stem or "microarray" in file_stem:
            name_hints["microarray"] = "文件名含 'rma/microarray'"

        # --- Layer 2: numeric heuristics ---
        numeric_hints: dict[str, str] = {}
        values = _flatten_numeric_values(frame)
        if values.empty:
            return DataKindDetection(data_kind=DataKind.counts, confidence=0.0, reasons=["空矩阵"])

        all_integer = bool(np.allclose(values.values, values.values.astype(int)))
        has_negative = bool((values < 0).any())
        mn, mx = float(values.min()), float(values.max())
        mean_val = float(values.mean())

        if all_integer and mn >= 0:
            numeric_hints["integer_nonneg"] = "全部为非负整数，符合 raw counts 特征"
        if not all_integer and mn >= 0 and mx < 200:
            numeric_hints["small_continuous"] = "非负连续小值，符合已归一化特征 (TPM/FPKM/RMA)"
        if has_negative:
            numeric_hints["has_negative"] = "含负值，符合 z-score 或部分芯片归一化特征"
        if mn >= 0 and mx > 1000 and all_integer:
            numeric_hints["large_integer"] = "大值整数，符合 RNA-seq raw counts"
        if mn >= 0 and mx < 50 and not all_integer and mean_val < 10:
            numeric_hints["tpm_like"] = "非负连续、值域小、均值低，符合 TPM/FPKM"

        # --- Combine heuristics ---
        pre_normalized = False
        data_kind = DataKind.counts
        confidence = 0.0

        # Rule 1: file name says normalized → trust it
        if "pre_normalized" in name_hints:
            pre_normalized = True
            reasons.append(name_hints["pre_normalized"])

        # Rule 2: file name says raw counts → trust it
        if "raw_counts" in name_hints:
            data_kind = DataKind.counts
            confidence = 0.8
            reasons.append(name_hints["raw_counts"])

        # Rule 3: file name says tpm/fpkm/microarray
        if "tpm" in name_hints:
            data_kind = DataKind.tpm
            pre_normalized = True
            confidence = 0.8
            reasons.append(name_hints["tpm"])
        if "fpkm" in name_hints:
            data_kind = DataKind.fpkm
            pre_normalized = True
            confidence = 0.8
            reasons.append(name_hints["fpkm"])
        if "microarray" in name_hints:
            data_kind = DataKind.microarray
            pre_normalized = True
            confidence = 0.7
            reasons.append(name_hints["microarray"])

        # Rule 4: numeric features override when confidence is low
        if confidence < 0.5:
            if "has_negative" in numeric_hints:
                data_kind = DataKind.microarray
                pre_normalized = True
                confidence = 0.6
                reasons.append(numeric_hints["has_negative"])
            elif "integer_nonneg" in numeric_hints:
                data_kind = DataKind.counts
                pre_normalized = False
                confidence = 0.7
                reasons.append(numeric_hints["integer_nonneg"])
            elif "tpm_like" in numeric_hints:
                data_kind = DataKind.tpm
                pre_normalized = True
                confidence = 0.6
                reasons.append(numeric_hints["tpm_like"])
            elif "small_continuous" in numeric_hints:
                data_kind = DataKind.relative_abundance
                pre_normalized = True
                confidence = 0.5
                reasons.append(numeric_hints["small_continuous"])
            elif "large_integer" in numeric_hints:
                data_kind = DataKind.counts
                pre_normalized = False
                confidence = 0.7
                reasons.append(numeric_hints["large_integer"])

        # Rule 5: if file name says normalized but numeric says integer counts,
        # the "normalized" might refer to something else (e.g. per-sample scaling)
        # In this case, trust numeric features more
        if pre_normalized and "integer_nonneg" in numeric_hints and "pre_normalized" in name_hints:
            pre_normalized = False
            confidence = 0.6
            reasons.append("文件名含 'normalized' 但数值为整数，判定为 raw counts")

        return DataKindDetection(
            data_kind=data_kind,
            pre_normalized=pre_normalized,
            confidence=round(confidence, 2),
            reasons=reasons,
        )

    # ------------------------------------------------------------------
    #  loading
    # ------------------------------------------------------------------
    def _load_numeric_frame(self, dataset: DatasetSpec) -> pd.DataFrame:
        path = Path(dataset.primary_path)
        suffix = path.suffix.lower()
        meta = dataset.metadata if isinstance(dataset.metadata, dict) else {}
        separator = meta.get("separator")
        sheet_name = meta.get("sheet_name")
        if suffix in {".xlsx", ".xls"}:
            frame = pd.read_excel(path, sheet_name=sheet_name)
        else:
            frame = pd.read_csv(path, sep=separator or "\t")

        feature_col = frame.columns[0]
        features = frame[feature_col].astype(str)
        numeric_frame = frame.drop(columns=[feature_col], errors="ignore")
        numeric_frame = numeric_frame.apply(pd.to_numeric, errors="coerce")
        numeric_frame.index = features
        return numeric_frame

    # ------------------------------------------------------------------
    #  QC
    # ------------------------------------------------------------------
    def _qc_filter(
        self,
        frame: pd.DataFrame,
        min_samples_pct: float = 0.2,
        min_mean_expression: float | None = None,
    ) -> Tuple[pd.DataFrame, QCReport]:
        """Filter low-quality genes and flag outlier samples.

        Returns (filtered_frame, qc_report).
        """
        n_genes_before, n_samples = frame.shape
        outlier_samples: List[str] = []
        corr_mean: float | None = None
        corr_min: float | None = None
        corr_range: List[float] | None = None

        # --- 1) filter genes with too few detected values ---
        detected = (frame > 0).sum(axis=1)
        keep_detect = detected >= (n_samples * min_samples_pct)
        genes_filtered_low_detect = int((~keep_detect).sum())
        frame = frame.loc[keep_detect]

        # --- 2) filter genes with very low mean expression ---
        genes_filtered_low_mean = 0
        if min_mean_expression is not None:
            gene_means = frame.mean(axis=1)
            keep_mean = gene_means >= min_mean_expression
            genes_filtered_low_mean = int((~keep_mean).sum())
            frame = frame.loc[keep_mean]

        # --- 3) sample outlier detection via correlation ---
        if frame.shape[1] >= 3:
            corr = frame.corr(method="pearson")
            mean_corr = corr.mean(axis=0)
            q1, q3 = mean_corr.quantile(0.25), mean_corr.quantile(0.75)
            iqr = q3 - q1
            lower = q1 - 1.5 * iqr
            outlier_series = mean_corr[mean_corr < lower]
            outlier_samples = outlier_series.index.tolist()
            corr_mean = round(float(mean_corr.mean()), 4)
            corr_min = round(float(mean_corr.min()), 4)
            corr_range = [round(float(mean_corr.min()), 4), round(float(mean_corr.max()), 4)]

            # 剔除离群样本
            if outlier_samples:
                frame = frame.drop(columns=outlier_samples)

        report = QCReport(
            genes_before_qc=n_genes_before,
            samples_before_qc=n_samples,
            genes_filtered_low_detect=genes_filtered_low_detect,
            genes_filtered_low_mean=genes_filtered_low_mean,
            outlier_samples=outlier_samples,
            sample_corr_mean=corr_mean,
            sample_corr_min=corr_min,
            sample_corr_range=corr_range,
            genes_after_qc=int(frame.shape[0]),
            samples_after_qc=int(frame.shape[1]),
        )
        return frame, report

    # ------------------------------------------------------------------
    #  normalization
    # ------------------------------------------------------------------
    def _suggest_method(self, frame: pd.DataFrame, data_kind: str | None = None, pre_normalized: bool = False) -> str:
        """Suggest a normalisation method based on value range and explicit *data_kind*.

        *data_kind* disambiguates raw-count matrices from already-normalised ones.
        Only ``counts`` is eligible for library-size scaling (CPM).
        If *pre_normalized* is True, skip library-size scaling entirely.
        """
        if pre_normalized:
            # Data already normalized — only safe transforms
            values = _flatten_numeric_values(frame)
            if not values.empty and float(values.min()) < 0:
                return NormalizationMethod.zscore.value
            return NormalizationMethod.log1p.value

        values = _flatten_numeric_values(frame)
        if values.empty:
            return NormalizationMethod.log1p.value
        mn, mx = float(values.min()), float(values.max())

        # explicit data-kind contract – only raw counts may receive CPM
        if data_kind == "counts":
            if mn >= 0 and mx > 1000:
                return NormalizationMethod.cpm.value
            return NormalizationMethod.log1p.value

        # already-normalised or unknown provenance → safe transforms only
        if mn >= 0:
            return NormalizationMethod.log1p.value
        return NormalizationMethod.zscore.value

    def _normalize(self, frame: pd.DataFrame, method: str) -> pd.DataFrame:
        frame = frame.fillna(0.0)
        if method == NormalizationMethod.log1p.value:
            return np.log1p(frame.clip(lower=0))
        elif method == NormalizationMethod.cpm.value:
            lib_sizes = frame.sum(axis=0)
            lib_sizes = lib_sizes.replace(0, 1.0)
            cpm = frame.div(lib_sizes, axis=1) * 1e6
            return np.log1p(cpm)
        elif method == NormalizationMethod.zscore.value:
            return frame.subtract(frame.mean(axis=1), axis=0).div(
                frame.std(axis=1, ddof=1).replace(0, 1.0), axis=0
            )
        else:
            return np.log1p(frame.clip(lower=0))

    # ------------------------------------------------------------------
    #  PCA  (via numpy SVD – no sklearn dependency)
    # ------------------------------------------------------------------
    def _pca(self, frame: pd.DataFrame, n_components: int = 10) -> Dict[str, Any]:
        """Run PCA on samples (columns).  Returns dict with coords & variance."""
        n_samples = frame.shape[1]
        n_components = min(n_components, n_samples, frame.shape[0])

        # transpose → rows=samples, cols=genes  (standard PCA convention)
        mat = frame.values.T.astype(np.float64)
        mat = mat - mat.mean(axis=0, keepdims=True)  # center

        # thin SVD
        u, s, vt = np.linalg.svd(mat, full_matrices=False)
        # variance explained
        total_var = (s**2).sum()
        var_ratio = ((s**2) / total_var).tolist() if total_var > 0 else []

        # PC scores = U * S  (n_samples × n_components)
        pc_scores = u[:, :n_components] * s[:n_components]

        coords: Dict[str, List[float]] = {}
        for i in range(min(n_components, pc_scores.shape[1])):
            coords[f"PC{i + 1}"] = pc_scores[:, i].tolist()

        result: Dict[str, Any] = {
            "pca_variance_ratio": [round(v, 6) for v in var_ratio[:n_components]],
            "pca_n_components": n_components,
            "pca_sample_ids": frame.columns.tolist(),
            "pca_coordinates": coords,
        }
        return result

    # ------------------------------------------------------------------
    #  main entry point
    # ------------------------------------------------------------------
    def run(
        self,
        dataset: DatasetSpec,
        params: Dict[str, Any] | None = None,
        out_dir: Path | None = None,
    ) -> PipelineResult:
        if dataset.dataset_type != DatasetType.expression_matrix:
            raise ValueError("pipeline only supports expression_matrix datasets")

        params = params or {}
        out_dir = Path(out_dir) if out_dir else Path(".")

        # 1. load
        frame = self._load_numeric_frame(dataset)
        if frame.empty or frame.shape[1] < 2:
            raise ValueError("expression matrix too small for processing")

        notes: List[str] = []
        tables: List[Dict[str, Any]] = []
        figures: List[Dict[str, Any]] = []

        # 2. QC filtering
        min_samples_pct = float(params.get("qc_min_samples_pct", 0.2))
        min_mean = params.get("qc_min_mean_expression")
        frame, qc_report = self._qc_filter(frame, min_samples_pct=min_samples_pct, min_mean_expression=min_mean)
        notes.append(
            f"QC: {qc_report.genes_before_qc}→{qc_report.genes_after_qc} genes, "
            f"{len(qc_report.outlier_samples)} outlier sample(s) flagged."
        )

        # 3. normalization (auto-detect data kind if not specified)
        data_kind = params.get("data_kind")
        pre_normalized = bool(params.get("pre_normalized", False))

        if not data_kind:
            detection = self.detect_data_kind(frame, file_path=dataset.primary_path)
            data_kind = detection.data_kind.value
            pre_normalized = detection.pre_normalized
            notes.append(
                f"自动检测: data_kind={data_kind}, pre_normalized={pre_normalized} "
                f"(置信度={detection.confidence}, 原因: {'; '.join(detection.reasons)})"
            )

        if pre_normalized:
            # 数据已归一化，跳过归一化步骤，直接使用 QC 后的矩阵
            normalized = frame.fillna(0.0)
            method = "none (pre-normalized)"
            notes.append("数据已归一化，跳过归一化步骤")
        else:
            method = params.get("normalization_method") or self._suggest_method(
                frame, data_kind=data_kind
            )
            normalized = self._normalize(frame, method)
            notes.append(f"Normalization: {method} applied.")

        # save normalized matrix
        norm_dir = out_dir / "normalized"
        norm_dir.mkdir(parents=True, exist_ok=True)
        norm_path = norm_dir / "normalized_matrix.csv"
        normalized.to_csv(norm_path)
        tables.append(
            {
                "title": "normalized_expression_matrix",
                "path": str(norm_path.relative_to(out_dir)),
                "rows": int(normalized.shape[0]),
            }
        )

        # 4. PCA
        n_components = int(params.get("pca_n_components", 10))
        pca_result = self._pca(normalized, n_components=n_components)
        notes.append(
            f"PCA: top {len(pca_result['pca_variance_ratio'])} PCs explain "
            f"{sum(pca_result['pca_variance_ratio'][:2]):.2%} variance (PC1+PC2)."
        )

        # save PCA coordinates
        pca_dir = out_dir / "pca"
        pca_dir.mkdir(parents=True, exist_ok=True)
        pca_path = pca_dir / "pca_coordinates.csv"
        pca_df = pd.DataFrame(pca_result["pca_coordinates"], index=pca_result["pca_sample_ids"])
        pca_df.to_csv(pca_path)
        tables.append(
            {
                "title": "pca_coordinates",
                "path": str(pca_path.relative_to(out_dir)),
                "rows": int(pca_df.shape[0]),
            }
        )

        # scatter-plot data for downstream visualisation
        figures.append(
            {
                "title": "pca_plot",
                "path": str(pca_path.relative_to(out_dir)),
                "caption": f"PCA scatter data (PC1 vs PC2).",
            }
        )

        # 5. post-normalisation variable features
        gene_var = normalized.var(axis=1, ddof=1).fillna(0.0).sort_values(ascending=False)
        top_var_features = gene_var.head(10).index.tolist()

        # 6. metrics  (stage-prefixed: qc_*, norm_*)
        sample_means = normalized.mean(axis=0)
        metrics: Dict[str, Any] = {
            # QC  (backed by QCReport Pydantic contract)
            **{f"qc_{k}": v for k, v in qc_report.model_dump().items()},
            # normalisation
            "normalization_method": method,
            "norm_matrix_path": str(norm_path),
            "norm_shape": [int(normalized.shape[0]), int(normalized.shape[1])],
            # PCA
            "pca_variance_ratio": pca_result["pca_variance_ratio"],
            "pca_n_components": pca_result["pca_n_components"],
            # post-norm stats
            "norm_sample_mean_range": [
                round(float(sample_means.min()), 4),
                round(float(sample_means.max()), 4),
            ] if not sample_means.empty else [0.0, 0.0],
            "norm_top_variable_features": [str(f) for f in top_var_features],
        }

        return PipelineResult(
            metrics=metrics,
            tables=tables,
            figures=figures,
            notes=notes,
            normalized_frame=normalized,
            pca_coords=pca_df,
        )

    # ------------------------------------------------------------------
    #  serialise
    # ------------------------------------------------------------------
    def write_summary(self, out_dir: Path, result: PipelineResult) -> Path:
        summary_dir = out_dir / "summary"
        summary_dir.mkdir(parents=True, exist_ok=True)
        summary_path = summary_dir / "pipeline_summary.json"
        payload = {
            "metrics": result.metrics,
            "tables": result.tables,
            "figures": result.figures,
            "notes": result.notes,
        }
        summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary_path
