from typing import Annotated, Literal
import unicodedata

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def validate_task_name(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 120 or any(unicodedata.category(c).startswith('C') for c in value):
        raise ValueError('任务名称须为 1–120 个字符，不能包含控制字符')
    return value


TaskName = Annotated[str, AfterValidator(validate_task_name)]


class RenameTaskRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: TaskName


class WorkflowParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threads: int = Field(default=4, ge=1, le=256)
    fastp_qualified_quality_phred: int = Field(default=20, ge=1, le=93)
    fastp_unqualified_percent_limit: int = Field(default=40, ge=0, le=100)
    fastp_n_base_limit: int = Field(default=5, ge=0)
    fastp_length_required: int = Field(default=50, ge=1)
    fastp_cut_front: bool = True
    fastp_cut_tail: bool = True
    fastp_cut_window_size: int = Field(default=4, ge=1)
    fastp_cut_mean_quality: int = Field(default=20, ge=1, le=93)
    fastp_trim_poly_g: bool = True
    fastp_correction: bool = False
    fastp_detect_adapter_for_pe: bool = True
    enable_mags: bool = False
    enable_reassembly: bool = True
    mag_threads: int = Field(default=8, ge=1, le=256)
    mag_memory_gb: int = Field(default=32, ge=4)
    assembler: Literal["megahit", "metaspades"] = "megahit"
    bin_completeness: float = Field(default=70, ge=0, le=100)
    bin_contamination: float = Field(default=5, ge=0, le=100)
    host_index: str | None = None
    host_filter_mode: Literal["strict_both_unmapped", "concordant_unmapped"] = "strict_both_unmapped"
    host_min_retained_pairs: int = Field(default=0, ge=0)
    host_max_removed_pct: float = Field(default=100, ge=0, le=100)
    host_bowtie2_preset: Literal["very-fast", "fast", "sensitive", "very-sensitive"] = "very-sensitive"
    kraken_db: str | None = None
    read_length: Literal[50, 75, 100, 150, 200, 250, 300] = 150
    humann_nucleotide_db: str | None = None
    humann_protein_db: str | None = None
    metaphlan_db: str | None = None


class CreateTaskRequest(BaseModel):
    name: TaskName | None = None
    manifest_path: str = Field(min_length=1)
    dataset_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    parameters: WorkflowParameters = Field(default_factory=WorkflowParameters)


class RetryDecisionRequest(BaseModel):
    allowed: bool
    reason: str = Field(min_length=1, max_length=500)


class UploadedPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    read1_upload_id: str = Field(min_length=1, max_length=128)
    read2_upload_id: str = Field(min_length=1, max_length=128)


class CreateUploadedManifestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[UploadedPair] = Field(min_length=1, max_length=1000)
    dataset_name: str | None = Field(default=None, min_length=1, max_length=120)


TaskStatus = Literal["queued", "validating", "running", "paused", "failed", "completed", "cancelled"]
StepStatus = Literal["pending", "running", "succeeded", "failed", "skipped"]
