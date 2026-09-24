"""Shared contract for external bioinformatics tool wrappers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
import subprocess
from typing import Any


@dataclass(frozen=True)
class BioToolSpec:
    name: str
    version: str
    stage: str
    description: str
    executable: str


@dataclass
class BioToolResult:
    tool: str
    status: str
    command: list[str]
    outputs: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


class BaseBioTool(ABC):
    """External tools are called safely without shell expansion."""

    spec: BioToolSpec

    @abstractmethod
    def build_command(self, **params: Any) -> list[str]:
        raise NotImplementedError

    def run(self, *, dry_run: bool = False, **params: Any) -> BioToolResult:
        command = self.build_command(**params)
        if dry_run:
            return BioToolResult(tool=self.spec.name, status="dry_run", command=command)
        try:
            completed = subprocess.run(command, check=False, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise RuntimeError(f"{self.spec.executable} is not installed or not on PATH") from exc
        return BioToolResult(
            tool=self.spec.name,
            status="success" if completed.returncode == 0 else "failed",
            command=command,
            stdout=completed.stdout,
            stderr=completed.stderr,
            metadata={"return_code": completed.returncode},
        )

    @staticmethod
    def path(value: str | Path) -> str:
        return str(Path(value))
