#!/usr/bin/env bash
set -euo pipefail

: "${MOCK_BIN:?MOCK_BIN is required}"
: "${MOCK_CALL_LOG:?MOCK_CALL_LOG is required}"
mkdir -p "$MOCK_BIN"
export MOCK_CALL_LOG

cat > "$MOCK_BIN/fastqc" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf 'fastqc\t%s\n' "$*" >> "$MOCK_CALL_LOG"
[[ "${1:-}" == "--version" ]] && { printf 'FastQC v0.12-test\n'; exit 0; }
outdir=""; inputs=()
while (($#)); do
  case "$1" in
    --outdir) outdir="$2"; shift 2 ;;
    --threads) shift 2 ;;
    *) inputs+=("$1"); shift ;;
  esac
done
mkdir -p "$outdir"
for input in "${inputs[@]}"; do
  base="$(basename "$input")"; base="${base%.gz}"; base="${base%.fastq}"; base="${base%.fq}"
  printf '<!doctype html><html><body>PASS %s</body></html>\n' "$base" > "$outdir/${base}_fastqc.html"
  python3 - "$outdir/${base}_fastqc.zip" "$base" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w") as archive:
    archive.writestr(f"{sys.argv[2]}_fastqc/summary.txt", "PASS\tBasic Statistics\n")
    archive.writestr(f"{sys.argv[2]}_fastqc/fastqc_data.txt", ">>Basic Statistics\tpass\n")
PY
done
MOCK

cat > "$MOCK_BIN/fastp" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf 'fastp\t%s\n' "$*" >> "$MOCK_CALL_LOG"
[[ "${1:-}" == "--version" ]] && { printf 'fastp 0.23-test\n'; exit 0; }
if [[ "${MOCK_FAIL_TOOL:-}" == "fastp" ]]; then
  printf 'mock fastp forced failure\n' >&2
  exit "${MOCK_FAIL_EXIT_CODE:-42}"
fi

out1=""; out2=""; json=""; html=""
while (($#)); do
  case "$1" in
    --out1) out1="$2"; shift 2 ;;
    --out2) out2="$2"; shift 2 ;;
    --json) json="$2"; shift 2 ;;
    --html) html="$2"; shift 2 ;;
    *) shift ;;
  esac
done
printf '@mock_read/1\nACGTACGT\n+\nIIIIIIII\n' | gzip -c > "$out1"
printf '@mock_read/2\nTGCATGCA\n+\nIIIIIIII\n' | gzip -c > "$out2"
printf '{"summary":{"before_filtering":{"total_reads":2},"after_filtering":{"total_reads":2}}}\n' > "$json"
printf '<!doctype html><html><body>fastp passed</body></html>\n' > "$html"
MOCK

cat > "$MOCK_BIN/bowtie2" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf 'bowtie2\t%s\n' "$*" >> "$MOCK_CALL_LOG"
[[ "${1:-}" == "--version" ]] && { printf 'bowtie2-align-s version 2.5-test\n'; exit 0; }
pattern=""
while (($#)); do
  case "$1" in
    --un-conc-gz) pattern="$2"; shift 2 ;;
    *) shift ;;
  esac
done
if [[ -n "$pattern" ]]; then
  printf '@mock_read/1\nACGTACGT\n+\nIIIIIIII\n' | gzip -c > "${pattern//%/1}"
  printf '@mock_read/2\nTGCATGCA\n+\nIIIIIIII\n' | gzip -c > "${pattern//%/2}"
else
  printf '@HD\tVN:1.6\n'
fi
printf '50.00%% overall alignment rate\n' >&2
MOCK

cat > "$MOCK_BIN/samtools" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf 'samtools\t%s\n' "$*" >> "$MOCK_CALL_LOG"
[[ "${1:-}" == "--version" ]] && { printf 'samtools 1.20-test\n'; exit 0; }
subcommand="$1"; shift
case "$subcommand" in
  view) cat ;;
  fastq)
    r1=""; r2=""
    while (($#)); do
      case "$1" in
        -1) r1="$2"; shift 2 ;;
        -2) r2="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    cat >/dev/null
    printf '@mock_read/1\nACGTACGT\n+\nIIIIIIII\n' > "$r1"
    printf '@mock_read/2\nTGCATGCA\n+\nIIIIIIII\n' > "$r2"
    ;;
esac
MOCK

cat > "$MOCK_BIN/kraken2" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf 'kraken2\t%s\n' "$*" >> "$MOCK_CALL_LOG"
[[ "${1:-}" == "--version" ]] && { printf 'Kraken version 2.1-test\n'; exit 0; }
report=""; output=""
while (($#)); do
  case "$1" in
    --report) report="$2"; shift 2 ;;
    --output) output="$2"; shift 2 ;;
    *) shift ;;
  esac
done
printf '100.00\t1\t1\tS\t562\tEscherichia coli\n' > "$report"
printf 'C\tmock_read\t562\t8\t562:8\n' > "$output"
MOCK

cat > "$MOCK_BIN/bracken" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf 'bracken\t%s\n' "$*" >> "$MOCK_CALL_LOG"
[[ "${1:-}" == "-v" ]] && { printf 'Bracken 2.9-test\n'; exit 0; }
output=""
while (($#)); do
  case "$1" in
    -o) output="$2"; shift 2 ;;
    *) shift ;;
  esac
done
printf 'name\ttaxonomy_id\ttaxonomy_lvl\tkraken_assigned_reads\tadded_reads\tnew_est_reads\tfraction_total_reads\n' > "$output"
printf 'Escherichia coli\t562\tS\t1\t0\t1\t0.5\n' >> "$output"
MOCK

