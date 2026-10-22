"""Analysis skills adapted from the Nature Skills project."""

from ..common.nature_reference import NatureSkillDefinition, register_nature_definitions


DEFINITIONS = (
    NatureSkillDefinition("nature-statistics", "analysis", "审查统计报告、实验单位、重复、检验和图注统计信息。", ("核对统计计划", "检查实验单位和重复", "输出统计报告缺失项"), ("不会在无数据和无指令时计算新分析", "不虚构显著性")),
)

register_nature_definitions(DEFINITIONS)
