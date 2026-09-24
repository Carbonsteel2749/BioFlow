"""跨模块任务单 Skill。"""

from __future__ import annotations

try:  # 标准包调用
    from ...core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from ...registry import register_skill
except ImportError:  # 兼容早期顶层脚本调用
    from core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from registry import register_skill


@register_skill
class TaskBriefSkill(BaseSkill):
    spec = SkillSpec(
        name="task_brief",
        version="0.1.0",
        category=SkillCategory.COMMON,
        description="整理研究目标、约束、输入和预期产物，形成跨模块任务单。",
        tags=("orchestration", "task", "handoff"),
        input_keys=("objective",),
        output_keys=("objective", "constraints", "inputs", "expected_artifacts"),
    )

    def run(self, skill_input: SkillInput):
        objective = str(skill_input.require("objective")["objective"]).strip()
        if not objective:
            raise ValueError("objective must not be empty")
        return self.success(
            data={
                "objective": objective,
                "constraints": skill_input.get("constraints", []),
                "inputs": skill_input.get("inputs", []),
                "expected_artifacts": skill_input.get("expected_artifacts", []),
            },
            summary="已生成结构化任务单。",
        )
