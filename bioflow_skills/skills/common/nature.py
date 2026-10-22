"""Shared skills adapted from the Nature Skills project."""

from .nature_reference import NatureSkillDefinition, register_nature_definitions


DEFINITIONS = (
    NatureSkillDefinition("nature-experiment-log", "common", "将文字、图片或语音整理为标准化实验日志。", ("收集原始记录", "整理时间、材料、操作与观察", "输出带元数据的日志"), ("不补造未记录的实验步骤或观察",)),
)

register_nature_definitions(DEFINITIONS)
