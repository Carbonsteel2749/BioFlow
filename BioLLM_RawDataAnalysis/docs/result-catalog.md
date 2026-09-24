# V1 result catalog

The final archive is `deliverables/<task_id>.tar.gz`. It contains result
artifacts only; raw FASTQ files, reference databases, SQLite state, and
Nextflow work directories are excluded.

## Archive contents

- `reports/multiqc_report.html`: MultiQC quality-control report.
- `reports/multiqc_report_data/`: sanitized machine-readable MultiQC data
  when produced.
- `reports/summary.txt`: template-generated research-use-only summary.
- `reports/summary.json`: machine-readable result counts and report
  entrypoints.
- `tables/read_counts.tsv`: raw, post-fastp, and post-host read counts.
- `tables/species_abundance.tsv`: combined standardized species abundance.
- `tables/gene_families.tsv`: combined standardized HUMAnN gene families.
- `tables/pathway_abundance.tsv`: combined standardized pathway abundance.
- `tables/pathway_coverage/`: per-sample pathway coverage tables, when
  produced.
- `tables/ko_abundance/` and `tables/ec_abundance/`: validated KO and EC
  abundance tables, when produced.
- `tables/mag_annotation.tsv`, `tables/mag_function_annotation.tsv`, and
  `tables/sample_mag_abundance.tsv`: validated MAG outputs, when produced.
- `tables/by_sample/`: per-sample source tables and provenance.
- `qc/`: selected FastQC, fastp, and host-depletion metrics.
- `provenance/run.json`: task, run name, source revision, and final status.
- `provenance/parameters.json`: sanitized workflow parameters.
- `provenance/input_checksums.tsv`: input sizes and SHA256 checksums.
- `provenance/software_versions.tsv`: locally observed tool versions.
- `provenance/database_versions.tsv`: database releases without local paths.
- `provenance/source_checksums.tsv`: workflow source checksums.
- `logs/`: redacted workflow log tails. Each source log is limited to the
  newest 8 MiB and receives an explicit truncation notice when older content
  is omitted.
- `manifest.json` and `SHA256SUMS`: archive inventory and integrity records.

The archive is written to a temporary file and atomically renamed only after
all required files have been verified. After extraction, run
`sha256sum -c SHA256SUMS` from the archive root to verify every listed
artifact.

