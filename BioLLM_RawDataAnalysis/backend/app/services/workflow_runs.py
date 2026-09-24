from __future__ import annotations

import csv
import json
import os
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from backend.app.config import Settings
from .input_lifecycle import input_operation
from backend.app.services.redaction import redact_log, tail_excerpt
from backend.app.services.tasks import TaskService, TaskValidationError
from backend.app.workflows.compiler import (
    WorkflowCompilationError,
    compile_workflow,
)
from backend.app.workflows.execution import build_nextflow_command
from backend.app.workflows.graph import WorkflowGraph
from backend.app.workflows.materializer import (
    CompiledArtifacts,
    materialize_compilation,
)
from backend.app.workflows.registry import NodeRegistry, default_node_registry
from backend.app.workflows.state import WorkflowRunRepository


class WorkflowRunValidationError(ValueError):
    """A dynamic workflow cannot be compiled or safely executed."""


class WorkflowRunService:
    def __init__(
        self,
        settings: Settings,
        task_service: TaskService,
        repository: WorkflowRunRepository,
        *,
        registry: NodeRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.task_service = task_service
        self.repository = repository
        self.registry = registry or default_node_registry()

    @input_operation
    def create_run(
        self,
        workflow: WorkflowGraph,
        manifest_path: str,
    ) -> dict[str, object]:
        try:
            manifest = self.task_service.validate_manifest_path(manifest_path)
            compiled = compile_workflow(
                workflow,
                self.registry,
                project_root=self.settings.project_root,
            )
        except (TaskValidationError, WorkflowCompilationError) as exc:
            raise WorkflowRunValidationError(str(exc)) from exc

        database_manifest: Path | None = None
        if "params.database_manifest" in compiled.source:
            configured = self.settings.default_database_manifest
            if configured is None:
                raise WorkflowRunValidationError(
                    "a resolved database manifest is required by this workflow"
                )
            try:
                database_manifest = configured.resolve(strict=True)
            except (FileNotFoundError, OSError) as exc:
                raise WorkflowRunValidationError(
                    "the configured database manifest does not exist"
                ) from exc
            if not database_manifest.is_file():
                raise WorkflowRunValidationError(
                    "the configured database manifest is not a regular file"
                )

        task_id = str(uuid.uuid4())
        artifacts = materialize_compilation(
            compiled,
            task_id=task_id,
            state_root=self.settings.state_root,
            workflow_root=self.settings.project_root / "workflow",
        )
        return self.repository.create_run(
            task_id=task_id,
            graph_hash=compiled.graph_hash,
            compiled_root=artifacts.root,
            nodes=workflow.nodes,
            topological_order=compiled.topological_order,
            input_manifest=manifest,
            database_manifest=database_manifest,
        )

    def get_run(self, task_id: str) -> dict[str, object] | None:
        return self.repository.get_run(task_id)

    def read_log(self, task_id: str, max_bytes: int = 65536) -> str:
        run = self.repository.get_run(task_id)
        if run is None:
            raise KeyError(task_id)
        raw_path = run.get("log_path")
        if not isinstance(raw_path, str) or not raw_path:
            raise FileNotFoundError("dynamic workflow log is not available")
        path = Path(raw_path)
        if not path.is_file():
            raise FileNotFoundError("dynamic workflow log is not available")
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            content = handle.read(max_bytes).decode("utf-8", errors="replace")
        return redact_log(
            tail_excerpt(content, max_chars=max_bytes),
            input_root=str(self.settings.input_root.resolve()),
        )


class DynamicWorkflowRunner:
    """Run an immutable compiled workflow without invoking a shell."""

    def __init__(
        self,
        settings: Settings,
        repository: WorkflowRunRepository,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self._lock = threading.Lock()
        self._active: set[str] = set()
        self._processes: dict[str, subprocess.Popen[bytes]] = {}

    def submit(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self._active:
                return False
            self._active.add(task_id)
        thread = threading.Thread(
            target=self._run_guarded,
            args=(task_id,),
            daemon=True,
        )
        thread.start()
        return True

    def _run_guarded(self, task_id: str) -> None:
        try:
            self.run(task_id)
        finally:
            with self._lock:
                self._active.discard(task_id)
                self._processes.pop(task_id, None)

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            process = self._processes.get(task_id)
        if process is None or process.poll() is not None:
            return False
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return False
        return True

    @staticmethod
    def _artifacts(run: dict[str, object]) -> CompiledArtifacts:
        root = Path(str(run["compiled_root"]))
        return CompiledArtifacts(
            root=root,
            source_path=root / "main.nf",
            config_path=root / "nextflow.config",
            parameters_path=root / "parameters.json",
            summary_path=root / "summary.json",
        )

    def _queue_nodes(self, task_id: str) -> None:
        run = self.repository.get_run(task_id)
        if run is None:
            raise KeyError(task_id)
        for node in run["nodes"]:
            if node["status"] == "pending":
                self.repository.update_node_status(
                    task_id, str(node["node_id"]), "queued"
                )

    def _finish_successfully(self, task_id: str) -> None:
        run = self.repository.get_run(task_id)
        if run is None:
            raise KeyError(task_id)
        for node in run["nodes"]:
            node_id = str(node["node_id"])
            status = str(node["status"])
            if status == "queued":
                self.repository.update_node_status(task_id, node_id, "running")
                status = "running"
            if status == "running":
                self.repository.update_node_status(task_id, node_id, "succeeded")

    @staticmethod
    def _node_aliases(run: dict[str, object]) -> dict[str, str]:
        summary_path = DynamicWorkflowRunner._artifacts(run).summary_path
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowRunValidationError(
                "compiled workflow summary cannot be read"
            ) from exc
        aliases = summary.get("node_aliases")
        if not isinstance(aliases, dict):
            raise WorkflowRunValidationError(
                "compiled workflow summary has no node aliases"
            )
        return {
            str(node_id): str(alias)
            for node_id, alias in aliases.items()
            if isinstance(node_id, str) and isinstance(alias, str)
        }

    @staticmethod
    def _read_trace(trace_path: Path) -> list[dict[str, str]]:
        if not trace_path.is_file():
            return []
        try:
            with trace_path.open(encoding="utf-8", errors="replace", newline="") as handle:
                return list(csv.DictReader(handle, delimiter="\t"))
        except OSError:
            return []

    def _sync_trace(
        self,
        task_id: str,
        trace_path: Path,
        aliases: dict[str, str],
        log_path: Path,
    ) -> str | None:
        """Reflect trace evidence in node state and identify the failed node."""
        rows = self._read_trace(trace_path)
        run = self.repository.get_run(task_id)
        if run is None:
            raise KeyError(task_id)
        nodes = {str(node["node_id"]): node for node in run["nodes"]}
        failed_node: str | None = None
        terminal = {"COMPLETED", "CACHED"}
        failed = {"FAILED", "ABORTED"}
        for node_id, alias in aliases.items():
            matching = [
                row
                for row in rows
                if alias and alias in str(row.get("name", ""))
            ]
            if not matching or node_id not in nodes:
                continue
            statuses = {str(row.get("status", "")).upper() for row in matching}
            if statuses & failed:
                failed_node = node_id
            completed = sum(
                str(row.get("status", "")).upper() in terminal
                for row in matching
            )
            # The trace grows while samples are discovered, so keep live progress
            # below 100 until Nextflow itself exits successfully.
            progress = min(95.0, max(5.0, completed / len(matching) * 95.0))
            current = str(nodes[node_id]["status"])
            if current == "queued":
                self.repository.update_node_status(
                    task_id,
                    node_id,
                    "running",
                    progress=progress,
                    log_path=log_path,
                )
            elif current == "running":
                self.repository.update_node_progress(task_id, node_id, progress)
        return failed_node

    def run(self, task_id: str) -> None:
        run = self.repository.get_run(task_id)
        if run is None:
            raise KeyError(task_id)
        if run["status"] != "queued":
            raise ValueError("dynamic workflow run is not queued")
        manifest_value = run.get("input_manifest")
        if not isinstance(manifest_value, str) or not manifest_value:
            raise WorkflowRunValidationError("run has no input manifest")

        self._queue_nodes(task_id)
        queued = self.repository.get_run(task_id)
        if queued is None:
            raise KeyError(task_id)
        input_nodes = [
            node for node in queued["nodes"] if node["node_type"] == "fastq_input"
        ]
        for node in input_nodes:
            node_id = str(node["node_id"])
            self.repository.update_node_status(task_id, node_id, "running")
            self.repository.update_node_status(task_id, node_id, "succeeded")

        executable = next(
            (
                node
                for node in self.repository.get_run(task_id)["nodes"]
                if node["status"] == "queued"
            ),
            None,
        )
        if executable is None:
            return
        active_node_id = str(executable["node_id"])

        output_dir = self.settings.state_root / "outputs" / task_id
        work_dir = self.settings.state_root / "work" / task_id
        log_dir = self.settings.state_root / "logs" / task_id
        for directory in (output_dir, work_dir, log_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        log_path = log_dir / "nextflow.log"
        trace_path = log_dir / "trace.tsv"
        self.repository.set_run_log_path(task_id, log_path)
        aliases = self._node_aliases(run)
        database_value = run.get("database_manifest")
        command = build_nextflow_command(
            self.settings,
            task_id=task_id,
            artifacts=self._artifacts(run),
            input_manifest=Path(manifest_value),
            database_manifest=(
                Path(database_value)
                if isinstance(database_value, str) and database_value
                else None
            ),
            output_dir=output_dir,
            work_dir=work_dir,
            log_dir=log_dir,
        )
        self.repository.update_node_status(
            task_id,
            active_node_id,
            "running",
            log_path=log_path,
        )
        try:
            with log_path.open("wb") as log_handle:
                process = subprocess.Popen(
                    command,
                    cwd=self.settings.project_root,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    start_new_session=True,
                )
                with self._lock:
                    self._processes[task_id] = process
                failed_node_id: str | None = None
                while True:
                    observed_failure = self._sync_trace(
                        task_id,
                        trace_path,
                        aliases,
                        log_path,
                    )
                    failed_node_id = observed_failure or failed_node_id
                    exit_code = process.poll()
                    if exit_code is not None:
                        break
                    time.sleep(max(0.05, self.settings.poll_interval_seconds))
                observed_failure = self._sync_trace(
                    task_id,
                    trace_path,
                    aliases,
                    log_path,
                )
                failed_node_id = observed_failure or failed_node_id
        except OSError as exc:
            self.repository.update_node_status(
                task_id,
                active_node_id,
                "failed",
                error_message=f"could not start Nextflow: {exc}",
                log_path=log_path,
            )
            return
        finally:
            with self._lock:
                self._processes.pop(task_id, None)

        if exit_code == 0:
            self._finish_successfully(task_id)
        else:
            failed_node_id = failed_node_id or active_node_id
            failed_run = self.repository.get_run(task_id)
            if failed_run is None:
                raise KeyError(task_id)
            failed_status = next(
                str(node["status"])
                for node in failed_run["nodes"]
                if str(node["node_id"]) == failed_node_id
            )
            if failed_status == "queued":
                self.repository.update_node_status(
                    task_id,
                    failed_node_id,
                    "running",
                    log_path=log_path,
                )
            self.repository.update_node_status(
                task_id,
                failed_node_id,
                "failed",
                error_message=f"Nextflow exited with code {exit_code}",
                log_path=log_path,
            )
