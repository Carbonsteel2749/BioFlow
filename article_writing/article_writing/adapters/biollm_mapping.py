"""Map a BioLLM metagenomics result package onto AnalysisBundle V1.

Only copies facts that are already in the archive. Does not invent wet-lab
methods, group comparisons, or mechanistic conclusions.
"""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any

from article_writing.contracts import (
    AnalysisBundle,
    FigureRef,
    MicrobiomeSequencing,
)

REPORT_DIR_NAMES = ("reports", "report")
DEFAULT_LIMITATIONS = [
    "This package reports descriptive QC, taxonomic, and functional counts only; "
    "no group comparison or differential abundance tests were supplied.",
    "Publication-ready figures (for example stacked bars or PCoA) were not included; "
    "MultiQC and TSV tables are the available displays.",
    "Wet-lab slots (sample collection, DNA extraction, library prep, sequencing "
    "platform) were not present in the analysis archive.",
]


class BioLLMPackageError(ValueError):
    """Raised when a path is not a usable BioLLM result package."""


def looks_like_biollm_package(path: Path) -> bool:
    if not path.is_dir():
        return False
    if _first_report_file(path, "summary.json") is not None:
        return True
    tables = path / "tables"
    return (tables / "read_counts.tsv").is_file() or (tables / "species_abundance.tsv").is_file()


def locate_package_root(path: Path | str) -> Path:
    root = Path(path).expanduser().resolve()
    if looks_like_biollm_package(root):
        return root
    if root.is_dir():
        for child in sorted(child for child in root.iterdir() if child.is_dir()):
            if looks_like_biollm_package(child):
                return child
    raise BioLLMPackageError(f"not a BioLLM result package: {root}")


def map_biollm_package(package_root: Path | str) -> AnalysisBundle:
    root = locate_package_root(package_root)
    summary = _read_json(_first_report_file(root, "summary.json"))
    parameters = _read_json(root / "provenance" / "parameters.json")
    run = _read_json(root / "provenance" / "run.json")
    software = _read_tsv(root / "provenance" / "software_versions.tsv")
    databases = _read_tsv(root / "provenance" / "database_versions.tsv")
    read_counts = _read_tsv(root / "tables" / "read_counts.tsv")
    species = _read_tsv(root / "tables" / "species_abundance.tsv")
    gene_families = _read_tsv(root / "tables" / "gene_families.tsv")
    pathways = _read_tsv(root / "tables" / "pathway_abundance.tsv")
    mag_rows = _read_tsv(root / "tables" / "mag_annotation.tsv")

    sample_count = _as_int(summary.get("sample_count") or run.get("sample_count")) or len(
        {row.get("sample_id") for row in read_counts if row.get("sample_id")}
    )
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    raw_reads = _numeric_column(read_counts, "raw_reads")
    clean_reads = _numeric_column(read_counts, "post_fastp_reads")
    host_removed = _numeric_column(read_counts, "host_removed_pairs")
    top_taxa = _top_taxa(species, limit=3)
    methods = _methods_dict(parameters, software, databases, mag_rows)
    qc_sentence = _qc_sentence(sample_count, raw_reads, clean_reads, host_removed)
    findings, evidence_ids = _findings(
        sample_count=sample_count,
        raw_reads=raw_reads,
        clean_reads=clean_reads,
        top_taxa=top_taxa,
        gene_n=_count_rows(counts, "gene_family_rows", gene_families),
        pathway_n=_count_rows(counts, "pathway_rows", pathways),
        mag_n=len(mag_rows),
    )
    metrics = _metrics(
        sample_count=sample_count,
        counts=counts,
        raw_reads=raw_reads,
        clean_reads=clean_reads,
        host_removed=host_removed,
        top_taxa=top_taxa,
        gene_n=len(gene_families),
        pathway_n=len(pathways),
        mag_n=len(mag_rows),
    )
    figure_refs = _figure_refs(root, mag_rows)
    summary_text = _summary_text(
        task_id=str(summary.get("task_id") or run.get("task_id") or ""),
        sample_count=sample_count,
        findings=findings,
    )
    seq = MicrobiomeSequencing(
        qc_pipeline=str(methods.get("qc") or ""),
        taxonomy_annotation=str(methods.get("taxonomy") or ""),
        pathway_annotation=str(methods.get("functional_annotation") or ""),
        post_filter_summary=qc_sentence,
        sequencing_strategy=_sequencing_strategy(parameters),
    )
    return AnalysisBundle(
        summary=summary_text,
        methods=methods,
        key_findings=findings,
        metrics=metrics,
        limitations=list(DEFAULT_LIMITATIONS),
        figure_refs=figure_refs,
        evidence_ids=evidence_ids,
        raw={
            "source": "biollm_raw_data_analysis",
            "task_id": summary.get("task_id") or run.get("task_id") or "",
            "package_root": str(root),
            "summary": summary,
            "run": run,
            "parameters": parameters,
        },
        microbiome_sequencing=seq if _has_text(seq) else None,
    )


