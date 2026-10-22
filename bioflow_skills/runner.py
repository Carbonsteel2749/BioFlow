"""Skill 的统一调用入口，兼容包调用和早期顶层脚本调用。"""

from __future__ import annotations

from typing import Mapping

try:  # 标准包调用：from bioflow_skills import run_skill
    from .core import SkillInput, SkillResult, SkillStatus
    from .registry import SkillNotFoundError, SkillRegistry, get_registry
except ImportError:  # 兼容早期测试：把 bioflow_skills 加入 sys.path 后 import runner
    from core import SkillInput, SkillResult, SkillStatus
    from registry import SkillNotFoundError, SkillRegistry, get_registry


def run_skill(
    skill_name: str,
    skill_input: SkillInput | Mapping[str, object] | None = None,
    *,
    payload: Mapping[str, object] | None = None,
    version: str | None = None,
    skill_registry: SkillRegistry | None = None,
) -> SkillResult:
    """按名称执行已注册 Skill，并统一返回 ``SkillResult``。

    支持 ``SkillInput``、字典输入，及早期 ``payload={...}`` 调用写法。
    """

    registry = skill_registry or get_registry()
    if skill_input is not None and payload is not None:
        return SkillResult(
            skill=skill_name, version=version or "", status=SkillStatus.FAILED,
            summary="Skill 输入格式错误。", error="pass either skill_input or payload, not both",
        )
    raw_input = payload if payload is not None else skill_input
    if raw_input is None:
        normalized_input = SkillInput()
    elif isinstance(raw_input, SkillInput):
        normalized_input = raw_input
    elif isinstance(raw_input, Mapping):
        normalized_input = SkillInput(payload=dict(raw_input))
    else:
        return SkillResult(
            skill=skill_name, version=version or "", status=SkillStatus.FAILED,
            summary="Skill 输入格式错误。",
            error="skill_input must be SkillInput, a mapping, or None",
        )

    try:
        skill = registry.create(skill_name, version=version)
    except SkillNotFoundError:
        return SkillResult(
            skill=skill_name, version=version or "", status=SkillStatus.FAILED,
            summary=f"Skill not found: {skill_name}", error=f"Skill not found: {skill_name}",
        )

    try:
        result = skill(normalized_input)
        if not isinstance(result, SkillResult):
            raise TypeError("Skill.run must return SkillResult")
        result.metadata.setdefault("runner", "bioflow_skills")
        return result
    except Exception as exc:
        return skill.failure(
            str(exc), summary=f"Skill 执行失败：{skill_name}",
            metadata={"runner": "bioflow_skills", "exception_type": type(exc).__name__},
        )
