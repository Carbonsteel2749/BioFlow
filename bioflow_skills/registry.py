"""Skill 注册、查询与版本管理。

典型用法::

    from bioflow_skills.core import BaseSkill, SkillSpec, SkillCategory, SkillInput
    from bioflow_skills.registry import register_skill, get_registry

    @register_skill
    class MySkill(BaseSkill):
        spec = SkillSpec(
            name="my_skill",
            version="1.0.0",
            category=SkillCategory.COMMON,
            description="示例 Skill",
        )

        def run(self, skill_input: SkillInput):
            return self.success(data=skill_input.payload)

    skill = get_registry().create("my_skill")
"""

from __future__ import annotations

from typing import Callable, Iterable, Type, TypeVar, overload

try:  # 标准包调用
    from .core import BaseSkill, SkillCategory, SkillError, SkillSpec, SkillValidationError
except ImportError:  # 兼容早期顶层脚本调用
    from core import BaseSkill, SkillCategory, SkillError, SkillSpec, SkillValidationError

SkillT = TypeVar("SkillT", bound=Type[BaseSkill])


class SkillNotFoundError(SkillError, KeyError):
    """按名称 / 版本查询不到 Skill。"""


class DuplicateSkillError(SkillError, ValueError):
    """重复注册同一 ``name@version``。"""


def parse_version(version: str) -> tuple[int | str, ...]:
    """将版本字符串解析为可比较的元组。

    支持常见语义化版本（如 ``1.2.3``、``1.2.3-alpha``）；无法解析的片段保留为字符串，
    保证同格式版本之间可稳定排序。
    """
    text = str(version).strip()
    if not text:
        raise SkillValidationError("version must be a non-empty string")

    parts: list[int | str] = []
    for chunk in text.replace("-", ".").replace("_", ".").split("."):
        if not chunk:
            continue
        if chunk.isdigit():
            parts.append(int(chunk))
        else:
            # 拆出前缀数字，便于 1.0.0rc1 这类版本大致排序
            num = ""
            rest = ""
            for ch in chunk:
                if ch.isdigit() and not rest:
                    num += ch
                else:
                    rest += ch
            if num:
                parts.append(int(num))
            if rest:
                parts.append(rest.lower())
    return tuple(parts) if parts else (text,)


