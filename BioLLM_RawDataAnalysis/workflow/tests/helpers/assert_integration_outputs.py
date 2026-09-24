#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import tarfile
from pathlib import Path, PurePosixPath


REQUIRED_FILES = {
    "reports/multiqc_report.html",
    "reports/summary.txt",
    "tables/read_counts.tsv",
    "tables/species_abundance.tsv",
    "tables/gene_families.tsv",
    "tables/pathway_abundance.tsv",
    "tables/pathway_coverage/read_pathcoverage.tsv",
    "tables/ko_abundance/read_ko.tsv",
    "tables/ec_abundance/read_ec.tsv",
    "provenance/run.json",
    "provenance/input_checksums.tsv",
    "provenance/database_versions.tsv",
    "provenance/software_versions.tsv",
    "provenance/source_checksums.tsv",
    "provenance/parameters.json",
    "manifest.json",
    "SHA256SUMS",
}


def read_table(data: bytes, delimiter: str = "\t"):
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")), delimiter=delimiter)
    if not reader.fieldnames:
        raise AssertionError("table has no header")
    return list(reader.fieldnames), [dict(row) for row in reader]


def require_columns(header, required, name):
    missing = set(required) - set(header)
    if missing:
        raise AssertionError(f"{name} lacks columns: {sorted(missing)}")


def require_unique(rows, key, name):
    values = [tuple(row.get(field, "").strip() for field in key) for row in rows]
    if any(not all(value) for value in values):
        raise AssertionError(f"{name} has an empty primary key {key}")
    if len(values) != len(set(values)):
        raise AssertionError(f"{name} has duplicate primary key {key}")


def require_numeric(rows, fields, name):
    for row in rows:
        for field in fields:
            try:
                float(row[field])
            except (KeyError, TypeError, ValueError) as error:
                raise AssertionError(f"{name}.{field} is not numeric") from error


def assert_manifest(path: Path, samples: set[str]):
    header, rows = read_table(path.read_bytes(), ",")
    if header != ["sample_id", "read1", "read2"]:
        raise AssertionError(f"invalid manifest header: {header}")
    require_unique(rows, ("sample_id",), str(path))
    if {row["sample_id"] for row in rows} != samples:
        raise AssertionError("validated manifest sample set does not match")
    for row in rows:
        for role in ("read1", "read2"):
            source = Path(row[role])
            if not source.is_absolute() or not source.is_file() or source.stat().st_size == 0:
                raise AssertionError(f"{role} is not a valid absolute file: {source}")


def assert_standard_tables(files: dict[str, bytes], samples: set[str]):
    contracts = {
        "tables/read_counts.tsv": (
            {"sample_id", "raw_reads", "post_fastp_reads", "post_host_pairs"},
            ("sample_id",),
            ("raw_reads", "post_fastp_reads", "post_host_pairs"),
        ),
        "tables/species_abundance.tsv": (
            {"sample_id", "taxonomy", "abundance"},
            ("sample_id", "taxonomy"),
            ("abundance",),
        ),
        "tables/gene_families.tsv": (
            {"sample_id", "function_id", "abundance"},
            ("sample_id", "function_id"),
            ("abundance",),
        ),
        "tables/pathway_abundance.tsv": (
            {"sample_id", "function_id", "abundance"},
            ("sample_id", "function_id"),
            ("abundance",),
        ),
    }
    for name, (required, key, numeric) in contracts.items():
        header, rows = read_table(files[name])
        require_columns(header, required, name)
        if not rows:
            raise AssertionError(f"{name} contains no data")
        require_unique(rows, key, name)
        require_numeric(rows, numeric, name)
        if {row["sample_id"] for row in rows} != samples:
            raise AssertionError(f"{name} sample set does not match manifest")


