from __future__ import annotations

from bioflow.core.registry import ModuleRegistry

_registry = ModuleRegistry()


def register(module_cls):
    return _registry.register(module_cls)


def get_registry() -> ModuleRegistry:
    return _registry


# Import modules to register them.
from bioflow.modules.analysis.data_processing import DataProcessingModule  # noqa: E402,F401
from bioflow.modules.analysis.differential_analysis import DifferentialAnalysisModule  # noqa: E402,F401
from bioflow.modules.analysis.result_analysis import ResultAnalysisModule  # noqa: E402,F401
from bioflow.modules.writing.abstract import AbstractModule  # noqa: E402,F401
from bioflow.modules.writing.introduction import IntroductionModule  # noqa: E402,F401
from bioflow.modules.writing.methods import MethodsModule  # noqa: E402,F401
from bioflow.modules.writing.results import ResultsModule  # noqa: E402,F401
from bioflow.modules.writing.discussion import DiscussionModule  # noqa: E402,F401
from bioflow.modules.writing.other import OtherModule  # noqa: E402,F401
