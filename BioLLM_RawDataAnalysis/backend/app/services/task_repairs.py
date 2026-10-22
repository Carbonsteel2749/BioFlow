"""Fail-closed, task-scoped repair plans for standard workflow tasks.

Model output is untrusted input.  This module never executes model-provided
commands: it accepts only a tiny parameter allowlist or a mechanical, single
file unified diff for an exact stage/script mapping.  Every repair version is
published once and is verified again immediately before execution.
"""
from __future__ import annotations

import difflib
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Mapping

from ..models import utc_now
from ..schemas import WorkflowParameters
from .llm_diagnostics import Diagnostic
from .redaction import redact_log


IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
VERSION = re.compile(r"v([0-9]{6})")
MAX_PATCH_BYTES = 64 * 1024
MAX_HUNKS = 3
MAX_CHANGED_LINES = 80

PATCH_TARGETS: dict[str, tuple[str, str]] = {
    "fastqc_raw": (
        "bin/core/fastqc.sh",
        "tests/test_preprocessing_fastqc.sh",
    ),
    "fastp": (
        "bin/core/fastp.sh",
        "tests/test_preprocessing_fastp.sh",
    ),
}

AUTO_RUNTIME_LIMITS: dict[str, tuple[int, int]] = {
    "threads": (1, 64),
    "mag_threads": (1, 64),
    "mag_memory_gb": (4, 256),
}
CONFIRMABLE_SCIENTIFIC_PARAMETERS = frozenset(
    {
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
        "host_filter_mode",
        "host_min_retained_pairs",
        "host_max_removed_pct",
        "host_bowtie2_preset",
        "read_length",
        "assembler",
        "bin_completeness",
        "bin_contamination",
        "enable_mags",
        "enable_reassembly",
    }
)
MANUAL_ONLY_PARAMETERS = frozenset(
    {
        "host_index",
        "kraken_db",
        "humann_nucleotide_db",
        "humann_protein_db",
        "metaphlan_db",
        "database_profile",
        "database_manifest",
        "sample_exclusions",
        "excluded_samples",
    }
)

_OOM_EVIDENCE = re.compile(
    r"(?i)(out of memory|oom(?:[-_ ]kill(?:ed)?)?|cannot allocate memory|"
    r"killed process|exit(?:ed)?(?: with)? (?:status|code)[=: ]*137)"
)
_FORBIDDEN_PATCH = re.compile(
    r"""(?ix)(?:
       \brm\b|\bunlink\b|\bshred\b|\btruncate\b|\bsudo\b|
       \bcurl\b|\bwget\b|\beval\b|\bexec\b|\bchmod\s+777\b|
       \bfind\b[^\n]{0,160}\s-delete\b|\|\|\s*true\b|
       \bset\s+\+[^\n]*[eu]\b|\bexit\s+0\b|\btouch\b|
       /dev/null\s*$|\.\./|\\
    )"""
)
_SCIENTIFIC_OR_GUARDRAIL = re.compile(
    r"(?i)(qualified_quality|unqualified_percent|n_base_limit|length_required|"
    r"cut_mean_quality|host_(?:filter|min|max|index)|kraken|humann|metaphlan|"
    r"database|bin_completeness|bin_contamination|sample_excl|"
    r"step_success|status_write|checkpoint_valid|outputs_are_complete|"
    r"reports_are_complete|set\s+-E?euo\s+pipefail)"
)


class TaskRepairError(ValueError):
    pass


class TaskRepairNotFound(TaskRepairError):
    pass


class TaskRepairConflict(TaskRepairError):
    pass


class TaskRepairValidationError(TaskRepairError):
    pass


class TaskRepairLimitReached(TaskRepairConflict):
    pass


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise TaskRepairValidationError("repair input is not a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _write_once(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _write_json_once(path: Path, payload: Any) -> None:
    _write_once(path, _canonical_json(payload))


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or str(path) != value
        or any(ord(character) < 32 for character in value)
    ):
        raise TaskRepairValidationError("unsafe repair-relative path")
    return value


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        raise TaskRepairValidationError("repair proposal is too deeply nested")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise TaskRepairValidationError("non-finite repair value")
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > 256:
            raise TaskRepairValidationError("repair proposal list is too long")
        return [_json_safe(item, depth=depth + 1) for item in value]
    if isinstance(value, Mapping):
        if len(value) > 256:
            raise TaskRepairValidationError("repair proposal object is too large")
        return {
            str(key): _json_safe(item, depth=depth + 1)
            for key, item in value.items()
        }
    raise TaskRepairValidationError("repair proposal contains an unsupported value")


def _safe_quote_addition(before: str, after: str) -> bool:
    # Removing quotes or merging whitespace can alter executable semantics.
    match = re.fullmatch(
        r'([A-Za-z_][A-Za-z0-9_]*)=([A-Za-z0-9_./:-]+|\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*)',
        before.strip(),
    )
    return bool(match and after.strip() == f'{match[1]}="{match[2]}"')


