from __future__ import annotations

from typing import Dict, Iterable, Type

from .models import ModuleSpec


class ModuleRegistry:
    def __init__(self) -> None:
        self._modules: Dict[str, Type] = {}
        self._specs: Dict[str, ModuleSpec] = {}

    def register(self, module_cls):
        spec = getattr(module_cls, "spec", None)
        if spec is None:
            raise ValueError("module class missing spec")
        if spec.name in self._modules:
            raise ValueError(f"duplicate module name: {spec.name}")
        self._modules[spec.name] = module_cls
        self._specs[spec.name] = spec
        return module_cls

    def list_specs(self) -> Iterable[ModuleSpec]:
        return self._specs.values()

    def get_spec(self, name: str) -> ModuleSpec:
        return self._specs[name]

    def create(self, name: str, **kwargs):
        module_cls = self._modules[name]
        return module_cls(**kwargs)

    def has(self, name: str) -> bool:
        return name in self._modules
