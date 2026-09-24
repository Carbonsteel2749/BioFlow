from __future__ import annotations

import re
from pathlib import Path

from backend.app.config import Settings

from .materializer import CompiledArtifacts


_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _validated_file(
    path: Path,
    label: str,
    *,
    contained_by: Path | None = None,
) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} does not exist: {path}") from exc
    if not resolved.is_file():
        raise ValueError(f"{label} is not a regular file: {path}")
    if contained_by is not None:
        root = contained_by.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError(f"{label} is outside compilation root")
    return resolved


def _runtime_path(
    settings: Settings,
    path: Path,
    label: str,
) -> Path:
    state_root = settings.state_root.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(state_root):
        raise ValueError(f"{label} must remain inside state_root")
    return resolved


def build_nextflow_command(
    settings: Settings,
    *,
    task_id: str,
    artifacts: CompiledArtifacts,
    input_manifest: Path,
    database_manifest: Path | None = None,
    resume: bool = False,
    output_dir: Path | None = None,
    work_dir: Path | None = None,
    log_dir: Path | None = None,
) -> tuple[str, ...]:
    """Build a shell-free Nextflow argv for one immutable compilation bundle."""
    if not _SAFE_TASK_ID.fullmatch(task_id):
        raise ValueError("task_id contains unsafe characters")
    if not settings.nextflow_bin or "\x00" in settings.nextflow_bin:
        raise ValueError("nextflow_bin must be a non-empty executable name")

    try:
        compilation_root = artifacts.root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"compilation root does not exist: {artifacts.root}"
        ) from exc
    if not compilation_root.is_dir():
        raise ValueError("compilation root is not a directory")

    source = _validated_file(
        artifacts.source_path, "compiled source", contained_by=compilation_root
    )
    config = _validated_file(
        artifacts.config_path, "compiled config", contained_by=compilation_root
    )
    parameters = _validated_file(
        artifacts.parameters_path,
        "compiled parameters",
        contained_by=compilation_root,
    )
    _validated_file(
        artifacts.summary_path,
        "compilation summary",
        contained_by=compilation_root,
    )
    manifest = _validated_file(input_manifest, "input manifest")
    resolved_database_manifest = (
        _validated_file(database_manifest, "database manifest")
        if database_manifest is not None
        else None
    )

    output = _runtime_path(
        settings,
        output_dir or settings.state_root / "outputs" / task_id,
        "output_dir",
    )
    work = _runtime_path(
        settings,
        work_dir or settings.state_root / "work" / task_id,
        "work_dir",
    )
    logs = _runtime_path(
        settings,
        log_dir or settings.state_root / "logs" / task_id,
        "log_dir",
    )

    command = [
        settings.nextflow_bin,
        "-c",
        str(config),
        "run",
        str(source),
        "-ansi-log",
        "false",
        "-params-file",
        str(parameters),
        "-work-dir",
        str(work),
        "-with-trace",
        str(logs / "trace.tsv"),
        "-with-timeline",
        str(logs / "timeline.html"),
        "-with-report",
        str(logs / "execution-report.html"),
        "--input_manifest",
        str(manifest),
        "--outdir",
        str(output),
        "--task_id",
        task_id,
        "--nextflow_work_root",
        str(work),
    ]
    if resolved_database_manifest is not None:
        command.extend(
            ["--database_manifest", str(resolved_database_manifest)]
        )
    if resume:
        command.append("-resume")
    return tuple(command)
