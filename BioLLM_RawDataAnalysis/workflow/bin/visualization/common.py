"""Shared input validation and deterministic data handling for QC figures.

The visualization entry point accepts the normalized QC contract in TSV or JSON form.
The contract deliberately keeps sample order in the data itself: an optional
sample_order column is honored, otherwise source row order is retained.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "1.0"
SAMPLE_ID = "sample_id"
SAMPLE_ORDER = "sample_order"
COUNT_COLUMNS = ("raw_reads", "fastp_reads", "host_removed_reads")
KNOWN_RATE_COLUMNS = {
    "q20_rate",
    "q30_rate",
    "gc_content",
    "duplication_rate",
    "fastp_retention_rate",
    "host_retention_rate",
    "host_removed_rate",
}
_SAFE_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_MISSING = object()


class QCValidationError(ValueError):
    """Raised when a standardized QC table violates the input contract."""


@dataclass(frozen=True)
class QCData:
    """Validated QC records and source metadata."""

    records: list[dict[str, Any]]
    columns: list[str]
    source_path: Path
    source_format: str
    source_sha256: str

    @property
    def sample_ids(self) -> list[str]:
        return [str(record[SAMPLE_ID]) for record in self.records]

    @property
    def present_columns(self) -> list[str]:
        return [
            column
            for column in self.columns
            if any(record.get(column, _MISSING) is not _MISSING for record in self.records)
        ]

    @property
    def optional_numeric_columns(self) -> list[str]:
        excluded = {SAMPLE_ID, SAMPLE_ORDER, *COUNT_COLUMNS}
        return [column for column in self.present_columns if column not in excluded]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _read_json(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise QCValidationError(f"cannot read JSON input {path.name}: {error}") from error

    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = None
        for key in ("records", "rows", "data"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
        if rows is None:
            raise QCValidationError(
                "JSON input must be a list of records or an object containing records, rows, or data"
            )
    else:
        raise QCValidationError("JSON input must contain an array of record objects")

    if not rows:
        raise QCValidationError("input contains no QC records")
    if not all(isinstance(row, dict) for row in rows):
        raise QCValidationError("every JSON QC record must be an object")

    columns: list[str] = []
    for row in rows:
        for column in row:
            if column not in columns:
                columns.append(str(column))
    return [dict(row) for row in rows], columns


def _read_tsv(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="	")
            if reader.fieldnames is None:
                raise QCValidationError("TSV input is missing a header row")
            columns = [str(column) for column in reader.fieldnames]
            rows = [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as error:
        raise QCValidationError(f"cannot read TSV input {path.name}: {error}") from error

    if not rows:
        raise QCValidationError("input contains no QC records")
    return rows, columns


def _as_number(value: Any, column: str, row_index: int) -> float | int:
    if isinstance(value, bool):
        raise QCValidationError(f"row {row_index}: {column} must be numeric, not boolean")
    if isinstance(value, int):
        number: float | int = value
    elif isinstance(value, float):
        number = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            number = float(text)
        except ValueError as error:
            raise QCValidationError(
                f"row {row_index}: {column} has an invalid numeric value {value!r}"
            ) from error
        if number.is_integer() and re.fullmatch(r"[+-]?[0-9]+(?:\.0+)?", text):
            number = int(number)
    else:
        raise QCValidationError(
            f"row {row_index}: {column} has an unsupported value type {type(value).__name__}"
        )

    if not math.isfinite(float(number)):
        raise QCValidationError(f"row {row_index}: {column} must be finite")
    return number


def _as_count(value: Any, column: str, row_index: int) -> int:
    number = _as_number(value, column, row_index)
    if isinstance(number, float) and not number.is_integer():
        raise QCValidationError(f"row {row_index}: {column} must be an integer count")
    integer = int(number)
    if integer < 0:
        raise QCValidationError(f"row {row_index}: {column} must be non-negative")
    return integer


def _validate_field_names(columns: Sequence[str]) -> None:
    if len(set(columns)) != len(columns):
        raise QCValidationError("input contains duplicate column names")
    for column in columns:
        if not column or not _SAFE_FIELD.fullmatch(column):
            raise QCValidationError(
                f"invalid field name {column!r}; use letters, digits, underscore, dot, or hyphen"
            )


def _normalize_records(
    rows: Iterable[Mapping[str, Any]], columns: Sequence[str]
) -> list[dict[str, Any]]:
    _validate_field_names(columns)
    if SAMPLE_ID not in columns:
        raise QCValidationError("required column sample_id is missing")

    normalized: list[dict[str, Any]] = []
    sample_ids: set[str] = set()
    has_explicit_order = SAMPLE_ORDER in columns
    orders: set[int] = set()

    for row_index, source in enumerate(rows, start=2):
        sample_raw = source.get(SAMPLE_ID)
        if _missing(sample_raw):
            raise QCValidationError(f"row {row_index}: sample_id is missing")
        sample_id = str(sample_raw).strip()
        if sample_id in sample_ids:
            raise QCValidationError(f"row {row_index}: duplicate sample_id {sample_id!r}")
        sample_ids.add(sample_id)

        record: dict[str, Any] = {SAMPLE_ID: sample_id}
        if has_explicit_order:
            order_raw = source.get(SAMPLE_ORDER)
            if _missing(order_raw):
                raise QCValidationError(
                    f"row {row_index}: sample_order must be provided for every record"
                )
            order = _as_count(order_raw, SAMPLE_ORDER, row_index)
            if order in orders:
                raise QCValidationError(f"row {row_index}: duplicate sample_order {order}")
            orders.add(order)
            record[SAMPLE_ORDER] = order

        for column in columns:
            if column in {SAMPLE_ID, SAMPLE_ORDER}:
                continue
            value = source.get(column)
            if _missing(value):
                continue
            if column in COUNT_COLUMNS:
                record[column] = _as_count(value, column, row_index)
                continue
            numeric = _as_number(value, column, row_index)
            if column in KNOWN_RATE_COLUMNS and not 0 <= float(numeric) <= 100:
                raise QCValidationError(
                    f"row {row_index}: {column} must be between 0 and 100"
                )
            record[column] = numeric

        raw = record.get("raw_reads")
        fastp = record.get("fastp_reads")
        host = record.get("host_removed_reads")
        if raw is not None and fastp is not None and fastp > raw:
            raise QCValidationError(
                f"row {row_index}: fastp_reads ({fastp}) exceeds raw_reads ({raw})"
            )
        if fastp is not None and host is not None and host > fastp:
            raise QCValidationError(
                f"row {row_index}: host_removed_reads ({host}) exceeds fastp_reads ({fastp})"
            )
        if raw is not None and host is not None and host > raw:
            raise QCValidationError(
                f"row {row_index}: host_removed_reads ({host}) exceeds raw_reads ({raw})"
            )
        normalized.append(record)

    if has_explicit_order:
        normalized.sort(key=lambda record: int(record[SAMPLE_ORDER]))
    else:
        for index, record in enumerate(normalized):
            record[SAMPLE_ORDER] = index

    return normalized


def _adapt_project_counts(rows, columns):
    """Package fastp fields count reads; host fields count pairs."""
    aliases = {'post_fastp_reads': ('fastp_reads', 1), 'post_fastp_pairs': ('fastp_reads', 2),
               'post_host_pairs': ('host_removed_reads', 2)}
    if not set(aliases).intersection(columns):
        return rows, columns
    adapted = []
    result_columns = [c for c in columns if c not in {*aliases, 'host_removed_pairs'}]
    for index, row in enumerate(rows, 2):
        item = dict(row)
        for source, (target, multiplier) in aliases.items():
            value = item.pop(source, None)
            if _missing(value):
                continue
            count = _as_count(value, source, index) * multiplier
            if not _missing(item.get(target)) and _as_count(item[target], target, index) != count:
                raise QCValidationError(f'row {index}: conflicting reads/pairs for {target}')
            item[target] = count
            if target not in result_columns:
                result_columns.append(target)
        removed = item.pop('host_removed_pairs', None)
        if not _missing(removed) and all(not _missing(row.get(k)) for k in ('post_host_pairs', 'post_fastp_pairs')):
            if (_as_count(removed, 'host_removed_pairs', index) +
                _as_count(row['post_host_pairs'], 'post_host_pairs', index) !=
                _as_count(row['post_fastp_pairs'], 'post_fastp_pairs', index)):
                raise QCValidationError(f'row {index}: host pair counts do not balance')
        adapted.append(item)
    return adapted, result_columns


def load_qc_table(path: str | Path) -> QCData:
    """Read and validate a standardized QC table from TSV or JSON."""

    source_path = Path(path)
    if not source_path.is_file():
        raise QCValidationError(f"input file does not exist: {source_path}")
    if not source_path.stat().st_size:
        raise QCValidationError(f"input file is empty: {source_path.name}")

    suffix = source_path.suffix.lower()
    if suffix in {".tsv", ".tab", ".txt"}:
        rows, columns = _read_tsv(source_path)
        source_format = "tsv"
    elif suffix == ".json":
        rows, columns = _read_json(source_path)
        source_format = "json"
    else:
        raise QCValidationError("input must be a .tsv, .tab, .txt, or .json file")

    rows, columns = _adapt_project_counts(rows, columns)
    normalized = _normalize_records(rows, columns)
    ordered_columns = [SAMPLE_ID]
    if SAMPLE_ORDER in columns:
        ordered_columns.append(SAMPLE_ORDER)
    ordered_columns.extend(column for column in columns if column not in ordered_columns)
    return QCData(
        records=normalized,
        columns=ordered_columns,
        source_path=source_path,
        source_format=source_format,
        source_sha256=_sha256(source_path),
    )


def metric_columns(data: QCData, *, include_counts: bool = False) -> list[str]:
    """Return present numeric metric columns in deterministic source order."""

    excluded = {SAMPLE_ID, SAMPLE_ORDER}
    if not include_counts:
        excluded.update(COUNT_COLUMNS)
    return [
        column
        for column in data.columns
        if column not in excluded and any(column in row for row in data.records)
    ]


def format_value(value: Any) -> str:
    """Format a numeric value stably for evidence TSV files."""

    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format(value, ".12g")
    return str(value)


def table_metadata(data: QCData) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_file": data.source_path.name,
        "source_format": data.source_format,
        "source_sha256": data.source_sha256,
        "columns": data.columns,
        "sample_order": data.sample_ids,
        "sample_count": len(data.records),
    }
