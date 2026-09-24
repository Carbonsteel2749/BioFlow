"""按分类展示已注册 Skill 的目录。

供调用方浏览当前 Skill 库能力，例如::

    from bioflow_skills.catalog import print_catalog, list_by_category

    print_catalog()
    print(list_by_category())
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .core import SkillCategory, SkillSpec
from .registry import SkillRegistry, get_registry


@dataclass(frozen=True)
class SkillCatalogEntry:
    """目录中的一条 Skill 记录（面向展示，不直接执行）。"""

    name: str
    version: str
    category: str
    description: str
    tags: tuple[str, ...] = ()
    input_keys: tuple[str, ...] = ()
    output_keys: tuple[str, ...] = ()
    is_latest: bool = True

    @classmethod
    def from_spec(cls, spec: SkillSpec, *, is_latest: bool = True) -> "SkillCatalogEntry":
        return cls(
            name=spec.name,
            version=spec.version,
            category=str(spec.category),
            description=spec.description,
            tags=tuple(spec.tags),
            input_keys=tuple(spec.input_keys),
            output_keys=tuple(spec.output_keys),
            is_latest=is_latest,
        )

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def list_catalog(
    category: SkillCategory | str | None = None,
    *,
    latest_only: bool = True,
    registry: SkillRegistry | None = None,
) -> list[SkillCatalogEntry]:
    """列出目录条目；默认每个 Skill 名称只展示最新版本。"""
    reg = registry or get_registry()
    specs = reg.list_specs(category=category, latest_only=False)
    latest_map = {
        name: reg.latest_version(name)
        for name in {spec.name for spec in specs}
    }

    entries: list[SkillCatalogEntry] = []
    for spec in specs:
        is_latest = spec.version == latest_map[spec.name]
        if latest_only and not is_latest:
            continue
        entries.append(SkillCatalogEntry.from_spec(spec, is_latest=is_latest))
    return entries


def list_by_category(
    *,
    latest_only: bool = True,
    registry: SkillRegistry | None = None,
) -> dict[str, list[SkillCatalogEntry]]:
    """按分类分组返回目录；空分类也会以空列表占位（内置分类）。"""
    reg = registry or get_registry()
    grouped: dict[str, list[SkillCatalogEntry]] = {
        cat.value: [] for cat in SkillCategory
    }
    for entry in list_catalog(latest_only=latest_only, registry=reg):
        grouped.setdefault(entry.category, []).append(entry)
    return grouped


def get_categories(*, registry: SkillRegistry | None = None) -> list[str]:
    """返回已有 Skill 实际覆盖到的分类（升序）。"""
    reg = registry or get_registry()
    return reg.list_categories()


def format_catalog(
    category: SkillCategory | str | None = None,
    *,
    latest_only: bool = True,
    registry: SkillRegistry | None = None,
) -> str:
    """生成人类可读的目录文本。"""
    reg = registry or get_registry()
    if category is not None:
        entries = list_catalog(category, latest_only=latest_only, registry=reg)
        title = f"Skill Catalog [{SkillCategory.normalize(category)}]"
        lines = [title, "=" * len(title)]
        if not entries:
            lines.append("(empty)")
            return "\n".join(lines)
        for entry in entries:
            lines.extend(_format_entry_lines(entry))
        return "\n".join(lines)

    grouped = list_by_category(latest_only=latest_only, registry=reg)
    lines = ["Skill Catalog", "============="]
    for cat_name in sorted(grouped.keys()):
        entries = grouped[cat_name]
        lines.append("")
        lines.append(f"[{cat_name}] ({len(entries)})")
        lines.append("-" * (len(cat_name) + 10))
        if not entries:
            lines.append("  (empty)")
            continue
        for entry in entries:
            lines.extend(_format_entry_lines(entry, indent="  "))
    return "\n".join(lines)


def print_catalog(
    category: SkillCategory | str | None = None,
    *,
    latest_only: bool = True,
    registry: SkillRegistry | None = None,
) -> None:
    """打印 Skill 目录。"""
    print(format_catalog(category, latest_only=latest_only, registry=registry))


def _format_entry_lines(entry: SkillCatalogEntry, indent: str = "") -> list[str]:
    latest_flag = "" if entry.is_latest else " (old)"
    lines = [
        f"{indent}- {entry.name}@{entry.version}{latest_flag}: {entry.description}",
    ]
    if entry.tags:
        lines.append(f"{indent}  tags: {', '.join(entry.tags)}")
    if entry.input_keys:
        lines.append(f"{indent}  inputs: {', '.join(entry.input_keys)}")
    if entry.output_keys:
        lines.append(f"{indent}  outputs: {', '.join(entry.output_keys)}")
    return lines
