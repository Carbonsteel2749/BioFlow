import csv
import json
import uuid
from pathlib import Path
from typing import Any

from ..config import Settings
from ..models import TaskRepository
from ..schemas import WorkflowParameters
from .redaction import redact_log, tail_excerpt


CORE_STEPS = [
    "validate",
    "fastqc_raw",
    "fastp",
    "host_depletion",
    "taxonomy",
    "functional_annotation",
]
MAG_STEPS = [
    "assembly",
    "binning",
    "bin_refinement",
    "bin_quantification",
    "bin_reassembly",
    "bin_annotation",
]
STEPS = [
    *CORE_STEPS,
    "report",
]

DATABASE_REFERENCE_PATHS = {
    "kraken_db": ("taxonomy_reads", "kraken2"),
    "humann_nucleotide_db": ("function_reads", "humann_nucleotide"),
    "humann_protein_db": ("function_reads", "humann_protein"),
    "metaphlan_db": ("function_reads", "metaphlan"),
}
INTERACTIVE_CAPABILITIES = {
    "file_uploads": True,
    "task_cancellation": True,
    "result_preview": True,
}


class TaskValidationError(ValueError):
    pass


from .input_lifecycle import input_operation


class TaskService:
    def __init__(self, settings: Settings, repository: TaskRepository):
        self.settings = settings
        self.repository = repository

    def validate_manifest_path(self, raw_path: str) -> Path:
        try:
            candidate = Path(raw_path).expanduser().resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise TaskValidationError("manifest_path does not exist") from exc
        approved_root = self.settings.input_root.resolve(strict=True)
        try:
            candidate.relative_to(approved_root)
        except ValueError as exc:
            raise TaskValidationError("manifest_path is outside the approved input root") from exc
        if candidate.suffix.lower() != ".csv":
            raise TaskValidationError("manifest_path must point to a .csv file")
        if not candidate.is_file():
            raise TaskValidationError("manifest_path must point to a regular file")
        with candidate.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
        if header != ["sample_id", "read1", "read2"]:
            raise TaskValidationError("manifest header must be exactly: sample_id,read1,read2")
        return candidate

    @input_operation
    def create_task(
        self,
        raw_manifest_path: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        manifest_path = self.validate_manifest_path(raw_manifest_path)
        validated = WorkflowParameters.model_validate(parameters or {}).model_dump(mode="json")
        self._apply_server_managed_references(validated)
        if validated["enable_mags"]:
            capabilities = self.capabilities()
            if not capabilities["mag_analysis"]:
                reason = capabilities["mag_unavailable_reason"]
                raise TaskValidationError(
                    f"MAG analysis is unavailable: {reason}"
                )
        return self.repository.create_task(
            str(uuid.uuid4()),
            str(manifest_path),
            steps_for_parameters(validated),
            validated,
        )

    def capabilities(self) -> dict[str, Any]:
        profile = self.settings.default_database_profile
        manifest_path = self.settings.default_database_manifest
        if manifest_path is None:
            return {
                "reads_analysis": False,
                "mag_analysis": False,
                "mag_unavailable_reason": (
                    "resolved database manifest is not configured"
                ),
                "database_profile": profile,
                **INTERACTIVE_CAPABILITIES,
            }
        try:
            payload = _database_manifest_payload(manifest_path)
        except TaskValidationError as error:
            return {
                "reads_analysis": False,
                "mag_analysis": False,
                "mag_unavailable_reason": str(error),
                "database_profile": profile,
                **INTERACTIVE_CAPABILITIES,
            }

        manifest_profile = payload.get("database_profile")
        if isinstance(manifest_profile, str) and manifest_profile.strip():
            profile = manifest_profile
        databases = payload.get("databases")
        capabilities = payload.get("capabilities")
        reads_available = _has_database_entries(
            databases,
            {
                "taxonomy_reads": ("kraken2",),
                "function_reads": (
                    "humann_nucleotide",
                    "humann_protein",
                    "metaphlan",
                ),
            },
        )
        mag_available = _has_database_entries(
            databases,
            {"mag_annotation": ("classification", "function")},
        )
        reason = None if mag_available else (
            "database profile does not provide complete MAG annotation databases"
        )
        if isinstance(capabilities, dict):
            if "reads_analysis" in capabilities:
                reads_available = (
                    reads_available
                    and capabilities["reads_analysis"] is True
                )
            if "mag_analysis" in capabilities:
                mag_available = (
                    mag_available
                    and capabilities["mag_analysis"] is True
                )
            configured_reason = capabilities.get("mag_unavailable_reason")
            if not mag_available and isinstance(configured_reason, str):
                reason = configured_reason
        return {
            "reads_analysis": reads_available,
            "mag_analysis": mag_available,
            "mag_unavailable_reason": reason,
            "database_profile": profile,
            **INTERACTIVE_CAPABILITIES,
        }

    def _apply_server_managed_references(self, parameters: dict[str, Any]) -> None:
        references = {
            "host_index": (
                _normalized_path(self.settings.default_host_index)
                if self.settings.default_host_index is not None
                else None
            ),
            "kraken_db": (
                _normalized_path(self.settings.default_kraken_db)
                if self.settings.default_kraken_db is not None
                else None
            ),
            "humann_nucleotide_db": (
                _normalized_path(self.settings.default_humann_nucleotide_db)
                if self.settings.default_humann_nucleotide_db is not None
                else None
            ),
            "humann_protein_db": (
                _normalized_path(self.settings.default_humann_protein_db)
                if self.settings.default_humann_protein_db is not None
                else None
            ),
            "metaphlan_db": (
                _normalized_path(self.settings.default_metaphlan_db)
                if self.settings.default_metaphlan_db is not None
                else None
            ),
        }
        if self.settings.default_database_manifest is not None:
            references.update(
                _database_references(self.settings.default_database_manifest)
            )

        for name, expected in references.items():
            supplied = parameters.get(name)
            if supplied and expected and _normalized_path(supplied) != expected:
                raise TaskValidationError(
                    f"{name} is server-managed and cannot be overridden per task"
                )
            if expected:
                parameters[name] = expected

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self.repository.get_task(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        return self.repository.list_tasks()

    def retry_task(self, task_id: str) -> dict[str, Any]:
        self.repository.reset_for_retry(task_id)
        task = self.repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        self.repository.cancel_task(task_id)
        task = self.repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def set_retry_decision(self, task_id: str, allowed: bool, reason: str) -> dict[str, Any]:
        task = self.repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if task["status"] != "failed":
            raise ValueError("retry decision can only be set for a failed task")
        self.repository.update_task(task_id, retry_allowed=allowed)
        self.repository.add_alert(task_id, "info", reason)
        updated = self.repository.get_task(task_id)
        if updated is None:
            raise KeyError(task_id)
        return updated

    def read_log(self, task_id: str, step: str, max_bytes: int = 65536) -> str:
        task = self.repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if step not in {item["name"] for item in task["steps"]}:
            raise TaskValidationError("unknown workflow step")
        if max_bytes < 1:
            raise TaskValidationError("max_bytes must be positive")

        candidates: list[Path] = []
        runner_log = self.settings.state_root / "logs" / task_id / f"{step}.log"
        if runner_log.is_file():
            candidates.append(runner_log)
        workflow_log_dir = self.settings.state_root / "outputs" / task_id / "logs"
        if workflow_log_dir.is_dir():
            candidates.extend(
                path
                for path in sorted(workflow_log_dir.glob(f"*.{step}.log"))
                if path.is_file()
            )
        if not candidates:
            with self.repository._connect() as connection:
                table = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='log_deletions'").fetchone()
                if table and connection.execute('SELECT 1 FROM log_deletions WHERE task_id=? AND step=? LIMIT 1', (task_id, step)).fetchone():
                    return '该阶段日志已由用户清理；分析结果和运行状态记录仍保留。'
            raise FileNotFoundError(f"no log is available for step {step}")

        per_file = max(1024, max_bytes // len(candidates))
        chunks: list[str] = []
        for path in candidates:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                offset = max(0, size - per_file)
                handle.seek(offset)
                content = handle.read(per_file).decode(
                    "utf-8",
                    errors="replace",
                )
            if offset:
                first_newline = content.find("\n")
                content = (
                    content[first_newline + 1 :] if first_newline >= 0 else ""
                )
            chunks.append(f"--- {path.name} ---\n{content}")
        combined = tail_excerpt("\n".join(chunks), max_chars=max_bytes)
        return redact_log(
            combined,
            input_root=str(self.settings.input_root.resolve()),
            sample_identifiers=_manifest_sample_ids(Path(task["manifest_path"])),
        )

    def result_archive(self, task_id: str) -> Path:
        task = self.repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if task["status"] != "completed" or not task["result_archive"]:
            raise FileNotFoundError("result archive is not available")
        stored_archive = Path(task["result_archive"])
        if stored_archive.is_symlink():
            raise TaskValidationError("result archive has an unexpected name or type")
        archive = stored_archive.resolve(strict=True)
        allowed_root = (self.settings.state_root / "outputs" / task_id).resolve()
        try:
            archive.relative_to(allowed_root)
        except ValueError as exc:
            raise TaskValidationError("result archive is outside the task output directory") from exc
        if archive.name != f"{task_id}.tar.gz":
            raise TaskValidationError("result archive has an unexpected name or type")
        return archive


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


def _normalized_path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve(strict=False))


def _database_manifest_payload(manifest_path: Path) -> dict:
    try:
        resolved = manifest_path.expanduser().resolve(strict=True)
        if not resolved.is_file():
            raise OSError("not a regular file")
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise TaskValidationError(
            "the server database manifest is missing or invalid"
        ) from exc

    if not isinstance(payload, dict):
        raise TaskValidationError("the server database manifest root is invalid")
    return payload


def _database_references(manifest_path: Path) -> dict[str, str]:
    payload = _database_manifest_payload(manifest_path)
    databases = payload.get("databases")
    if not isinstance(databases, dict):
        raise TaskValidationError("the server database manifest lacks databases")
    references: dict[str, str] = {}
    for parameter, (group, name) in DATABASE_REFERENCE_PATHS.items():
        group_payload = databases.get(group)
        entry = group_payload.get(name) if isinstance(group_payload, dict) else None
        path = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(path, str) or not path.strip():
            raise TaskValidationError(
                f"the server database manifest lacks {group}.{name}.path"
            )
        references[parameter] = _normalized_path(path)
    return references


def _has_database_entries(
    databases: object,
    required: dict[str, tuple[str, ...]],
) -> bool:
    if not isinstance(databases, dict):
        return False
    for group, names in required.items():
        entries = databases.get(group)
        if not isinstance(entries, dict):
            return False
        if any(not isinstance(entries.get(name), dict) for name in names):
            return False
    return True


def steps_for_parameters(parameters: dict[str, Any]) -> list[str]:
    steps = list(CORE_STEPS)
    if parameters.get("enable_mags"):
        steps.extend(MAG_STEPS[:4])
        if parameters.get("enable_reassembly", True):
            steps.append("bin_reassembly")
        steps.append("bin_annotation")
    steps.append("report")
    return steps