def _first_report_file(root: Path, name: str) -> Path | None:
    for folder in REPORT_DIR_NAMES:
        candidate = root / folder / name
        if candidate.is_file():
            return candidate
    return None


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    if number is None:
        return None
    return int(number)


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _numeric_column(rows: list[dict[str, str]], column: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        number = _as_float(row.get(column))
        if number is not None:
            values.append(number)
    return values


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _fmt_number(value: float | None) -> str:
    if value is None:
        return "not reported"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.1f}"


def _count_rows(counts: dict[str, Any], key: str, rows: list[dict[str, str]]) -> int:
    reported = _as_int(counts.get(key))
    if reported is not None:
        return reported
    return len(rows)


def _top_taxa(rows: list[dict[str, str]], limit: int = 3) -> list[tuple[str, float]]:
    totals: dict[str, list[float]] = {}
    for row in rows:
        name = next(
            (
                row.get(key, "").strip()
                for key in ("taxonomy", "name", "species")
                if row.get(key, "").strip()
            ),
            "",
        )
        abundance = _as_float(row.get("abundance") or row.get("relative_abundance"))
        if not name or abundance is None:
            continue
        if 0 <= abundance <= 1:
            abundance *= 100
        totals.setdefault(name, []).append(abundance)
    ranked = sorted(
        ((name, sum(values) / len(values)) for name, values in totals.items()),
        key=lambda item: item[1],
        reverse=True,
    )
    return ranked[:limit]


def _tool_version(rows: list[dict[str, str]], needle: str) -> str:
    for row in rows:
        tool = (row.get("tool") or "").strip()
        if needle in tool.lower():
            return f"{tool} {(row.get('version') or '').strip()}".strip()
    return ""


def _database_line(rows: list[dict[str, str]], needle: str) -> str:
    for row in rows:
        blob = " ".join(
            str(row.get(key) or "") for key in ("section", "database_name", "taxonomy_system")
        ).lower()
        if needle not in blob:
            continue
        name = (row.get("database_name") or "").strip()
        release = (row.get("release") or "").strip()
        return " ".join(part for part in (name, release) if part)
    return ""


def _param(parameters: dict[str, Any], key: str) -> str:
    value = parameters.get(key)
    return "" if value is None or value == "" else str(value)


def _methods_dict(
    parameters: dict[str, Any],
    software: list[dict[str, str]],
    databases: list[dict[str, str]],
    mag_rows: list[dict[str, str]],
) -> dict[str, Any]:
    qc_bits = [
        f"{key}={_param(parameters, key)}"
        for key in (
            "fastp_qualified_quality_phred",
            "fastp_unqualified_percent_limit",
            "fastp_length_required",
            "fastp_cut_mean_quality",
        )
        if _param(parameters, key)
    ]
    host_bits = [
        f"{key}={_param(parameters, key)}"
        for key in ("host_filter_mode", "host_bowtie2_preset")
        if _param(parameters, key)
    ]
    mag_enabled = bool(parameters.get("enable_mags")) or bool(mag_rows)
    mag_bits = []
    if mag_enabled:
        for key in ("assembler", "bin_completeness", "bin_contamination"):
            if _param(parameters, key):
                mag_bits.append(f"{key}={_param(parameters, key)}")
    methods: dict[str, Any] = {
        "pipeline": "BioLLM metagenomics V1 (Nextflow)",
        "qc": "fastp "
        + ("; ".join(qc_bits) if qc_bits else "quality filtering (see provenance)"),
        "host_depletion": "Bowtie2 host read removal"
        + (f" ({'; '.join(host_bits)})" if host_bits else ""),
        "taxonomy": "Kraken2 + Bracken",
        "functional_annotation": "HUMAnN",
    }
    fastp = _tool_version(software, "fastp")
    kraken = _tool_version(software, "kraken")
    humann = _tool_version(software, "humann")
    if fastp:
        methods["qc"] += f"; software={fastp}"
    kraken_db = _database_line(databases, "kraken")
    if kraken:
        methods["taxonomy"] += f"; software={kraken}"
    if kraken_db:
        methods["taxonomy"] += f"; database={kraken_db}"
    humann_db = _database_line(databases, "humann") or _database_line(databases, "chocophlan")
    if humann:
        methods["functional_annotation"] += f"; software={humann}"
    if humann_db:
        methods["functional_annotation"] += f"; database={humann_db}"
    if mag_enabled:
        methods["mag"] = "enabled" + (f" ({'; '.join(mag_bits)})" if mag_bits else "")
    else:
        methods["mag"] = "disabled"
    return methods


def _sequencing_strategy(parameters: dict[str, Any]) -> str:
    read_length = _param(parameters, "read_length")
    if not read_length:
        return ""
    return f"{read_length} bp paired-end reads (workflow parameter; platform not supplied)"


