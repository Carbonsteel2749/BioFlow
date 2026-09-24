"""算法库的公共数据契约、基类和注册表。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd


@dataclass
class MLInput:
    """已整理的机器学习输入：样本 × 特征矩阵，标签可选。"""

    X: pd.DataFrame
    y: Optional[pd.Series] = None
    task: str = "clustering"
    params: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.X, pd.DataFrame) or self.X.empty:
            raise ValueError("X must be a non-empty pandas DataFrame (samples × features)")
        if not self.X.index.is_unique or not self.X.columns.is_unique:
            raise ValueError("sample IDs and feature IDs must be unique")
        if not all(pd.api.types.is_numeric_dtype(dtype) for dtype in self.X.dtypes):
            raise TypeError("all X columns must be numeric")
        if not np.isfinite(self.X.to_numpy(dtype=float)).all():
            raise ValueError("X must not contain missing or infinite values")
        if self.y is not None:
            if not isinstance(self.y, pd.Series):
                self.y = pd.Series(self.y, index=self.X.index, name="label")
            if not self.y.index.equals(self.X.index):
                raise ValueError("y index must exactly match X sample IDs and order")

    def require_labels(self) -> pd.Series:
        if self.y is None:
            raise ValueError("this algorithm requires y labels")
        return self.y


@dataclass(frozen=True)
class AlgorithmSpec:
    name: str
    version: str
    tasks: tuple[str, ...]
    description: str


@dataclass
class AlgorithmResult:
    algorithm: str
    metrics: dict[str, float] = field(default_factory=dict)
    labels: Optional[pd.Series] = None
    predictions: Optional[pd.Series] = None
    transformed_data: Optional[pd.DataFrame] = None
    feature_scores: Optional[pd.DataFrame] = None
    model: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class BaseAlgorithm(ABC):
    """成员实现的所有算法都必须继承此类。"""

    spec: AlgorithmSpec

    def __init__(self, **params: Any) -> None:
        self.params = params

    def merged_params(self, data: MLInput) -> dict[str, Any]:
        return {**self.params, **data.params}

    @abstractmethod
    def run(self, data: MLInput) -> AlgorithmResult:
        raise NotImplementedError


class AlgorithmRegistry:
    def __init__(self) -> None:
        self._algorithms: dict[str, type[BaseAlgorithm]] = {}

    def register(self, algorithm_cls: type[BaseAlgorithm]) -> type[BaseAlgorithm]:
        spec = getattr(algorithm_cls, "spec", None)
        if not isinstance(spec, AlgorithmSpec):
            raise TypeError("algorithm must define AlgorithmSpec as spec")
        if spec.name in self._algorithms:
            raise ValueError(f"duplicate algorithm name: {spec.name}")
        self._algorithms[spec.name] = algorithm_cls
        return algorithm_cls

    def create(self, name: str, **params: Any) -> BaseAlgorithm:
        if name not in self._algorithms:
            raise KeyError(f"unknown algorithm: {name}")
        return self._algorithms[name](**params)

    def list_specs(self) -> list[AlgorithmSpec]:
        return sorted(
            (algorithm.spec for algorithm in self._algorithms.values()),
            key=lambda spec: spec.name,
        )


registry = AlgorithmRegistry()


def register_algorithm(algorithm_cls: Optional[type[BaseAlgorithm]] = None) -> Callable:
    """成员用作 @register_algorithm 的装饰器。"""

    if algorithm_cls is None:
        return registry.register
    return registry.register(algorithm_cls)
