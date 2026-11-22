"""Public request/response models; mirrored in generated OpenAPI types."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=3000)

    @field_validator("name")
    @classmethod
    def meaningful_name(cls, value):
        if not value.strip():
            raise ValueError("请填写项目名称。")
        return value.strip()


class ImportOptions(StrictModel):
    header_row: int = Field(default=1, ge=1, le=1000)
    encoding: Literal["auto", "utf-8-sig", "gb18030"] = "auto"
    delimiter: Literal["auto", "tab", "comma", "semicolon"] = "auto"
    kind: Literal["auto", "taxonomy", "functional", "metadata", "table"] = "auto"
    unit: Literal["auto", "fraction", "percent", "counts", "other"] = "auto"
    transpose: bool = False
    id_column: str | None = None
    sheet: str | None = None


class MappingUpdate(StrictModel):
    mapping: dict[str, str]
    excluded_samples: list[str] = []


class StudyConfig(StrictModel):
    group_column: str | None = None
    independent: bool = False
    excluded_samples: list[str] = []


class PlotOptions(StrictModel):
    title: str = Field(default="", max_length=150)
    palette: Literal["forest", "ocean", "earth"] = "forest"
    top_n: int = Field(default=15, ge=1, le=30)
    group_order: list[str] = []


class Preparation(StrictModel):
    """A typed data operation, interpreted identically during preview and execution."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    op: Literal["filter", "quantile_group", "threshold_group", "recode", "arithmetic"]
    dataset_id: str
    column: str = Field(min_length=1, max_length=500)
    output: str = Field(default="", max_length=500)
    operator: Literal["eq", "ne", "gt", "ge", "lt", "le", "in", "not_in", "not_missing"] = (
        "not_missing"
    )
    values: list[str] = Field(default_factory=list, max_length=100)
    lower: float = Field(default=0.25, ge=0, le=1)
    upper: float = Field(default=0.75, ge=0, le=1)
    threshold: float = 0
    low_label: str = Field(default="低值组", min_length=1, max_length=100)
    high_label: str = Field(default="高值组", min_length=1, max_length=100)
    mapping: dict[str, str] = Field(default_factory=dict, max_length=100)
    arithmetic: Literal["add", "subtract", "multiply", "divide", "log1p"] = "add"
    operand_column: str | None = None
    operand: float = 0


class SkillParams(PlotOptions):
    preparation: list[Preparation] = Field(default_factory=list, max_length=12)
    dataset_id: str | None = None
    x: str | None = None
    y: str | None = None
    source: str | None = None
    target: str | None = None
    weight: str | None = None
    fields: list[str] = Field(default_factory=list, max_length=64)
    metadata_id: str | None = None
    outcome_dataset_id: str | None = None
    right_dataset_id: str | None = None
    predictors: list[str] = Field(default_factory=list, max_length=1000)
    outcomes: list[str] = Field(default_factory=list, max_length=1000)
    covariates: list[str] = Field(default_factory=list, max_length=32)
    categorical_covariates: list[str] = Field(default_factory=list, max_length=32)
    references: dict[str, str] = Field(default_factory=dict, max_length=32)
    correlation_method: Literal["spearman", "pearson", "residual_spearman"] = "spearman"
    transform: Literal["none", "log1p", "clr"] = "none"
    outcome_transform: Literal["none", "log1p", "clr"] = "none"
    standardize: bool = True
    standard_error: Literal["HC3", "classical"] = "HC3"
    adjustment: Literal["BH", "none"] = "BH"
    correction_family: Literal["all", "outcome"] = "all"
    alpha: float = Field(default=0.05, gt=0, le=0.2)
    pseudocount: float = Field(default=1e-6, gt=0, le=1)
    functional_mode: Literal["community", "stratified", "contribution_sum"] = "community"
    candidate_column: str = "feature"
    left_key: str | None = None
    right_key: str | None = None
    join_how: Literal["inner", "left"] = "inner"
    baseline_test: Literal["mannwhitney", "welch"] = "mannwhitney"
    baseline_categorical: list[str] = Field(default_factory=list, max_length=64)
    result_predictor: str = "predictor"
    result_outcome: str = "outcome"
    result_effect: str = "effect"
    result_p: str = "p"
    result_q: str = "q"
    result_ci_low: str = "ci_low"
    result_ci_high: str = "ci_high"
    effect_metric: str = Field(default="", max_length=100)
    effect_style: Literal["forest", "lollipop"] = "forest"
    effect_direction: str | None = None
    positive_group: str | None = None
    significance: Literal["q", "p", "all"] = "q"
    network_stages: list[str] = Field(
        default_factory=lambda: ["菌种", "通路", "表型"], min_length=3, max_length=3
    )
    samples: list[str] = Field(default_factory=list, max_length=40)
    group_values: list[str] = Field(default_factory=list, max_length=12)
    variable: str | None = Field(default=None, max_length=200)
    group_by: str | None = Field(default=None, max_length=200)
    rank: Literal["species", "genus", "family", "phylum"] = "species"
    pathway: str | None = Field(default=None, max_length=500)
    prevalence: float = Field(default=0.1, ge=0, le=1)
    permutations: int = Field(default=999, ge=99, le=9999)
    seed: int = Field(default=42, ge=0)
    group_a: str | None = None
    group_b: str | None = None