cat > "$MOCK_BIN/humann" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf 'humann\t%s\n' "$*" >> "$MOCK_CALL_LOG"
[[ "${1:-}" == "--version" ]] && { printf 'HUMAnN 3.9-test\n'; exit 0; }
outdir=""; basename=""
while (($#)); do
  case "$1" in
    --output) outdir="$2"; shift 2 ;;
    --output-basename) basename="$2"; shift 2 ;;
    *) shift ;;
  esac
done
mkdir -p "$outdir"
printf '# Gene Family\t%s_Abundance\nGF1\t1.25\n' "$basename" > "$outdir/${basename}_genefamilies.tsv"
printf '# Pathway\t%s_Abundance\nPWY1\t0.40\n' "$basename" > "$outdir/${basename}_pathabundance.tsv"
printf '# Pathway\t%s_Coverage\nPWY1\t0.80\n' "$basename" > "$outdir/${basename}_pathcoverage.tsv"
MOCK

cat > "$MOCK_BIN/humann_regroup_table" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf 'humann_regroup_table\t%s\n' "$*" >> "$MOCK_CALL_LOG"
input=""; custom=""; output=""
while (($#)); do
  case "$1" in
    --input) input="$2"; shift 2 ;;
    --custom) custom="$2"; shift 2 ;;
    --output) output="$2"; shift 2 ;;
    *) printf 'unexpected humann_regroup_table argument: %s\n' "$1" >&2; exit 64 ;;
  esac
done
[[ -s "$input" && -f "$custom" && -n "$output" ]]
case "$(basename "$custom")" in
  map_ko_uniref90.txt.gz)
    printf '# Gene Family\tS01_Abundance\nK00001\t0.30\n' > "$output"
    ;;
  map_level4ec_uniref90.txt.gz)
    printf '# Gene Family\tS01_Abundance\n1.1.1.1\t0.20\n' > "$output"
    ;;
  *) printf 'unexpected custom mapping: %s\n' "$custom" >&2; exit 64 ;;
esac
MOCK

cat > "$MOCK_BIN/multiqc" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf 'multiqc\t%s\n' "$*" >> "$MOCK_CALL_LOG"
[[ "${1:-}" == "--version" ]] && { printf 'multiqc, version 1.25-test\n'; exit 0; }
outdir=""; filename="multiqc_report.html"
while (($#)); do
  case "$1" in
    --outdir) outdir="$2"; shift 2 ;;
    --filename) filename="$2"; shift 2 ;;
    *) shift ;;
  esac
done
mkdir -p "$outdir/multiqc_data"
printf '<!doctype html><html><body>MultiQC integration report</body></html>\n' > "$outdir/$filename"
printf 'sample\tstatus\nall\tPASS\n' > "$outdir/multiqc_data/multiqc_general_stats.txt"
MOCK

cat > "$MOCK_BIN/metaphlan" <<'MOCK'
#!/usr/bin/env bash
[[ "${1:-}" == "--version" ]] && printf 'MetaPhlAn 4-test\n'
MOCK

cat > "$MOCK_BIN/metawrap" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf 'metawrap\t%s\n' "$*" >> "$MOCK_CALL_LOG"
module="$1"; shift
outdir=""; completeness=70; contamination=5
while (($#)); do
  case "$1" in
    -o) outdir="$2"; shift 2 ;;
    -c) completeness="$2"; shift 2 ;;
    -x) contamination="$2"; shift 2 ;;
    *) shift ;;
  esac
done
mkdir -p "$outdir"
case "$module" in
  assembly)
    printf '>contig_1\nACGTACGTACGT\n' > "$outdir/final_assembly.fasta"
    printf '<!doctype html><html><body>assembly</body></html>\n' > "$outdir/assembly_report.html"
    ;;
  binning)
    for name in metabat2_bins maxbin2_bins concoct_bins; do
      mkdir -p "$outdir/$name"; printf '>bin_1\nACGTACGT\n' > "$outdir/$name/bin.1.fa"
    done
    ;;
  bin_refinement)
    mkdir -p "$outdir/metawrap_${completeness}_${contamination}_bins"
    printf '>bin_1\nACGTACGT\n' > "$outdir/metawrap_${completeness}_${contamination}_bins/bin.1.fa"
    printf 'bin_id\tcompleteness\tcontamination\nbin.1\t95\t1\n' > "$outdir/metawrap_${completeness}_${contamination}_bins.stats"
    ;;
  quant_bins)
    printf 'bin_id\tS01\tS02\nbin.1\t0.75\t0.25\n' > "$outdir/bin_abundance_table.tab"
    ;;
  reassemble_bins)
    mkdir -p "$outdir/reassembled_bins"
    printf '>bin_1\nACGTACGTACGT\n' > "$outdir/reassembled_bins/bin.1.fa"
    printf 'bin_id\tstatus\nbin.1\tPASS\n' > "$outdir/reassembled_bins.stats"
    ;;
  classify_bins)
    printf 'bin_id\ttaxonomy\nbin.1\td__Bacteria;p__Proteobacteria\n' > "$outdir/bin_taxonomy.tab"
    ;;
  annotate_bins)
    mkdir -p "$outdir/bin_funct_annotations"
    printf '##gff-version 3\nbin.1\tmock\tgene\t1\t8\t.\t+\t.\tID=gene1\n' > "$outdir/bin_funct_annotations/bin.1.gff"
    ;;
  *) printf 'unknown metawrap module: %s\n' "$module" >&2; exit 64 ;;
esac
MOCK

chmod +x "$MOCK_BIN"/*
