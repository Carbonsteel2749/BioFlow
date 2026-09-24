from __future__ import annotations

import csv
import gzip
import hashlib
import os
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO, Iterable

from ..config import Settings
from ..models import TaskRepository
from ..schemas import UploadedPair
from .tasks import TaskValidationError
from .input_lifecycle import input_operation


FASTQ_SUFFIXES = (".fastq", ".fq", ".fastq.gz", ".fq.gz")
COPY_CHUNK_BYTES = 1024 * 1024


class UploadService:
    def __init__(self, settings: Settings, repository: TaskRepository):
        self.settings = settings
        self.repository = repository

    def store(self, original_name: str | None, source: BinaryIO) -> dict:
        safe_name = _safe_original_name(original_name)
        lower_name = safe_name.lower()
        suffix = next((item for item in FASTQ_SUFFIXES if lower_name.endswith(item)), None)
        if suffix is None:
            raise TaskValidationError("file must have a .fastq, .fq, .fastq.gz or .fq.gz suffix")
        if self.settings.max_upload_bytes < 1:
            raise TaskValidationError("server upload limit is not configured")

        upload_id = uuid.uuid4().hex
        upload_dir = self.settings.input_root / "uploads" / upload_id
        upload_dir.mkdir(parents=True, exist_ok=False)
        destination = upload_dir / f"reads{suffix}"
        digest = hashlib.sha256()
        size = 0
        try:
            with destination.open("xb") as handle:
                while chunk := source.read(COPY_CHUNK_BYTES):
                    size += len(chunk)
                    if size > self.settings.max_upload_bytes:
                        raise TaskValidationError("uploaded file exceeds the configured size limit")
                    digest.update(chunk)
                    handle.write(chunk)
            if size == 0:
                raise TaskValidationError("uploaded file is empty")
            _validate_first_fastq_record(destination, compressed=suffix.endswith(".gz"))
            row = self.repository.create_upload(
                upload_id=upload_id,
                original_name=safe_name,
                stored_path=str(destination.resolve()),
                size_bytes=size,
                sha256=digest.hexdigest(),
            )
        except Exception:
            shutil.rmtree(upload_dir, ignore_errors=True)
            raise
        return {
            "id": row["id"],
            "original_name": row["original_name"],
            "size": row["size_bytes"],
            "checksum": f"sha256:{row['sha256']}",
            "status": row["status"],
        }

    @input_operation
    def create_manifest(self, pairs: Iterable[UploadedPair]) -> tuple[Path, int]:
        entries = list(pairs)
        sample_ids = [entry.sample_id for entry in entries]
        if len(sample_ids) != len(set(sample_ids)):
            raise TaskValidationError("sample_id values in an uploaded manifest must be unique")

        rows: list[dict[str, str]] = []
        for entry in entries:
            if entry.read1_upload_id == entry.read2_upload_id:
                raise TaskValidationError("read1 and read2 must reference different uploads")
            read1 = self._resolve_upload(entry.read1_upload_id)
            read2 = self._resolve_upload(entry.read2_upload_id)
            rows.append(
                {"sample_id": entry.sample_id, "read1": str(read1), "read2": str(read2)}
            )

        manifest_dir = self.settings.input_root / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest = manifest_dir / f"{uuid.uuid4().hex}.csv"
        temporary = manifest.with_suffix(".csv.tmp")
        reads_dir = manifest.with_suffix(".reads")
        reads_dir.mkdir(exist_ok=False)
        try:
            # A symlink would resolve back to reads.fastq during workflow validation.
            # Hard links preserve mate names without copying large uploaded datasets.
            for row in rows:
                for mate in (1, 2):
                    key = f"read{mate}"
                    source = Path(row[key])
                    suffix = next((item for item in FASTQ_SUFFIXES
                                   if source.name.lower().endswith(item)), None)
                    if suffix is None:
                        raise TaskValidationError("uploaded file has an unsupported FASTQ suffix")
                    destination = reads_dir / f"{row['sample_id']}_R{mate}{suffix}"
                    os.link(source, destination)
                    row[key] = str(destination.resolve())
            with temporary.open("x", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["sample_id", "read1", "read2"]
                )
                writer.writeheader()
                writer.writerows(rows)
            os.replace(temporary, manifest)
        except Exception as exc:
            shutil.rmtree(reads_dir)
            if isinstance(exc, OSError):
                raise TaskValidationError(
                    "unable to prepare paired FASTQ files; check storage permissions, "
                    "space and hard-link support for uploads and manifests"
                ) from exc
            raise
        finally:
            temporary.unlink(missing_ok=True)
        return manifest.resolve(), len(rows)

    def _resolve_upload(self, upload_id: str) -> Path:
        row = self.repository.get_upload(upload_id)
        if row is None or row.get("status") != "validated":
            raise TaskValidationError(f"upload is not available: {upload_id}")
        stored = Path(row["stored_path"])
        if stored.is_symlink():
            raise TaskValidationError(f"upload is not available: {upload_id}")
        try:
            resolved = stored.resolve(strict=True)
            resolved.relative_to(self.settings.input_root.resolve(strict=True))
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise TaskValidationError(f"upload is not available: {upload_id}") from exc
        if not resolved.is_file():
            raise TaskValidationError(f"upload is not available: {upload_id}")
        return resolved


def _safe_original_name(value: str | None) -> str:
    name = (value or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or len(name) > 255 or "\x00" in name:
        raise TaskValidationError("uploaded filename is invalid")
    return name


def _validate_first_fastq_record(path: Path, *, compressed: bool) -> None:
    try:
        opener = gzip.open if compressed else open
        with opener(path, "rb") as handle:
            record = [handle.readline().rstrip(b"\r\n") for _ in range(4)]
    except (EOFError, OSError, gzip.BadGzipFile) as exc:
        raise TaskValidationError("uploaded file is not a readable FASTQ") from exc
    if (
        len(record) != 4
        or not record[0].startswith(b"@")
        or not record[2].startswith(b"+")
        or not record[1]
        or len(record[1]) != len(record[3])
    ):
        raise TaskValidationError("uploaded file does not contain a valid FASTQ record")
