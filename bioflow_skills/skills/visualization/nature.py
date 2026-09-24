"""Visualization skills adapted from the Nature Skills project."""

from ..common.nature_reference import NatureSkillDefinition, register_nature_definitions


DEFINITIONS = (
    NatureSkillDefinition("nature-figure", "visualization", "依据结果、统计和图注要求设计、审查或导出科研图任务。", ("确认图要表达的结论", "绑定数据与统计", "输出图形规范或审查项"), ("不得让图表达超出数据支持的结论",)),
    NatureSkillDefinition("nature-image2ppt", "visualization", "将图片、截图或扫描件重建为可编辑演示文稿任务。", ("识别图层与文本", "重建可编辑对象", "核查视觉一致性"), ("必须标注图片来源和可编辑范围",)),
    NatureSkillDefinition("nature-paper2ppt", "visualization", "依据论文或阅读笔记制作带来源标注的学术汇报任务。", ("提取论文主线", "选择关键图表", "生成演讲结构与备注"), ("图表和结论须保留来源",)),
)

register_nature_definitions(DEFINITIONS)
