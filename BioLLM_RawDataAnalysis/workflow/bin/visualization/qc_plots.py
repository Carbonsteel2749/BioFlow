#!/usr/bin/env python3
"""Generate deterministic, publication-ready QC figures from a normalized table."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

try:
    from .common import (
        COUNT_COLUMNS,
        QCData,
        QCValidationError,
        format_value,
        load_qc_table,
        metric_columns,
        table_metadata,
    )
except ImportError:
    from common import (  # type: ignore
        COUNT_COLUMNS,
        QCData,
        QCValidationError,
        format_value,
        load_qc_table,
        metric_columns,
        table_metadata,
    )

TOOL_NAME = "BioLLM QC visualization"
TOOL_VERSION = "1.0.0"
DEFAULT_DPI = 300
FIGURE_SIZE = (10.0, 6.0)
PALETTE = {
    "raw_reads": "#0072B2",
    "fastp_reads": "#009E73",
    "host_removed_reads": "#D55E00",
    "fastp_retention_pct": "#56B4E9",
    "host_retention_pct": "#CC79A7",
}
STAGE_LABELS = {
    "raw_reads": "Raw reads",
    "fastp_reads": "After fastp",
    "host_removed_reads": "After host depletion",
    "fastp_retention_pct": "After fastp / raw",
    "host_retention_pct": "After host depletion / fastp",
}
DATA_HEADER = ("figure", "sample_id", "metric", "value", "unit")


def configure_style() -> None:
    plt.rcParams.update(
        {
            "font.family": ["DejaVu Sans", "sans-serif"],
            "font.size": 10,
            "axes.titlesize": 14,
            "axes.labelsize": 11,
            "axes.titleweight": "bold",
            "axes.edgecolor": "#404040",
            "axes.linewidth": 0.8,
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": "#D9D9D9",
            "grid.linewidth": 0.6,
            "grid.alpha": 0.8,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
            "savefig.edgecolor": "white",
            "svg.fonttype": "none",
            "svg.hashsalt": "biollm-qc-v1",
            "pdf.fonttype": 42,
        }
    )


def _unit(metric: str) -> str:
    if metric.endswith("_pct") or metric.endswith("_rate") or metric in {"gc_content", "duplication_rate"}:
        return "%"
    if metric.endswith("_reads") or metric.endswith("_bases"):
        return "count"
    return "value"


def _value_rows(
    figure_name: str,
    data: QCData,
    metrics: Iterable[str],
    values_by_metric: dict[str, list[Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    values_by_metric = values_by_metric or {}
    for metric in metrics:
        values = values_by_metric.get(metric, [record.get(metric) for record in data.records])
        for record, value in zip(data.records, values):
            rows.append(
                {
                    "figure": figure_name,
                    "sample_id": record["sample_id"],
                    "metric": metric,
                    "value": value,
                    "unit": _unit(metric),
                }
            )
    return rows


def _write_data(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DATA_HEADER, delimiter="	")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "figure": row["figure"],
                    "sample_id": row["sample_id"],
                    "metric": row["metric"],
                    "value": format_value(row.get("value")),
                    "unit": row["unit"],
                }
            )


def _save_figure(fig: plt.Figure, directory: Path, name: str, dpi: int) -> list[str]:
    outputs: list[str] = []
    for extension in ("png", "svg", "pdf"):
        target = directory / f"figure.{extension}"
        metadata = None
        if extension == "svg":
            metadata = {
                "Title": name,
                "Creator": f"{TOOL_NAME} {TOOL_VERSION}",
                "Date": None,
            }
        elif extension == "pdf":
            metadata = {
                "Title": name,
                "Creator": f"{TOOL_NAME} {TOOL_VERSION}",
                "CreationDate": None,
                "ModDate": None,
            }
        fig.savefig(target, dpi=dpi, bbox_inches="tight", metadata=metadata)
        outputs.append(target.name)
    plt.close(fig)
    return outputs


def _write_bundle(
    output_root: Path,
    figure_name: str,
    figure: plt.Figure | None,
    data_rows: list[dict[str, Any]],
    base_provenance: dict[str, Any],
    *,
    reason: str | None = None,
    dpi: int = DEFAULT_DPI,
) -> dict[str, Any]:
    directory = output_root / "figures" / figure_name
    directory.mkdir(parents=True, exist_ok=True)
    data_path = directory / "figure.data.tsv"
    provenance_path = directory / "figure.provenance.json"
    _write_data(data_path, data_rows)

    if figure is None:
        status = "skipped"
        outputs = [data_path.name, provenance_path.name]
    else:
        status = "written"
        outputs = _save_figure(figure, directory, figure_name, dpi)
        outputs.extend((data_path.name, provenance_path.name))

    figure_provenance = {
        **base_provenance,
        "figure": figure_name,
        "status": status,
        "reason": reason,
        "data_rows": len(data_rows),
        "outputs": sorted(outputs),
    }
    provenance_path.write_text(
        json.dumps(figure_provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "name": figure_name,
        "status": status,
        "reason": reason,
        "data_rows": len(data_rows),
        "outputs": sorted(outputs),
    }


def _counts_figure(data: QCData) -> tuple[plt.Figure | None, list[dict[str, Any]], str | None]:
    available = [metric for metric in COUNT_COLUMNS if any(metric in record for record in data.records)]
    if not available:
        return None, [], "none of raw_reads, fastp_reads, host_removed_reads is present"

    figure, axis = plt.subplots(figsize=FIGURE_SIZE)
    positions = np.arange(len(data.records), dtype=float)
    width = 0.78 / max(len(available), 1)
    rows = _value_rows("reads_counts", data, available)
    for index, metric in enumerate(available):
        values = [float(record[metric]) if metric in record else np.nan for record in data.records]
        axis.bar(
            positions + (index - (len(available) - 1) / 2) * width,
            values,
            width=width,
            label=STAGE_LABELS[metric],
            color=PALETTE[metric],
            edgecolor="white",
            linewidth=0.5,
        )
    axis.set_title("Read counts across preprocessing stages")
    axis.set_ylabel("Reads")
    axis.set_xticks(positions)
    axis.set_xticklabels(data.sample_ids, rotation=45, ha="right")
    axis.ticklabel_format(axis="y", style="sci", scilimits=(0, 0))
    axis.legend(frameon=False, loc="best")
    figure.tight_layout()
    return figure, rows, None


def _retention_figure(
    data: QCData,
) -> tuple[plt.Figure | None, list[dict[str, Any]], str | None, list[str]]:
    pairs = (
        ("fastp_retention_pct", "fastp_reads", "raw_reads"),
        ("host_retention_pct", "host_removed_reads", "fastp_reads"),
    )
    values_by_metric: dict[str, list[Any]] = {}
    notes: list[str] = []
    available: list[str] = []
    for output_metric, numerator, denominator in pairs:
        if not all(any(field in record for record in data.records) for field in (numerator, denominator)):
            notes.append(f"{output_metric} skipped: requires {numerator} and {denominator}")
            continue
        values: list[float | None] = []
        for record in data.records:
            if numerator not in record or denominator not in record:
                values.append(None)
            elif record[denominator] == 0:
                values.append(None)
                notes.append(f"{output_metric} undefined for sample {record['sample_id']}: zero denominator")
            else:
                values.append(100.0 * record[numerator] / record[denominator])
        available.append(output_metric)
        values_by_metric[output_metric] = values

    if not available:
        return None, [], "; ".join(notes), notes

    figure, axis = plt.subplots(figsize=FIGURE_SIZE)
    positions = np.arange(len(data.records), dtype=float)
    width = 0.78 / len(available)
    rows = _value_rows("reads_retention", data, available, values_by_metric)
    for index, metric in enumerate(available):
        values = [np.nan if value is None else float(value) for value in values_by_metric[metric]]
        axis.bar(
            positions + (index - (len(available) - 1) / 2) * width,
            values,
            width=width,
            label=STAGE_LABELS[metric],
            color=PALETTE[metric],
            edgecolor="white",
            linewidth=0.5,
        )
    axis.set_title("Read retention")
    axis.set_ylabel("Retained reads (%)")
    axis.set_ylim(0, 105)
    axis.set_xticks(positions)
    axis.set_xticklabels(data.sample_ids, rotation=45, ha="right")
    axis.legend(frameon=False, loc="best")
    figure.tight_layout()
    return figure, rows, "; ".join(notes) if notes else None, notes


def _heatmap_figure(
    data: QCData,
) -> tuple[plt.Figure | None, list[dict[str, Any]], str | None]:
    metrics = metric_columns(data)
    if not metrics:
        return None, [], "no optional numeric QC metric is present"

    raw_matrix = np.array(
        [
            [np.nan if metric not in record else float(record[metric]) for metric in metrics]
            for record in data.records
        ],
        dtype=float,
    )
    scaled = np.full_like(raw_matrix, np.nan)
    for column_index in range(raw_matrix.shape[1]):
        column = raw_matrix[:, column_index]
        finite = np.isfinite(column)
        if not finite.any():
            continue
        mean = float(np.nanmean(column))
        standard_deviation = float(np.nanstd(column))
        if standard_deviation == 0:
            scaled[finite, column_index] = 0.0
        else:
            scaled[finite, column_index] = (column[finite] - mean) / standard_deviation

    height = max(4.5, 0.45 * len(data.records) + 2.5)
    figure, axis = plt.subplots(figsize=(max(8.0, 1.1 * len(metrics) + 3.0), height))
    image = axis.imshow(
        np.ma.masked_invalid(scaled),
        aspect="auto",
        cmap="viridis",
        vmin=-2,
        vmax=2,
        interpolation="nearest",
    )
    axis.set_title("QC metrics heatmap (column-wise z-score)")
    axis.set_xlabel("QC metric")
    axis.set_ylabel("Sample")
    axis.set_xticks(np.arange(len(metrics)))
    axis.set_xticklabels(metrics, rotation=45, ha="right")
    axis.set_yticks(np.arange(len(data.records)))
    axis.set_yticklabels(data.sample_ids)
    colorbar = figure.colorbar(image, ax=axis, fraction=0.046, pad=0.04)
    colorbar.set_label("z-score (clipped at +/-2)")
    figure.tight_layout()
    return figure, _value_rows("qc_heatmap", data, metrics), None


def build_figures(input_path: str | Path, output_dir: str | Path, dpi: int = DEFAULT_DPI) -> dict[str, Any]:
    data = load_qc_table(input_path)
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    configure_style()

    base_provenance = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "input": table_metadata(data),
        "sample_order": data.sample_ids,
        "style": {
            "dpi": dpi,
            "font_family": ["DejaVu Sans", "sans-serif"],
            "palette": PALETTE,
            "figure_size_in": list(FIGURE_SIZE),
        },
    }
    figure_results: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []

    counts_figure, counts_rows, counts_reason = _counts_figure(data)
    result = _write_bundle(
        output_root, "reads_counts", counts_figure, counts_rows, base_provenance,
        reason=counts_reason, dpi=dpi,
    )
    figure_results.append(result)
    all_rows.extend(counts_rows)

    retention_figure, retention_rows, retention_reason, retention_notes = _retention_figure(data)
    result = _write_bundle(
        output_root, "reads_retention", retention_figure, retention_rows, base_provenance,
        reason=retention_reason, dpi=dpi,
    )
    figure_results.append(result)
    all_rows.extend(retention_rows)

    heatmap_figure, heatmap_rows, heatmap_reason = _heatmap_figure(data)
    result = _write_bundle(
        output_root, "qc_heatmap", heatmap_figure, heatmap_rows, base_provenance,
        reason=heatmap_reason, dpi=dpi,
    )
    figure_results.append(result)
    all_rows.extend(heatmap_rows)

    _write_data(output_root / "figure.data.tsv", all_rows)
    provenance = {
        **base_provenance,
        "figures": figure_results,
        "skipped_reasons": [
            result["reason"] for result in figure_results
            if result["status"] == "skipped" and result["reason"]
        ],
        "notes": retention_notes,
        "validation": {
            "status": "passed",
            "record_count": len(data.records),
            "present_columns": data.present_columns,
        },
    }
    provenance_json = json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (output_root / "figure.provenance.json").write_text(provenance_json, encoding="utf-8")
    (output_root / "qc_visualization.summary.json").write_text(provenance_json, encoding="utf-8")

    log_lines = [
        f"tool={TOOL_NAME} version={TOOL_VERSION}",
        f"input={data.source_path.name} format={data.source_format} sha256={data.source_sha256}",
        f"samples={','.join(data.sample_ids)}",
    ]
    for result in figure_results:
        if result["status"] == "written":
            log_lines.append(f"figure={result['name']} status=written")
        else:
            log_lines.append(f"figure={result['name']} status=skipped reason={result['reason']}")
    (output_root / "qc_visualization.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    return provenance


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate standardized QC figures from TSV or JSON")
    parser.add_argument("--input", required=True, help="standardized QC TSV or JSON")
    parser.add_argument("--output-dir", required=True, help="output directory")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    args = parser.parse_args(argv)
    if args.dpi <= 0:
        parser.error("--dpi must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        build_figures(args.input, args.output_dir, dpi=args.dpi)
    except QCValidationError as error:
        print(f"QC_VISUALIZATION_INPUT_ERROR: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"QC_VISUALIZATION_ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
