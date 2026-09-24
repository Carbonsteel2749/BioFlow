"""Section registry used by the orchestrator."""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Type

from article_writing.contracts import SectionId

if TYPE_CHECKING:
    from article_writing.sections.base import BaseSection

_REGISTRY: Dict[str, Type["BaseSection"]] = {}
_LOADED = False


def register_section(section_cls: Type["BaseSection"]) -> Type["BaseSection"]:
    name = section_cls.section_id.value
    if name in _REGISTRY:
        raise ValueError(f"duplicate section: {name}")
    _REGISTRY[name] = section_cls
    return section_cls


def _ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    from article_writing.sections import (  # noqa: F401
        abstract,
        back_matter,
        conclusion,
        discussion,
        introduction,
        methods,
        results,
    )

    _LOADED = True


def get_registry() -> Dict[str, Type["BaseSection"]]:
    _ensure_loaded()
    return dict(_REGISTRY)


def list_sections() -> List[SectionId]:
    return [SectionId(name) for name in get_registry()]


def create_section(section_id: SectionId | str) -> "BaseSection":
    registry = get_registry()
    key = section_id.value if isinstance(section_id, SectionId) else section_id
    if key not in registry:
        raise KeyError(f"unknown section: {key}")
    return registry[key]()
