import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from ..models import TaskRepository, utc_now


PROCESS_PATTERNS = [
    ("validate", ("VALIDATE",)),
    ("fastqc_raw", ("FASTQC",)),
    ("fastp", ("FASTP",)),
    ("host_depletion", ("HOST", "BOWTIE", "KNEAD")),
    ("taxonomy", ("TAXON", "KRAKEN", "BRACKEN")),
    ("functional_annotation", ("FUNCTION", "HUMANN")),
    ("bin_reassembly", ("BIN_REASSEMBLY",)),
    ("bin_refinement", ("BIN_REFINEMENT",)),
    ("bin_quantification", ("BIN_QUANTIFICATION", "QUANT_BINS")),
    ("bin_annotation", ("BIN_ANNOTATION", "CLASSIFY_BINS", "ANNOTATE_BINS")),
    ("binning", ("BINNING",)),
    ("assembly", ("ASSEMBLY",)),
    ("report", ("REPORT", "MULTIQC", "PACKAGE", "ARCHIVE")),
]


def step_for_process(process_name: str) -> str | None:
    upper = process_name.upper()
    for step, patterns in PROCESS_PATTERNS:
        if any(pattern in upper for pattern in patterns):
            return step
    return None


def sync_trace(repository: TaskRepository, task_id: str, trace_path: Path) -> None:
    task = repository.get_task(task_id)
    if task is None or task["status"] in {"cancelled", "completed", "paused", "failed"}:
        return
    current_steps = {step["name"]: step for step in task["steps"]}
    latest_by_process: dict[str, tuple[str, str]] = {}
    if trace_path.is_file():
        with trace_path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                process_name = (row.get("name") or "").strip()
                step = step_for_process(process_name)
                if step:
                    latest_by_process[process_name] = (
                        step,
                        (row.get("status") or "").upper(),
                    )

    # Nextflow may only append trace rows when processes finish. Shell status
    # files provide live starts; terminal trace records remain authoritative.
    live_starts: dict[str, list[str]] = {}
    for path in sorted((trace_path.parent / "status").glob("*.json")):
        try:
            if path.is_symlink():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            step = payload.get("step")
            sample = payload.get("sample_id")
            started = payload.get("started_at")
            if (payload.get("task_id") != task_id or payload.get("status") != "running"
                    or not isinstance(step, str) or step not in current_steps
                    or not isinstance(sample, str) or not sample
                    or not isinstance(started, str)):
                continue
            started_time = datetime.fromisoformat(started.replace("Z", "+00:00"))
            if started_time.tzinfo is None:
                continue
            attempt_start = task.get("started_at")
            if attempt_start:
                attempt_time = datetime.fromisoformat(attempt_start.replace("Z", "+00:00"))
                # Shell timestamps have second precision, unlike API timestamps.
                if started_time < attempt_time.replace(
                    microsecond=0, tzinfo=attempt_time.tzinfo or timezone.utc
                ):
                    continue
        except (OSError, ValueError, TypeError):
            continue
        matching = [status for name, (trace_step, status) in latest_by_process.items()
                    if trace_step == step and name.endswith(f"({sample})")]
        if matching and all(status in {"COMPLETED", "CACHED", "FAILED", "ABORTED"}
                            for status in matching):
            continue
        live_starts.setdefault(step, []).append(started)
        if not matching:
            latest_by_process[f"status:{step}:{sample}"] = (step, "RUNNING")

    statuses_by_step: dict[str, list[str]] = {}
    for step, status in latest_by_process.values():
        if status:
            statuses_by_step.setdefault(step, []).append(status)

    now = utc_now()
    completed_statuses = {"COMPLETED", "CACHED"}
    active_statuses = {"RUNNING", "SUBMITTED", "PENDING", "NEW"}
    failed_statuses = {"FAILED", "ABORTED"}
    updates = {}
    current_step = None
    for step, statuses in statuses_by_step.items():
        if step not in current_steps:
            continue
        completed = sum(status in completed_statuses for status in statuses)
        progress = round(100.0 * completed / len(statuses), 2)
        existing = current_steps.get(step, {})
        started_at = existing.get("started_at") or min(live_starts.get(step, [now]))
        finished_at = existing.get("finished_at") or now
        if any(status in failed_statuses for status in statuses):
            updates[step] = dict(
                status="failed",
                started_at=started_at,
                finished_at=finished_at,
                progress=progress,
            )
        elif completed == len(statuses):
            updates[step] = dict(
                status="succeeded",
                started_at=started_at,
                finished_at=finished_at,
                progress=100.0,
            )
        elif any(status in active_statuses for status in statuses):
            updates[step] = dict(
                status="running",
                started_at=started_at,
                finished_at=None,
                progress=None if step in live_starts else progress,
            )
            current_step = step
    repository.apply_progress(task_id, updates, current_step)