def assert_mag(files: dict[str, bytes], samples: set[str]):
    abundance_names = [name for name in files if name.endswith("sample_mag_abundance.tsv")]
    annotation_names = [name for name in files if name.endswith("mag_annotation.tsv")]
    provenance_names = [name for name in files if name.endswith("mag_annotation.provenance.json")]
    if not abundance_names or not annotation_names or not provenance_names:
        raise AssertionError("MAG package artifacts are incomplete")
    _, abundance = read_table(files[abundance_names[0]])
    _, annotation = read_table(files[annotation_names[0]])
    if not abundance or not annotation:
        raise AssertionError("MAG tables contain no data")
    require_unique(abundance, ("sample_id", "mag_id"), abundance_names[0])
    require_unique(annotation, ("mag_id",), annotation_names[0])
    require_numeric(abundance, ("abundance",), abundance_names[0])
    if {row["sample_id"] for row in abundance} != samples:
        raise AssertionError("MAG abundance sample set does not match")
    abundance_ids = {row["mag_id"] for row in abundance}
    annotation_ids = {row["mag_id"] for row in annotation}
    if not abundance_ids <= annotation_ids:
        raise AssertionError(
            "MAG abundance references unknown mag_id: "
            f"unknown={sorted(abundance_ids - annotation_ids)}, "
            f"annotations={sorted(annotation_ids)}, "
            f"abundance_file={abundance_names[0]}, "
            f"annotation_file={annotation_names[0]}"
        )
    provenance = json.loads(files[provenance_names[0]])
    for key in ("database_name", "database_release", "database_manifest_sha256", "tool_name"):
        if not provenance.get(key):
            raise AssertionError(f"MAG provenance lacks {key}")


def assert_package(path: Path, samples: set[str], require_mag: bool):
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        if any(member.issym() or member.islnk() for member in members):
            raise AssertionError("result package contains a symlink")
        files = {
            member.name: archive.extractfile(member).read()
            for member in members
            if member.isfile()
        }
    missing = REQUIRED_FILES - files.keys()
    if missing:
        raise AssertionError(f"package lacks files: {sorted(missing)}")
    forbidden = (".fastq", ".fq", ".fastq.gz", ".fq.gz")
    if any(
        name.lower().endswith(forbidden)
        or PurePosixPath(name).parts[0] in {"work", "input"}
        for name in files
    ):
        raise AssertionError("package contains raw input or work data")
    if not files["reports/multiqc_report.html"].lstrip().lower().startswith(b"<!doctype html"):
        raise AssertionError("MultiQC report is not valid HTML")
    assert_standard_tables(files, samples)

    run = json.loads(files["provenance/run.json"])
    if run.get("final_status") != "completed" or run.get("sample_count") != len(samples):
        raise AssertionError(f"invalid run provenance: {run}")
    header, checksums = read_table(files["provenance/input_checksums.tsv"])
    require_columns(header, {"sample_id", "role", "size_bytes", "sha256"}, "input_checksums")
    require_unique(checksums, ("sample_id", "role"), "input_checksums")
    if len(checksums) != len(samples) * 2:
        raise AssertionError("input checksum count does not match paired inputs")
    if {row["sample_id"] for row in checksums} != samples:
        raise AssertionError("input checksum sample set does not match")
    for row in checksums:
        if int(row["size_bytes"]) <= 0 or len(row["sha256"]) != 64:
            raise AssertionError(f"invalid input checksum row: {row}")

    inventory = json.loads(files["manifest.json"])["artifacts"]
    records = {record["path"]: record for record in inventory}
    for name, record in records.items():
        digest = hashlib.sha256(files[name]).hexdigest() if name in files else ""
        if record["size_bytes"] != len(files.get(name, b"")) or record["sha256"] != digest:
            raise AssertionError(f"archive inventory mismatch for {name}")
    sums = {}
    for line in files["SHA256SUMS"].decode().splitlines():
        digest, name = line.split("  ", 1)
        sums[name] = digest
    if sums != {name: record["sha256"] for name, record in records.items()}:
        raise AssertionError("SHA256SUMS does not match manifest.json")
    database_versions = files["provenance/database_versions.tsv"].decode()
    if "/home/" in database_versions or "/tmp/" in database_versions:
        raise AssertionError("database provenance leaks a local path")
    if require_mag:
        assert_mag(files, samples)
    elif any("mag_" in name or "/mag/" in name for name in files):
        raise AssertionError("no-MAG package contains MAG artifacts")


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("path", type=Path)
    manifest.add_argument("samples")
    package = subparsers.add_parser("package")
    package.add_argument("path", type=Path)
    package.add_argument("samples")
    package.add_argument("--require-mag", action="store_true")
    args = parser.parse_args()
    samples = set(args.samples.split(","))
    if args.command == "manifest":
        assert_manifest(args.path, samples)
    else:
        assert_package(args.path, samples, args.require_mag)


if __name__ == "__main__":
    main()
