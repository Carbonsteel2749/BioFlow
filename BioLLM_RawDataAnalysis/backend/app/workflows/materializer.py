from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .compiler import CompiledWorkflow


_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class CompilationArtifactConflict(RuntimeError):
    """A task already owns a different immutable compilation bundle."""


@dataclass(frozen=True)
class CompiledArtifacts:
    root: Path
    source_path: Path
    config_path: Path
    parameters_path: Path
    summary_path: Path

    @property
    def files(self) -> tuple[Path, ...]:
        return (
            self.source_path,
            self.config_path,
            self.parameters_path,
            self.summary_path,
        )


def _artifacts(root: Path) -> CompiledArtifacts:
    return CompiledArtifacts(
        root=root,
        source_path=root / "main.nf",
        config_path=root / "nextflow.config",
        parameters_path=root / "parameters.json",
        summary_path=root / "summary.json",
    )


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_private(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _summary(compiled: CompiledWorkflow) -> dict[str, object]:
    return {
        "compiler_version": compiled.compiler_version,
        "graph_hash": compiled.graph_hash,
        "node_aliases": compiled.node_aliases,
        "node_output_channels": compiled.node_output_channels,
        "node_parameters": compiled.node_parameters,
        "registry_version": compiled.registry_version,
        "topological_order": list(compiled.topological_order),
    }


def materialize_compilation(
    compiled: CompiledWorkflow,
    *,
    task_id: str,
    state_root: Path,
    workflow_root: Path | None = None,
) -> CompiledArtifacts:
    if not _SAFE_TASK_ID.fullmatch(task_id):
        raise ValueError("task_id contains unsafe characters")

    run_root = state_root.resolve() / "workflow_runs" / task_id
    run_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(run_root, 0o700)
    final_root = run_root / "compiled"
    if final_root.exists():
        raise CompilationArtifactConflict(
            "an immutable compilation bundle already exists for this task"
        )

    staging_root = Path(
        tempfile.mkdtemp(prefix=".compiled-", dir=run_root)
    )
    os.chmod(staging_root, 0o700)
    staging = _artifacts(staging_root)
    try:
        if workflow_root is not None:
            resolved_workflow_root = workflow_root.resolve(strict=True)
            for relative, destination in (
                ("modules", staging_root / "workflow" / "modules"),
                ("bin", staging_root / "bin"),
            ):
                source = resolved_workflow_root / relative
                if not source.is_dir():
                    raise FileNotFoundError(
                        f"workflow support directory does not exist: {source}"
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, destination, symlinks=False)
        _write_private(staging.source_path, compiled.source.encode("utf-8"))
        _write_private(
            staging.config_path,
            compiled.nextflow_config.encode("utf-8"),
        )
        _write_private(
            staging.parameters_path,
            _json_bytes(compiled.nextflow_parameters),
        )
        _write_private(staging.summary_path, _json_bytes(_summary(compiled)))
        _fsync_directory(staging_root)
        try:
            os.rename(staging_root, final_root)
        except FileExistsError as exc:
            raise CompilationArtifactConflict(
                "an immutable compilation bundle already exists for this task"
            ) from exc
        _fsync_directory(run_root)
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root)

    return _artifacts(final_root)