class ProgramOutput(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")
    kind: Literal["table", "chart"]
    title: str = Field(min_length=1, max_length=200)
    columns: list[str] = Field(default_factory=list, max_length=100)
    min_rows: int = Field(default=0, ge=0, le=1000000)


class ProgramRequirement(StrictModel):
    id: str = Field(pattern=r"^r[0-9]{1,2}$")
    description: str = Field(min_length=1, max_length=600)


class AnalysisProgram(StrictModel):
    method: str = Field(min_length=1, max_length=2000)
    inputs: list[str] = Field(default_factory=list, max_length=20)
    requirements: list[ProgramRequirement] = Field(min_length=1, max_length=12)
    outputs: list[ProgramOutput] = Field(min_length=1, max_length=12)
    requires_independent: bool = False
    code: str = Field(default="", max_length=40000)


class ResultInput(StrictModel):
    step_id: str = Field(pattern=r"^s[0-9]{1,2}$")
    output: str = Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")


class Step(StrictModel):
    id: str = Field(pattern=r"^s[0-9]{1,2}$")
    skill: str
    reason: str = Field(default="", max_length=1000)
    parameters: SkillParams = Field(default_factory=SkillParams)
    depends_on: list[str] = []
    cohort_from: str | None = None
    program: AnalysisProgram | None = None
    inputs: dict[str, ResultInput] = Field(default_factory=dict, max_length=8)


class FieldOption(StrictModel):
    dataset_id: str
    field: str = Field(min_length=1, max_length=500)
    values: list[str] = Field(default_factory=list, max_length=12)


class Choice(StrictModel):
    value: str = Field(max_length=500)
    label: str = Field(default="", max_length=600)
    hint: str = Field(default="", max_length=500)


class Clarification(StrictModel):
    """One thing that stops a plan, together with the way to resolve it.

    `source` says who raised it: the planner (a question about the research) or a
    check (a failed precondition of a step). A `select` or `samples` answer is
    written to `target`: a step parameter, a study setting, the file selection,
    the sample mapping, or an accepted limitation.
    """

    id: str = Field(pattern=r"^c[0-9]{1,2}$")
    kind: Literal["bind_field", "unit", "encoding", "choice", "text", "select", "samples"]
    prompt: str = Field(min_length=1, max_length=800)
    concept: str = Field(default="", max_length=80)
    dataset_id: str | None = None
    field: str | None = None
    options: list[FieldOption] = Field(default_factory=list, max_length=40)
    values: list[str] = Field(default_factory=list, max_length=12)
    choices: list[str] = Field(default_factory=list, max_length=200)
    source: Literal["planner", "check"] = "planner"
    code: str = Field(default="", max_length=60)
    target: str = Field(default="", max_length=60)
    steps: list[str] = Field(default_factory=list, max_length=12)
    items: list[Choice] = Field(default_factory=list, max_length=200)
    min_pick: int = Field(default=1, ge=1, le=8)
    max_pick: int = Field(default=1, ge=1, le=8)
    required: bool = False
    action: Literal["", "data"] = ""


class ResearchAim(StrictModel):
    id: str = Field(pattern=r"^a[0-9]{1,2}$")
    question: str = Field(min_length=1, max_length=600)
    relation: Literal["association", "contribution", "prediction", "comparison", "description"]
    step_ids: list[str] = Field(min_length=1, max_length=12)


class ResearchDesign(StrictModel):
    aims: list[ResearchAim] = Field(min_length=1, max_length=12)
    synthesis_step: str | None = None
    interpretation: str = Field(default="", max_length=800)


class AnalysisPlan(StrictModel):
    objective: str = Field(min_length=1, max_length=8000)
    summary: str = Field(max_length=3000)
    steps: list[Step] = Field(min_length=1, max_length=12)
    questions: list[str] = Field(default_factory=list, max_length=8)
    clarifications: list[Clarification] = Field(default_factory=list, max_length=8)
    research_design: ResearchDesign | None = None


class ClarificationAnswer(StrictModel):
    id: str
    dataset_id: str | None = None
    field: str | None = Field(default=None, max_length=500)
    unit: str = Field(default="", max_length=100)
    encoding: str = Field(default="", max_length=500)
    unknown_unit: bool = False
    text: str = Field(default="", max_length=1000)
    picked: list[str] = Field(default_factory=list, max_length=8)
    mapping: dict[str, str] = Field(default_factory=dict)


class ClarifyRequest(StrictModel):
    answers: list[ClarificationAnswer] = Field(min_length=1, max_length=8)


class ReviseRequest(StrictModel):
    """Free-text adjustment after a plan is ready but before the user confirms it."""

    message: str = Field(min_length=1, max_length=2000)


class ChatRequest(StrictModel):
    dataset_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    study: StudyConfig | None = None
    include_report: bool = True
    message: str = Field(min_length=1, max_length=8000)
    mode: Literal["agent", "overview", "comparison", "contribution", "skill"] = "agent"
    parameters: SkillParams = Field(default_factory=SkillParams)
    skill_id: str | None = None


class RunConfirm(StrictModel):
    plan_id: str


class FieldMeaning(StrictModel):
    name: str = Field(min_length=1, max_length=500)
    meaning: str = Field(default="", max_length=500)
    unit: str = Field(default="", max_length=100)
    encoding: str = Field(default="", max_length=500)
    role: Literal[
        "unknown", "identifier", "subject", "categorical", "numeric", "ordinal", "time"
    ] = "unknown"


class UnderstandingUpdate(StrictModel):
    description: str = Field(default="", max_length=8000)
    fields: list[FieldMeaning] = Field(default_factory=list, max_length=500)
    expected_revision: int = Field(default=0, ge=0)


class MeaningRequest(StrictModel):
    description: str = Field(min_length=1, max_length=8000)


class ReportSection(StrictModel):
    id: str
    title: str = Field(max_length=200)
    text: str = Field(max_length=20000)
    evidence_ids: list[str] = []
    artifact_ids: list[str] = []


class ReportUpdate(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    sections: list[ReportSection] = Field(max_length=100)
    expected_revision: int


class DatasetVersion(BaseModel):
    id: str
    project_id: str
    data_type: str
    samples: list[str]
    features: int
    unit: str
    source_sha256: str
    transformations: list[dict[str, Any]]


class SkillResult(BaseModel):
    skill: str
    version: str = "1.0.0"
    status: Literal["success", "skipped", "failed"] = "success"
    failure: dict[str, Any] | None = None
    summary: str
    metrics: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    method: str
    warnings: list[str] = []
