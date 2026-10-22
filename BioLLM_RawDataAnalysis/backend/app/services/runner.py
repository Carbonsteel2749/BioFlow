from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tarfile
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import httpx

from ..config import Settings
from ..models import TaskRepository, utc_now
from .diagnostic_audit import append_diagnostic_audit
from .resource_scheduler import ResourceScheduler
from .execution_units import ExecutionUnitError, ExecutionUnitManager, ExecutionUnitProcess, ExecutionUnitUnavailable
from .llm_diagnostics import (
    Diagnostic,
    DiagnosticResponseError,
    OllamaDiagnosticService,
    unavailable_diagnostic,
)
from .progress import step_for_process, sync_trace
from .redaction import redact_log, tail_excerpt
from .retry_policy import RetryDecision, evaluate_retry
from .task_repairs import (
    PATCH_TARGETS,
    TaskRepairConflict,
    TaskRepairError,
    TaskRepairLimitReached,
    TaskRepairService,
)


def _strip_terminal_controls(text: str) -> str:
    text = re.sub(r"\x1b\].*?(?:\x07|\x1b\\)", "", text, flags=re.DOTALL)
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)


def _source_error_excerpt(redacted_excerpt: str) -> str:
    """Keep the workflow error visible even if the optional AI service fails."""
    text = _strip_terminal_controls(redacted_excerpt)
    matches = list(re.finditer(r"(?m)^.*ERROR\s*~", text))
    if matches:
        text = text[matches[-1].start():]
    else:
        text = "\n".join(text.strip().splitlines()[-12:])
    lines = text.strip().splitlines()
    # Keep actionable tool errors before verbose command/alignment output.
    causes = [line.strip() for line in lines if re.search(
        r'\[ERROR\]|failure_stage=|reason=|(?:Error|Exception):|Not a valid|No such file|Cannot |OutOfMemory|Killed', line
    )]
    heading = next((line.strip() for line in lines if 'ERROR' in line and '~' in line), '')
    if causes:
        return '\n'.join(dict.fromkeys([heading, *causes[-8:]])).strip()[:1800]
    text = re.split(r"\n\s*(?:Tip:|-- Check)", text, maxsplit=1)[0]
    if len(text.strip()) > 1800:
        return text.strip()[:500] + '\n[... 中间输出省略，完整内容请查看阶段日志 ...]\n' + text.strip()[-1200:]
    return text.strip()


WORKFLOW_PARAMETER_NAMES = (
    # Backend-owned immutable metadata snapshot, never client file paths.
    "sample_metadata",
    "metadata_sha256",
    "threads",
    "fastp_qualified_quality_phred",
    "fastp_unqualified_percent_limit",
    "fastp_n_base_limit",
    "fastp_length_required",
    "fastp_cut_front",
    "fastp_cut_tail",
    "fastp_cut_window_size",
    "fastp_cut_mean_quality",
    "fastp_trim_poly_g",
    "fastp_correction",
    "fastp_detect_adapter_for_pe",
    "enable_mags",
    "enable_reassembly",
    "mag_threads",
    "mag_memory_gb",
    "assembler",
    "bin_completeness",
    "bin_contamination",
    "host_index",
    "host_filter_mode",
    "host_min_retained_pairs",
    "host_max_removed_pct",
    "host_bowtie2_preset",
    "kraken_db",
    "read_length",
    "humann_nucleotide_db",
    "humann_protein_db",
    "metaphlan_db",
    "taxonomy_plot_top_n",
    "taxonomy_plot_low_abundance_threshold",
    "taxonomy_alpha_style",
    "functional_plot_top_n",
    "functional_plot_scale",
    "figure_extended_plots",
    "figure_ai_advice",
)


def _nextflow_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def build_nextflow_command(
    settings: Settings,
    task_id: str,
    manifest_path: Path,
    parameters: Mapping[str, Any] | None = None,
    *,
    resume: bool = False,
    output_dir: Path | None = None,
    work_dir: Path | None = None,
    workflow_path: Path | None = None,
) -> list[str]:
    output_dir = (
        output_dir or settings.state_root / "outputs" / task_id
    ).resolve()
    work_dir = (
        work_dir or settings.state_root / "work" / task_id
    ).resolve()
    selected_workflow = (workflow_path or settings.workflow_path).resolve()
    trace_path = output_dir / "trace.tsv"
    command = [
        settings.nextflow_bin,
        "run",
        str(selected_workflow),
        "--input_manifest",
        str(manifest_path.resolve()),
        "--outdir",
        str(output_dir),
        "--task_id",
        task_id,
    ]
    if settings.default_database_manifest is not None:
        if settings.default_database_registry is not None:
            command.extend(
                (
                    "--database_registry",
                    str(settings.default_database_registry.resolve()),
                )
            )
        if settings.default_database_profile is not None:
            command.extend(
                ("--database_profile", settings.default_database_profile)
            )
        command.extend(
            (
                "--database_manifest",
                str(settings.default_database_manifest.resolve()),
            )
        )
    supplied = parameters or {}
    for name in WORKFLOW_PARAMETER_NAMES:
        value = supplied.get(name)
        if value is not None:
            command.extend((f"--{name}", _nextflow_value(value)))
    command.extend(
        [
            "-work-dir",
            str(work_dir),
            "-with-trace",
            str(trace_path),
            "-with-timeline",
            str(output_dir / "timeline.html"),
            "-with-report",
            str(output_dir / "nextflow-report.html"),
        ]
    )
    if resume:
        command.append("-resume")
    return command


