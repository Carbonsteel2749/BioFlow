from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .redaction import redact_log


_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_AUDIT_STRING = 24000


def _sanitize(
    value: Any,
    *,
    input_root: str | None,
    sample_identifiers: Iterable[str],
) -> Any:
    if isinstance(value, str):
        bounded = value[:_MAX_AUDIT_STRING]
        return redact_log(
            bounded,
            input_root=input_root,
            sample_identifiers=sample_identifiers,
        )
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize(
                child,
                input_root=input_root,
                sample_identifiers=sample_identifiers,
            )
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _sanitize(
                child,
                input_root=input_root,
                sample_identifiers=sample_identifiers,
            )
            for child in value
        ]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize(
        str(value),
        input_root=input_root,
        sample_identifiers=sample_identifiers,
    )


def append_diagnostic_audit(
    *,
    state_root: Path,
    task_id: str,
    event: str,
    record: Mapping[str, Any],
    input_root: str | None = None,
    sample_identifiers: Iterable[str] = (),
) -> Path:
    """Append one redacted, fsynced JSON event to a task diagnostic audit log."""

    if not _SAFE_TASK_ID.fullmatch(task_id) or ".." in task_id:
        raise ValueError("task ID contains unsafe characters")
    audit_root = state_root / "diagnostics"
    audit_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if audit_root.is_symlink():
        raise ValueError("diagnostic audit directory must not be a symlink")

    destination = audit_root / f"{task_id}.jsonl"
    if destination.exists() and (
        destination.is_symlink() or not destination.is_file()
    ):
        raise ValueError("diagnostic audit destination must be a regular file")

    payload = {
        **dict(record),
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "task_id": task_id,
        "event": event,
    }
    safe_payload = _sanitize(
        payload,
        input_root=input_root,
        sample_identifiers=tuple(sample_identifiers),
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(destination, flags, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(safe_payload, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return destination
