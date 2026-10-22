"""Skill 通用数据结构与基类。

后续文献检索、论文撰写、数据分析、可视化等模块新增 Skill 时，
都应继承 ``BaseSkill``，并使用此处定义的统一输入 / 输出格式。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Mapping


# ---------------------------------------------------------------------------
#  exceptions
# ---------------------------------------------------------------------------
class SkillError(Exception):
    """Skill 框架相关异常的基类。"""


class SkillValidationError(SkillError, ValueError):
    """输入校验失败时抛出。"""


class SkillExecutionError(SkillError):
    """Skill 执行过程中的可预期失败。"""


# ---------------------------------------------------------------------------
#  enums
# ---------------------------------------------------------------------------
class SkillCategory(str, Enum):
    """Skill 分类，与 ``skills/`` 下目录对应。"""

    COMMON = "common"
    LITERATURE = "literature"
    WRITING = "writing"
    ANALYSIS = "analysis"
    VISUALIZATION = "visualization"

    @classmethod
    def normalize(cls, value: "SkillCategory | str") -> str:
        if isinstance(value, cls):
            return value.value
        text = str(value).strip().lower()
        if not text:
            raise SkillValidationError("category must be a non-empty string")
        return text


class SkillStatus(str, Enum):
    """Skill 执行状态。"""

    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
#  data contracts
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SkillSpec:
    """Skill 元数据：名称、版本、分类与功能说明。

    Attributes:
        name: 全局唯一的 Skill 名称（同一名称可有多版本）。
        version: 语义化版本字符串，例如 ``\"1.0.0\"``。
        category: 所属分类，建议使用 :class:`SkillCategory`。
        description: 功能说明，供目录展示与调用方查阅。
        tags: 可选标签，便于检索与过滤。
        input_keys: 期望的 ``SkillInput.payload`` 主键（文档约定，不强制校验）。
        output_keys: 期望写入 ``SkillResult.data`` 的主键（文档约定）。
    """

    name: str
    version: str
    category: SkillCategory | str
    description: str
    tags: tuple[str, ...] = ()
    input_keys: tuple[str, ...] = ()
    output_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name or not str(self.name).strip():
            raise SkillValidationError("SkillSpec.name must be a non-empty string")
        if not self.version or not str(self.version).strip():
            raise SkillValidationError("SkillSpec.version must be a non-empty string")
        if not self.description or not str(self.description).strip():
            raise SkillValidationError("SkillSpec.description must be a non-empty string")
        object.__setattr__(self, "name", str(self.name).strip())
        object.__setattr__(self, "version", str(self.version).strip())
        object.__setattr__(self, "category", SkillCategory.normalize(self.category))
        object.__setattr__(self, "description", str(self.description).strip())
        object.__setattr__(self, "tags", tuple(str(t).strip() for t in self.tags if str(t).strip()))
        object.__setattr__(
            self,
            "input_keys",
            tuple(str(k).strip() for k in self.input_keys if str(k).strip()),
        )
        object.__setattr__(
            self,
            "output_keys",
            tuple(str(k).strip() for k in self.output_keys if str(k).strip()),
        )

    @property
    def key(self) -> str:
        """``name@version`` 形式的唯一键。"""
        return f"{self.name}@{self.version}"


@dataclass
class SkillInput:
    """所有 Skill 的统一输入。

    Attributes:
        payload: 业务参数字典，具体字段由各 Skill 自行约定。
        context: 可选运行上下文（如 run_id、调用方模块名、路径配置等）。
        options: 可选运行开关（如 dry_run、verbose），不参与业务语义。
    """

    payload: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)

    def require(self, *keys: str) -> Mapping[str, Any]:
        """校验 ``payload`` 中必须包含的键，缺失则抛出 :class:`SkillValidationError`。"""
        missing = [key for key in keys if key not in self.payload]
        if missing:
            raise SkillValidationError(f"missing required payload keys: {', '.join(missing)}")
        return {key: self.payload[key] for key in keys}

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)


@dataclass
class SkillResult:
    """所有 Skill 的统一输出。

    Attributes:
        skill: Skill 名称。
        version: 实际执行的版本。
        status: 执行状态。
        data: 结构化业务结果，约定以 ``dict`` 为主，也可为其它可序列化对象。
        summary: 一句话结果摘要，便于日志与目录展示。
        artifacts: 产出文件 / 资源路径列表。
        warnings: 非致命告警。
        metadata: 附加元数据（耗时、来源等）。
        error: 失败时的错误信息；成功时为 ``None``。
    """

    skill: str
    version: str
    status: SkillStatus | str
    data: Any = None
    summary: str = ""
    artifacts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.status, SkillStatus):
            self.status = self.status.value
        else:
            self.status = str(self.status).strip().lower()

    @property
    def succeeded(self) -> bool:
        return self.status == SkillStatus.SUCCESS.value

    @property
    def failed(self) -> bool:
        return self.status == SkillStatus.FAILED.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "version": self.version,
            "status": self.status,
            "data": self.data,
            "summary": self.summary,
            "artifacts": list(self.artifacts),
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
            "error": self.error,
        }


# ---------------------------------------------------------------------------
#  base class
# ---------------------------------------------------------------------------
class BaseSkill(ABC):
    """所有具体 Skill 的抽象基类。

    子类必须：

    1. 定义类属性 ``spec: SkillSpec``；
    2. 实现 :meth:`run`，接收 :class:`SkillInput`，返回 :class:`SkillResult`。

    可选覆盖 :meth:`validate_input` 做参数校验。推荐使用
    :meth:`success` / :meth:`failure` 构造统一结果。
    """

    spec: ClassVar[SkillSpec]

    def validate_input(self, skill_input: SkillInput) -> None:
        """默认校验：``payload`` 必须是 ``dict``。子类可覆盖加强约束。"""
        if not isinstance(skill_input, SkillInput):
            raise SkillValidationError(
                f"expected SkillInput, got {type(skill_input).__name__}"
            )
        if not isinstance(skill_input.payload, dict):
            raise SkillValidationError("SkillInput.payload must be a dict")

    @abstractmethod
    def run(self, skill_input: SkillInput) -> SkillResult:
        """执行 Skill 的业务逻辑。"""

    def __call__(self, skill_input: SkillInput) -> SkillResult:
        self.validate_input(skill_input)
        return self.run(skill_input)

    def success(
        self,
        data: Any = None,
        *,
        summary: str = "",
        artifacts: list[str] | None = None,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SkillResult:
        return SkillResult(
            skill=self.spec.name,
            version=self.spec.version,
            status=SkillStatus.SUCCESS,
            data=data,
            summary=summary or self.spec.description,
            artifacts=list(artifacts or []),
            warnings=list(warnings or []),
            metadata=dict(metadata or {}),
        )

    def failure(
        self,
        error: str,
        *,
        summary: str = "",
        data: Any = None,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SkillResult:
        return SkillResult(
            skill=self.spec.name,
            version=self.spec.version,
            status=SkillStatus.FAILED,
            data=data,
            summary=summary or error,
            warnings=list(warnings or []),
            metadata=dict(metadata or {}),
            error=error,
        )

    def skipped(
        self,
        *,
        summary: str = "skipped",
        data: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> SkillResult:
        return SkillResult(
            skill=self.spec.name,
            version=self.spec.version,
            status=SkillStatus.SKIPPED,
            data=data,
            summary=summary,
            metadata=dict(metadata or {}),
        )
