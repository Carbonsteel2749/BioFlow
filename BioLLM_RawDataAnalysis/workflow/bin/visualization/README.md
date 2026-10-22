# QC visualization

qc_visualization.nf is a standalone Nextflow DSL2 node. It consumes one standardized QC table and does not run FastQC, fastp, Bowtie2, samtools, or host-depletion tools.

Input can be TSV or JSON. sample_id is required and unique. raw_reads, fastp_reads, and host_removed_reads are optional count fields. Other numeric fields are accepted for the heatmap. Optional sample_order is honored; otherwise source row order is preserved.

Counts must be non-negative integers and must satisfy host_removed_reads <= fastp_reads <= raw_reads when available. Invalid numeric values, non-finite values, duplicate samples, missing headers, and invalid ordering fail explicitly.

Each generated figure directory contains figure.png, figure.svg, figure.pdf, figure.data.tsv, and figure.provenance.json. Missing optional inputs skip only the affected figure and record the reason in provenance, summary, and log. Global figure.data.tsv and figure.provenance.json are also written. The style uses DejaVu Sans, a colorblind-friendly palette, and 300 DPI raster output.
