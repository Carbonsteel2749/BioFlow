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
from .resource_scheduler import ResourceScheduler

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
        metadata_service=None,
    ) -> None:
        self.settings = settings
        self.task_service = task_service
        self.repository = repository
        self.registry = registry or default_node_registry()
        self.metadata_service = metadata_service

    @input_operation
    def create_run(
        self,
        workflow: WorkflowGraph,
        manifest_path: str,
        *, name: str = '', auto_repair: bool = False,
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
        # The graph shown in history is the submitted graph, never a later template.
        (artifacts.root / 'workflow.json').write_text(workflow.model_dump_json(), encoding='utf-8')
        try:
            snapshot = self.metadata_service.snapshot_for_manifest(manifest, task_id,
                self.settings.state_root / 'outputs' / task_id / 'metadata') if self.metadata_service else None
        except ValueError as exc:
            raise WorkflowRunValidationError(str(exc)) from exc
        self.repository.create_run(
            task_id=task_id,
            graph_hash=compiled.graph_hash,
            compiled_root=artifacts.root,
            nodes=workflow.nodes,
            topological_order=compiled.topological_order,
            input_manifest=manifest,
            database_manifest=database_manifest,
            name=name, auto_repair=auto_repair,
        )
        if snapshot:
            self.repository.set_metadata_snapshot(task_id, Path(snapshot['groups_path']), snapshot['sha256'])
        return self.repository.get_run(task_id)

    def get_run(self, task_id: str) -> dict[str, object] | None:
        return self.repository.get_run(task_id)

    def get_workflow(self, task_id: str) -> dict:
        run = self.repository.get_run(task_id)
        if run is None:
            raise KeyError(task_id)
        root = Path(str(run['compiled_root'])).resolve()
        path = root / 'workflow.json'
        if not root.is_relative_to(self.settings.state_root.resolve()) or path.is_symlink():
            raise FileNotFoundError('workflow snapshot is unavailable')
        try:
            return WorkflowGraph.model_validate_json(path.read_text(encoding='utf-8')).model_dump()
        except (OSError, ValueError) as exc:
            raise FileNotFoundError('workflow snapshot is unavailable for this historical run') from exc

    def resume_run(self, task_id: str) -> dict[str, object]:
        run = self.repository.get_run(task_id)
        if run is None:
            raise KeyError(task_id)
        root = Path(str(run['compiled_root'])).resolve()
        if not root.is_relative_to(self.settings.state_root.resolve()) or not (root / 'main.nf').is_file():
            raise WorkflowRunValidationError('immutable workflow compilation is missing; cannot resume')
        return self.repository.resume_run(task_id)

    def read_log(self, task_id: str, max_bytes: int = 65536, *, attempt: int | None = None) -> str:
        run = self.repository.get_run(task_id)
        if run is None:
            raise KeyError(task_id)
        raw_path = run.get("log_path")
        if attempt is not None:
            raw_path = next((item['log_path'] for item in run.get('attempts', []) if item['attempt'] == attempt), None)
        if not isinstance(raw_path, str) or not raw_path:
            raise FileNotFoundError("dynamic workflow log is not available")
        path = Path(raw_path)
        log_root = (self.settings.state_root / 'logs' / task_id).resolve()
        if not path.is_file() or path.resolve() != path.absolute() or not path.resolve().is_relative_to(log_root):
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
        *, scheduler: ResourceScheduler | None = None,
        artifact_service=None,
        execution_manager=None,
        repair_service=None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.artifact_service = artifact_service
        self.execution_manager = execution_manager
        self.repair_service = repair_service
        if settings.execution_isolation == 'systemd' and execution_manager is None:
            raise RuntimeError('systemd isolation requires an execution manager')
        self.scheduler = scheduler or ResourceScheduler(settings.state_root,
            default_limit=settings.max_concurrent_tasks, minimum_free_bytes=settings.minimum_free_disk_bytes)
        self._lock = threading.Lock()
        self._active: set[str] = set()
        self._retry_pending: set[str] = set()
        self._processes: dict[str, subprocess.Popen[bytes]] = {}

    def submit(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self._active:
                return False
            self._active.add(task_id)
        submitted = self.scheduler.submit('dynamic:' + task_id, lambda: self._run_guarded(task_id),
            on_rejected=lambda reason: self._admission_rejected(task_id, reason),
            on_finished=lambda: self._release_active(task_id),
            thread_budget=lambda: self._configured_threads(task_id))
        if not submitted:
            with self._lock:
                self._active.discard(task_id)
        return submitted

    def _configured_threads(self, task_id: str) -> int | None:
        run = self.repository.get_run(task_id)
        if not run:
            return None
        try:
            root = Path(str(run['compiled_root'])).resolve()
            if not root.is_relative_to((self.settings.state_root/'workflow_runs'/task_id).resolve()):
                return None
            path = root/'summary.json'
            if path.is_symlink() or path.stat().st_size > 1024*1024:
                return None
            summary = json.loads(path.read_text(encoding='utf-8'))
            parameters = summary['node_parameters']
            registry = default_node_registry()
            budgets = [1]
            for node in run['nodes']:
                if node['node_type'] == 'fastq_input':
                    continue
                values = parameters[node['node_id']]
                budget = values.get('threads', registry[node['node_type']].default_cpus)
                if values.get('enable_mags') is True:
                    budget = max(budget, values.get('mag_threads', 8))
                if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
                    return None
                budgets.append(budget)
            return max(budgets)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def is_active(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._active

    def _admission_rejected(self, task_id: str, reason: str) -> None:
        self.repository.pause_run(task_id, reason)

    def _release_active(self, task_id: str) -> None:
        # Remain active through archive finalization and scheduler slot release.
        with self._lock:
            self._active.discard(task_id)
            retry = task_id in self._retry_pending
            self._retry_pending.discard(task_id)
        if retry and (self.repository.get_run(task_id) or {}).get('status') == 'queued':
            if not self.submit(task_id):
                self.repository.pause_run(task_id, 'automatic resume admission failed; please retry manually')

    def _run_guarded(self, task_id: str) -> None:
        try:
            self.run(task_id)
            if self.repair_service and self.repair_service.prepare_retry(task_id):
                with self._lock:
                    self._retry_pending.add(task_id)
        except Exception as exc:
            self.repository.fail_run(task_id, f'workflow execution failed: {type(exc).__name__}: {exc}')
        finally:
            from .full_archive import archive_after_run
            from .input_lifecycle import input_lock
            with input_lock(self.settings):
                try:
                    if self.artifact_service is not None:
                        from .dynamic_artifacts import publish_dynamic_artifacts
                        publish_dynamic_artifacts(self.settings, self.repository, self.artifact_service, task_id)
                    archive_after_run(self.settings, task_id)
                finally:
                    with self._lock:
                        self._processes.pop(task_id, None)

    def cancel(self, task_id: str) -> bool:
        self.repository.cancel_run(task_id)
        if self.scheduler.cancel_pending('dynamic:' + task_id):
            with self._lock:
                self._active.discard(task_id)
            return True
        with self._lock:
            process = self._processes.get(task_id)
        if process is None or process.poll() is not None:
            return False
        from .runner import TaskRunner
        TaskRunner._terminate_process(process)
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
                if self._trace_matches(row, alias)
            ]
            if not matching or node_id not in nodes:
                continue
            statuses = {str(row.get("status", "")).upper() for row in matching}
            if 'FAILED' in statuses:
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

    @staticmethod
    def _trace_matches(row: dict[str, str], alias: str) -> bool:
        # Nextflow names are workflow:process (tag). Tags are sample labels,
        # not process identity, and must not prove another node's completion.
        process_name = str(row.get('name', '')).partition(' (')[0].strip().rsplit(':', 1)[-1]
        return bool(alias) and process_name == alias

    def _retain_completed_nodes(self, task_id: str, trace: Path, aliases: dict[str, str], manifest: Path) -> None:
        """Only final trace evidence for all expected sample invocations proves completion."""
        with manifest.open(encoding='utf-8', newline='') as handle:
            sample_count = sum(bool(row.get('sample_id')) for row in csv.DictReader(handle))
        run = self.repository.get_run(task_id)
        if run is None or run['status'] == 'cancelled':
            return
        cohort_types = {'assembly','binning','bin_refinement','bin_quantification','bin_reassembly','bin_annotation','report'}
        rows = self._read_trace(trace)
        for node in run['nodes']:
            if node['status'] not in {'running', 'queued'}:
                continue
            alias = aliases.get(str(node['node_id']))
            matching = [row for row in rows if self._trace_matches(row, alias)]
            expected = 1 if node['node_type'] in cohort_types else sample_count
            if not matching or len(matching) < expected or not all(row.get('status') in {'COMPLETED','CACHED'} for row in matching):
                continue
            if node['status'] == 'queued':
                self.repository.update_node_status(task_id, str(node['node_id']), 'running')
            self.repository.update_node_status(task_id, str(node['node_id']), 'succeeded',
                cache_id=','.join(sorted({row.get('hash', '') for row in matching})))

    def run(self, task_id: str) -> None:
        run = self.repository.get_run(task_id)
        if run is None:
            raise KeyError(task_id)
        if run["status"] != "queued":
            if run['status'] == 'cancelled':
                return
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
            if node['status'] == 'succeeded':
                continue
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
        attempt = int(run.get('attempt', 1))
        log_dir = self.settings.state_root / "logs" / task_id / f'attempt-{attempt:03d}'
        for directory in (output_dir, work_dir, log_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        log_path = log_dir / "nextflow.log"
        trace_path = log_dir / "trace.tsv"
        self.repository.set_run_log_path(task_id, log_path)
        aliases = self._node_aliases(run)
        if self.repository.get_run(task_id)['status'] == 'cancelled':
            return
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
            resume=attempt > 1,
            sample_metadata=Path(run['metadata_snapshot']) if run.get('metadata_snapshot') else None,
            metadata_sha256=run.get('metadata_sha256'),
            repair_config=self.repair_service.execution_config(run) if self.repair_service else None,
        )
        self.repository.update_node_status(
            task_id,
            active_node_id,
            "running",
            log_path=log_path,
        )
        try:
            with log_path.open("wb") as log_handle:
                process = self.execution_manager.launch(command, cwd=self._artifacts(run).root,
                    log_handle=log_handle, task_id=task_id, attempt=attempt) if self.execution_manager else subprocess.Popen(
                    command,
                    cwd=self._artifacts(run).root,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    start_new_session=True,
                )
                with self._lock:
                    self._processes[task_id] = process
                if self.repair_service:
                    self.repair_service.mark_started(run)
                failed_node_id: str | None = None
                while True:
                    current = self.repository.get_run(task_id)
                    if current and current['status'] == 'cancelled':
                        from .runner import TaskRunner
                        TaskRunner._terminate_process(process)
                        return
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
        except Exception as exc:
            current = self.repository.get_run(task_id)
            if current and current['status'] != 'cancelled':
                self.repository.fail_run(task_id, f'Nextflow execution failed: {type(exc).__name__}: {exc}')
            return
        finally:
            if 'process' in locals() and process.poll() is None:
                from .runner import TaskRunner
                TaskRunner._terminate_process(process)
            with self._lock:
                self._processes.pop(task_id, None)

        if self.repository.get_run(task_id)['status'] == 'cancelled':
            return
        if exit_code == 0:
            self._finish_successfully(task_id)
        else:
            self._retain_completed_nodes(task_id, trace_path, aliases, Path(manifest_value))
            failed_aliases = {node_id for node_id, alias in aliases.items()
                if any(self._trace_matches(row, alias) and row.get('status') == 'FAILED' for row in self._read_trace(trace_path))}
            if len(failed_aliases) != 1:
                self.repository.fail_run(task_id, f'Nextflow exited with code {exit_code}; failure cannot be uniquely attributed to a calculation node; inspect launcher logs')
                return
            failed_node_id = next(iter(failed_aliases))
            failed_node_id = failed_node_id or active_node_id
            failed_run = self.repository.get_run(task_id)
            if failed_run is None:
                raise KeyError(task_id)
            failed_status = next(
                str(node["status"])
                for node in failed_run["nodes"]
                if str(node["node_id"]) == failed_node_id
            )
            if failed_status == 'succeeded':
                remaining = next((node for node in failed_run['nodes'] if node['status'] in {'queued','running'}), None)
                if remaining is None:
                    self.repository.fail_run(task_id, f'Nextflow exited with code {exit_code} after completed node traces; inspect launcher logs')
                    return
                failed_node_id = str(remaining['node_id'])
                failed_status = str(remaining['status'])
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