class TaskRunner:
    def __init__(
        self,
        settings: Settings,
        repository: TaskRepository,
        diagnostic_service: OllamaDiagnosticService | None = None,
        repair_service: TaskRepairService | None = None,
        scheduler: ResourceScheduler | None = None,
        execution_manager: ExecutionUnitManager | None = None,
    ):
        self.settings = settings
        self.repository = repository
        self.execution_manager = execution_manager
        if settings.execution_isolation == 'systemd' and execution_manager is None:
            raise ExecutionUnitUnavailable('systemd execution isolation requires an initialized execution manager')
        self.scheduler = scheduler or ResourceScheduler(settings.state_root,
            default_limit=settings.max_concurrent_tasks, minimum_free_bytes=settings.minimum_free_disk_bytes)
        self.diagnostic_service = diagnostic_service or OllamaDiagnosticService(
            base_url=settings.ollama_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
        )
        self.repair_service = repair_service or TaskRepairService(
            settings.state_root,
            settings.workflow_path,
        )
        self._lock = threading.Lock()
        self._active: set[str] = set()
        self._processes: dict[str, subprocess.Popen[bytes] | ExecutionUnitProcess] = {}

    def submit(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self._active:
                return False
            self._active.add(task_id)
        submitted = self.scheduler.submit('standard:' + task_id,
            lambda: self._run_guarded(task_id),
            on_rejected=lambda reason: self._admission_rejected(task_id, reason),
            on_finished=lambda: self._handoff_retry(task_id),
            resource_preflight=lambda: self._resource_report(task_id),
            thread_budget=lambda: self._configured_threads(task_id))
        if not submitted:
            with self._lock:
                self._active.discard(task_id)
        return submitted

    def _configured_threads(self, task_id: str) -> int | None:
        task = self.repository.get_task(task_id)
        if not task:
            return None
        parameters = task.get('parameters') or {}
        threads = parameters.get('threads', 4)
        if parameters.get('enable_mags') is True:
            threads = max(threads, parameters.get('mag_threads', 8))
        return threads

    def _resource_report(self, task_id: str) -> dict:
        from .resource_preflight import ResourcePreflight
        task = self.repository.get_task(task_id)
        if not task or task.get('cancel_requested') or task['status'] == 'cancelled':
            raise ValueError('task is missing or cancelled')
        threads = self._configured_threads(task_id)
        mags = (task.get('parameters') or {}).get('enable_mags') is True
        return ResourcePreflight(self.settings).inspect(manifest_path=task['manifest_path'],
            threads=threads, enable_mags=mags)

    def _admission_rejected(self, task_id: str, reason: str) -> None:
        self.repository.finalize_failure(task_id, 'validate', reason, retry_allowed=True)
        with self._lock:
            self._active.discard(task_id)

    def _handoff_retry(self, task_id: str) -> None:
        task = self.repository.get_task(task_id)
        if task and task['status'] == 'queued' and not task.get('cancel_requested'):
            self.submit(task_id)

    def _run_guarded(self, task_id: str) -> None:
        try:
            self.run(task_id)
        finally:
            from .full_archive import archive_after_run
            from .input_lifecycle import input_lock
            try:
                with input_lock(self.settings):
                    task = self.repository.get_task(task_id)
                    if task and task["status"] in {"completed", "failed", "paused", "cancelled"}:
                        archive_after_run(self.settings, task_id)
            finally:
                with self._lock:
                    self._active.discard(task_id)
                # Scheduler hands off a queued retry only after releasing its slot.

    def cancel(self, task_id: str) -> bool:
        """Stop the active local Nextflow process tree, if this runner owns it."""
        if self.scheduler.cancel_pending('standard:' + task_id):
            with self._lock:
                self._active.discard(task_id)
            return True
        with self._lock:
            process = self._processes.get(task_id)
        if process is None or process.poll() is not None:
            return False
        if isinstance(process, ExecutionUnitProcess):
            return process.stop()
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return False
        return True

    @staticmethod
    def _terminate_process(process: subprocess.Popen[bytes] | ExecutionUnitProcess) -> None:
        if isinstance(process, ExecutionUnitProcess):
            process.stop()
            return
        # Runners start a private session with pgid == child pid. Reaping the
        # leader does not prove its descendants have stopped computing.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    def run(self, task_id: str) -> None:
        while True:
            task = self.repository.get_task(task_id)
            if task is None:
                raise KeyError(task_id)
            if task.get("status") == "cancelled" or task.get("cancel_requested"):
                return
            if not self.repository.claim_for_run(task_id):
                return
            task = self.repository.get_task(task_id)
            if task is None:
                raise KeyError(task_id)
            output_dir = self.settings.state_root / "outputs" / task_id
            work_dir = self.settings.state_root / "work" / task_id
            log_dir = self.settings.state_root / "logs" / task_id
            for directory in (output_dir, work_dir, log_dir):
                directory.mkdir(parents=True, exist_ok=True)
            runner_log = log_dir / "validate.log"
            retry_count = int(task.get("retry_count") or 0)
            resume = retry_count > 0
            # Task consent and the platform kill switch are independent gates.
            # Missing legacy consent must never inherit a global opt-in.
            automatic_repair_enabled = (
                task.get("auto_repair") is True and self.settings.auto_retry_enabled
            )
            try:
                repair_plan = self.repair_service.execution_plan(
                    task_id,
                    retry_count=retry_count,
                )
            except TaskRepairError as exc:
                persisted = self.repository.finalize_failure(
                    task_id,
                    "validate",
                    f"verified repair bundle rejected: {exc}",
                    retry_allowed=False,
                )
                if persisted:
                    self.repository.add_alert(
                        task_id,
                        "error",
                        "修复副本在执行前校验失败，未启动续跑；公共脚本未修改。",
                    )
                return
            parameters = dict(task.get("parameters") or {})
            workflow_path = None
            if repair_plan is not None:
                parameters.update(repair_plan["runtime_overrides"])
                workflow_path = repair_plan["workflow_path"]
                self.repair_service.mark_event(
                    task_id,
                    int(repair_plan["version"]),
                    "execution_started",
                    "running",
                )
            command = build_nextflow_command(
                self.settings,
                task_id,
                Path(task["manifest_path"]),
                parameters,
                resume=resume,
                output_dir=output_dir,
                work_dir=work_dir,
                workflow_path=workflow_path,
            )
            now = str(task.get("started_at") or utc_now())
            self.repository.update_step(
                task_id,
                "validate",
                status="running",
                started_at=now,
                finished_at=None,
                error_message=None,
            )
            try:
                return_code = self._run_nextflow(
                    command=command,
                    task_id=task_id,
                    output_dir=output_dir,
                    runner_log=runner_log,
                )
            except ExecutionUnitError as exc:
                self.repository.finalize_failure(
                    task_id, 'validate', f'Execution isolation failed: {exc}', retry_allowed=False,
                )
                self.repository.add_alert(task_id, 'error',
                    '执行隔离校验失败，未降级为进程组运行；请检查用户 systemd 与执行单元记录。')
                return
            except FileNotFoundError:
                if self._is_cancelled(task_id):
                    if repair_plan is not None:
                        self.repair_service.mark_event(
                            task_id,
                            int(repair_plan["version"]),
                            "execution_finished",
                            "cancelled",
                        )
                    return
                if repair_plan is not None:
                    self.repair_service.mark_event(
                        task_id,
                        int(repair_plan["version"]),
                        "execution_finished",
                        "failed",
                    )
                diagnostic = Diagnostic(
                    classification="missing_dependency",
                    confidence=1.0,
                    summary="Nextflow executable not found.",
                    possible_causes=(
                        "BIOLLM_NEXTFLOW_BIN does not point to an executable.",
                    ),
                    recommended_actions=(
                        "Configure BIOLLM_NEXTFLOW_BIN to a verified local Nextflow executable.",
                    ),
                    needs_user_approval=True,
                )
                decision = RetryDecision(
                    False,
                    "missing dependencies are not retryable",
                    category="missing_dependency",
                )
                self._record_failure(
                    task_id=task_id,
                    step="validate",
                    exit_code=127,
                    diagnostic=diagnostic,
                    decision=decision,
                    finished=utc_now(),
                )
                self._record_diagnostic_audit(
                    task=task,
                    task_id=task_id,
                    event="diagnosis_completed",
                    step="validate",
                    exit_code=127,
                    retry_count=retry_count,
                    diagnostic=diagnostic,
                    decision=decision,
                    result="paused",
                )
                return

            sync_trace(self.repository, task_id, output_dir / "trace.tsv")
            if self._is_cancelled(task_id):
                if repair_plan is not None:
                    self.repair_service.mark_event(
                        task_id,
                        int(repair_plan["version"]),
                        "execution_finished",
                        "cancelled",
                    )
                return
            if return_code == 0:
                archive = output_dir / "deliverables" / f"{task_id}.tar.gz"
                if not self._verify_result_archive(archive):
                    if repair_plan is not None:
                        self.repair_service.mark_event(
                            task_id,
                            int(repair_plan["version"]),
                            "execution_finished",
                            "failed",
                        )
                    diagnostic = Diagnostic(
                        classification="tool_failure",
                        confidence=1.0,
                        summary="The workflow ended without a verified result archive.",
                        possible_causes=(
                            "The report package, manifest or artifact checksums are invalid.",
                        ),
                        recommended_actions=(
                            "Inspect the report/package stage; do not mark the task complete.",
                        ),
                        needs_user_approval=True,
                    )
                    decision = RetryDecision(
                        False,
                        "a missing deliverable is not an automatic retry condition",
                        category="tool_failure",
                    )
                    self._record_failure(
                        task_id=task_id,
                        step="report",
                        exit_code=66,
                        diagnostic=diagnostic,
                        decision=decision,
                        finished=utc_now(),
                    )
                    self._record_diagnostic_audit(
                        task=task,
                        task_id=task_id,
                        event="diagnosis_completed",
                        step="report",
                        exit_code=66,
                        retry_count=retry_count,
                        diagnostic=diagnostic,
                        decision=decision,
                        result="paused",
                    )
                    return
                if repair_plan is not None:
                    self.repair_service.mark_event(
                        task_id,
                        int(repair_plan["version"]),
                        "execution_finished",
                        "succeeded",
                    )
                self._record_success(task_id, archive)
                return

            step, tool_exit_code, redacted_excerpt = self._failure_context(
                task=task,
                task_id=task_id,
                output_dir=output_dir,
                runner_log=runner_log,
                nextflow_exit_code=return_code,
            )
            source_error = _source_error_excerpt(redacted_excerpt)
            self.repository.publish_failure_pending(
                task_id, step,
                f"原始流程错误（{step}，exit_code={tool_exit_code}）：{source_error}\n"
                "建议操作：查看该阶段脱敏日志，核对输入与运行条件；AI 辅助诊断处理中，不会自动更改分析参数。",
            )
            if self._is_cancelled(task_id):
                return
            try:
                diagnostic_context = self._diagnostic_context(
                    step=step,
                    task={**task, "parameters": parameters},
                    output_dir=output_dir,
                    work_dir=work_dir,
                    workflow_path=workflow_path,
                )
                diagnostic = self.diagnostic_service.diagnose_failure(
                    step=step,
                    log_excerpt=source_error,
                    exit_code=tool_exit_code,
                    retry_count=retry_count,
                    **diagnostic_context,
                )
            except DiagnosticResponseError as exc:
                diagnostic = unavailable_diagnostic(
                    redact_log(f"{type(exc).__name__}: {exc}"),
                    raw_model_output=exc.raw_model_output,
                    source_error=source_error,
                )
            except httpx.HTTPError as exc:
                diagnostic = unavailable_diagnostic(
                    redact_log(f"{type(exc).__name__}: {exc}"),
                    source_error=source_error,
                )
            except (ValueError, KeyError, TypeError) as exc:
                diagnostic = unavailable_diagnostic(
                    redact_log(f"{type(exc).__name__}: {exc}"),
                    source_error=source_error,
                )

            if self._is_cancelled(task_id):
                return
            if repair_plan is not None:
                self.repair_service.mark_event(
                    task_id,
                    int(repair_plan["version"]),
                    "execution_finished",
                    "failed",
                )
            policy_decision = evaluate_retry(
                step=step,
                exit_code=tool_exit_code,
                retry_count=retry_count,
                redacted_log_excerpt=redacted_excerpt,
                diagnostic=diagnostic,
                minimum_confidence=self.settings.retry_minimum_confidence,
            )
            repair_attempt: dict[str, Any] | None = None
            repair_error = ""
            if automatic_repair_enabled and (
                diagnostic.parameter_adjustments or diagnostic.script_patch is not None
            ):
                current_task = self.repository.get_task(task_id) or task
                try:
                    repair_attempt = self.repair_service.create_plan(
                        task=current_task,
                        step=step,
                        diagnostic=diagnostic,
                        redacted_log_excerpt=redacted_excerpt,
                        cancel_check=lambda: self._is_cancelled(task_id),
                    )
                except TaskRepairLimitReached as exc:
                    repair_error = str(exc)
                    policy_decision = RetryDecision(
                        False,
                        repair_error,
                        category="repair_limit_reached",
                        action="pause_and_notify",
                    )
                except (TaskRepairConflict, TaskRepairError, OSError) as exc:
                    repair_error = str(exc)
                    policy_decision = RetryDecision(
                        False,
                        f"untrusted repair proposal rejected: {repair_error}",
                        category="repair_rejected",
                        action="pause_and_notify",
                    )
                    if not self._is_cancelled(task_id):
                        try:
                            repair_attempt = self.repair_service.record_rejection(
                                task=current_task,
                                step=step,
                                diagnostic=diagnostic,
                                redacted_log_excerpt=redacted_excerpt,
                                reason=repair_error,
                            )
                        except (TaskRepairError, OSError):
                            # The diagnostic audit still records the rejection.
                            repair_attempt = None
                if self._is_cancelled(task_id):
                    return
                if repair_attempt is not None:
                    repair_status = repair_attempt["status"]
                    proposal = repair_attempt["proposal"]
                    modifications = tuple(
                        f"{name}={value}"
                        for name, value in proposal["automatic_runtime_overrides"].items()
                    )
                    if proposal.get("script_patch"):
                        modifications += (
                            f"task-private patch:{proposal['script_patch']['target_path']}",
                        )
                    if repair_status == "validated":
                        policy_decision = RetryDecision(
                            True,
                            "task-private repair passed path, syntax, targeted and output-contract checks",
                            category="verified_repair",
                            action="validate_then_resume",
                            modifications=modifications,
                        )
                    elif repair_status == "awaiting_confirmation":
                        policy_decision = RetryDecision(
                            False,
                            "scientific or server-managed changes require explicit user confirmation",
                            category="user_confirmation_required",
                            action="await_user_confirmation",
                        )
                    else:
                        policy_decision = RetryDecision(
                            False,
                            repair_attempt["decision_reason"],
                            category="repair_rejected",
                            action="pause_and_notify",
                        )
            decision = policy_decision
            if policy_decision.allowed and automatic_repair_enabled:
                decision = RetryDecision(
                    False,
                    "automatic small-sample validation is in progress",
                    category=policy_decision.category,
                    action="validation_pending",
                    modifications=policy_decision.modifications,
                )
            elif policy_decision.allowed:
                decision = RetryDecision(
                    True,
                    (
                        "task automatic repair is disabled; an explicit user retry is required"
                        if task.get("auto_repair") is not True
                        else "platform automatic repair is disabled; an explicit user retry is required"
                    ),
                    category=policy_decision.category,
                    action="manual_retry_available",
                    modifications=policy_decision.modifications,
                )
            self._record_failure(
                task_id=task_id,
                step=step,
                exit_code=tool_exit_code,
                diagnostic=diagnostic,
                decision=decision,
                finished=utc_now(),
                source_error=source_error,
            )
            audit_result = "paused"
            if policy_decision.allowed and automatic_repair_enabled:
                audit_result = "retry_validation_pending"
            elif policy_decision.allowed:
                audit_result = "automatic_retry_disabled"
            audit_written = self._record_diagnostic_audit(
                task=task,
                task_id=task_id,
                event="diagnosis_completed",
                step=step,
                exit_code=tool_exit_code,
                retry_count=retry_count,
                redacted_log_excerpt=redacted_excerpt,
                diagnostic=diagnostic,
                decision=decision,
                policy_decision=policy_decision,
                result=audit_result,
                extra={
                    "repair": (
                        {
                            "version": repair_attempt["version"],
                            "status": repair_attempt["status"],
                            "plan_sha256": repair_attempt["plan_sha256"],
                        }
                        if repair_attempt is not None
                        else {"status": "rejected", "reason": repair_error}
                    )
                } if repair_attempt is not None or repair_error else None,
            )
            if not audit_written:
                return
            if retry_count > 0:
                retry_result_written = self._record_diagnostic_audit(
                    task=task,
                    task_id=task_id,
                    event="automatic_retry_finished",
                    step=step,
                    exit_code=tool_exit_code,
                    retry_count=retry_count,
                    redacted_log_excerpt=redacted_excerpt,
                    diagnostic=diagnostic,
                    decision=decision,
                    policy_decision=policy_decision,
                    result="failed",
                )
                if not retry_result_written:
                    return
            if not policy_decision.allowed or not automatic_repair_enabled:
                return
            validation_plan: dict[str, Any] | None = None
            if self.repair_service.has_history(task_id):
                try:
                    validation_plan = self.repair_service.execution_plan(
                        task_id,
                        retry_count=retry_count + 1,
                    )
                except TaskRepairError:
                    validation_plan = None
                if validation_plan is None:
                    self.repository.add_alert(
                        task_id,
                        "error",
                        "修复副本在续跑验证前复核失败，自动重试已停止。",
                    )
                    self.repository.update_task(task_id, retry_allowed=False)
                    return
            validation_succeeded = self._run_retry_validation(
                task_id,
                task,
                repair_plan=validation_plan,
            )
            if self._is_cancelled(task_id):
                if repair_attempt is not None:
                    self.repair_service.mark_event(
                        task_id,
                        int(repair_attempt["version"]),
                        "full_validation_completed",
                        "cancelled",
                    )
                return
            if repair_attempt is not None:
                self.repair_service.mark_event(
                    task_id,
                    int(repair_attempt["version"]),
                    "full_validation_completed",
                    "validated" if validation_succeeded else "validation_failed",
                )
            if validation_succeeded:
                validation_decision = policy_decision
            else:
                validation_decision = RetryDecision(
                    False,
                    "configured small-sample validation failed",
                    category="validation_failed",
                    action="pause_and_notify",
                )
                self.repository.update_task(task_id, retry_allowed=False)
            validation_audit_written = self._record_diagnostic_audit(
                task=task,
                task_id=task_id,
                event="retry_validation_completed",
                step=step,
                exit_code=tool_exit_code,
                retry_count=retry_count,
                diagnostic=diagnostic,
                decision=validation_decision,
                policy_decision=policy_decision,
                result="succeeded" if validation_succeeded else "failed",
            )
            if not validation_audit_written:
                return
            if not validation_succeeded:
                self.repository.add_alert(
                    task_id,
                    "warning",
                    "自动重试已停止：配置的小样本验证未成功。",
                )
                return
            authorization_written = self._record_diagnostic_audit(
                task=task,
                task_id=task_id,
                event="automatic_retry_authorized",
                step=step,
                exit_code=tool_exit_code,
                retry_count=retry_count,
                diagnostic=diagnostic,
                decision=policy_decision,
                policy_decision=policy_decision,
                result="authorized",
                extra={"resume": True},
            )
            if not authorization_written:
                return
            self.repository.update_task(task_id, retry_allowed=True)
            try:
                retry_claimed = self.repository.claim_retry_for_queue(
                    task_id,
                    max_retries=2,
                )
            except ValueError:
                retry_claimed = None
            if retry_claimed is None:
                rejected_decision = RetryDecision(
                    False,
                    "task state changed before the authorized retry could start",
                    category="state_changed",
                    action="pause_and_notify",
                )
                self.repository.update_task(task_id, retry_allowed=False)
                self.repository.add_alert(
                    task_id,
                    "warning",
                    "自动重试状态发生变化，未启动重复任务。",
                )
                self._record_diagnostic_audit(
                    task=task,
                    task_id=task_id,
                    event="automatic_retry_rejected",
                    step=step,
                    exit_code=tool_exit_code,
                    retry_count=retry_count,
                    diagnostic=diagnostic,
                    decision=rejected_decision,
                    policy_decision=policy_decision,
                    result="state_changed",
                )
                return
            if repair_attempt is not None:
                self.repair_service.mark_event(
                    task_id,
                    int(repair_attempt["version"]),
                    "retry_claimed",
                    "retry_claimed",
                )
            retry_start_written = self._record_diagnostic_audit(
                task=task,
                task_id=task_id,
                event="automatic_retry_started",
                step=step,
                exit_code=tool_exit_code,
                retry_count=retry_count + 1,
                diagnostic=diagnostic,
                decision=policy_decision,
                policy_decision=policy_decision,
                result="running",
                extra={"resume": True},
            )
            if not retry_start_written:
                return
            self.repository.add_alert(
                task_id,
                "info",
                "小样本验证通过，将使用已验证的任务私有修复和受限参数（如有）执行 Nextflow -resume。",
            )

    def _workflow_environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        pythonpath = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = str(self.settings.project_root) + (os.pathsep + pythonpath if pythonpath else "")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return environment

    def _run_nextflow(
        self,
        *,
        command: list[str],
        task_id: str,
        output_dir: Path,
        runner_log: Path,
    ) -> int:
        with runner_log.open("ab") as log_handle:
            task = self.repository.get_task(task_id) or {}
            process = self._launch_process(
                command, cwd=output_dir, log_handle=log_handle, task_id=task_id,
                attempt=int(task.get('retry_count') or 0) + 1,
            )
            with self._lock:
                self._processes[task_id] = process
            try:
                cancellation_started: float | None = None
                while process.poll() is None:
                    if self._is_cancelled(task_id):
                        if isinstance(process, ExecutionUnitProcess):
                            process.stop()
                        elif cancellation_started is None:
                            cancellation_started = time.monotonic()
                            try:
                                os.killpg(process.pid, signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                        elif time.monotonic() - cancellation_started >= 5:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                    sync_trace(
                        self.repository,
                        task_id,
                        output_dir / "trace.tsv",
                    )
                    time.sleep(self.settings.poll_interval_seconds)
                return int(process.returncode)
            finally:
                try:
                    if process.poll() is None or self._is_cancelled(task_id):
                        self._terminate_process(process)
                finally:
                    with self._lock:
                        self._processes.pop(task_id, None)

    def _launch_process(self, command, *, cwd, log_handle, task_id, attempt):
        if self.execution_manager is not None:
            return self.execution_manager.launch(
                command, cwd=cwd, log_handle=log_handle, task_id=task_id,
                attempt=attempt, env=self._workflow_environment(),
            )
        # Only explicitly configured legacy/test mode reaches this branch.
        if self.settings.execution_isolation != 'process_group':
            raise ExecutionUnitUnavailable('owned execution manager is unavailable; refusing fallback')
        return subprocess.Popen(
            command, cwd=cwd, env=self._workflow_environment(), stdout=log_handle,
            stderr=subprocess.STDOUT, shell=False, start_new_session=True,
        )

    def _is_cancelled(self, task_id: str) -> bool:
        task = self.repository.get_task(task_id)
        return task is not None and (
            task.get("status") == "cancelled" or bool(task.get("cancel_requested"))
        )

    def _diagnostic_context(
        self,
        *,
        step: str,
        task: Mapping[str, Any],
        output_dir: Path,
        work_dir: Path,
        workflow_path: Path | None = None,
    ) -> dict[str, Any]:
        """Collect bounded, non-secret facts; model suggestions remain untrusted."""
        command_by_step: dict[str, tuple[str, ...]] = {
            "fastqc_raw": ("fastqc", "--version"),
            "fastp": ("fastp", "--version"),
            "host_depletion": ("bowtie2", "--version"),
            "taxonomy": ("kraken2", "--version"),
            "functional_annotation": ("humann", "--version"),
            "mag_assembly": ("metaspades.py", "--version"),
            "mag_binning": ("metabat2", "--help"),
        }
        tool_versions: dict[str, str] = {}
        version_command = command_by_step.get(step)
        if version_command is not None:
            try:
                completed = subprocess.run(
                    list(version_command),
                    cwd=self.settings.project_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    timeout=5,
                    check=False,
                    text=True,
                )
                first_line = (completed.stdout or "").strip().splitlines()[:1]
                tool_versions[version_command[0]] = redact_log(
                    first_line[0] if first_line else f"exit_code={completed.returncode}"
                )[:500]
            except (OSError, subprocess.TimeoutExpired):
                tool_versions[version_command[0]] = "version unavailable"

        parameters: dict[str, Any] = {}
        for name, value in dict(task.get("parameters") or {}).items():
            if any(token in name.lower() for token in ("db", "index", "manifest", "path")):
                parameters[name] = "[SERVER_MANAGED_PATH]" if value else value
            elif isinstance(value, (str, int, float, bool)) or value is None:
                parameters[name] = value

        resources: dict[str, Any] = {
            "cpu_count": os.cpu_count(),
            "output_free_bytes": shutil.disk_usage(output_dir).free,
            "work_free_bytes": shutil.disk_usage(work_dir).free,
        }
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith(("MemTotal:", "MemAvailable:")):
                    name, value = line.split(":", 1)
                    resources[name] = value.strip()
        except OSError:
            pass

        script_context: dict[str, Any] | None = None
        target, _ = PATCH_TARGETS.get(step, ("", ""))
        if target:
            path = (workflow_path or self.settings.workflow_path).resolve().parent / target
            try:
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and path.stat().st_size <= 128 * 1024
                ):
                    content = path.read_bytes()
                    script_context = {
                        "target_path": target,
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "content": content.decode("utf-8", errors="replace")[:16000],
                    }
            except OSError:
                script_context = None
        return {
            "tool_versions": tool_versions,
            "parameters": parameters,
            "resources": resources,
            "script_context": script_context,
        }

    @staticmethod
    def _verify_result_archive(archive: Path) -> bool:
        """Verify every packaged artifact without extracting untrusted paths."""
        try:
            if (
                not archive.is_file()
                or archive.is_symlink()
                or archive.stat().st_size == 0
            ):
                return False
            with tarfile.open(archive, "r:gz") as bundle:
                members = bundle.getmembers()
                if not members or len(members) > 100000:
                    return False
                by_name: dict[str, tarfile.TarInfo] = {}
                for member in members:
                    relative = PurePosixPath(member.name)
                    if (
                        not member.name
                        or relative.is_absolute()
                        or ".." in relative.parts
                        or "\\" in member.name
                        or str(relative) != member.name
                        or member.name in by_name
                        or not member.isfile()
                    ):
                        return False
                    by_name[member.name] = member
                if not {"manifest.json", "SHA256SUMS"}.issubset(by_name):
                    return False

                manifest_file = bundle.extractfile(by_name["manifest.json"])
                checksum_file = bundle.extractfile(by_name["SHA256SUMS"])
                if manifest_file is None or checksum_file is None:
                    return False
                manifest_bytes = manifest_file.read(10 * 1024 * 1024 + 1)
                checksum_bytes = checksum_file.read(10 * 1024 * 1024 + 1)
                if (
                    len(manifest_bytes) > 10 * 1024 * 1024
                    or len(checksum_bytes) > 10 * 1024 * 1024
                ):
                    return False
                manifest = json.loads(manifest_bytes.decode("utf-8"))
                if (
                    not isinstance(manifest, dict)
                    or manifest.get("checksum_algorithm") != "sha256"
                    or manifest.get("checksum_file") != "SHA256SUMS"
                    or not isinstance(manifest.get("artifacts"), list)
                ):
                    return False
                declared_checksums: dict[str, str] = {}
                for line in checksum_bytes.decode("utf-8").splitlines():
                    digest, separator, path = line.partition("  ")
                    if (
                        separator != "  "
                        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                        or path in declared_checksums
                    ):
                        return False
                    declared_checksums[path] = digest

                artifact_paths: set[str] = set()
                for record in manifest["artifacts"]:
                    if not isinstance(record, dict):
                        return False
                    path = record.get("path")
                    digest = record.get("sha256")
                    size = record.get("size_bytes")
                    if (
                        not isinstance(path, str)
                        or path in artifact_paths
                        or path in {"manifest.json", "SHA256SUMS"}
                        or path not in by_name
                        or re.fullmatch(r"[0-9a-f]{64}", str(digest)) is None
                        or isinstance(size, bool)
                        or not isinstance(size, int)
                        or size < 0
                        or by_name[path].size != size
                        or declared_checksums.get(path) != digest
                    ):
                        return False
                    payload = bundle.extractfile(by_name[path])
                    if payload is None:
                        return False
                    observed = hashlib.sha256()
                    for block in iter(lambda: payload.read(1024 * 1024), b""):
                        observed.update(block)
                    if observed.hexdigest() != digest:
                        return False
                    artifact_paths.add(path)
                packaged = set(by_name) - {"manifest.json", "SHA256SUMS"}
                return bool(artifact_paths) and artifact_paths == packaged == set(declared_checksums)
        except (OSError, tarfile.TarError, ValueError, TypeError, UnicodeError):
            return False

    def _run_retry_validation(
        self,
        task_id: str,
        task: Mapping[str, Any],
        *,
        repair_plan: Mapping[str, Any] | None = None,
    ) -> bool:
        manifest = self.settings.retry_validation_manifest
        if manifest is None or not manifest.is_file():
            self.repository.add_alert(
                task_id,
                "warning",
                "自动重试未执行：未配置可用的脱敏小样本验证 manifest。",
            )
            return False
        attempt = int(task.get("retry_count") or 0) + 1
        validation_root = (
            self.settings.state_root
            / "retry_validation"
            / task_id
            / f"attempt-{attempt}"
        )
        output_dir = validation_root / "outputs"
        work_dir = validation_root / "work"
        validation_root.mkdir(parents=True, exist_ok=True)
        parameters = dict(task.get("parameters") or {})
        workflow_path = None
        if repair_plan is not None:
            parameters.update(repair_plan.get("runtime_overrides") or {})
            workflow_path = repair_plan.get("workflow_path")
        command = build_nextflow_command(
            self.settings,
            f"{task_id}-retry-validation-{attempt}",
            manifest,
            parameters,
            output_dir=output_dir,
            work_dir=work_dir,
            workflow_path=workflow_path,
        )
        log_path = validation_root / "validation.log"
        process: subprocess.Popen[bytes] | ExecutionUnitProcess | None = None
        deadline = time.monotonic() + self.settings.retry_validation_timeout_seconds
        try:
            with log_path.open("ab") as log_handle:
                process = self._launch_process(
                    command, cwd=self.settings.project_root, log_handle=log_handle,
                    task_id=task_id, attempt=attempt,
                )
                with self._lock:
                    self._processes[task_id] = process
                while process.poll() is None:
                    if self._is_cancelled(task_id):
                        self._terminate_process(process)
                        return False
                    if time.monotonic() >= deadline:
                        self._terminate_process(process)
                        return False
                    time.sleep(min(0.2, self.settings.poll_interval_seconds))
        except FileNotFoundError:
            return False
        finally:
            if process is not None:
                try:
                    if process.poll() is None:
                        self._terminate_process(process)
                finally:
                    with self._lock:
                        if self._processes.get(task_id) is process:
                            self._processes.pop(task_id, None)
        archive = (
            output_dir
            / "deliverables"
            / f"{task_id}-retry-validation-{attempt}.tar.gz"
        )
        return (
            process is not None
            and process.returncode == 0
            and self._verify_result_archive(archive)
        )

    def _failure_context(
        self,
        *,
        task: Mapping[str, Any],
        task_id: str,
        output_dir: Path,
        runner_log: Path,
        nextflow_exit_code: int,
    ) -> tuple[str, int, str]:
        current = self.repository.get_task(task_id) or {}
        step = current.get("current_step") or "validate"
        task_steps = {item["name"] for item in task.get("steps", [])}
        runner_tail = _strip_terminal_controls(_read_tail(runner_log, 24000))
        # Input staging can fail before a process writes a trace/status record.
        # In that case current_step still names the preceding successful stage.
        failures = re.findall(r"Error executing process\s*>\s*['\"]([^'\"]+)['\"]", runner_tail)
        if failures:
            reported_step = step_for_process(failures[-1])
            if reported_step in task_steps:
                step = reported_step
        if step not in task_steps:
            step = "validate"
        tool_exit_code = nextflow_exit_code
        status_dir = output_dir / "status"
        status_files = (
            sorted(
                status_dir.glob(f"*.{step}.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if status_dir.is_dir()
            else []
        )
        status_message = ""
        for status_path in status_files:
            try:
                payload = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if payload.get("status") == "failed":
                try:
                    tool_exit_code = int(payload.get("exit_code", nextflow_exit_code))
                except (TypeError, ValueError):
                    tool_exit_code = nextflow_exit_code
                status_message = " ".join(
                    str(payload.get(key) or "")
                    for key in ("message", "failed_command")
                )
                break

        log_parts = []
        workflow_log_dir = output_dir / "logs"
        if workflow_log_dir.is_dir():
            for path in sorted(workflow_log_dir.glob(f"*.{step}.log")):
                log_parts.append(_read_tail(path, 12000))
        log_parts.append(runner_tail)
        raw_excerpt = tail_excerpt(_strip_terminal_controls("\n".join(log_parts)), max_chars=10000)
        # Structured stage status is authoritative evidence; retain it even when
        # Nextflow's verbose tail consumes the log budget.
        if status_message:
            raw_excerpt += '\n[ERROR] ' + _strip_terminal_controls(status_message)[:2000]
        redacted = redact_log(
            raw_excerpt,
            input_root=str(self.settings.input_root.resolve()),
            sample_identifiers=_manifest_sample_ids(Path(task["manifest_path"])),
        )
        return step, tool_exit_code, redacted

    def _record_diagnostic_audit(
        self,
        *,
        task: Mapping[str, Any],
        task_id: str,
        event: str,
        step: str,
        exit_code: int | None,
        retry_count: int,
        result: str,
        redacted_log_excerpt: str = "",
        diagnostic: Diagnostic | None = None,
        decision: RetryDecision | None = None,
        policy_decision: RetryDecision | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> bool:
        parsed_diagnostic: dict[str, Any] | None = None
        raw_model_diagnostic = ""
        if diagnostic is not None:
            parsed_diagnostic = diagnostic.to_dict()
            raw_model_diagnostic = str(
                parsed_diagnostic.pop("raw_model_output", "")
            )
        record: dict[str, Any] = {
            "step": step,
            "exit_code": exit_code,
            "retry_count": retry_count,
            "result": result,
            "model": self.settings.ollama_model,
            "redacted_log_excerpt": redacted_log_excerpt,
            "model_raw_diagnostic": raw_model_diagnostic,
            "parsed_diagnostic": parsed_diagnostic,
            "policy_decision": (
                (policy_decision or decision).to_dict()
                if (policy_decision or decision)
                else None
            ),
            "final_decision": decision.to_dict() if decision else None,
            "modifications": list(decision.modifications) if decision else [],
            "task_auto_repair": task.get("auto_repair") is True,
            "platform_auto_retry_enabled": self.settings.auto_retry_enabled,
            "automatic_retry_enabled": (
                task.get("auto_repair") is True and self.settings.auto_retry_enabled
            ),
        }
        if extra:
            record["extra"] = dict(extra)
        sample_ids = _manifest_sample_ids(Path(task["manifest_path"]))
        try:
            append_diagnostic_audit(
                state_root=self.settings.state_root,
                task_id=task_id,
                event=event,
                record=record,
                input_root=str(self.settings.input_root.resolve()),
                sample_identifiers=sample_ids,
            )
        except (OSError, ValueError):
            persisted = self.repository.finalize_failure(
                task_id,
                step,
                "Diagnostic audit persistence failed",
                retry_allowed=False,
            )
            if persisted:
                self.repository.add_alert(
                    task_id,
                    "error",
                    "诊断审计记录写入失败；任务已保持暂停，请管理员检查服务端状态。",
                )
            return False
        return True

    def _record_failure(
        self,
        *,
        task_id: str,
        step: str,
        exit_code: int,
        diagnostic: Diagnostic,
        decision: RetryDecision,
        finished: str,
        source_error: str = "",
    ) -> None:
        safe_summary = redact_log(diagnostic.summary)
        safe_causes = tuple(
            redact_log(cause) for cause in diagnostic.possible_causes
        )
        safe_actions = tuple(
            redact_log(action) for action in diagnostic.recommended_actions
        )
        message = (
            f"{safe_summary} [classification={diagnostic.classification}; "
            f"exit_code={exit_code}]"
        )
        safe_source = redact_log(source_error)
        if safe_source:
            message = f"原始流程错误（{step}，exit_code={exit_code}）：{safe_source}\nAI 辅助诊断：{message}"
        message += '\n建议操作：' + '；'.join(safe_actions)
        if not self.repository.finalize_failure(
            task_id,
            step,
            message,
            retry_allowed=decision.allowed,
        ):
            return
        self.repository.add_alert(
            task_id,
            (
                "warning"
                if decision.allowed or decision.action == "validation_pending"
                else "error"
            ),
            (
                (f"原始流程错误：{safe_source}；AI 辅助诊断：" if safe_source else "") +
                f"错误摘要：{safe_summary} "
                f"可能原因：{'；'.join(safe_causes)} "
                f"建议操作：{'；'.join(safe_actions)} "
                f"诊断置信度={diagnostic.confidence:.2f}；"
                f"最终决定={decision.action}；依据：{decision.reason}"
            ),
        )

    def _record_success(self, task_id: str, archive: Path) -> None:
        task = self.repository.get_task(task_id)
        if task is not None and int(task.get("retry_count") or 0) > 0:
            audit_written = self._record_diagnostic_audit(
                task=task,
                task_id=task_id,
                event="automatic_retry_finished",
                step="report",
                exit_code=0,
                retry_count=int(task["retry_count"]),
                result="succeeded",
                extra={"archive_name": archive.name},
            )
            if not audit_written:
                return
        # Keep ownership of an earlier named full archive until the post-run
        # packager atomically replaces it using this run's UUID report staging file.
        from .archive_naming import ArchiveNamingService, SUFFIX
        from .input_lifecycle import input_lock
        with input_lock(self.settings):
            previous = ArchiveNamingService(self.settings, self.repository).existing(task_id)
            published = previous if previous and previous.name.endswith(SUFFIX) else archive
            self.repository.finalize_success(task_id, str(published))


def _read_tail(path: Path, max_bytes: int) -> str:
    if not path.is_file():
        return ""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            offset = max(0, size - max_bytes)
            handle.seek(offset)
            text = handle.read(max_bytes).decode("utf-8", errors="replace")
        if offset:
            first_newline = text.find("\n")
            notice = "[... earlier log content omitted ...]\n"
            if first_newline < 0:
                return notice
            return notice + text[first_newline + 1 :]
        return text
    except OSError:
        return ""


def _manifest_sample_ids(manifest_path: Path) -> list[str]:
    try:
        with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [
                row["sample_id"].strip()
                for row in csv.DictReader(handle)
                if row.get("sample_id", "").strip()
            ]
    except (OSError, csv.Error, KeyError):
        return []
