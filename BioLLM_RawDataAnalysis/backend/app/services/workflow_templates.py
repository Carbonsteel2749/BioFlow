from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import threading
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..workflows.graph import WorkflowGraph
from ..workflows.registry import REGISTRY_VERSION, NodeRegistry, default_node_registry
from ..workflows.validation import GraphValidationResult, validate_workflow_graph


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_VERSION_FILE = re.compile(r"^v([0-9]{6})\.json$")
_MAX_JSON_BYTES = 4 * 1024 * 1024


class WorkflowTemplateError(ValueError):
    """Invalid template or execution-snapshot data."""


class TemplateNotFoundError(KeyError):
    """The requested template or version does not exist."""


class TemplateVersionConflictError(WorkflowTemplateError):
    """A template update was based on a stale or missing version."""


class ExecutionSnapshotConflictError(WorkflowTemplateError):
    """An immutable execution snapshot already exists for the task."""


class ExecutionSnapshotIntegrityError(WorkflowTemplateError):
    """A persisted execution snapshot failed checksum verification."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WorkflowTemplateError("value must be finite JSON data") from exc
    if len(rendered) > _MAX_JSON_BYTES:
        raise WorkflowTemplateError("JSON document exceeds the 4 MiB safety limit")
    return rendered


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _validate_safe_id(value: str, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise WorkflowTemplateError(f"{field} is not a safe identifier")
    return value


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write while persisting JSON")
        view = view[written:]


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_create_json(path: Path, value: Any, *, mode: int = 0o600) -> None:
    """Create a JSON file without ever replacing an existing target."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    payload = _canonical_bytes(value) + b"\n"
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temporary, flags, mode)
    try:
        _write_all(fd, payload)
        os.fsync(fd)
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
    try:
        os.link(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise WorkflowTemplateError("refusing to read a symbolic-link state file")
    size = path.stat().st_size
    if size > _MAX_JSON_BYTES + 1:
        raise WorkflowTemplateError("persisted JSON exceeds the safety limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowTemplateError(f"invalid persisted JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise WorkflowTemplateError("persisted JSON root must be an object")
    return value


def validate_registered_parameters(
    graph: WorkflowGraph,
    registry: NodeRegistry,
) -> None:
    """Reject parameters outside each registered node's deterministic schema."""

    for node in graph.nodes:
        definition = registry.get(node.type)
        if definition is None:
            continue
        schema = definition.parameters_schema
        unknown = sorted(set(node.parameters) - set(schema))
        if unknown:
            raise WorkflowTemplateError(
                f"node {node.id} contains non-registered parameters: "
                + ", ".join(unknown)
            )
        for key, value in node.parameters.items():
            rule = schema[key]
            expected = rule.get("type")
            if expected == "integer":
                valid_type = type(value) is int
            elif expected == "number":
                valid_type = (
                    type(value) in {int, float}
                    and math.isfinite(float(value))
                )
            elif expected == "string":
                valid_type = isinstance(value, str) and len(value) <= 1000
            elif expected == "boolean":
                valid_type = type(value) is bool
            else:
                valid_type = False
            if not valid_type:
                raise WorkflowTemplateError(
                    f"node {node.id} parameter {key} has an invalid type"
                )
            if "enum" in rule and value not in rule["enum"]:
                raise WorkflowTemplateError(
                    f"node {node.id} parameter {key} is outside its allow-list"
                )
            if "minimum" in rule and value < rule["minimum"]:
                raise WorkflowTemplateError(
                    f"node {node.id} parameter {key} is below its minimum"
                )
            if "maximum" in rule and value > rule["maximum"]:
                raise WorkflowTemplateError(
                    f"node {node.id} parameter {key} exceeds its maximum"
                )


def validate_workflow_document(
    workflow: WorkflowGraph | Mapping[str, Any],
    registry: NodeRegistry,
    *,
    require_executable: bool = False,
) -> tuple[WorkflowGraph, GraphValidationResult]:
    try:
        graph = (
            workflow
            if isinstance(workflow, WorkflowGraph)
            else WorkflowGraph.model_validate(workflow)
        )
    except ValidationError as exc:
        raise WorkflowTemplateError(f"invalid Workflow JSON: {exc}") from exc
    validate_registered_parameters(graph, registry)
    result = validate_workflow_graph(graph, registry)
    if result.hard_errors:
        codes = ", ".join(issue.code for issue in result.hard_errors)
        raise WorkflowTemplateError(
            f"workflow failed hard validation: {codes}"
        )
    if require_executable and not result.can_execute:
        keys = [
            issue.confirmation_key
            for issue in result.unconfirmed_warnings
            if issue.confirmation_key
        ]
        raise WorkflowTemplateError(
            "workflow has unconfirmed risks: " + ", ".join(keys)
        )
    return graph, result


def _node(node_id: str, node_type: str, **parameters: Any) -> dict[str, Any]:
    return {"id": node_id, "type": node_type, "parameters": parameters}


def _edge(
    edge_id: str,
    source_node: str,
    source_port: str,
    target_node: str,
    target_port: str,
) -> dict[str, str]:
    return {
        "id": edge_id,
        "source_node": source_node,
        "source_port": source_port,
        "target_node": target_node,
        "target_port": target_port,
    }


def _standard_workflow() -> dict[str, Any]:
    nodes = [
        _node("input", "fastq_input"),
        _node("raw_qc", "fastqc", threads=4),
        _node("trim", "fastp", threads=4),
        _node("host", "host_depletion", threads=4),
        _node("taxonomy", "taxonomy", threads=4),
        _node("function", "functional_annotation", threads=4),
        _node("report", "report"),
    ]
    edges = [
        _edge("e01", "input", "reads", "raw_qc", "reads"),
        _edge("e02", "input", "reads", "trim", "reads"),
        _edge("e03", "trim", "reads", "host", "reads"),
        _edge("e04", "host", "reads", "taxonomy", "reads"),
        _edge("e05", "host", "reads", "function", "reads"),
        _edge("e06", "raw_qc", "report", "report", "artifacts"),
        _edge("e07", "trim", "metrics", "report", "artifacts"),
        _edge("e08", "host", "metrics", "report", "artifacts"),
        _edge("e09", "taxonomy", "abundance", "report", "artifacts"),
        _edge("e10", "function", "functions", "report", "artifacts"),
    ]
    return {
        "schema_version": "1.0",
        "nodes": nodes,
        "edges": edges,
        "accepted_risks": [],
    }


def _mag_workflow() -> dict[str, Any]:
    nodes = [
        _node("input", "fastq_input"),
        _node("trim", "fastp", threads=8),
        _node("host", "host_depletion", threads=8),
        _node(
            "assembly",
            "assembly",
            threads=8,
            memory_gb=32,
            assembler="megahit",
        ),
        _node("binning", "binning"),
        _node(
            "refine",
            "bin_refinement",
            completeness=70,
            contamination=5,
        ),
        _node("quantify", "bin_quantification"),
        _node("annotate", "bin_annotation"),
        _node("report", "report"),
    ]
    edges = [
        _edge("e01", "input", "reads", "trim", "reads"),
        _edge("e02", "trim", "reads", "host", "reads"),
        _edge("e03", "host", "reads", "assembly", "reads"),
        _edge("e04", "assembly", "assembly", "binning", "assembly"),
        _edge("e05", "binning", "bins", "refine", "bins"),
        _edge("e06", "refine", "bins", "quantify", "bins"),
        _edge("e07", "host", "reads", "quantify", "reads"),
        _edge("e08", "refine", "bins", "annotate", "mags"),
        _edge("e09", "quantify", "abundance", "report", "artifacts"),
        _edge("e10", "annotate", "taxonomy", "report", "artifacts"),
        _edge("e11", "annotate", "functions", "report", "artifacts"),
    ]
    return {
        "schema_version": "1.0",
        "nodes": nodes,
        "edges": edges,
        "accepted_risks": [],
    }


_BUILTIN_SPECS = (
    (
        "builtin-read-profile",
        "标准 reads 物种与功能分析",
        "质控、去宿主、物种注释、功能注释和汇总报告。",
        _standard_workflow(),
    ),
    (
        "builtin-mag",
        "标准 MAG 分析",
        "去宿主、联合组装、分箱、精炼、定量、注释和汇总报告。",
        _mag_workflow(),
    ),
)


class WorkflowTemplateService:
    """Immutable Workflow JSON versions and per-task execution snapshots."""

    def __init__(
        self,
        state_root: Path,
        input_root: Path,
        *,
        registry: NodeRegistry | None = None,
    ) -> None:
        self.root = Path(state_root)
        self.template_root = self.root / "templates"
        self.snapshot_root = self.root / "execution_snapshots"
        self.input_root = Path(input_root).resolve()
        self.registry = registry or default_node_registry()
        self._lock = threading.RLock()
        self.template_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.snapshot_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.template_root, 0o700)
        os.chmod(self.snapshot_root, 0o700)
        self._builtins = self._build_builtins()

    def _build_builtins(self) -> dict[str, dict[str, Any]]:
        builtins: dict[str, dict[str, Any]] = {}
        for template_id, name, description, workflow in _BUILTIN_SPECS:
            graph, validation = validate_workflow_document(
                workflow,
                self.registry,
            )
            workflow_json = graph.model_dump(mode="json")
            builtins[template_id] = {
                "schema_version": "1.0",
                "template_id": template_id,
                "version": 1,
                "name": name,
                "description": description,
                "source": "builtin",
                "read_only": True,
                "parent_version": None,
                "copied_from": None,
                "created_at": "2026-08-26T00:00:00Z",
                "registry_version": REGISTRY_VERSION,
                "workflow_sha256": _sha256_json(workflow_json),
                "workflow": workflow_json,
                "validation": validation.model_dump(mode="json"),
            }
        return builtins

    def _template_dir(self, template_id: str) -> Path:
        return self.template_root / _validate_safe_id(
            template_id,
            "template_id",
        )

    def _available_versions(self, template_id: str) -> list[int]:
        directory = self._template_dir(template_id)
        if not directory.is_dir() or directory.is_symlink():
            return []
        versions = []
        for path in directory.iterdir():
            match = _VERSION_FILE.fullmatch(path.name)
            if match and path.is_file() and not path.is_symlink():
                versions.append(int(match.group(1)))
        return sorted(versions)

    def list_templates(self) -> list[dict[str, Any]]:
        items = [
            copy.deepcopy(value) for value in self._builtins.values()
        ]
        directories = sorted(
            self.template_root.iterdir(),
            key=lambda item: item.name,
        )
        for directory in directories:
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                versions = self._available_versions(directory.name)
            except WorkflowTemplateError:
                continue
            if versions:
                items.append(
                    self.get_template(directory.name, versions[-1])
                )
        return items

    def _verify_template_record(
        self,
        record: dict[str, Any],
        template_id: str,
        version: int,
    ) -> dict[str, Any]:
        if (
            record.get("template_id") != template_id
            or record.get("version") != version
        ):
            raise WorkflowTemplateError(
                "persisted template identity does not match its path"
            )
        workflow = record.get("workflow")
        digest = record.get("workflow_sha256")
        if (
            not isinstance(workflow, dict)
            or not isinstance(digest, str)
            or digest != _sha256_json(workflow)
        ):
            raise WorkflowTemplateError(
                "persisted template workflow checksum failed"
            )
        return record

    def get_template(
        self,
        template_id: str,
        version: int | None = None,
    ) -> dict[str, Any]:
        _validate_safe_id(template_id, "template_id")
        if template_id in self._builtins:
            if version not in (None, 1):
                raise TemplateNotFoundError((template_id, version))
            return copy.deepcopy(self._builtins[template_id])
        versions = self._available_versions(template_id)
        if not versions:
            raise TemplateNotFoundError(template_id)
        selected = versions[-1] if version is None else version
        if selected not in versions:
            raise TemplateNotFoundError((template_id, selected))
        path = self._template_dir(template_id) / f"v{selected:06d}.json"
        record = self._verify_template_record(
            _read_json(path),
            template_id,
            selected,
        )
        return copy.deepcopy(record)

    def save_template(
        self,
        *,
        name: str,
        workflow: WorkflowGraph | Mapping[str, Any],
        description: str = "",
        template_id: str | None = None,
        base_version: int | None = None,
        copied_from: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        name = name.strip() if isinstance(name, str) else ""
        description = (
            description.strip() if isinstance(description, str) else ""
        )
        if not name or len(name) > 120:
            raise WorkflowTemplateError(
                "name must contain 1 to 120 characters"
            )
        if len(description) > 1000:
            raise WorkflowTemplateError(
                "description exceeds 1000 characters"
            )
        graph, validation = validate_workflow_document(
            workflow,
            self.registry,
        )
        workflow_json = graph.model_dump(mode="json")
        with self._lock:
            if template_id is None:
                template_id = f"wf-{uuid.uuid4().hex}"
            _validate_safe_id(template_id, "template_id")
            if template_id in self._builtins:
                raise TemplateVersionConflictError(
                    "built-in templates are read-only; copy one before editing"
                )
            versions = self._available_versions(template_id)
            if versions:
                latest = versions[-1]
                self.get_template(template_id, latest)
                if base_version is None:
                    raise TemplateVersionConflictError(
                        "base_version is required when updating a template"
                    )
                if base_version != latest:
                    raise TemplateVersionConflictError(
                        f"base_version {base_version} is stale; "
                        f"latest is {latest}"
                    )
                version = latest + 1
                parent_version = latest
            else:
                if base_version is not None:
                    raise TemplateVersionConflictError(
                        "base_version must be omitted for a new template"
                    )
                version = 1
                parent_version = None
            record = {
                "schema_version": "1.0",
                "template_id": template_id,
                "version": version,
                "name": name,
                "description": description,
                "source": "user",
                "read_only": False,
                "parent_version": parent_version,
                "copied_from": (
                    copy.deepcopy(dict(copied_from))
                    if copied_from
                    else None
                ),
                "created_at": _utc_now(),
                "registry_version": REGISTRY_VERSION,
                "workflow_sha256": _sha256_json(workflow_json),
                "workflow": workflow_json,
                "validation": validation.model_dump(mode="json"),
            }
            path = (
                self._template_dir(template_id)
                / f"v{version:06d}.json"
            )
            try:
                _atomic_create_json(path, record)
            except FileExistsError as exc:
                raise TemplateVersionConflictError(
                    "template version was created concurrently"
                ) from exc
            return copy.deepcopy(record)

    def copy_template(
        self,
        source_template_id: str,
        *,
        source_version: int | None = None,
        name: str,
        description: str | None = None,
        template_id: str | None = None,
    ) -> dict[str, Any]:
        source = self.get_template(
            source_template_id,
            source_version,
        )
        return self.save_template(
            name=name,
            description=(
                source["description"]
                if description is None
                else description
            ),
            workflow=source["workflow"],
            template_id=template_id,
            copied_from={
                "template_id": source["template_id"],
                "version": source["version"],
                "workflow_sha256": source["workflow_sha256"],
            },
        )

    def _hash_input_file(
        self,
        raw_path: str | Path,
    ) -> dict[str, Any]:
        path = Path(raw_path)
        if path.is_symlink():
            raise WorkflowTemplateError(
                "input file cannot be a symbolic link"
            )
        try:
            resolved = path.resolve(strict=True)
            relative = resolved.relative_to(self.input_root)
        except (FileNotFoundError, ValueError) as exc:
            raise WorkflowTemplateError(
                "input file is missing or outside the approved input root"
            ) from exc
        if not resolved.is_file():
            raise WorkflowTemplateError(
                "input path must reference a regular file"
            )
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            for chunk in iter(
                lambda: handle.read(1024 * 1024),
                b"",
            ):
                digest.update(chunk)
        return {
            "relative_path": relative.as_posix(),
            "size_bytes": resolved.stat().st_size,
            "sha256": digest.hexdigest(),
        }

    @staticmethod
    def _bounded_mapping(
        value: Mapping[str, Any],
        field: str,
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise WorkflowTemplateError(
                f"{field} must be a JSON object"
            )
        copied = copy.deepcopy(dict(value))
        if len(_canonical_bytes(copied)) > 256 * 1024:
            raise WorkflowTemplateError(
                f"{field} exceeds 256 KiB"
            )
        return copied

    def create_execution_snapshot(
        self,
        *,
        task_id: str,
        workflow: WorkflowGraph | Mapping[str, Any],
        confirmed_risks: Sequence[str],
        input_files: Sequence[str | Path],
        database_profile: str,
        resolved_manifest_summary: Mapping[str, Any],
        compilation_summary: Mapping[str, Any],
    ) -> dict[str, Any]:
        _validate_safe_id(task_id, "task_id")
        graph, validation = validate_workflow_document(
            workflow,
            self.registry,
            require_executable=True,
        )
        if isinstance(confirmed_risks, (str, bytes)):
            raise WorkflowTemplateError(
                "confirmed_risks must be a list"
            )
        risk_list = list(confirmed_risks)
        if any(not isinstance(item, str) for item in risk_list):
            raise WorkflowTemplateError(
                "confirmed_risks entries must be strings"
            )
        known_risks = {
            issue.confirmation_key
            for issue in validation.warnings
            if issue.confirmation_key
        }
        if set(risk_list) != set(graph.accepted_risks):
            raise WorkflowTemplateError(
                "confirmed_risks must exactly match "
                "workflow.accepted_risks"
            )
        if set(risk_list) - known_risks:
            raise WorkflowTemplateError(
                "confirmed_risks contains an unknown risk key"
            )
        if len(risk_list) != len(set(risk_list)):
            raise WorkflowTemplateError(
                "confirmed_risks cannot contain duplicates"
            )
        if (
            not isinstance(database_profile, str)
            or not database_profile.strip()
        ):
            raise WorkflowTemplateError(
                "database_profile is required"
            )
        if len(database_profile) > 128:
            raise WorkflowTemplateError(
                "database_profile exceeds 128 characters"
            )
        if isinstance(input_files, (str, bytes)) or not input_files:
            raise WorkflowTemplateError(
                "at least one input file is required"
            )
        hashed_inputs = [
            self._hash_input_file(path) for path in input_files
        ]
        relative_paths = [
            item["relative_path"] for item in hashed_inputs
        ]
        if len(relative_paths) != len(set(relative_paths)):
            raise WorkflowTemplateError(
                "input_files cannot contain duplicates"
            )
        workflow_json = graph.model_dump(mode="json")
        body = {
            "schema_version": "1.0",
            "task_id": task_id,
            "created_at": _utc_now(),
            "workflow": workflow_json,
            "workflow_sha256": _sha256_json(workflow_json),
            "confirmed_risks": risk_list,
            "input_files": hashed_inputs,
            "node_parameters": {
                node.id: copy.deepcopy(node.parameters)
                for node in graph.nodes
            },
            "node_registry_version": REGISTRY_VERSION,
            "database_profile": database_profile.strip(),
            "resolved_manifest_summary": self._bounded_mapping(
                resolved_manifest_summary,
                "resolved_manifest_summary",
            ),
            "compilation_summary": self._bounded_mapping(
                compilation_summary,
                "compilation_summary",
            ),
        }
        snapshot = {
            **body,
            "snapshot_sha256": _sha256_json(body),
        }
        path = self.snapshot_root / f"{task_id}.json"
        with self._lock:
            try:
                _atomic_create_json(
                    path,
                    snapshot,
                    mode=0o440,
                )
            except FileExistsError as exc:
                raise ExecutionSnapshotConflictError(
                    "execution snapshot is immutable and already exists"
                ) from exc
        return copy.deepcopy(snapshot)

    def get_execution_snapshot(
        self,
        task_id: str,
    ) -> dict[str, Any]:
        _validate_safe_id(task_id, "task_id")
        path = self.snapshot_root / f"{task_id}.json"
        try:
            snapshot = _read_json(path)
        except FileNotFoundError as exc:
            raise TemplateNotFoundError(task_id) from exc
        digest = snapshot.get("snapshot_sha256")
        body = {
            key: value
            for key, value in snapshot.items()
            if key != "snapshot_sha256"
        }
        if (
            not isinstance(digest, str)
            or digest != _sha256_json(body)
        ):
            raise ExecutionSnapshotIntegrityError(
                "execution snapshot checksum verification failed"
            )
        return copy.deepcopy(snapshot)
