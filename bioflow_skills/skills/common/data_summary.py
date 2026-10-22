"""结构化输入摘要 Skill。"""

from __future__ import annotations

try:  # 标准包调用
    from ...core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from ...registry import register_skill
except ImportError:  # 兼容早期顶层脚本调用
    from core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from registry import register_skill


@register_skill
class DataSummarySkill(BaseSkill):
    spec = SkillSpec(
        name="data_summary",
        version="0.1.0",
        category=SkillCategory.COMMON,
        description="汇总结构化输入中的字段名、数据类型与集合长度。",
        tags=("summary", "metadata"),
        output_keys=("fields",),
    )

    def run(self, skill_input: SkillInput):
        fields: dict[str, dict[str, object]] = {}
        for key, value in skill_input.payload.items():
            item: dict[str, object] = {"type": type(value).__name__}
            if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
                item["length"] = len(value)
            fields[str(key)] = item
        return self.success(
            data={"fields": fields},
            summary=f"已汇总 {len(fields)} 个输入字段。",
        )
