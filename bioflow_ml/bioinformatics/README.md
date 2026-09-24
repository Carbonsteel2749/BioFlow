# Bioinformatics tool wrappers

This package wraps external command-line tools; it does not reimplement them.
The external executable and its reference database must be installed separately.

Available wrappers:

- `qc.FastpTool`: FASTQ quality control and adapter trimming;
- `host_removal.KneadDataTool`: host-read removal for microbiome reads;
- `taxonomy.Kraken2Tool`: metagenomic taxonomic classification;
- `taxonomy.BrackenTool`: abundance re-estimation from Kraken2 reports.

All tools support `run(dry_run=True, ...)`, which returns the generated command without running it. Use this first to verify paths, databases, read length and parameters.

Example:

```python
from bioflow_ml.bioinformatics import Kraken2Tool

result = Kraken2Tool().run(
    dry_run=True,
    database="/data/kraken2_db",
    read1="sample_R1.fastq.gz",
    read2="sample_R2.fastq.gz",
    report="result/sample.kreport",
    output="result/sample.kraken",
    threads=8,
)
print(" ".join(result.command))
```

`KneadData -> Kraken2 -> Bracken` is appropriate for host-associated shotgun metagenomic reads. It is not the default route for normalized RNA-seq matrices or 16S ASV tables.