def _heredoc_lines(lines: list[str]) -> set[int]:
    protected: set[int] = set()
    delimiter: str | None = None
    for index, line in enumerate(lines):
        if delimiter is not None:
            protected.add(index)
            if line.strip() == delimiter:
                delimiter = None
        elif "<<" in line:
            protected.add(index)
            match = re.search(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", line)
            delimiter = match[2] if match else "[UNRECOGNIZED_HEREDOC]"
    return protected


def _apply_single_file_diff(
    original: str,
    unified_diff: str,
    expected_path: str,
) -> tuple[str, str]:
    if len(unified_diff.encode("utf-8")) > MAX_PATCH_BYTES:
        raise TaskRepairValidationError("patch exceeds the 64 KiB limit")
    lines = unified_diff.splitlines(keepends=True)
    if len(lines) < 3:
        raise TaskRepairValidationError("patch is incomplete")
    if lines[0].rstrip("\r\n") != f"--- a/{expected_path}":
        raise TaskRepairValidationError("patch old path is not the allowlisted script")
    if lines[1].rstrip("\r\n") != f"+++ b/{expected_path}":
        raise TaskRepairValidationError("patch new path is not the allowlisted script")
    if any(line.startswith(("--- ", "+++ ", "diff --git", "Binary files")) for line in lines[2:]):
        raise TaskRepairValidationError("only one existing text file may be patched")

    source = original.splitlines(keepends=True)
    protected = _heredoc_lines(source)
    output: list[str] = []
    cursor = 0
    index = 2
    hunks = 0
    changed = 0
    while index < len(lines):
        header = lines[index].rstrip("\r\n")
        match = re.fullmatch(
            r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?",
            header,
        )
        if match is None:
            raise TaskRepairValidationError("patch contains data outside a hunk")
        hunks += 1
        if hunks > MAX_HUNKS:
            raise TaskRepairValidationError("patch contains too many hunks")
        old_start = int(match.group(1))
        old_count = int(match.group(2) or "1")
        new_count = int(match.group(4) or "1")
        start = old_start - 1
        if start < cursor or start > len(source):
            raise TaskRepairValidationError("patch hunk position is invalid")
        output.extend(source[cursor:start])
        cursor = start
        index += 1
        old_seen = 0
        new_seen = 0
        removed: list[str] = []
        added: list[str] = []
        while index < len(lines) and not lines[index].startswith("@@ "):
            line = lines[index]
            if line.startswith(" "):
                if cursor >= len(source) or source[cursor] != line[1:]:
                    raise TaskRepairValidationError("patch context does not match the base script")
                output.append(source[cursor])
                cursor += 1
                old_seen += 1
                new_seen += 1
            elif line.startswith("-"):
                if cursor >= len(source) or source[cursor] != line[1:]:
                    raise TaskRepairValidationError("patch deletion does not match the base script")
                if cursor in protected:
                    raise TaskRepairValidationError("automatic repair cannot modify embedded code or heredocs")
                removed.append(line[1:])
                cursor += 1
                old_seen += 1
                changed += 1
            elif line.startswith("+"):
                added.append(line[1:])
                output.append(line[1:])
                new_seen += 1
                changed += 1
            else:
                raise TaskRepairValidationError("unsupported patch marker")
            index += 1
        if old_seen != old_count or new_seen != new_count:
            raise TaskRepairValidationError("patch hunk counts do not match its header")
        if len(removed) != len(added) or any(
            not _safe_quote_addition(before, after)
            for before, after in zip(removed, added)
        ):
            raise TaskRepairValidationError(
                "automatic script repair is limited to double-quote or whitespace changes on simple assignment values"
            )
        if changed > MAX_CHANGED_LINES:
            raise TaskRepairValidationError("patch changes too many lines")
        changed_text = "".join((*removed, *added))
        if _FORBIDDEN_PATCH.search(changed_text):
            raise TaskRepairValidationError("patch contains a forbidden operation")
        if _SCIENTIFIC_OR_GUARDRAIL.search(changed_text):
            raise TaskRepairValidationError("patch touches a scientific or integrity guardrail")
    if hunks == 0 or changed == 0:
        raise TaskRepairValidationError("patch does not contain a change")
    output.extend(source[cursor:])
    patched = "".join(output)
    for required in (
        "set -Eeuo pipefail",
        "status_write",
        "step_success",
    ):
        if required not in patched:
            raise TaskRepairValidationError(f"patched script removed required guardrail: {required}")
    canonical = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=f"a/{expected_path}",
            tofile=f"b/{expected_path}",
        )
    )
    return patched, canonical


