#!/usr/bin/env python3
"""Stream and validate paired-end FASTQ records referenced by a manifest."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, TextIO


VALID_SUFFIXES = (".fastq", ".fq", ".fastq.gz", ".fq.gz")
SAMPLE_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]+\Z")
MATE_FILENAME_PATTERN = re.compile(
    r"^(?P<prefix>.*?)(?P<token>_R|\.R|_)(?P<mate>[12])"
    r"(?P<extension>\.(?:fastq|fq)(?:\.gz)?)$",
    flags=re.IGNORECASE,
)


@dataclass
class ValidationError(Exception):
    stage: str
    reason: str
    sample_id: str = "global"
    line_number: int | None = None
    exit_code: int = 65


@dataclass
class FastqRecord:
    core_id: str
    mate: str


def write_json_atomically(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def normalize_path(raw_path: str, manifest_directory: Path, key: str, sample_id: str, line_number: int) -> Path:
    value = raw_path.strip()
    if not value:
        raise ValidationError("manifest_file", f"{key} path is empty", sample_id, line_number)
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = manifest_directory / candidate
    path = candidate.resolve()
    if not path.is_file():
        raise ValidationError("manifest_file", f"{key} file does not exist: {path}", sample_id, line_number, 66)
    if path.stat().st_size == 0:
        raise ValidationError("fastq_readable", f"{key} file is empty: {path}", sample_id, line_number)
    try:
        with path.open("rb") as handle:
            handle.read(1)
    except OSError as error:
        raise ValidationError("fastq_readable", f"{key} file is not readable: {path}: {error}", sample_id, line_number, 66) from error
    if not path.name.lower().endswith(VALID_SUFFIXES):
        raise ValidationError("fastq_suffix", f"unsupported FASTQ extension for {key}: {path}", sample_id, line_number)
    return path


def filename_mate(path: Path, key: str, sample_id: str, line_number: int) -> tuple[str, str, str]:
    match = MATE_FILENAME_PATTERN.fullmatch(path.name)
    if not match:
        raise ValidationError(
            "fastq_pairing",
            f"{key} filename does not use a supported mate suffix: {path.name}",
            sample_id,
            line_number,
        )
    return match.group("prefix"), match.group("token").lower(), match.group("mate")


def validate_filename_pair(r1: Path, r2: Path, sample_id: str, line_number: int) -> None:
    prefix1, token1, mate1 = filename_mate(r1, "read1", sample_id, line_number)
    prefix2, token2, mate2 = filename_mate(r2, "read2", sample_id, line_number)
    if prefix1 != prefix2 or token1 != token2 or mate1 != "1" or mate2 != "2":
        raise ValidationError(
            "fastq_pairing",
            f"R1/R2 filename mate tokens do not match: {r1.name} vs {r2.name}",
            sample_id,
            line_number,
        )


def open_fastq(path: Path) -> TextIO:
    if path.name.lower().endswith(".gz"):
        return gzip.open(path, mode="rt", encoding="utf-8", newline="")
    return path.open(mode="rt", encoding="utf-8", newline="")


def parse_header(header: str, mate_file: str, sample_id: str, record_number: int) -> FastqRecord:
    if not header.startswith("@"):
        raise ValidationError("fastq_format", f"record {record_number}: header does not start with @", sample_id)
    value = header[1:]
    if not value:
        raise ValidationError("fastq_format", f"record {record_number}: empty read identifier", sample_id)
    parts = value.split(maxsplit=1)
    read_id = parts[0]
    mate = ""
    if read_id.endswith("/1") or read_id.endswith("/2"):
        mate = read_id[-1]
        read_id = read_id[:-2]
    elif len(parts) == 2 and (parts[1].endswith("/1") or parts[1].endswith("/2")):
        mate = parts[1][-1]
    elif len(parts) == 2 and len(parts[1]) >= 2 and parts[1][0] in "12" and parts[1][1] == ":":
        mate = parts[1][0]
    if not mate:
        raise ValidationError(
            "fastq_pairing",
            f"record {record_number}: read header has no /1,/2 or Illumina mate marker",
            sample_id,
        )
    if mate != mate_file:
        raise ValidationError(
            "fastq_pairing",
            f"record {record_number}: expected mate {mate_file}, found mate {mate}",
            sample_id,
        )
    return FastqRecord(core_id=read_id, mate=mate)


def read_record(handle: TextIO, mate_file: str, sample_id: str, record_number: int) -> FastqRecord | None:
    lines = [handle.readline() for _ in range(4)]
    if all(line == "" for line in lines):
        return None
    if any(line == "" for line in lines):
        raise ValidationError("fastq_format", f"record {record_number}: incomplete FASTQ record", sample_id)
    header, sequence, separator, quality = (line.rstrip("\r\n") for line in lines)
    record = parse_header(header, mate_file, sample_id, record_number)
    if not separator.startswith("+"):
        raise ValidationError("fastq_format", f"record {record_number}: separator does not start with +", sample_id)
    if not sequence:
        raise ValidationError("fastq_format", f"record {record_number}: sequence is empty", sample_id)
    if len(sequence) != len(quality):
        raise ValidationError(
            "fastq_format",
            f"record {record_number}: sequence length {len(sequence)} differs from quality length {len(quality)}",
            sample_id,
        )
    return record


def validate_fastq_pair(r1: Path, r2: Path, sample_id: str) -> tuple[int, int]:
    r1_count = 0
    r2_count = 0
    try:
        with open_fastq(r1) as r1_handle, open_fastq(r2) as r2_handle:
            record_number = 0
            while True:
                record_number += 1
                r1_record = read_record(r1_handle, "1", sample_id, record_number)
                r2_record = read_record(r2_handle, "2", sample_id, record_number)
                if r1_record is None and r2_record is None:
                    break
                if r1_record is None or r2_record is None:
                    raise ValidationError(
                        "fastq_count",
                        f"R1/R2 record count differs at pair {record_number}",
                        sample_id,
                    )
                r1_count += 1
                r2_count += 1
                if r1_record.core_id != r2_record.core_id:
                    raise ValidationError(
                        "fastq_pairing",
                        f"record {record_number}: R1/R2 read identifiers differ ({r1_record.core_id} vs {r2_record.core_id})",
                        sample_id,
                    )
    except (gzip.BadGzipFile, EOFError, OSError, UnicodeDecodeError) as error:
        raise ValidationError("fastq_gzip", f"unable to read FASTQ pair: {error}", sample_id, exit_code=65) from error
    if r1_count == 0:
        raise ValidationError("fastq_format", "FASTQ pair contains no records", sample_id)
    return r1_count, r2_count


def validate_manifest(manifest: Path, destination: Path) -> list[dict]:
    if not manifest.is_file():
        raise ValidationError("manifest_file", f"manifest file does not exist: {manifest}", exit_code=66)
    manifest = manifest.resolve()
    rows: list[dict] = []
    seen_samples: set[str] = set()
    summaries: list[dict] = []
    try:
        with manifest.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != ["sample_id", "read1", "read2"]:
                raise ValidationError("manifest_header", "manifest header must be exactly sample_id,read1,read2")
            for line_number, row in enumerate(reader, start=2):
                if row is None or None in row or set(row) != {"sample_id", "read1", "read2"}:
                    raise ValidationError("manifest_header", "manifest row does not have exactly three columns", "global", line_number)
                sample_id = row["sample_id"].strip()
                if not SAMPLE_ID_PATTERN.fullmatch(sample_id):
                    raise ValidationError("manifest_sample_id", f"invalid sample_id: {sample_id!r}", sample_id or "global", line_number)
                if sample_id in seen_samples:
                    raise ValidationError("manifest_sample_id", f"duplicate sample_id: {sample_id}", sample_id, line_number)
                seen_samples.add(sample_id)
                r1 = normalize_path(row["read1"], manifest.parent, "read1", sample_id, line_number)
                r2 = normalize_path(row["read2"], manifest.parent, "read2", sample_id, line_number)
                if r1.name.lower().endswith(".gz") != r2.name.lower().endswith(".gz"):
                    raise ValidationError("fastq_suffix", "R1 and R2 compression formats differ", sample_id, line_number)
                validate_filename_pair(r1, r2, sample_id, line_number)
                r1_reads, r2_reads = validate_fastq_pair(r1, r2, sample_id)
                rows.append({"sample_id": sample_id, "read1": str(r1), "read2": str(r2)})
                summaries.append({"sample_id": sample_id, "r1_reads": r1_reads, "r2_reads": r2_reads})
    except UnicodeDecodeError as error:
        raise ValidationError("manifest_header", f"manifest is not valid UTF-8 text: {error}") from error
    except csv.Error as error:
        raise ValidationError("manifest_header", f"invalid CSV manifest: {error}") from error
    if not rows:
        raise ValidationError("manifest_header", "manifest contains no samples")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["sample_id", "read1", "read2"])
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return summaries


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--diagnostic", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summaries = validate_manifest(args.manifest, args.output)
    except ValidationError as error:
        write_json_atomically(
            args.diagnostic,
            {
                "status": "failed",
                "stage": error.stage,
                "sample_id": error.sample_id,
                "line_number": error.line_number,
                "reason": error.reason,
            },
        )
        return error.exit_code
    except Exception as error:  # Diagnostics must survive unexpected implementation errors.
        write_json_atomically(
            args.diagnostic,
            {
                "status": "failed",
                "stage": "validation_runtime",
                "sample_id": "global",
                "line_number": None,
                "reason": f"unexpected validator error: {error}",
            },
        )
        return 70
    write_json_atomically(
        args.diagnostic,
        {
            "status": "succeeded",
            "stage": "complete",
            "sample_id": "global",
            "samples": summaries,
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