def _qc_sentence(
    sample_count: int,
    raw_reads: list[float],
    clean_reads: list[float],
    host_removed: list[float],
) -> str:
    parts = [f"{sample_count} samples had packaged read-count records"]
    if raw_reads:
        parts.append(f"median raw reads={_fmt_number(_median(raw_reads))}")
    if clean_reads:
        parts.append(f"median post-fastp reads={_fmt_number(_median(clean_reads))}")
    if host_removed:
        parts.append(f"median host-removed pairs={_fmt_number(_median(host_removed))}")
    return "; ".join(parts) + "."


def _findings(
    *,
    sample_count: int,
    raw_reads: list[float],
    clean_reads: list[float],
    top_taxa: list[tuple[str, float]],
    gene_n: int,
    pathway_n: int,
    mag_n: int,
) -> tuple[list[str], list[str]]:
    findings: list[str] = []
    evidence_ids: list[str] = []
    findings.append(
        f"{sample_count} samples completed QC "
        f"(median raw reads={_fmt_number(_median(raw_reads))}; "
        f"median post-fastp reads={_fmt_number(_median(clean_reads))})."
    )
    evidence_ids.append("ev_qc_reads")
    if top_taxa:
        name, abundance = top_taxa[0]
        extras = ""
        if len(top_taxa) > 1:
            extras = " Next taxa by mean relative abundance: " + "; ".join(
                f"{item[0]} ({item[1]:.2f}%)" for item in top_taxa[1:]
            )
        findings.append(
            f"The highest mean relative abundance among profiled taxa was "
            f"{name} ({abundance:.2f}%).{extras}"
        )
        evidence_ids.append("ev_taxonomy_top")
    if gene_n or pathway_n:
        findings.append(
            f"Functional annotation reported {gene_n} gene-family rows and "
            f"{pathway_n} pathway-abundance rows."
        )
        evidence_ids.append("ev_pathways")
    if mag_n:
        findings.append(f"MAG annotation reported {mag_n} bins.")
        evidence_ids.append("ev_mag")
    return findings, evidence_ids


def _metrics(
    *,
    sample_count: int,
    counts: dict[str, Any],
    raw_reads: list[float],
    clean_reads: list[float],
    host_removed: list[float],
    top_taxa: list[tuple[str, float]],
    gene_n: int,
    pathway_n: int,
    mag_n: int,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "sample_count": sample_count,
        "species_rows": _as_int(counts.get("species_rows")) or 0,
        "gene_family_rows": _as_int(counts.get("gene_family_rows")) or gene_n,
        "pathway_rows": _as_int(counts.get("pathway_rows")) or pathway_n,
    }
    if raw_reads:
        metrics["median_raw_reads"] = _median(raw_reads)
    if clean_reads:
        metrics["median_clean_reads"] = _median(clean_reads)
    if host_removed:
        metrics["median_host_removed_pairs"] = _median(host_removed)
    if top_taxa:
        metrics["top_taxon"] = top_taxa[0][0]
        metrics["top_taxon_mean_abundance_pct"] = round(top_taxa[0][1], 4)
    if mag_n:
        metrics["mag_annotation_rows"] = mag_n
    return metrics


def _figure_refs(root: Path, mag_rows: list[dict[str, str]]) -> list[FigureRef]:
    refs: list[FigureRef] = []
    table_specs = [
        ("tbl_read_counts", "tables/read_counts.tsv", "Per-sample raw, post-fastp, and host-depletion read counts"),
        ("tbl_species", "tables/species_abundance.tsv", "Combined standardized species abundance"),
        ("tbl_gene_families", "tables/gene_families.tsv", "Combined standardized HUMAnN gene families"),
        ("tbl_pathways", "tables/pathway_abundance.tsv", "Combined standardized pathway abundance"),
    ]
    if mag_rows:
        table_specs.append(
            ("tbl_mag_annotation", "tables/mag_annotation.tsv", "MAG taxonomic annotation")
        )
    for figure_id, relative, caption in table_specs:
        path = root / relative
        if path.is_file():
            refs.append(
                FigureRef(
                    figure_id=figure_id,
                    path=str(path.resolve()),
                    caption=caption,
                    kind="table",
                )
            )
    multiqc = _first_report_file(root, "multiqc_report.html")
    if multiqc is not None:
        refs.append(
            FigureRef(
                figure_id="fig_multiqc",
                path=str(multiqc.resolve()),
                caption="MultiQC quality-control report from the analysis package",
                kind="figure",
            )
        )
    return refs


def _summary_text(task_id: str, sample_count: int, findings: list[str]) -> str:
    prefix = (
        f"BioLLM metagenomics task {task_id} packaged descriptive results for "
        f"{sample_count} samples."
        if task_id
        else f"BioLLM metagenomics packaged descriptive results for {sample_count} samples."
    )
    if not findings:
        return prefix
    return prefix + " " + " ".join(findings)


def _has_text(seq: MicrobiomeSequencing) -> bool:
    return any(
        [
            seq.qc_pipeline,
            seq.taxonomy_annotation,
            seq.pathway_annotation,
            seq.post_filter_summary,
            seq.sequencing_strategy,
        ]
    )
