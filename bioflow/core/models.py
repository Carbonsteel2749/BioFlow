from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
#  custom exceptions
# ---------------------------------------------------------------------------
class DatasetValidationError(ValueError):
    """Raised when an input dataset fails structural or content validation."""

    def __init__(self, message: str, dataset_path: str = "") -> None:
        super().__init__(message)
        self.dataset_path = dataset_path


class PipelineInputError(ValueError):
    """Raised when pipeline inputs are inconsistent (e.g. data_kind mismatch)."""


# ---------------------------------------------------------------------------
#  enums
# ---------------------------------------------------------------------------
class ModuleStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"
    blocked = "blocked"


class DatasetType(str, Enum):
    unknown = "unknown"
    expression_matrix = "expression_matrix"


class DataKind(str, Enum):
    """Explicit contract for the measurement unit of a numeric matrix.

    ``counts`` is the only kind eligible for library-size scaling (CPM).
    All other kinds are considered already-normalised and only receive
    safe transforms (log1p, z-score).
    """

    counts = "counts"
    tpm = "tpm"
    fpkm = "fpkm"
    microarray = "microarray"
    relative_abundance = "relative_abundance"


class DataKindDetection(BaseModel):
    """Result of automatic data-kind detection from file name and numeric features."""

    data_kind: DataKind = DataKind.counts
    pre_normalized: bool = False
    confidence: float = 0.0
    reasons: List[str] = Field(default_factory=list)


class NormalizationMethod(str, Enum):
    """Canonical normalisation method names used across the whole pipeline."""

    log1p = "log1p"
    cpm = "cpm"
    zscore = "zscore"


class QCReport(BaseModel):
    """Contract for quality-control metrics emitted by ExpressionMatrixPipeline."""

    genes_before_qc: int
    samples_before_qc: int
    genes_filtered_low_detect: int = 0
    genes_filtered_low_mean: int = 0
    outlier_samples: List[str] = Field(default_factory=list)
    sample_corr_mean: Optional[float] = None
    sample_corr_min: Optional[float] = None
    sample_corr_range: Optional[List[float]] = None
    genes_after_qc: int
    samples_after_qc: int


class ArtifactRef(BaseModel):
    path: str
    kind: str = "file"
    description: Optional[str] = None


class TableRef(BaseModel):
    title: str
    path: str
    rows: Optional[int] = None


class FigureRef(BaseModel):
    title: str
    path: str
    caption: Optional[str] = None


class EvidenceRef(BaseModel):
    evidence_id: str
    title: str
    source: str
    locator: str
    snippet: str
    score: float = 0.0
    tags: List[str] = Field(default_factory=list)


class ArtifactManifest(BaseModel):
    root: str
    items: List[ArtifactRef] = Field(default_factory=list)


class RunProvenance(BaseModel):
    runner: str
    pipeline: str
    params: Dict[str, Any] = Field(default_factory=dict)
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    status: str = ""
    logs: List[str] = Field(default_factory=list)


class ResultBundle(BaseModel):
    run_id: str
    provenance: RunProvenance
    artifacts: ArtifactManifest
    metrics: Dict[str, Any] = Field(default_factory=dict)
    tables: List[TableRef] = Field(default_factory=list)
    figures: List[FigureRef] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class DatasetSpec(BaseModel):
    dataset_type: DatasetType = DatasetType.unknown
    primary_path: str = ""
    source_paths: List[str] = Field(default_factory=list)
    format: str = ""
    confidence: float = 0.0
    shape: Optional[List[int]] = None
    feature_axis: Optional[str] = None
    sample_axis: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PipelineSpec(BaseModel):
    pipeline_id: str
    dataset_type: DatasetType
    runner_mode: str = "mock"
    params: Dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class ModuleSpec(BaseModel):
    name: str
    kind: str
    deps: List[str] = Field(default_factory=list)
    version: str = "0.1.0"
    description: str = ""
    tags: List[str] = Field(default_factory=list)


class ModuleInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    module: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    deps: Dict[str, "ModuleOutput"] = Field(default_factory=dict)
    evidence: List[EvidenceRef] = Field(default_factory=list)
    dataset: Optional[DatasetSpec] = None


class ModuleOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    module: str
    status: ModuleStatus
    summary: str = ""
    artifacts: ArtifactManifest
    dataset: Optional[DatasetSpec] = None
    evidence: List[EvidenceRef] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    tables: List[TableRef] = Field(default_factory=list)
    figures: List[FigureRef] = Field(default_factory=list)


class ModelSpec(BaseModel):
    name: str
    provider: str
    max_tokens: int = 1024
    temperature: float = 0.2
    allow_external: bool = False
    tags: List[str] = Field(default_factory=list)


class RoutePolicy(BaseModel):
    allow_external: bool = False
    preferred_models: List[str] = Field(default_factory=list)
    max_tokens: int = 2048


class AnalysisData(BaseModel):
    """Normalized inputs consumed by result_analysis."""

    data_type: str = "unknown"
    matrix_info: Optional[Dict[str, Any]] = None
    qc_info: Optional[Dict[str, Any]] = None
    normalization_info: Optional[Dict[str, Any]] = None
    diff_expr: Optional[Dict[str, Any]] = None
    enrichment: Optional[Dict[str, Any]] = None
    extra_analyses: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    available: bool = True
    data_quality: str = "normal"
    warnings: List[str] = Field(default_factory=list)
    detected_types: List[str] = Field(default_factory=list)


class FindingItem(BaseModel):
    """A data-backed finding from the independent result analysis stage."""

    statement: str
    data_basis: str
    verified: bool = True


class AnalysisConclusion(BaseModel):
    """Independent conclusion generated only from the current analysis result."""

    summary: str
    key_findings: List[FindingItem] = Field(default_factory=list)
    methodology_notes: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)


class ComparisonItem(BaseModel):
    """One literature comparison item with traceable RAG evidence."""

    topic: str
    our_finding: str
    literature_finding: str
    direction: str
    source: str = "RAG"
    snippet: str = ""
    confidence: float = 0.5


class IntegrationReport(BaseModel):
    """Literature integration report generated from an AnalysisConclusion."""

    conclusion: AnalysisConclusion
    comparisons: List[ComparisonItem] = Field(default_factory=list)
    consistent_points: List[str] = Field(default_factory=list)
    divergent_points: List[str] = Field(default_factory=list)
    not_found_points: List[str] = Field(default_factory=list)
    overall_assessment: str = ""
    agreement_score: float = 0.5
    rag_sources: List[str] = Field(default_factory=list)
    rag_query: str = ""
