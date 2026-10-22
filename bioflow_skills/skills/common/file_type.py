"""文件类型识别 Skill。"""

try:  # 标准包调用
    from ...core import BaseSkill, SkillCategory, SkillInput, SkillResult, SkillSpec
    from ...registry import register_skill
except ImportError:  # 兼容早期顶层脚本调用
    from core import BaseSkill, SkillCategory, SkillInput, SkillResult, SkillSpec
    from registry import register_skill


@register_skill
class FileTypeIdentifier(BaseSkill):
    """根据文件扩展名识别文件类型。"""

    spec = SkillSpec(
        name="file_type_identifier",
        version="1.0.0",
        category=SkillCategory.COMMON,
        description="根据文件路径的扩展名识别文件类型。",
        tags=["file", "utility"],
        input_keys=("file_path",),
        output_keys=("file_type",),
    )

    def run(self, skill_input: SkillInput) -> SkillResult:
        """执行 Skill 的业务逻辑。"""
        # 1. 校验输入
        skill_input.require("file_path")
        file_path = skill_input.get("file_path")

        if not isinstance(file_path, str) or not file_path.strip():
            return self.failure(
                error="InvalidInput: file_path must be a non-empty string.",
                summary="输入文件路径无效",
            )

        # 2. 提取扩展名
        try:
            parts = file_path.strip().split(".")
            if len(parts) > 1:
                extension = parts[-1].lower()
            else:
                # 没有扩展名的情况
                return self.success(
                    data={"file_type": "unknown"},
                    summary=f"File '{file_path}' has no extension, type is unknown.",
                )
        except Exception as e:
            return self.failure(
                error=f"ProcessingError: Failed to parse file path '{file_path}'.",
                summary="解析文件路径失败",
                metadata={"exception": str(e)},
            )

        # 3. 返回成功结果
        return self.success(
            data={"file_type": extension},
            summary=f"File type identified as '{extension}'.",
        )
