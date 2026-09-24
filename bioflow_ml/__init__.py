"""BioFLow 机器学习算法库。

导入本包会登记内置算法。调用方应从本模块取得算法目录或创建算法，
而不需要依赖各实现模块的导入顺序。
"""

from .core import (
    AlgorithmResult,
    AlgorithmSpec,
    BaseAlgorithm,
    MLInput,
    registry,
)


def load_builtin_algorithms() -> None:
    """导入内置算法模块以触发注册；可重复调用。"""

    from . import algorithms  # noqa: F401


# 注册是包初始化的一部分，保证 ``import bioflow_ml`` 后目录立即可用。
load_builtin_algorithms()

from .catalog import (  # noqa: E402
    AlgorithmCatalogEntry,
    format_algorithm_catalog,
    get_algorithm_catalog,
    list_algorithms,
    print_algorithm_catalog,
)

__all__ = [
    "AlgorithmCatalogEntry",
    "AlgorithmResult",
    "AlgorithmSpec",
    "BaseAlgorithm",
    "MLInput",
    "format_algorithm_catalog",
    "get_algorithm_catalog",
    "list_algorithms",
    "load_builtin_algorithms",
    "print_algorithm_catalog",
    "registry",
]
