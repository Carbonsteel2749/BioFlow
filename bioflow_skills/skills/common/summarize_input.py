"""输入数据摘要 Skill。"""

try:  # 标准包调用
    from ...core import BaseSkill, SkillCategory, SkillInput, SkillResult, SkillSpec
    from ...registry import register_skill
except ImportError:  # 兼容早期顶层脚本调用
    from core import BaseSkill, SkillCategory, SkillInput, SkillResult, SkillSpec
    from registry import register_skill


@register_skill
class SummarizeInput(BaseSkill):
    """生成输入数据的摘要信息。"""

    spec = SkillSpec(
        name="summarize_input",
        version="1.0.0",
        category=SkillCategory.COMMON,
        description="为给定的数据生成一个简单的摘要 (例如，字符串长度、列表项数)。",
        tags=["data", "utility", "summary"],
        input_keys=("data",),
        output_keys=("summary", "data_type"),
    )

    def run(self, skill_input: SkillInput) -> SkillResult:
        """执行 Skill 的业务逻辑。"""
        skill_input.require("data")
        data = skill_input.get("data")

        data_type = type(data).__name__
        summary = ""

        if isinstance(data, str):
            summary = f"String with {len(data)} characters."
        elif isinstance(data, list) or isinstance(data, tuple):
            summary = f"List/Tuple with {len(data)} items."
        elif isinstance(data, dict):
            summary = f"Dictionary with {len(data)} keys."
        elif data is None:
            summary = "Input data is None."
        else:
            summary = f"Data of type '{data_type}'."

        return self.success(
            data={
                "summary": summary,
                "data_type": data_type,
            },
            summary=summary,
        )