class SkillRegistry:
    """内存中的 Skill 注册表，支持多版本共存。

    内部结构：``{name: {version: skill_cls}}``。
    未指定版本时，默认返回最新版本（按 :func:`parse_version` 排序）。
    """

    def __init__(self) -> None:
        self._skills: dict[str, dict[str, Type[BaseSkill]]] = {}

    # -- register / unregister -------------------------------------------------
    def register(
        self,
        skill_cls: Type[BaseSkill],
        *,
        overwrite: bool = False,
    ) -> Type[BaseSkill]:
        """注册一个 Skill 类。可直接调用，也可作为装饰器使用（见 :func:`register_skill`）。"""
        spec = getattr(skill_cls, "spec", None)
        if not isinstance(spec, SkillSpec):
            raise SkillValidationError(
                f"{skill_cls.__name__} must define a SkillSpec on class attribute 'spec'"
            )
        if not issubclass(skill_cls, BaseSkill):
            raise SkillValidationError(f"{skill_cls.__name__} must inherit BaseSkill")

        versions = self._skills.setdefault(spec.name, {})
        if spec.version in versions and not overwrite:
            raise DuplicateSkillError(
                f"skill already registered: {spec.key}. "
                "Pass overwrite=True to replace it."
            )
        versions[spec.version] = skill_cls
        return skill_cls

    def unregister(self, name: str, version: str | None = None) -> None:
        """注销 Skill。``version`` 为 ``None`` 时移除该名称下全部版本。"""
        if name not in self._skills:
            raise SkillNotFoundError(f"skill not found: {name}")
        if version is None:
            del self._skills[name]
            return
        versions = self._skills[name]
        if version not in versions:
            raise SkillNotFoundError(f"skill not found: {name}@{version}")
        del versions[version]
        if not versions:
            del self._skills[name]

    def clear(self) -> None:
        """清空注册表（主要用于测试）。"""
        self._skills.clear()

    # -- query -----------------------------------------------------------------
    def has(self, name: str, version: str | None = None) -> bool:
        if name not in self._skills:
            return False
        if version is None:
            return True
        return version in self._skills[name]

    def get(self, name: str, version: str | None = None) -> Type[BaseSkill]:
        """获取 Skill 类；未指定版本时返回最新版本。"""
        if name not in self._skills or not self._skills[name]:
            raise SkillNotFoundError(f"skill not found: {name}")
        versions = self._skills[name]
        if version is None:
            return versions[self.latest_version(name)]
        if version not in versions:
            available = ", ".join(self.list_versions(name))
            raise SkillNotFoundError(
                f"skill not found: {name}@{version}. available: {available}"
            )
        return versions[version]

    def create(self, name: str, version: str | None = None, **kwargs: object) -> BaseSkill:
        """实例化已注册的 Skill。"""
        skill_cls = self.get(name, version=version)
        return skill_cls(**kwargs)

    def get_spec(self, name: str, version: str | None = None) -> SkillSpec:
        return self.get(name, version=version).spec

    def latest_version(self, name: str) -> str:
        versions = self.list_versions(name)
        if not versions:
            raise SkillNotFoundError(f"skill not found: {name}")
        return versions[-1]

    def list_versions(self, name: str) -> list[str]:
        """返回某 Skill 的全部版本（升序，最后一项为最新）。"""
        if name not in self._skills:
            raise SkillNotFoundError(f"skill not found: {name}")
        return sorted(self._skills[name].keys(), key=parse_version)

    def list_names(self) -> list[str]:
        return sorted(self._skills.keys())

    def list_categories(self) -> list[str]:
        categories = {skill_cls.spec.category for skill_cls in self._iter_classes()}
        return sorted(categories)

    def list_specs(
        self,
        *,
        category: SkillCategory | str | None = None,
        name: str | None = None,
        latest_only: bool = False,
    ) -> list[SkillSpec]:
        """列出已注册 Skill 的元数据。

        Args:
            category: 按分类过滤。
            name: 按名称过滤。
            latest_only: 为 ``True`` 时每个名称只返回最新版本。
        """
        category_value = (
            SkillCategory.normalize(category) if category is not None else None
        )
        specs: list[SkillSpec] = []

        names = [name] if name is not None else self.list_names()
        for skill_name in names:
            if skill_name not in self._skills:
                continue
            versions = (
                [self.latest_version(skill_name)]
                if latest_only
                else self.list_versions(skill_name)
            )
            for ver in versions:
                spec = self._skills[skill_name][ver].spec
                if category_value is not None and spec.category != category_value:
                    continue
                specs.append(spec)
        return specs

    def _iter_classes(self) -> Iterable[Type[BaseSkill]]:
        for versions in self._skills.values():
            yield from versions.values()


# ---------------------------------------------------------------------------
#  module-level singleton + decorator
# ---------------------------------------------------------------------------
_default_registry = SkillRegistry()


def get_registry() -> SkillRegistry:
    """返回进程内默认注册表。"""
    return _default_registry


@overload
def register_skill(skill_cls: SkillT) -> SkillT: ...


@overload
def register_skill(
    *,
    overwrite: bool = False,
    registry: SkillRegistry | None = None,
) -> Callable[[SkillT], SkillT]: ...


def register_skill(
    skill_cls: SkillT | None = None,
    *,
    overwrite: bool = False,
    registry: SkillRegistry | None = None,
):
    """将 Skill 类注册到注册表。

    支持两种写法::

        @register_skill
        class A(BaseSkill): ...

        @register_skill(overwrite=True)
        class B(BaseSkill): ...
    """

    target = registry or _default_registry

    def decorator(cls: SkillT) -> SkillT:
        return target.register(cls, overwrite=overwrite)  # type: ignore[return-value]

    if skill_cls is not None:
        return decorator(skill_cls)
    return decorator
