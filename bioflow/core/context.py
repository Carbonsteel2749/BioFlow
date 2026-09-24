from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd


@dataclass
class RunContext:
    run_id: str
    workspace: Path
    artifacts_dir: Path = field(init=False)
    logs_dir: Path = field(init=False)
    cache_dir: Path = field(init=False)
    rag_url: Optional[str] = None
    rag_timeout: int = 60
    runners: Dict[str, Any] = field(default_factory=dict)
    evidence_index: Optional[Any] = None
    sanitizer: Optional[Any] = None
    model_router: Optional[Any] = None
    module_outputs: Dict[str, Any] = field(default_factory=dict)
    _frame_cache: Dict[str, pd.DataFrame] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace)
        self.artifacts_dir = self.workspace / self.run_id / "artifacts"
        self.logs_dir = self.workspace / self.run_id / "logs"
        self.cache_dir = self.workspace / self.run_id / "cache"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def run_dir(self) -> Path:
        return self.workspace / self.run_id

    def artifact_path(self, *parts: str) -> Path:
        return self.artifacts_dir.joinpath(*parts)

    def get_cached_frame(self, path: str) -> pd.DataFrame | None:
        return self._frame_cache.get(path)

    def set_cached_frame(self, path: str, frame: pd.DataFrame) -> None:
        self._frame_cache[path] = frame
