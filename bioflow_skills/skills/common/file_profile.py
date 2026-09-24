"""输入文件的轻量级描述 Skill。"""

from __future__ import annotations

from pathlib import Path

try:  # 标准包调用
    from ...core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from ...registry import register_skill
except ImportError:  # 兼容早期顶层脚本调用
    from core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from registry import register_skill


@register_skill
class FileProfileSkill(BaseSkill):
    spec = SkillSpec(
        name="file_profile",
        version="0.1.0",
        category=SkillCategory.COMMON,
        description="识别输入文件类型，并返回路径、后缀和基础文件信息。",
        tags=("file", "input", "metadata"),
        input_keys=("path",),
        output_keys=("path", "suffix", "format", "exists", "size_bytes"),
    )

    def run(self, skill_input: SkillInput):
        path_value = skill_input.require("path")["path"]
        path = Path(str(path_value))
        suffix = path.suffix.lower()
        formats = {
            ".csv": "csv", ".tsv": "tsv", ".txt": "text",
            ".xlsx": "excel", ".xls": "excel", ".json": "json",
            ".fastq": "fastq", ".fq": "fastq", ".gz": "compressed",
        }
        exists = path.exists()
        return self.success(
            data={
                "path": str(path),
                "suffix": suffix,
                "format": formats.get(suffix, "unknown"),
                "exists": exists,
                "size_bytes": path.stat().st_size if exists and path.is_file() else None,
            },
            summary=f"识别到 {formats.get(suffix, 'unknown')} 文件。",
        )
