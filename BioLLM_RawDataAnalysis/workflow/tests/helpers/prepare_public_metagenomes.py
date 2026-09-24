#!/usr/bin/env python3
"""Download pinned public paired metagenomes and create reproducible test subsets."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import shutil
import subprocess
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--small-pairs", type=int, default=10_000)
    parser.add_argument("--medium-pairs", type=int, default=250_000)
    return parser.parse_args()


def require_positive(name: str, value: int) -> None:
    if value < 1:
        raise SystemExit(f"{name} must be positive")


def download(url: str, destination: Path) -> None:
    if destination.is_file() and destination.stat().st_size:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        subprocess.run(
            ["curl", "--fail", "--location", "--retry", "3", "--output", str(temporary), url],
            check=True,
        )
        with gzip.open(temporary, "rb") as handle:
            handle.read(1)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def trim_fastq(source: Path, destination: Path, pairs: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with gzip.open(source, "rt", encoding="utf-8", newline="") as reader, gzip.open(
        destination, "wt", encoding="utf-8", newline=""
    ) as writer:
        for _ in range(pairs):
            record = [reader.readline() for _ in range(4)]
            if any(line == "" for line in record):
                break
            writer.writelines(record)
            written += 1
    if written != pairs:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"{source} has only {written} readable FASTQ records; expected {pairs}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sample_id", "read1", "read2"])
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    require_positive("--small-pairs", args.small_pairs)
    require_positive("--medium-pairs", args.medium_pairs)
    if shutil.which("curl") is None:
        raise SystemExit("curl is required to download public fixtures")
    with args.catalog.open(encoding="utf-8", newline="") as handle:
        samples = list(csv.DictReader(handle, delimiter="\t"))
    if len(samples) != 3 or any(not sample.get("sample_id") for sample in samples):
        raise SystemExit("catalog must contain exactly three named public samples")

    raw_root = args.output / "raw"
    subset_root = args.output / "subsets"
    metadata: list[dict[str, str]] = []
    small_rows: list[dict[str, str]] = []
    for sample in samples:
        sample_id = sample["sample_id"]
        raw_r1 = raw_root / f"{sample_id}_R1.fastq.gz"
        raw_r2 = raw_root / f"{sample_id}_R2.fastq.gz"
        download(sample["r1_url"], raw_r1)
        download(sample["r2_url"], raw_r2)
        small_r1 = subset_root / "small" / f"{sample_id}_R1.fastq.gz"
        small_r2 = subset_root / "small" / f"{sample_id}_R2.fastq.gz"
        trim_fastq(raw_r1, small_r1, args.small_pairs)
        trim_fastq(raw_r2, small_r2, args.small_pairs)
        small_rows.append({"sample_id": sample_id, "read1": str(small_r1.resolve()), "read2": str(small_r2.resolve())})
        metadata.append({"sample_id": sample_id, "run_accession": sample["run_accession"], "subset": "small", "pairs": str(args.small_pairs), "r1_sha256": sha256(small_r1), "r2_sha256": sha256(small_r2)})

    write_manifest(args.output / "manifests" / "single-small.csv", [small_rows[0]])
    write_manifest(args.output / "manifests" / "multi-small.csv", small_rows)
    medium_sample = samples[0]
    medium_r1 = subset_root / "medium" / f"{medium_sample['sample_id']}_R1.fastq.gz"
    medium_r2 = subset_root / "medium" / f"{medium_sample['sample_id']}_R2.fastq.gz"
    trim_fastq(raw_root / f"{medium_sample['sample_id']}_R1.fastq.gz", medium_r1, args.medium_pairs)
    trim_fastq(raw_root / f"{medium_sample['sample_id']}_R2.fastq.gz", medium_r2, args.medium_pairs)
    write_manifest(args.output / "manifests" / "single-medium.csv", [{"sample_id": medium_sample["sample_id"], "read1": str(medium_r1.resolve()), "read2": str(medium_r2.resolve())}])
    metadata.append({"sample_id": medium_sample["sample_id"], "run_accession": medium_sample["run_accession"], "subset": "medium", "pairs": str(args.medium_pairs), "r1_sha256": sha256(medium_r1), "r2_sha256": sha256(medium_r2)})
    with (args.output / "fixture-manifest.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(metadata[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
