"""任务信息整理 Skill。"""

try:  # 标准包调用
    from ...core import BaseSkill, SkillCategory, SkillInput, SkillResult, SkillSpec
    from ...registry import register_skill
except ImportError:  # 兼容早期顶层脚本调用
    from core import BaseSkill, SkillCategory, SkillInput, SkillResult, SkillSpec
    from registry import register_skill


@register_skill
class OrganizeTaskInfo(BaseSkill):
    """整理任务信息，生成格式化的字符串。"""

    spec = SkillSpec(
        name="organize_task_info",
        version="1.0.0",
        category=SkillCategory.COMMON,
        description="将任务相关信息整理成一个结构化的字符串。",
        tags=["task", "organize", "format"],
        input_keys=("task_info",),
        output_keys=("formatted_string",),
    )

    def run(self, skill_input: SkillInput) -> SkillResult:
        """执行 Skill 的业务逻辑。"""
        skill_input.require("task_info")
        task_info = skill_input.get("task_info")

        if not isinstance(task_info, dict):
            return self.failure("Input 'task_info' must be a dictionary.")

        formatted_lines = []
        for key, value in task_info.items():
            formatted_lines.append(f"- {key.replace('_', ' ').capitalize()}: {value}")

        formatted_string = "\n".join(formatted_lines)
        summary = f"Task information organized for {task_info.get('task_name', 'a task')}."

        return self.success(
            data={"formatted_string": formatted_string},
            summary=summary,
        )
