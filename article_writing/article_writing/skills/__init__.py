"""Optional bioflow_skills client used by the writing pipeline."""

from article_writing.skills.client import SkillClient, playbook_text
from article_writing.skills.hooks import (
    POLISH_SKILL,
    RESPONSE_SKILL,
    REVIEW_SKILL,
    WRITING_SECTIONS,
    WRITING_SKILL,
)

__all__ = [
    "POLISH_SKILL",
    "RESPONSE_SKILL",
    "REVIEW_SKILL",
    "SkillClient",
    "WRITING_SECTIONS",
    "WRITING_SKILL",
    "playbook_text",
]
