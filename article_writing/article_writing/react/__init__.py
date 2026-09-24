"""ReAct loop for Stage 3 (Related Work).

PaperQA/STORM are design references only — this package does not depend on them.
"""

from __future__ import annotations

from article_writing.react.loop import ReactGatherResult, build_search_queries, run_related_work_react
from article_writing.react.trajectory import ReactActionName, ReactStep, ReactTrajectory

__all__ = [
    "ReactActionName",
    "ReactGatherResult",
    "ReactStep",
    "ReactTrajectory",
    "build_search_queries",
    "run_related_work_react",
]
