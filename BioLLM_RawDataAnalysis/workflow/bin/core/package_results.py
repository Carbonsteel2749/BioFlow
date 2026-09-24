#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TOOLS = (
    ("nextflow", ("nextflow", "-version")),
    ("fastqc", ("fastqc", "--version")),
    ("fastp", ("fastp", "--version")),
    ("bowtie2", ("bowtie2", "--version")),
    ("samtools", ("samtools", "--version")),
    ("kraken2", ("kraken2", "--version")),
    ("bracken", ("bracken", "-v")),
    ("humann", ("humann", "--version")),
    ("metaphlan", ("metaphlan", "--version")),
    ("multiqc", ("multiqc", "--version")),
)
EXCLUDED_SCAN_DIRS = {
    ".nextflow",
    "input",
    "report",
    "tables",
    "provenance",
    "deliverables",
    "logs",
    "status",
    "validate",
    "work",
}
TEXT_ARTIFACT_NAME = re.compile(
    r".*\.(?:csv|html|json|md|tsv|txt|yaml|yml)(?:\.\d+)?$",
    re.IGNORECASE,
)
MULTIQC_UNSAFE_SUFFIXES = {".log", ".parquet"}
EXCLUDED_SCAN_FILES = {
    # Nextflow stages the already-consumed database manifest into REPORT as a
    # symlink. It is provenance input, not an analysis artifact to package.
    "database.resolved.json",
    "report-database.resolved.json",
}
MAX_PACKAGED_LOG_BYTES = 8 * 1024 * 1024
TRUNCATED_LOG_NOTICE = "[... earlier log content omitted ...]\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--tables-dir", type=Path, required=True)
    parser.add_argument("--provenance-dir", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--nextflow-work-root", type=Path, required=True)
    parser.add_argument("--input-manifest", type=Path, required=True)
    parser.add_argument("--database-manifest", type=Path, required=True)
    parser.add_argument("--exclude-artifact", action="append", type=Path, default=[])
    parser.add_argument("--parameters-base64", required=True)
    parser.add_argument("--run-name", default="unknown")
    parser.add_argument("--project-root", type=Path, required=True)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_component(value: str) -> str:
    rendered = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return rendered.strip("._")[:100] or "unknown"


def redact_known_paths(
    text: str,
    replacements: Iterable[tuple[str, str]],
) -> str:
    redacted = text
    for original, replacement in sorted(
        replacements,
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if original:
            redacted = redacted.replace(original, replacement)
    return redacted


def copy_sanitized_artifact(
    source: Path,
    destination: Path,
    path_replacements: Iterable[tuple[str, str]],
) -> None:
    if TEXT_ARTIFACT_NAME.fullmatch(source.name):
        text = source.read_text(encoding="utf-8", errors="replace")
        destination.write_text(
            redact_known_paths(text, path_replacements),
            encoding="utf-8",
        )
        return
    shutil.copy2(source, destination, follow_symlinks=True)


def sanitize_multiqc_outputs(
    report: Path,
    data_dir: Path,
    path_replacements: Iterable[tuple[str, str]],
) -> None:
    report.write_text(
        redact_known_paths(
            report.read_text(encoding="utf-8", errors="replace"),
            path_replacements,
        ),
        encoding="utf-8",
    )
    if not data_dir.is_dir():
        return
    reject_tree_symlinks(data_dir, "MultiQC data directory")
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in MULTIQC_UNSAFE_SUFFIXES:
            path.unlink()
            continue
        if not TEXT_ARTIFACT_NAME.fullmatch(path.name):
            path.unlink()
            continue
        path.write_text(
            redact_known_paths(
                path.read_text(encoding="utf-8", errors="replace"),
                path_replacements,
            ),
            encoding="utf-8",
        )


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["sample_id", "read1", "read2"]:
            raise ValueError("input manifest header is invalid")
        rows = [dict(row) for row in reader]
    if not rows:
        raise ValueError("input manifest does not contain samples")
    return rows


def resolve_manifest_file(manifest: Path, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = manifest.parent / candidate
    return candidate.resolve(strict=True)


def write_input_checksums(
    destination: Path,
    manifest: Path,
    rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in rows:
        for role in ("read1", "read2"):
            source = resolve_manifest_file(manifest, row[role])
            if not source.is_file() or source.is_symlink():
                raise ValueError(f"input {role} is not a regular file")
            records.append(
                {
                    "sample_id": row["sample_id"],
                    "role": role,
                    "size_bytes": source.stat().st_size,
                    "sha256": sha256_file(source),
                }
            )
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["sample_id", "role", "size_bytes", "sha256"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(records)
    return records


def lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def scan_artifacts(
    root: Path,
    allowed_symlink_root: Path,
    excluded_paths: Iterable[Path] = (),
) -> list[Path]:
    artifacts: list[Path] = []
    allowed_symlink_root = allowed_symlink_root.resolve(strict=False)
    excluded = {lexical_absolute(path) for path in excluded_paths}
    for path in root.rglob("*"):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if relative.parts and relative.parts[0] in EXCLUDED_SCAN_DIRS:
            continue
        if lexical_absolute(path) in excluded or relative.name in EXCLUDED_SCAN_FILES:
            continue
        if path.is_symlink():
            try:
                target = path.resolve(strict=True)
                target.relative_to(allowed_symlink_root)
            except (FileNotFoundError, OSError, ValueError) as exc:
                raise ValueError(
                    f"artifact symlink escapes the approved work directory: {relative}"
                ) from exc
            if target.is_dir():
                for nested in target.rglob("*"):
                    if nested.is_symlink():
                        try:
                            nested_target = nested.resolve(strict=True)
                            nested_target.relative_to(allowed_symlink_root)
                        except (FileNotFoundError, OSError, ValueError) as exc:
                            raise ValueError(
                                f"artifact directory contains an unsafe symlink: {relative}"
                            ) from exc
                        if not nested_target.is_file():
                            raise ValueError(
                                f"artifact directory symlink target is not a file: {relative}"
                            )
                    if nested.is_file():
                        artifacts.append(nested)
                continue
            if not target.is_file():
                raise ValueError(f"artifact symlink target is not a file: {relative}")
        if path.is_file():
            artifacts.append(path)
    return artifacts


def matches_name(path: Path, patterns: Iterable[str]) -> bool:
    name = path.name.lower()
    return any(re.fullmatch(pattern, name) for pattern in patterns)


def json_sample_id(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    for key in ("sample_id", "sample_id_or_mag_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def tsv_sample_id(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            row = next(reader, None)
    except (OSError, csv.Error):
        return None
    if row and row.get("sample_id", "").strip():
        return row["sample_id"].strip()
    return None


def artifact_sample_id(path: Path) -> str:
    if path.suffix.lower() == ".json":
        sample = json_sample_id(path)
        if sample:
            return sample
    if ".tsv" in path.name.lower():
        sample = tsv_sample_id(path)
        if sample:
            return sample
    for marker in (
        ".fastp.",
        ".host_depletion.",
        "_genefamilies",
        "_pathabundance",
        "_pathcoverage",
        ".kraken.",
        ".bracken.",
    ):
        if marker in path.name:
            return path.name.split(marker, 1)[0]
    return path.stem


def copy_selected_artifacts(
    artifacts: list[Path],
    package_root: Path,
    path_replacements: Iterable[tuple[str, str]],
) -> dict[str, list[Path]]:
    patterns = {
        "taxonomy_figures": (r"taxonomy_.*\.png(?:\.\d+)?", r"taxonomy_plots\.provenance\.json(?:\.\d+)?"),
        "functional_figures": (r"functional_.*\.png(?:\.\d+)?", r"functional_plots\.provenance\.json(?:\.\d+)?"),
        "mag_figures": (r"mag_.*\.png(?:\.\d+)?", r"sample_mag_abundance_heatmap\.png(?:\.\d+)?", r"mag_plots\.provenance\.json(?:\.\d+)?"),
        "qc": (
            r".*_fastqc\.(?:html|zip)(?:\.\d+)?",
            r".*\.fastp\.(?:json|html)(?:\.\d+)?",
            r".*\.host_depletion\.metrics\.json(?:\.\d+)?",
        ),
        "taxonomy": (
            r"species_abundance(?:\.\d+)?\.tsv(?:\.\d+)?",
            r".*\.bracken\.species\.tsv(?:\.\d+)?",
            r"bracken\.species\.tsv(?:\.\d+)?",
            r"taxonomy\.provenance(?:\.\d+)?\.json(?:\.\d+)?",
            r"taxonomy\.validation(?:\.\d+)?\.json(?:\.\d+)?",
        ),
        "functional": (
            r"read_genefamilies(?:\.\d+)?\.tsv(?:\.\d+)?",
            r".*_genefamilies\.tsv(?:\.\d+)?",
            r"read_pathabundance(?:\.\d+)?\.tsv(?:\.\d+)?",
            r".*_pathabundance\.tsv(?:\.\d+)?",
            r"read_pathways(?:\.\d+)?\.tsv(?:\.\d+)?",
            r"humann_raw_.*\.tsv(?:\.\d+)?",
            r"functional\.provenance(?:\.\d+)?\.json(?:\.\d+)?",
            r"functional\.validation(?:\.\d+)?\.json(?:\.\d+)?",
        ),
        "pathcoverage": (
            r"read_pathcoverage(?:\.\d+)?\.tsv(?:\.\d+)?",
            r".*_pathcoverage\.tsv(?:\.\d+)?",
        ),
        "ko": (
            r"read_ko(?:\.\d+)?\.tsv(?:\.\d+)?",
        ),
        "ec": (
            r"read_ec(?:\.\d+)?\.tsv(?:\.\d+)?",
        ),
        "mag_taxonomy": (
            r"mag_annotation(?:\.\d+)?\.tsv(?:\.\d+)?",
        ),
        "mag_function": (
            r"mag_function_annotation(?:\.\d+)?\.tsv(?:\.\d+)?",
        ),
        "mag_abundance": (
            r"sample_mag_abundance(?:\.\d+)?\.tsv(?:\.\d+)?",
        ),
        "mag": (
            r".*(?:bin|mag).*\.(?:tsv|json)(?:\.\d+)?",
        ),
    }
    selected: dict[str, list[Path]] = {key: [] for key in patterns}
    for source in artifacts:
        for group, group_patterns in patterns.items():
            if not matches_name(source, group_patterns):
                continue
            selected[group].append(source)
            if group in {
                "pathcoverage",
                "ko",
                "ec",
                "mag_taxonomy",
                "mag_function",
                "mag_abundance",
            }:
                break
            sample = safe_component(artifact_sample_id(source))
            destination_dir = package_root / "tables" / "by_sample" / sample
            if group == "qc":
                destination_dir = package_root / "qc" / sample
            elif group.endswith('_figures'):
                destination_dir = package_root / 'figures' / group.removesuffix('_figures')
                if source.stat().st_size == 0:
                    raise ValueError(f"figure artifact is empty: {safe_component(source.name)}")
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / safe_component(source.name)
            counter = 1
            while destination.exists():
                destination = destination_dir / f"{safe_component(source.name)}.{counter}"
                counter += 1
            copy_sanitized_artifact(source, destination, path_replacements)
            break
    return selected


def combine_standard_tables(
    sources: list[Path],
    destination: Path,
    *,
    required_name: str,
) -> int:
    required = Path(required_name)
    collision_pattern = re.compile(
        rf"^{re.escape(required.stem)}(?:\.\d+)?"
        rf"{re.escape(required.suffix)}(?:\.\d+)?$",
        re.IGNORECASE,
    )
    matched = [
        path
        for path in sources
        if collision_pattern.fullmatch(path.name) and tsv_sample_id(path)
    ]
    header: list[str] | None = None
    rows: list[dict[str, str]] = []
    for path in matched:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if not reader.fieldnames:
                continue
            if header is None:
                header = list(reader.fieldnames)
            if list(reader.fieldnames) != header:
                continue
            rows.extend(dict(row) for row in reader)
    if header is not None:
        with destination.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=header, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
        return len(rows)
    return 0


def collision_named_files(sources: Iterable[Path], required_name: str) -> list[Path]:
    required = Path(required_name)
    collision_pattern = re.compile(
        rf"^{re.escape(required.stem)}(?:\.\d+)?"
        rf"{re.escape(required.suffix)}(?:\.\d+)?$",
        re.IGNORECASE,
    )
    return sorted(
        path for path in sources if collision_pattern.fullmatch(path.name)
    )


def copy_optional_artifact_set(
    sources: list[Path],
    destination: Path,
    path_replacements: Iterable[tuple[str, str]],
) -> int:
    matched = sorted(sources)
    if not matched:
        return 0
    destination.mkdir(parents=True, exist_ok=True)
    for source in matched:
        if source.stat().st_size == 0:
            raise ValueError(
                f"optional result artifact is empty: {safe_component(source.name)}"
            )
        target = destination / safe_component(source.name)
        counter = 1
        while target.exists():
            target = destination / f"{safe_component(source.name)}.{counter}"
            counter += 1
        copy_sanitized_artifact(source, target, path_replacements)
    return len(matched)


def combine_optional_standard_tables(
    sources: list[Path],
    destination: Path,
    *,
    required_name: str,
    required_fields: Iterable[str],
) -> int:
    header: list[str] | None = None
    rows: list[dict[str, str]] = []
    expected = set(required_fields)
    for path in collision_named_files(sources, required_name):
        safe_name = safe_component(path.name)
        try:
            with path.open(
                "r",
                encoding="utf-8",
                errors="replace",
                newline="",
            ) as handle:
                reader = csv.DictReader(handle, delimiter="\t")
                fields = list(reader.fieldnames or [])
                if (
                    not fields
                    or len(fields) != len(set(fields))
                    or not expected.issubset(fields)
                ):
                    raise ValueError(
                        f"optional result table has invalid required columns: {safe_name}"
                    )
                if header is None:
                    header = fields
                if fields != header:
                    raise ValueError(
                        f"optional result table headers do not match: {safe_name}"
                    )
                for row in reader:
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError(
                            f"optional result table has an invalid row: {safe_name}"
                        )
                    rows.append(dict(row))
        except (OSError, csv.Error) as exc:
            raise ValueError(
                f"unable to read optional result table: {safe_name}"
            ) from exc
    if header is None:
        return 0
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def copy_legacy_table(
    sources: list[Path],
    destination: Path,
    patterns: tuple[str, ...],
    path_replacements: Iterable[tuple[str, str]],
) -> bool:
    for source in sources:
        if matches_name(source, patterns):
            copy_sanitized_artifact(source, destination, path_replacements)
            return True
    return False


def write_read_counts(artifacts: list[Path], destination: Path) -> int:
    samples: dict[str, dict[str, Any]] = {}
    for path in artifacts:
        name = path.name.lower()
        if re.fullmatch(r".*\.fastp\.json(?:\.\d+)?", name):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                sample = artifact_sample_id(path)
                record = samples.setdefault(sample, {"sample_id": sample})
                record["raw_reads"] = payload["summary"]["before_filtering"]["total_reads"]
                record["post_fastp_reads"] = payload["summary"]["after_filtering"]["total_reads"]
            except (OSError, ValueError, TypeError, KeyError):
                continue
        elif re.fullmatch(r".*\.host_depletion\.metrics\.json(?:\.\d+)?", name):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                sample = str(payload["sample_id"])
                record = samples.setdefault(sample, {"sample_id": sample})
                record["post_fastp_pairs"] = payload.get("input_pair_count", "")
                record["post_host_pairs"] = payload.get("retained_pair_count", "")
                record["host_removed_pairs"] = payload.get("removed_pair_count", "")
            except (OSError, ValueError, TypeError, KeyError):
                continue
    fields = [
        "sample_id",
        "raw_reads",
        "post_fastp_reads",
        "post_fastp_pairs",
        "post_host_pairs",
        "host_removed_pairs",
    ]
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for sample in sorted(samples):
            writer.writerow(samples[sample])
    return len(samples)


def count_tsv_data_rows(path: Path) -> int:
    with path.open(
        "r",
        encoding="utf-8",
        errors="replace",
        newline="",
    ) as handle:
        reader = csv.reader(handle, delimiter="\t")
        next(reader, None)
        return sum(1 for row in reader if row and any(cell.strip() for cell in row))


def database_records(payload: dict[str, Any]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []

    def visit(section: str, value: Any) -> None:
        if isinstance(value, dict):
            if "database_name" in value or "release" in value:
                records.append(
                    {
                        "section": section,
                        "database_name": str(value.get("database_name", "")),
                        "release": str(value.get("release", "")),
                        "taxonomy_system": str(value.get("taxonomy_system", "")),
                        "manifest_sha256": str(
                            value.get("manifest_sha256")
                            or value.get("database_manifest_sha256")
                            or ""
                        ),
                    }
                )
            else:
                for key, child in value.items():
                    visit(f"{section}.{key}".strip("."), child)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(f"{section}[{index}]", child)

    visit("", payload.get("databases", {}))
    return records


def write_database_versions(source: Path, destination: Path) -> str:
    payload = json.loads(source.read_text(encoding="utf-8"))
    records = database_records(payload)
    fields = [
        "section",
        "database_name",
        "release",
        "taxonomy_system",
        "manifest_sha256",
    ]
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(records)
    return str(payload.get("resolved_manifest_sha256") or sha256_file(source))


def write_software_versions(
    destination: Path,
    redact: Callable[..., str],
) -> None:
    rows: list[dict[str, str]] = []
    for name, command in TOOLS:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            rendered = (completed.stdout or completed.stderr).strip().splitlines()
            version = rendered[0] if rendered else f"exit_code={completed.returncode}"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            version = "not available"
        rows.append({"tool": name, "version": redact(version)})
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["tool", "version"], delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def source_revision(project_root: Path, destination: Path) -> str:
    candidates: list[Path] = []
    workflow_root = project_root / "workflow"
    for pattern in ("main.nf", "modules/*.nf", "bin/**/*.sh", "bin/**/*.py"):
        candidates.extend(workflow_root.glob(pattern))
    rows: list[tuple[str, str]] = []
    for path in sorted({path.resolve() for path in candidates if path.is_file()}):
        rows.append((str(path.relative_to(project_root)), sha256_file(path)))
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["source_file", "sha256"])
        writer.writerows(rows)
    digest = hashlib.sha256()
    for name, checksum in rows:
        digest.update(f"{name}\t{checksum}\n".encode())
    return digest.hexdigest()


def sanitized_parameters(
    encoded: str,
    redact: Callable[..., str],
    sample_ids: list[str],
) -> dict[str, Any]:
    raw = base64.b64decode(encoded, validate=True).decode("utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("workflow parameters must be a JSON object")

    def sanitize(value: Any) -> Any:
        if isinstance(value, str):
            return redact(value, sample_identifiers=sample_ids)
        if isinstance(value, dict):
            return {str(key): sanitize(child) for key, child in value.items()}
        if isinstance(value, list):
            return [sanitize(child) for child in value]
        return value

    return sanitize(payload)


def read_bounded_log(path: Path, max_bytes: int = MAX_PACKAGED_LOG_BYTES) -> str:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        offset = max(0, size - max_bytes)
        handle.seek(offset)
        payload = handle.read(max_bytes)
    text = payload.decode("utf-8", errors="replace")
    if offset:
        first_newline = text.find("\n")
        if first_newline < 0:
            return TRUNCATED_LOG_NOTICE
        return TRUNCATED_LOG_NOTICE + text[first_newline + 1 :]
    return text


def copy_redacted_logs(
    state_root: Path,
    destination: Path,
    redact: Callable[..., str],
    sample_ids: list[str],
    input_root: str,
) -> int:
    log_root = state_root / "logs"
    if not log_root.is_dir():
        return 0
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    for source in sorted(log_root.glob("*.log")):
        if not source.is_file():
            continue
        if source.is_symlink():
            raise ValueError(f"log file must not be a symlink: {source.name}")
        text = read_bounded_log(source)
        safe = redact(
            text,
            input_root=input_root,
            sample_identifiers=sample_ids,
        )
        safe_name = source.name
        for index, sample_id in enumerate(
            sorted(set(sample_ids), key=len, reverse=True),
            start=1,
        ):
            safe_name = re.sub(
                re.escape(sample_id),
                f"SAMPLE_{index:03d}",
                safe_name,
                flags=re.IGNORECASE,
            )
        (destination / safe_component(safe_name)).write_text(
            safe,
            encoding="utf-8",
        )
        count += 1
    return count


def artifact_manifest(package_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(package_root.rglob("*")):
        if not path.is_file() or path.name in {"SHA256SUMS", "manifest.json"}:
            continue
        records.append(
            {
                "path": path.relative_to(package_root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return records


def verify_artifact_manifest(
    package_root: Path,
    records: Iterable[dict[str, Any]],
) -> None:
    for record in records:
        relative = Path(str(record["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("artifact manifest contains an unsafe path")
        path = package_root / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"manifest artifact is not a regular file: {relative}")
        if path.stat().st_size != int(record["size_bytes"]):
            raise ValueError(f"manifest artifact size changed: {relative}")
        if sha256_file(path) != str(record["sha256"]):
            raise ValueError(f"manifest artifact checksum changed: {relative}")


def reject_tree_symlinks(root: Path, label: str) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(
                f"{label} must not contain symlinks: {path.relative_to(root)}"
            )


def write_archive(package_root: Path, archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{archive.name}.",
        suffix=".tmp",
        dir=archive.parent,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        with tarfile.open(temporary, "w:gz", format=tarfile.PAX_FORMAT) as tar:
            for path in sorted(package_root.rglob("*")):
                if path.is_file():
                    tar.add(
                        path,
                        arcname=path.relative_to(package_root).as_posix(),
                        recursive=False,
                    )
        if temporary.stat().st_size == 0:
            raise ValueError("result archive is empty")
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    if not SAFE_TASK_ID.fullmatch(args.task_id) or ".." in args.task_id:
        raise ValueError("task ID contains unsafe characters")
    project_root = args.project_root.resolve(strict=True)
    sys.path.insert(0, str(project_root))
    from backend.app.services.redaction import redact_log

    results_root = args.results_root.resolve(strict=True)
    report_dir = args.report_dir.resolve(strict=True)
    state_root = args.state_root.resolve(strict=True)
    nextflow_work_root = args.nextflow_work_root.resolve(strict=True)
    input_manifest = args.input_manifest.resolve(strict=True)
    database_manifest = args.database_manifest.resolve(strict=True)
    tables_dir = args.tables_dir.resolve()
    provenance_dir = args.provenance_dir.resolve()
    archive = args.archive.resolve()
    for directory in (tables_dir, provenance_dir, archive.parent):
        directory.mkdir(parents=True, exist_ok=True)
    multiqc_report = report_dir / "multiqc_report.html"
    if (
        not multiqc_report.is_file()
        or multiqc_report.is_symlink()
        or multiqc_report.stat().st_size == 0
    ):
        raise ValueError("MultiQC report is missing or empty")

    manifest_rows = read_manifest(input_manifest)
    sample_ids = [row["sample_id"] for row in manifest_rows]
    path_replacements = (
        (str(input_manifest.parent.resolve()), "[INPUT_ROOT]"),
        (str(nextflow_work_root), "[NEXTFLOW_WORK]"),
        (str(state_root), "[STATE_ROOT]"),
        (str(project_root), "[PROJECT_ROOT]"),
        (str(Path.home().resolve()), "[HOME]"),
    )
    multiqc_data = report_dir / "multiqc_report_data"
    sanitize_multiqc_outputs(
        multiqc_report,
        multiqc_data,
        path_replacements,
    )
    parameters = sanitized_parameters(
        args.parameters_base64,
        redact_log,
        sample_ids,
    )
    (provenance_dir / "parameters.json").write_text(
        json.dumps(parameters, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    input_records = write_input_checksums(
        provenance_dir / "input_checksums.tsv",
        input_manifest,
        manifest_rows,
    )
    database_sha = write_database_versions(
        database_manifest,
        provenance_dir / "database_versions.tsv",
    )
    write_software_versions(
        provenance_dir / "software_versions.tsv",
        redact_log,
    )
    workflow_revision = source_revision(
        project_root,
        provenance_dir / "source_checksums.tsv",
    )

    artifacts = scan_artifacts(
        results_root,
        nextflow_work_root,
        args.exclude_artifact,
    )
    with tempfile.TemporaryDirectory(prefix="biollm-package-") as temporary:
        package_root = Path(temporary) / "package"
        (package_root / "reports").mkdir(parents=True)
        shutil.copy2(multiqc_report, package_root / "reports" / multiqc_report.name)
        if multiqc_data.is_dir():
            reject_tree_symlinks(multiqc_data, "MultiQC data directory")
            shutil.copytree(
                multiqc_data,
                package_root / "reports" / multiqc_data.name,
                symlinks=False,
            )
        (package_root / "tables").mkdir(parents=True)
        selected = copy_selected_artifacts(
            artifacts,
            package_root,
            path_replacements,
        )

        read_count_rows = write_read_counts(
            artifacts,
            package_root / "tables" / "read_counts.tsv",
        )
        species_rows = combine_standard_tables(
            selected["taxonomy"],
            package_root / "tables" / "species_abundance.tsv",
            required_name="species_abundance.tsv",
        )
        if species_rows == 0:
            copy_legacy_table(
                selected["taxonomy"],
                package_root / "tables" / "species_abundance.tsv",
                (
                    r".*\.bracken\.species\.tsv(?:\.\d+)?",
                    r"bracken\.species\.tsv(?:\.\d+)?",
                ),
                path_replacements,
            )
        gene_rows = combine_standard_tables(
            selected["functional"],
            package_root / "tables" / "gene_families.tsv",
            required_name="read_genefamilies.tsv",
        )
        if gene_rows == 0:
            copy_legacy_table(
                selected["functional"],
                package_root / "tables" / "gene_families.tsv",
                (r".*_genefamilies\.tsv(?:\.\d+)?",),
                path_replacements,
            )
        pathway_rows = combine_standard_tables(
            selected["functional"],
            package_root / "tables" / "pathway_abundance.tsv",
            required_name="read_pathabundance.tsv",
        )
        if pathway_rows == 0:
            copy_legacy_table(
                selected["functional"],
                package_root / "tables" / "pathway_abundance.tsv",
                (r".*_pathabundance\.tsv(?:\.\d+)?",),
                path_replacements,
            )
        pathcoverage_files = copy_optional_artifact_set(
            selected["pathcoverage"],
            package_root / "tables" / "pathway_coverage",
            path_replacements,
        )
        ko_files = copy_optional_artifact_set(
            selected["ko"],
            package_root / "tables" / "ko_abundance",
            path_replacements,
        )
        ec_files = copy_optional_artifact_set(
            selected["ec"],
            package_root / "tables" / "ec_abundance",
            path_replacements,
        )
        mag_taxonomy_rows = combine_optional_standard_tables(
            selected["mag_taxonomy"],
            package_root / "tables" / "mag_annotation.tsv",
            required_name="mag_annotation.tsv",
            required_fields=("mag_id", "taxonomy"),
        )
        mag_function_rows = combine_optional_standard_tables(
            selected["mag_function"],
            package_root / "tables" / "mag_function_annotation.tsv",
            required_name="mag_function_annotation.tsv",
            required_fields=("mag_id", "function_id"),
        )
        sample_mag_rows = combine_optional_standard_tables(
            selected["mag_abundance"],
            package_root / "tables" / "sample_mag_abundance.tsv",
            required_name="sample_mag_abundance.tsv",
            required_fields=("sample_id", "mag_id", "abundance"),
        )
        required_tables = (
            package_root / "tables" / "read_counts.tsv",
            package_root / "tables" / "species_abundance.tsv",
            package_root / "tables" / "gene_families.tsv",
            package_root / "tables" / "pathway_abundance.tsv",
        )
        if read_count_rows == 0:
            raise ValueError("no fastp or host-depletion read counts were found")
        for required_table in required_tables:
            if not required_table.is_file() or required_table.stat().st_size == 0:
                raise ValueError(f"required result table is missing: {required_table.name}")
        read_count_rows = count_tsv_data_rows(required_tables[0])
        species_rows = count_tsv_data_rows(required_tables[1])
        gene_rows = count_tsv_data_rows(required_tables[2])
        pathway_rows = count_tsv_data_rows(required_tables[3])
        qc_files = len(selected["qc"])
        figure_files = sum(
            bool(re.search(r'\.png(?:\.\d+)?$', source.name, re.IGNORECASE))
            for group in ('taxonomy_figures', 'functional_figures', 'mag_figures')
            for source in selected[group]
        )
        shutil.copytree(
            package_root / "tables",
            tables_dir,
            symlinks=False,
            dirs_exist_ok=True,
        )

        shutil.copytree(
            provenance_dir,
            package_root / "provenance",
            symlinks=False,
            dirs_exist_ok=True,
        )
        log_count = copy_redacted_logs(
            state_root,
            package_root / "logs",
            redact_log,
            sample_ids,
            str(input_manifest.parent.resolve()),
        )
        summary_payload = {
            "schema_version": 1,
            "task_id": args.task_id,
            "sample_count": len(manifest_rows),
            "entrypoints": {
                "multiqc_report": "reports/multiqc_report.html",
                "summary": "reports/summary.txt",
                "artifact_manifest": "manifest.json",
                "checksums": "SHA256SUMS",
            },
            "counts": {
                "read_count_rows": read_count_rows,
                "species_rows": species_rows,
                "gene_family_rows": gene_rows,
                "pathway_rows": pathway_rows,
                "pathway_coverage_files": pathcoverage_files,
                "ko_abundance_files": ko_files,
                "ec_abundance_files": ec_files,
                "qc_files": qc_files,
                "figure_files": figure_files,
                "redacted_log_files": log_count,
            },
        }
        summary = (
            "BioLLM metagenomics V1 result package\n"
            "Research use only; this file does not contain model-generated scientific conclusions.\n"
            "MultiQC report entry: reports/multiqc_report.html\n"
            "Verify package files: sha256sum -c SHA256SUMS\n"
            f"Task: {args.task_id}\n"
            f"Samples: {len(manifest_rows)}\n"
            f"Read-count records: {read_count_rows}\n"
            f"Species rows: {species_rows}\n"
            f"Gene-family rows: {gene_rows}\n"
            f"Pathway rows: {pathway_rows}\n"
            f"Pathway-coverage files: {pathcoverage_files}\n"
            f"KO abundance files: {ko_files}\n"
            f"EC abundance files: {ec_files}\n"
            f"QC files: {qc_files}\n"
            f"Figure files: {figure_files}\n"
            f"MAG taxonomy rows: {mag_taxonomy_rows}\n"
            f"MAG function rows: {mag_function_rows}\n"
            f"Sample-MAG abundance rows: {sample_mag_rows}\n"
            f"Redacted log files: {log_count}\n"
        )
        (package_root / "reports" / "summary.txt").write_text(
            summary,
            encoding="utf-8",
        )
        (report_dir / "summary.txt").write_text(summary, encoding="utf-8")
        summary_json = (
            json.dumps(summary_payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )
        (package_root / "reports" / "summary.json").write_text(
            summary_json,
            encoding="utf-8",
        )
        (report_dir / "summary.json").write_text(summary_json, encoding="utf-8")
        readme = (
            "BioLLM result package\n\n"
            "1. Open reports/multiqc_report.html for the interactive QC report.\n"
            "2. Review reports/summary.txt and reports/summary.json for package counts.\n"
            "3. Tables contain species, gene-family, KO, EC and pathway outputs when generated.\n"
            "4. QC artifacts are under qc/, provenance under provenance/, and redacted logs under logs/.\n"
            "5. From the package root, run: sha256sum -c SHA256SUMS\n"
            "6. Generated plots and their provenance are under figures/taxonomy, figures/functional and figures/mag when available.\n"
        )
        (package_root / "README.txt").write_text(readme, encoding="utf-8")
        (report_dir / "README.txt").write_text(readme, encoding="utf-8")
        run_payload = {
            "task_id": args.task_id,
            "nextflow_run_name": args.run_name,
            "workflow_revision_sha256": workflow_revision,
            "database_manifest_sha256": database_sha,
            "input_file_count": len(input_records),
            "sample_count": len(manifest_rows),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "final_status": "completed",
        }
        (package_root / "provenance" / "run.json").write_text(
            json.dumps(run_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        shutil.copy2(
            package_root / "provenance" / "run.json",
            provenance_dir / "run.json",
        )
        records = artifact_manifest(package_root)
        (package_root / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "task_id": args.task_id,
                    "checksum_algorithm": "sha256",
                    "checksum_file": "SHA256SUMS",
                    "artifacts": records,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        with (package_root / "SHA256SUMS").open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(f"{record['sha256']}  {record['path']}\n")
        verify_artifact_manifest(package_root, records)
        write_archive(package_root, archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
