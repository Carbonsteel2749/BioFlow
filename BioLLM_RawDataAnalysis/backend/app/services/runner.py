from __future__ import annotations

import csv
import json
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Mapping

import httpx

from ..config import Settings
from ..models import TaskRepository, utc_now
from .diagnostic_audit import append_diagnostic_audit
from .llm_diagnostics import (
    Diagnostic,
    DiagnosticResponseError,
    OllamaDiagnosticService,
    unavailable_diagnostic,
)
from .progress import step_for_process, sync_trace
from .redaction import redact_log, tail_excerpt
from .retry_policy import RetryDecision, evaluate_retry


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
) -> list[str]:
    output_dir = (
        output_dir or settings.state_root / "outputs" / task_id
    ).resolve()
    work_dir = (
        work_dir or settings.state_root / "work" / task_id
    ).resolve()
    trace_path = output_dir / "trace.tsv"
    command = [
        settings.nextflow_bin,
        "run",
        str(settings.workflow_path.resolve()),
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
    ):
        self.settings = settings
        self.repository = repository
        self.diagnostic_service = diagnostic_service or OllamaDiagnosticService(
            base_url=settings.ollama_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
        )
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

    def cancel(self, task_id: str) -> bool:
        """Stop the active local Nextflow process tree, if this runner owns it."""
        with self._lock:
            process = self._processes.get(task_id)
        if process is None or process.poll() is not None:
            return False
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return False
        return True

    def run(self, task_id: str) -> None:
        while True:
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
            command = build_nextflow_command(
                self.settings,
                task_id,
                Path(task["manifest_path"]),
                task.get("parameters"),
                resume=resume,
                output_dir=output_dir,
                work_dir=work_dir,
            )
            now = utc_now()
            self.repository.update_task(
                task_id,
                status="running",
                started_at=now,
                finished_at=None,
                current_step="validate",
            )
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
            except FileNotFoundError:
                if self._is_cancelled(task_id):
                    return
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
                return
            if return_code == 0:
                archive = output_dir / "deliverables" / f"{task_id}.tar.gz"
                if not archive.is_file() or archive.is_symlink() or archive.stat().st_size == 0:
                    diagnostic = Diagnostic(
                        classification="tool_failure",
                        confidence=1.0,
                        summary="The workflow ended without a valid result archive.",
                        possible_causes=(
                            "The report stage did not publish a regular non-empty archive.",
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
                diagnostic = self.diagnostic_service.diagnose_failure(
                    step=step,
                    log_excerpt=source_error,
                    exit_code=tool_exit_code,
                    retry_count=retry_count,
                )
            except DiagnosticResponseError as exc:
                diagnostic = unavailable_diagnostic(
                    type(exc).__name__,
                    raw_model_output=exc.raw_model_output,
                )
            except httpx.HTTPError as exc:
                diagnostic = unavailable_diagnostic(type(exc).__name__)
            except (ValueError, KeyError, TypeError) as exc:
                diagnostic = unavailable_diagnostic(type(exc).__name__)

            if self._is_cancelled(task_id):
                return
            decision = evaluate_retry(
                step=step,
                exit_code=tool_exit_code,
                retry_count=retry_count,
                redacted_log_excerpt=redacted_excerpt,
                diagnostic=diagnostic,
                minimum_confidence=self.settings.retry_minimum_confidence,
            )
            policy_decision = decision
            if policy_decision.allowed and self.settings.auto_retry_enabled:
                decision = RetryDecision(
                    False,
                    "automatic small-sample validation is in progress",
                    category=policy_decision.category,
                    action="validation_pending",
                )
            elif policy_decision.allowed:
                decision = RetryDecision(
                    False,
                    "automatic retry is disabled by server configuration",
                    category=policy_decision.category,
                    action="pause_and_notify",
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
            if policy_decision.allowed and self.settings.auto_retry_enabled:
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
            if not policy_decision.allowed or not self.settings.auto_retry_enabled:
                return
            validation_succeeded = self._run_retry_validation(task_id, task)
            if validation_succeeded:
                validation_decision = policy_decision
            else:
                validation_decision = RetryDecision(
                    False,
                    "configured small-sample validation failed",
                    category="validation_failed",
                    action="pause_and_notify",
                )
                self.repository.update_task(
                    task_id,
                    status="paused",
                    finished_at=utc_now(),
                    retry_allowed=False,
                )
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
            try:
                self.repository.update_task(task_id, retry_allowed=True)
                self.repository.reset_for_retry(task_id)
            except ValueError:
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
                "小样本验证通过，将使用原参数和同一工作目录执行一次 Nextflow -resume。",
            )

    def _run_nextflow(
        self,
        *,
        command: list[str],
        task_id: str,
        output_dir: Path,
        runner_log: Path,
    ) -> int:
        with runner_log.open("ab") as log_handle:
            process = subprocess.Popen(
                command,
                cwd=output_dir,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                shell=False,
                start_new_session=True,
            )
            with self._lock:
                self._processes[task_id] = process
            try:
                while process.poll() is None:
                    if self._is_cancelled(task_id):
                        self.cancel(task_id)
                    sync_trace(
                        self.repository,
                        task_id,
                        output_dir / "trace.tsv",
                    )
                    time.sleep(self.settings.poll_interval_seconds)
                return int(process.returncode)
            finally:
                with self._lock:
                    self._processes.pop(task_id, None)

    def _is_cancelled(self, task_id: str) -> bool:
        task = self.repository.get_task(task_id)
        return task is not None and task.get("status") == "cancelled"

    def _run_retry_validation(self, task_id: str, task: Mapping[str, Any]) -> bool:
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
        command = build_nextflow_command(
            self.settings,
            f"{task_id}-retry-validation-{attempt}",
            manifest,
            task.get("parameters"),
            output_dir=output_dir,
            work_dir=work_dir,
        )
        log_path = validation_root / "validation.log"
        try:
            with log_path.open("ab") as log_handle:
                completed = subprocess.run(
                    command,
                    cwd=self.settings.project_root,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    timeout=self.settings.retry_validation_timeout_seconds,
                    check=False,
                )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

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
            "automatic_retry_enabled": self.settings.auto_retry_enabled,
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
            self.repository.update_task(
                task_id,
                status="paused",
                finished_at=utc_now(),
                retry_allowed=False,
                error_message="Diagnostic audit persistence failed",
            )
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
        self.repository.update_step(
            task_id,
            step,
            status="failed",
            finished_at=finished,
            error_message=message,
        )
        task_status = (
            "failed"
            if decision.action == "validation_pending"
            else "paused"
        )
        self.repository.update_task(
            task_id,
            status=task_status,
            current_step=step,
            finished_at=finished,
            retry_allowed=decision.allowed,
            error_message=message,
        )
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
        finished = utc_now()
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
        if task is not None:
            for step in task["steps"]:
                if step["status"] in {"pending", "running"}:
                    self.repository.update_step(
                        task_id,
                        step["name"],
                        status="succeeded",
                        progress=100.0,
                        finished_at=finished,
                    )
        self.repository.update_task(
            task_id,
            status="completed",
            current_step="report",
            finished_at=finished,
            retry_allowed=False,
            result_archive=str(archive),
            error_message=None,
        )


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