class TaskRepairService:
    def __init__(
        self,
        state_root: Path,
        workflow_path: Path,
        *,
        max_attempts: int = 2,
        validation_timeout_seconds: float = 300.0,
    ) -> None:
        if not 1 <= max_attempts <= 2:
            raise ValueError("max_attempts must be between one and two")
        self.state_root = state_root.resolve()
        self.root = self.state_root / "task_repairs"
        self.workflow_root = workflow_path.resolve().parent
        self.max_attempts = max_attempts
        self.validation_timeout_seconds = validation_timeout_seconds
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink() or self.root.resolve() != self.root:
            raise TaskRepairValidationError("unsafe task repair root")
        os.chmod(self.root, 0o700)

    def _task_root(self, task_id: str, *, create: bool = False) -> Path:
        if IDENTIFIER.fullmatch(task_id) is None:
            raise TaskRepairValidationError("unsafe task ID")
        path = self.root / task_id
        if create:
            path.mkdir(mode=0o700, exist_ok=True)
            os.chmod(path, 0o700)
        if path.exists() and (path.is_symlink() or path.resolve() != path):
            raise TaskRepairValidationError("unsafe task repair directory")
        return path

    @contextmanager
    def _locked(self, task_id: str) -> Iterator[Path]:
        task_root = self._task_root(task_id, create=True)
        lock_path = task_root / ".lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "r+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield task_root

    @staticmethod
    def _versions(task_root: Path) -> list[tuple[int, Path]]:
        versions: list[tuple[int, Path]] = []
        versions_root = task_root / "versions"
        if not versions_root.is_dir():
            return versions
        for path in versions_root.iterdir():
            match = VERSION.fullmatch(path.name)
            if match and path.is_dir() and not path.is_symlink():
                versions.append((int(match.group(1)), path))
        return sorted(versions)

    def _next_version(self, task_root: Path) -> int:
        versions = self._versions(task_root)
        next_version = (versions[-1][0] + 1) if versions else 1
        if next_version > self.max_attempts:
            raise TaskRepairLimitReached("maximum of two automatic repair rounds reached")
        return next_version

    def _copy_workflow(self, destination: Path, source_root: Path | None = None) -> dict[str, str]:
        source_root = source_root or self.workflow_root
        destination.mkdir(mode=0o700)
        hashes: dict[str, str] = {}
        for parent, directories, filenames in os.walk(source_root, followlinks=False):
            source_parent = Path(parent)
            relative_parent = source_parent.relative_to(source_root)
            directories[:] = sorted(
                name for name in directories if name not in {"__pycache__", ".git"}
            )
            for directory in directories:
                source = source_parent / directory
                if source.is_symlink() or not stat.S_ISDIR(source.lstat().st_mode):
                    raise TaskRepairValidationError("workflow bundle contains an unsafe directory")
                (destination / relative_parent / directory).mkdir(
                    parents=True,
                    exist_ok=False,
                    mode=0o700,
                )
            for filename in sorted(filenames):
                source = source_parent / filename
                relative = (relative_parent / filename).as_posix()
                info = source.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                    raise TaskRepairValidationError("workflow bundle contains a link or special file")
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    with os.fdopen(source_fd, "rb", closefd=False) as reader:
                        with os.fdopen(target_fd, "wb", closefd=False) as writer:
                            shutil.copyfileobj(reader, writer)
                            writer.flush()
                            os.fsync(writer.fileno())
                finally:
                    os.close(source_fd)
                    os.close(target_fd)
                if info.st_mode & 0o111:
                    os.chmod(target, 0o700)
                hashes[relative] = _sha_file(target)
        return hashes

    @staticmethod
    def _replace_regular(path: Path, content: bytes, executable: bool) -> None:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise TaskRepairValidationError("patch target is not a regular file")
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(descriptor)
        os.chmod(temporary, 0o700 if executable else 0o600)
        os.replace(temporary, path)

    @staticmethod
    def _adjustments(diagnostic: Diagnostic) -> list[dict[str, Any]]:
        return [
            item.to_dict() if hasattr(item, "to_dict") else dict(item)
            for item in diagnostic.parameter_adjustments
        ]

    def _classify_parameters(
        self,
        current: Mapping[str, Any],
        adjustments: list[dict[str, Any]],
        redacted_log_excerpt: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        automatic: dict[str, Any] = {}
        pending: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for item in adjustments:
            name = item.get("name")
            proposed = item.get("proposed_value")
            reason = str(item.get("reason") or "")[:500]
            record = {"name": name, "proposed_value": _json_safe(proposed), "reason": reason}
            if not isinstance(name, str) or name not in current:
                rejected.append({**record, "reason": "unknown or unavailable task parameter"})
                continue
            record["current_value"] = _json_safe(current[name])
            if name in AUTO_RUNTIME_LIMITS:
                minimum, maximum = AUTO_RUNTIME_LIMITS[name]
                if isinstance(proposed, bool) or not isinstance(proposed, int):
                    rejected.append({**record, "reason": "runtime value must be an integer"})
                    continue
                existing = current.get(name)
                if isinstance(existing, bool) or not isinstance(existing, int):
                    rejected.append({**record, "reason": "current runtime value is invalid"})
                    continue
                if not minimum <= proposed <= maximum:
                    rejected.append({**record, "reason": "runtime value exceeds the server bound"})
                    continue
                if not _OOM_EVIDENCE.search(redacted_log_excerpt):
                    rejected.append({**record, "reason": "resource change lacks deterministic OOM evidence"})
                    continue
                if name in {"threads", "mag_threads"}:
                    if not max(minimum, (existing + 1) // 2) <= proposed <= existing:
                        rejected.append({**record, "reason": "thread reduction must be no more than one half per round"})
                        continue
                elif not existing <= proposed <= min(maximum, existing * 2):
                    rejected.append({**record, "reason": "memory increase must be at most two-fold per round"})
                    continue
                automatic[name] = proposed
            elif name in CONFIRMABLE_SCIENTIFIC_PARAMETERS:
                pending.append({**record, "applicable_after_confirmation": True})
            elif name in MANUAL_ONLY_PARAMETERS:
                pending.append({**record, "applicable_after_confirmation": False})
            else:
                rejected.append({**record, "reason": "parameter is not on a repair allowlist"})
        return automatic, pending, rejected

    def _run_fixed_test(
        self,
        command: list[str],
        *,
        cwd: Path,
        cancel_check: Callable[[], bool] | None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            start_new_session=True,
            text=True,
        )
        timed_out = False
        cancelled = False
        while process.poll() is None:
            if cancel_check is not None and cancel_check():
                cancelled = True
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                break
            if time.monotonic() - started >= self.validation_timeout_seconds:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                break
            time.sleep(0.05)
        try:
            output, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            output, _ = process.communicate()
        safe_output = redact_log(output or "")[-12000:]
        return {
            "argv": [Path(command[0]).name, Path(command[1]).name],
            "return_code": process.returncode,
            "timed_out": timed_out,
            "cancelled": cancelled,
            "duration_seconds": round(time.monotonic() - started, 3),
            "redacted_output": safe_output,
            "passed": process.returncode == 0 and not timed_out and not cancelled,
        }

    def create_plan(
        self,
        *,
        task: Mapping[str, Any],
        step: str,
        diagnostic: Diagnostic,
        redacted_log_excerpt: str,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        task_id = str(task.get("id") or "")
        if task.get("status") == "cancelled" or (cancel_check is not None and cancel_check()):
            raise TaskRepairConflict("cancelled tasks cannot create repair plans")
        with self._locked(task_id) as task_root:
            version = self._next_version(task_root)
            versions_root = task_root / "versions"
            versions_root.mkdir(mode=0o700, exist_ok=True)
            name = f"v{version:06d}"
            destination = versions_root / name
            temporary = versions_root / f".{name}.{uuid.uuid4().hex}.tmp"
            temporary.mkdir(mode=0o700)
            try:
                applied = self.applied_inputs(task_id, retry_count=int(task.get("retry_count") or 0))
                parameters = dict(task.get("parameters") or {})
                parameters.update(applied["runtime_overrides"])
                adjustments = self._adjustments(diagnostic)
                automatic, pending, rejected = self._classify_parameters(
                    parameters,
                    adjustments,
                    redacted_log_excerpt,
                )
                request = {
                    "schema_version": 1,
                    "task_id": task_id,
                    "version": version,
                    "created_at": utc_now(),
                    "step": step,
                    "exit_context_sha256": _sha_bytes(redacted_log_excerpt.encode("utf-8")),
                    "base_parameters_sha256": _sha_bytes(_canonical_json(parameters)),
                    "expected_retry_count": int(task.get("retry_count") or 0) + 1,
                }
                _write_json_once(temporary / "request.json", request)
                diagnosis = diagnostic.to_dict()
                raw_model = str(diagnosis.pop("raw_model_output", ""))
                diagnosis_bytes = _canonical_json(_json_safe(diagnosis))
                raw_model_bytes = redact_log(raw_model)[:20000].encode("utf-8")
                _write_once(temporary / "diagnosis.json", diagnosis_bytes)
                _write_once(temporary / "model.raw.txt", raw_model_bytes)
                record_hashes = {
                    "diagnosis.json": _sha_bytes(diagnosis_bytes),
                    "model.raw.txt": _sha_bytes(raw_model_bytes),
                }
                proposal: dict[str, Any] = {
                    "automatic_runtime_overrides": automatic,
                    "pending_confirmation": pending,
                    "rejected_adjustments": rejected,
                    "script_patch": None,
                }
                validation: dict[str, Any] = {
                    "path_scope": {"passed": True},
                    "syntax": {"passed": True, "skipped": True},
                    "targeted_test": {"passed": True, "skipped": True},
                    "output_contract": {"passed": True, "skipped": True},
                }
                patch_error = ""
                script_patch = diagnostic.script_patch
                if script_patch is not None:
                    patch_data = (
                        script_patch.to_dict()
                        if hasattr(script_patch, "to_dict")
                        else dict(script_patch)
                    )
                    target, test = PATCH_TARGETS.get(step, ("", ""))
                    supplied_target = _safe_relative(str(patch_data.get("target_path") or ""))
                    if not target or supplied_target != target:
                        raise TaskRepairValidationError("failed stage is not mapped to the proposed script")
                    source_root = applied["workflow_path"].parent if applied["workflow_path"] else self.workflow_root
                    source = source_root / target
                    if source.is_symlink() or not source.is_file():
                        raise TaskRepairValidationError("public patch source is unsafe")
                    original_bytes = source.read_bytes()
                    original_sha = _sha_bytes(original_bytes)
                    if patch_data.get("base_sha256") != original_sha:
                        raise TaskRepairValidationError("patch base checksum does not match the public script")
                    try:
                        original_text = original_bytes.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise TaskRepairValidationError("patch source is not UTF-8 text") from exc
                    patched_text, canonical_diff = _apply_single_file_diff(
                        original_text,
                        str(patch_data.get("unified_diff") or ""),
                        target,
                    )
                    workflow = temporary / "workflow"
                    before_hashes = self._copy_workflow(workflow, source_root)
                    _write_once(temporary / "original" / target, original_bytes)
                    patched_path = workflow / target
                    self._replace_regular(
                        patched_path,
                        patched_text.encode("utf-8"),
                        executable=True,
                    )
                    after_hashes = {
                        relative: _sha_file(workflow / relative)
                        for relative in before_hashes
                    }
                    changed_paths = [
                        relative
                        for relative in before_hashes
                        if before_hashes[relative] != after_hashes[relative]
                    ]
                    if changed_paths != [target]:
                        raise TaskRepairValidationError("repair changed files outside the one-file allowlist")
                    _write_once(temporary / "change.diff", canonical_diff.encode("utf-8"))
                    syntax = self._run_fixed_test(
                        ["/bin/bash", "-n", str(patched_path)],
                        cwd=workflow,
                        cancel_check=cancel_check,
                    )
                    validation["syntax"] = syntax
                    if syntax["passed"]:
                        targeted = self._run_fixed_test(
                            ["/bin/bash", str(workflow / test)],
                            cwd=temporary,
                            cancel_check=cancel_check,
                        )
                    else:
                        targeted = {"passed": False, "skipped": True, "reason": "syntax failed"}
                    validation["targeted_test"] = targeted
                    validation["output_contract"] = {
                        "passed": targeted.get("passed") is True,
                        "contract": (
                            "fixed stage regression verifies status, non-empty outputs, "
                            "format integrity and checkpoint behavior"
                        ),
                    }
                    _write_json_once(
                        temporary / "checksums.json",
                        {
                            "algorithm": "sha256",
                            "base_workflow": before_hashes,
                            "execution_workflow": after_hashes,
                            "target": target,
                            "original_sha256": original_sha,
                            "patched_sha256": after_hashes[target],
                            "diff_sha256": _sha_bytes(canonical_diff.encode("utf-8")),
                        },
                    )
                    proposal["script_patch"] = {
                        "target_path": target,
                        "reason": str(patch_data.get("reason") or "")[:1000],
                        "base_sha256": original_sha,
                        "patched_sha256": after_hashes[target],
                        "diff_sha256": _sha_bytes(canonical_diff.encode("utf-8")),
                    }
                    if not all(
                        validation[key].get("passed") is True
                        for key in ("path_scope", "syntax", "targeted_test", "output_contract")
                    ):
                        patch_error = "script repair validation failed"
                _write_json_once(temporary / "proposal.json", proposal)
                _write_json_once(temporary / "validation" / "record.json", validation)
                safe_change = bool(automatic or proposal["script_patch"])
                if rejected or patch_error:
                    status = "rejected"
                    reason = patch_error or "proposal contains a rejected parameter change"
                elif pending:
                    status = "awaiting_confirmation"
                    reason = "scientific or server-managed changes require user confirmation"
                elif safe_change:
                    status = "validated"
                    reason = "all deterministic repair checks passed"
                else:
                    status = "rejected"
                    reason = "model did not propose an allowlisted repair"
                decision = {
                    "status": status,
                    "reason": reason,
                    "created_at": utc_now(),
                    "record_hashes": record_hashes,
                }
                plan_material = {"request": request, "proposal": proposal, "validation": validation, "decision": decision}
                plan_sha256 = _sha_bytes(_canonical_json(plan_material))
                decision["plan_sha256"] = plan_sha256
                _write_json_once(temporary / "decision.json", decision)
                os.rename(temporary, destination)
                self._make_read_only(destination)
                return self.get_attempt(task_id, version)
            except Exception:
                shutil.rmtree(temporary, ignore_errors=True)
                raise

    def record_rejection(
        self,
        *,
        task: Mapping[str, Any],
        step: str,
        diagnostic: Diagnostic,
        redacted_log_excerpt: str,
        reason: str,
    ) -> dict[str, Any]:
        """Persist a rejected untrusted proposal without copying or changing code."""
        task_id = str(task.get("id") or "")
        if task.get("status") == "cancelled" or task.get("cancel_requested"):
            raise TaskRepairConflict("cancelled tasks cannot create repair records")
        with self._locked(task_id) as task_root:
            version = self._next_version(task_root)
            versions_root = task_root / "versions"
            versions_root.mkdir(mode=0o700, exist_ok=True)
            name = f"v{version:06d}"
            destination = versions_root / name
            temporary = versions_root / f".{name}.{uuid.uuid4().hex}.tmp"
            temporary.mkdir(mode=0o700)
            try:
                parameters = dict(task.get("parameters") or {})
                request = {
                    "schema_version": 1,
                    "task_id": task_id,
                    "version": version,
                    "created_at": utc_now(),
                    "step": step,
                    "exit_context_sha256": _sha_bytes(
                        redacted_log_excerpt.encode("utf-8")
                    ),
                    "base_parameters_sha256": _sha_bytes(
                        _canonical_json(parameters)
                    ),
                    "expected_retry_count": int(task.get("retry_count") or 0) + 1,
                }
                _write_json_once(temporary / "request.json", request)
                diagnosis = diagnostic.to_dict()
                raw_model = str(diagnosis.pop("raw_model_output", ""))
                diagnosis_bytes = _canonical_json(_json_safe(diagnosis))
                raw_model_bytes = redact_log(raw_model)[:20000].encode("utf-8")
                _write_once(temporary / "diagnosis.json", diagnosis_bytes)
                _write_once(temporary / "model.raw.txt", raw_model_bytes)
                record_hashes = {
                    "diagnosis.json": _sha_bytes(diagnosis_bytes),
                    "model.raw.txt": _sha_bytes(raw_model_bytes),
                }
                patch_summary = None
                if diagnostic.script_patch is not None:
                    patch = diagnostic.script_patch.to_dict()
                    patch_summary = {
                        "target_path": str(patch.get("target_path") or "")[:512],
                        "base_sha256": str(patch.get("base_sha256") or "")[:128],
                        "reason": str(patch.get("reason") or "")[:1000],
                        "diff_sha256": _sha_bytes(
                            str(patch.get("unified_diff") or "").encode("utf-8")
                        ),
                    }
                proposal = {
                    "automatic_runtime_overrides": {},
                    "pending_confirmation": [],
                    "rejected_adjustments": self._adjustments(diagnostic),
                    "script_patch": patch_summary,
                }
                validation = {
                    "path_scope": {
                        "passed": False,
                        "reason": redact_log(reason)[:1000],
                    },
                    "syntax": {"passed": False, "skipped": True},
                    "targeted_test": {"passed": False, "skipped": True},
                    "output_contract": {"passed": False, "skipped": True},
                }
                decision = {
                    "status": "rejected",
                    "reason": redact_log(reason)[:1000],
                    "created_at": utc_now(),
                    "record_hashes": record_hashes,
                }
                material = {
                    "request": request,
                    "proposal": proposal,
                    "validation": validation,
                    "decision": decision,
                }
                decision["plan_sha256"] = _sha_bytes(_canonical_json(material))
                _write_json_once(temporary / "proposal.json", proposal)
                _write_json_once(
                    temporary / "validation" / "record.json",
                    validation,
                )
                _write_json_once(temporary / "decision.json", decision)
                os.rename(temporary, destination)
                self._make_read_only(destination)
                return self.get_attempt(task_id, version)
            except Exception:
                shutil.rmtree(temporary, ignore_errors=True)
                raise

    @staticmethod
    def _make_read_only(root: Path) -> None:
        for parent, directories, filenames in os.walk(root, topdown=False, followlinks=False):
            for filename in filenames:
                path = Path(parent) / filename
                mode = 0o500 if path.stat().st_mode & 0o111 else 0o400
                os.chmod(path, mode)
            for directory in directories:
                os.chmod(Path(parent) / directory, 0o700)
        # Files stay read-only and checksummed; private directories allow safe
        # task deletion through the existing validated cleanup service.
        os.chmod(root, 0o700)

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise TaskRepairValidationError("repair record is unreadable") from exc
        if not isinstance(payload, dict):
            raise TaskRepairValidationError("repair record is not an object")
        return payload

    def _attempt_path(self, task_id: str, version: int) -> Path:
        if not 1 <= version <= self.max_attempts:
            raise TaskRepairNotFound("repair version not found")
        path = self._task_root(task_id) / "versions" / f"v{version:06d}"
        if not path.is_dir() or path.is_symlink() or path.resolve() != path:
            raise TaskRepairNotFound("repair version not found")
        return path

    def _events(self, task_id: str) -> list[dict[str, Any]]:
        events_root = self._task_root(task_id) / "events"
        if not events_root.is_dir():
            return []
        if events_root.is_symlink() or events_root.resolve() != events_root:
            raise TaskRepairValidationError("unsafe repair event directory")
        events: list[dict[str, Any]] = []
        previous = ""
        paths = sorted(events_root.iterdir())
        for index, path in enumerate(paths, start=1):
            if (
                path.name != f"e{index:06d}.json"
                or path.is_symlink()
                or not path.is_file()
            ):
                raise TaskRepairValidationError("unsafe repair event")
            payload = self._load_json(path)
            stored = payload.pop("event_sha256", None)
            if payload.get("previous_event_sha256", "") != previous:
                raise TaskRepairValidationError("repair event chain is broken")
            calculated = _sha_bytes(_canonical_json(payload))
            if stored != calculated:
                raise TaskRepairValidationError("repair event checksum mismatch")
            payload["event_sha256"] = stored
            previous = stored
            events.append(payload)
        return events

    def _append_event(self, task_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
        events = self._events(task_root.name)
        events_root = task_root / "events"
        events_root.mkdir(mode=0o700, exist_ok=True)
        record = {
            **payload,
            "created_at": utc_now(),
            "previous_event_sha256": events[-1]["event_sha256"] if events else "",
        }
        record["event_sha256"] = _sha_bytes(_canonical_json(record))
        _write_json_once(events_root / f"e{len(events) + 1:06d}.json", record)
        return record

    @staticmethod
    def _verify_plan_records(
        path: Path,
        *,
        request: dict[str, Any],
        proposal: dict[str, Any],
        validation: dict[str, Any],
        decision: dict[str, Any],
    ) -> None:
        record_hashes = decision.get("record_hashes")
        expected_names = {"diagnosis.json", "model.raw.txt"}
        if not isinstance(record_hashes, dict) or set(record_hashes) != expected_names:
            raise TaskRepairValidationError("repair record checksums are missing")
        for relative, expected in record_hashes.items():
            if (
                not isinstance(expected, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected) is None
                or _sha_file(path / relative) != expected
            ):
                raise TaskRepairValidationError("repair record checksum mismatch")
        stored_plan_sha = decision.get("plan_sha256")
        if not isinstance(stored_plan_sha, str) or re.fullmatch(
            r"[0-9a-f]{64}", stored_plan_sha
        ) is None:
            raise TaskRepairValidationError("repair plan checksum is invalid")
        decision_material = dict(decision)
        decision_material.pop("plan_sha256", None)
        material = {
            "request": request,
            "proposal": proposal,
            "validation": validation,
            "decision": decision_material,
        }
        if _sha_bytes(_canonical_json(material)) != stored_plan_sha:
            raise TaskRepairValidationError("repair plan checksum mismatch")

    def get_attempt(self, task_id: str, version: int) -> dict[str, Any]:
        path = self._attempt_path(task_id, version)
        request = self._load_json(path / "request.json")
        diagnosis = self._load_json(path / "diagnosis.json")
        proposal = self._load_json(path / "proposal.json")
        validation = self._load_json(path / "validation" / "record.json")
        decision = self._load_json(path / "decision.json")
        self._verify_plan_records(
            path,
            request=request,
            proposal=proposal,
            validation=validation,
            decision=decision,
        )
        events = [event for event in self._events(task_id) if event.get("version") == version]
        effective_status = decision["status"]
        if events:
            effective_status = str(events[-1].get("status") or effective_status)
        return {
            "task_id": task_id,
            "version": version,
            "created_at": request["created_at"],
            "step": request["step"],
            "expected_retry_count": request["expected_retry_count"],
            "plan_sha256": decision["plan_sha256"],
            "status": effective_status,
            "decision_reason": decision["reason"],
            "diagnosis": diagnosis,
            "proposal": proposal,
            "validation": validation,
            "events": events,
        }

    def summary(self, task_id: str) -> dict[str, Any]:
        task_root = self._task_root(task_id)
        attempts = [self.get_attempt(task_id, version) for version, _ in self._versions(task_root)]
        pending = [
            {"version": attempt["version"], **item}
            for attempt in attempts
            if attempt["status"] == "awaiting_confirmation"
            for item in attempt["proposal"]["pending_confirmation"]
        ]
        return {
            "task_id": task_id,
            "attempts_used": len(attempts),
            "max_attempts": self.max_attempts,
            "remaining_attempts": max(0, self.max_attempts - len(attempts)),
            "current_status": attempts[-1]["status"] if attempts else "not_started",
            "pending_confirmations": pending,
            "attempts": attempts,
        }

    def confirm(
        self,
        task_id: str,
        version: int,
        *,
        approved: bool,
        expected_plan_sha256: str,
        current_parameters: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._locked(task_id) as task_root:
            attempt = self.get_attempt(task_id, version)
            if attempt["plan_sha256"] != expected_plan_sha256:
                raise TaskRepairConflict("repair confirmation is stale")
            if attempt["status"] != "awaiting_confirmation":
                raise TaskRepairConflict("repair version is not awaiting confirmation")
            if not approved:
                self._append_event(task_root, {"version": version, "event": "user_decision", "status": "rejected_by_user"})
                return self.get_attempt(task_id, version)
            pending = attempt["proposal"]["pending_confirmation"]
            if any(not item.get("applicable_after_confirmation") for item in pending):
                status = "manual_action_required"
                overrides: dict[str, Any] = {}
            else:
                overrides = {
                    item["name"]: item["proposed_value"]
                    for item in pending
                }
                merged = {**dict(current_parameters), **overrides}
                WorkflowParameters.model_validate(merged)
                status = "authorized"
            self._append_event(
                task_root,
                {
                    "version": version,
                    "event": "user_decision",
                    "status": status,
                    "confirmed_overrides": overrides,
                },
            )
            return self.get_attempt(task_id, version)

    def execution_plan(
        self,
        task_id: str,
        *,
        retry_count: int,
    ) -> dict[str, Any] | None:
        summary = self.summary(task_id)
        if not summary["attempts"]:
            return None
        attempt = summary["attempts"][-1]
        if attempt["status"] == "failed" and retry_count == attempt["expected_retry_count"] + 1 and any(
            event.get("event") == "execution_started" for event in attempt["events"]
        ):
            applied = self.applied_inputs(task_id, retry_count=retry_count)
            return {"version": attempt["version"], "plan_sha256": attempt["plan_sha256"], **applied}
        if attempt["status"] not in {"validated", "authorized", "retry_claimed"}:
            return None
        if attempt["expected_retry_count"] != retry_count:
            return None
        path = self._attempt_path(task_id, int(attempt["version"]))
        proposal = attempt["proposal"]
        applied = self.applied_inputs(task_id, retry_count=retry_count)
        overrides = dict(applied["runtime_overrides"])
        overrides.update(proposal["automatic_runtime_overrides"])
        for event in attempt["events"]:
            overrides.update(event.get("confirmed_overrides") or {})
        workflow_path: Path | None = applied["workflow_path"]
        if proposal.get("script_patch"):
            self._verify_bundle(path, proposal)
            workflow_path = path / "workflow" / "main.nf"
        return {
            "version": attempt["version"],
            "plan_sha256": attempt["plan_sha256"],
            "workflow_path": workflow_path,
            "runtime_overrides": overrides,
        }

    def applied_inputs(self, task_id: str, *, retry_count: int) -> dict[str, Any]:
        """Carry forward only versions that actually began task execution."""
        overrides: dict[str, Any] = {}
        workflow_path: Path | None = None
        for version, path in self._versions(self._task_root(task_id)):
            attempt = self.get_attempt(task_id, version)
            if attempt["expected_retry_count"] > retry_count or not any(
                event.get("event") == "execution_started" for event in attempt["events"]
            ):
                continue
            proposal = attempt["proposal"]
            overrides.update(proposal.get("automatic_runtime_overrides") or {})
            for event in attempt["events"]:
                overrides.update(event.get("confirmed_overrides") or {})
            if proposal.get("script_patch"):
                self._verify_bundle(path, proposal)
                workflow_path = path / "workflow" / "main.nf"
        return {"runtime_overrides": overrides, "workflow_path": workflow_path}

    def _verify_bundle(self, attempt_path: Path, proposal: Mapping[str, Any]) -> None:
        checksums = self._load_json(attempt_path / "checksums.json")
        expected = checksums.get("execution_workflow")
        workflow = attempt_path / "workflow"
        if not isinstance(expected, dict) or workflow.is_symlink() or workflow.resolve() != workflow:
            raise TaskRepairValidationError("repair bundle checksum record is invalid")
        patch = proposal.get("script_patch")
        base = checksums.get("base_workflow")
        target = checksums.get("target")
        if (
            not isinstance(patch, dict)
            or not isinstance(base, dict)
            or not isinstance(target, str)
            or patch.get("target_path") != target
            or base.get(target) != patch.get("base_sha256")
            or expected.get(target) != patch.get("patched_sha256")
            or checksums.get("original_sha256") != patch.get("base_sha256")
            or checksums.get("patched_sha256") != patch.get("patched_sha256")
            or checksums.get("diff_sha256") != patch.get("diff_sha256")
        ):
            raise TaskRepairValidationError("repair bundle provenance mismatch")
        original = attempt_path / "original" / target
        change_diff = attempt_path / "change.diff"
        if (
            original.is_symlink()
            or change_diff.is_symlink()
            or _sha_file(original) != patch["base_sha256"]
            or _sha_file(change_diff) != patch["diff_sha256"]
        ):
            raise TaskRepairValidationError("repair bundle provenance checksum mismatch")
        observed: dict[str, str] = {}
        for parent, directories, filenames in os.walk(workflow, followlinks=False):
            parent_path = Path(parent)
            for name in directories:
                path = parent_path / name
                if path.is_symlink():
                    raise TaskRepairValidationError("repair bundle contains a dangerous directory link")
            for name in filenames:
                path = parent_path / name
                if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
                    raise TaskRepairValidationError("repair bundle contains a dangerous file")
                observed[path.relative_to(workflow).as_posix()] = _sha_file(path)
        if observed != expected:
            raise TaskRepairValidationError("repair bundle checksum verification failed")

    def mark_event(self, task_id: str, version: int, event: str, status: str) -> dict[str, Any]:
        with self._locked(task_id) as task_root:
            self._attempt_path(task_id, version)
            return self._append_event(
                task_root,
                {"version": version, "event": event, "status": status},
            )

    def has_history(self, task_id: str) -> bool:
        return bool(self._versions(self._task_root(task_id)))
