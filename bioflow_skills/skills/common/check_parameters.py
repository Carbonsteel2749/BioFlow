"""参数检查 Skill。"""

try:  # 标准包调用
    from ...core import BaseSkill, SkillCategory, SkillInput, SkillResult, SkillSpec
    from ...registry import register_skill
except ImportError:  # 兼容早期顶层脚本调用
    from core import BaseSkill, SkillCategory, SkillInput, SkillResult, SkillSpec
    from registry import register_skill


@register_skill
class CheckParameters(BaseSkill):
    """根据一套规则检查输入参数。"""

    spec = SkillSpec(
        name="check_parameters",
        version="1.0.0",
        category=SkillCategory.COMMON,
        description="根据定义的规则集校验参数的有效性。",
        tags=["validation", "utility", "parameters"],
        input_keys=("parameters", "rules"),
        output_keys=("is_valid", "errors"),
    )

    def run(self, skill_input: SkillInput) -> SkillResult:
        """执行 Skill 的业务逻辑。"""
        skill_input.require("parameters", "rules")
        parameters = skill_input.get("parameters")
        rules = skill_input.get("rules")

        if not isinstance(parameters, dict) or not isinstance(rules, dict):
            return self.failure("'parameters' and 'rules' must be dictionaries.")

        errors = []
        for param, value in parameters.items():
            if param in rules:
                rule = rules[param]
                if "required" in rule and rule["required"] and value is None:
                    errors.append(f"Parameter '{param}' is required.")
                    continue
                if "type" in rule and not isinstance(value, eval(rule["type"])):
                    errors.append(f"Parameter '{param}' has wrong type: expected {rule['type']}, got {type(value).__name__}")

        is_valid = not errors
        summary = "Parameters are valid." if is_valid else "Parameter validation failed."

        return self.success(
            data={
                "is_valid": is_valid,
                "errors": errors,
            },
            summary=summary,
        )
