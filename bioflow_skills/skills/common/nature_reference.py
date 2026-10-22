"""BioFlow-native reference adapters inspired by Nature Skills workflows.

These are not copies of upstream files.  They preserve only compact,
BioFlow-specific task-routing rules and safety constraints.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Type

try:  # Standard package import
    from ...core import BaseSkill, SkillInput, SkillResult, SkillSpec, SkillStatus
    from ...registry import get_registry, register_skill
except ImportError:  # Compatibility with existing top-level test imports
    from core import BaseSkill, SkillInput, SkillResult, SkillSpec, SkillStatus
    from registry import get_registry, register_skill


NATURE_SKILLS_REFERENCE = "https://github.com/Yuan1z0825/nature-skills"


@dataclass(frozen=True)
class NatureSkillDefinition:
    """BioFlow-owned definition of one reference-guided Agent workflow."""

    name: str
    category: str
    description: str
    workflow: tuple[str, ...]
    constraints: tuple[str, ...]
    availability: str = "requires_agent"
    intake_fields: tuple[str, ...] = ()
    routing_axes: Mapping[str, tuple[str, ...]] | None = None
    output_contract: tuple[str, ...] = ()
    quality_checks: tuple[str, ...] = ()
    maturity: str = "outline"


class NatureReferenceSkill(BaseSkill):
    """Produce a safe, source-bound handoff for an Agent workflow.

    The class never executes a model, browser, downloader, or an upstream
    script.  A later writing/orchestration adapter can consume the returned
    handoff using BioFlow's own LLM and evidence contracts.
    """

    definition: NatureSkillDefinition

    def validate_input(self, skill_input: SkillInput) -> None:
        super().validate_input(skill_input)
        task_context = skill_input.get("task_context")
        if not isinstance(task_context, str) or not task_context.strip():
            raise ValueError("task_context must be a non-empty string")

    def run(self, skill_input: SkillInput) -> SkillResult:
        return SkillResult(
            skill=self.spec.name,
            version=self.spec.version,
            status=SkillStatus.SKIPPED,
            summary=f"{self.spec.name} 已生成 BioFlow Agent 交接任务。",
            data={
                "execution_mode": "agent",
                "availability": self.definition.availability,
                "maturity": self.definition.maturity,
                "reference": NATURE_SKILLS_REFERENCE,
                "intake_fields": list(self.definition.intake_fields),
                "routing_axes": {
                    key: list(values)
                    for key, values in (self.definition.routing_axes or {}).items()
                },
                "workflow": list(self.definition.workflow),
                "constraints": list(self.definition.constraints),
                "output_contract": list(self.definition.output_contract),
                "quality_checks": list(self.definition.quality_checks),
                "payload": dict(skill_input.payload),
                "context": dict(skill_input.context),
            },
            warnings=[
                "该 skill 是 BioFlow 本地适配规则，不会直接执行 GitHub 上的脚本。",
                "所有结论、统计值和引文必须由输入材料或已验证来源支撑。",
            ],
            metadata={"reference_project": "nature-skills", "adapted": True},
        )


def _class_name(name: str) -> str:
    return "".join(part.title() for part in name.replace("-", "_").split("_")) + "Skill"


def _make_skill(definition: NatureSkillDefinition) -> Type[NatureReferenceSkill]:
    spec = SkillSpec(
        name=definition.name,
        version="0.1.0",
        category=definition.category,
        description=definition.description,
        tags=(
            "agent", "adapted", "nature-skills-reference", definition.availability,
            definition.maturity,
        ),
        input_keys=("task_context",),
        output_keys=("agent_handoff",),
    )
    return type(
        _class_name(definition.name),
        (NatureReferenceSkill,),
        {"spec": spec, "definition": definition, "__module__": __name__},
    )


def register_nature_definitions(definitions: Iterable[NatureSkillDefinition]) -> list[str]:
    """Register local, category-owned Nature Skills adaptations once."""

    registry = get_registry()
    registered: list[str] = []
    for definition in definitions:
        if registry.has(definition.name, "0.1.0"):
            continue
        register_skill(_make_skill(definition))
        registered.append(definition.name)
    return registered
