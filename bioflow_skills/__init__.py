"""BioFlow AI Skill 库。"""

from .catalog import (
    SkillCatalogEntry,
    format_catalog,
    get_categories,
    list_by_category,
    list_catalog,
    print_catalog,
)
from .core import (
    BaseSkill,
    SkillCategory,
    SkillError,
    SkillExecutionError,
    SkillInput,
    SkillResult,
    SkillSpec,
    SkillStatus,
    SkillValidationError,
)
from .registry import (
    DuplicateSkillError,
    SkillNotFoundError,
    SkillRegistry,
    get_registry,
    parse_version,
    register_skill,
)


def load_builtin_skills() -> None:
    """Load native skills and their category-specific external adapters.

    Importing this package registers native Python skills and the Nature Skills
    adapters. The latter are intentionally dispatch-only: they never execute
    upstream Agent instructions inside this Python process.
    """

    from . import skills  # noqa: F401


load_builtin_skills()

from .runner import run_skill  # noqa: E402

__all__ = [
    "BaseSkill", "DuplicateSkillError", "SkillCatalogEntry", "SkillCategory",
    "SkillError", "SkillExecutionError", "SkillInput", "SkillNotFoundError",
    "SkillRegistry", "SkillResult", "SkillSpec", "SkillStatus",
    "SkillValidationError", "format_catalog", "get_categories", "get_registry",
    "list_by_category", "list_catalog", "load_builtin_skills", "parse_version",
    "print_catalog", "register_skill", "run_skill",
]
