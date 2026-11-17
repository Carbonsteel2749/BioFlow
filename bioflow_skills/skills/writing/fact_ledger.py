"""bio-fact-ledger: compute every number a manuscript may cite, in code.

The writing model never does arithmetic. This skill reads a BioLLM metagenomic
result package and returns a ledger of facts. Each fact has a readable id
(for example ``QC.raw_reads.max``), the exact value, a display string and the
source file it came from. Writing skills cite facts by id; the number guard
checks prose against the display strings.

Only descriptive quantities are produced. No group comparison, test statistic
or causal statement is derived here.
"""

from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path
from statistics import median
from typing import Any, Iterable

try:  # 标准包调用
    from ...core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from ...registry import register_skill
except ImportError:  # 兼容早期顶层脚本调用
    from core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from registry import register_skill


NON_PATHWAYS = {"UNMAPPED", "UNINTEGRATED", "UNGROUPED"}
QC_COLUMNS = {
    "raw_reads": ("raw reads", "reads"),
    "post_fastp_reads": ("reads after fastp", "reads"),
    "post_fastp_pairs": ("read pairs after fastp", "read pairs"),
    "post_host_pairs": ("read pairs after host removal", "read pairs"),
    "host_removed_pairs": ("host read pairs removed", "read pairs"),
}
PREVALENCE_THRESHOLD = 0.001  # fraction of reads
_VERSION = re.compile(r"v?(\d+(?:\.\d+)+[a-z]?)")


def fmt_int(value: float) -> str:
    return f"{int(round(value)):,}"


def fmt_pct(fraction: float, digits: int = 2) -> str:
    return f"{fraction * 100:.{digits}f}%"


def fmt_float(value: float, digits: int = 3) -> str:
    return f"{value:.{digits}f}"


def _rows(path: Path) -> Iterable[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        yield from csv.DictReader(stream, delimiter="\t")


class Ledger:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.facts: list[dict[str, Any]] = []
        self.notes: list[str] = []
        self._ids: set[str] = set()

    def rel(self, path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            return str(path.resolve().relative_to(self.root.parent.parent.resolve()))
        except ValueError:
            return path.name

    def add(self, fid: str, group: str, label: str, value: Any, text: str, *,
            unit: str = "", sample: str | None = None, source: Path | None = None,
            derivation: str = "") -> None:
        if fid in self._ids:
            return
        self._ids.add(fid)
        self.facts.append({
            "id": fid, "group": group, "label": label, "value": value, "text": text,
            "unit": unit, "sample_id": sample, "source": self.rel(source),
            "derivation": derivation,
        })

    def range_facts(self, prefix: str, group: str, label: str, per_sample: dict[str, float],
                    fmt, unit: str, source: Path | None, derivation: str = "") -> None:
        if not per_sample:
            return
        items = sorted(per_sample.items(), key=lambda kv: kv[1])
        low_s, low = items[0]
        high_s, high = items[-1]
        values = [v for _, v in items]
        self.add(f"{prefix}.min", group, f"lowest {label}", low, f"{fmt(low)} ({low_s})",
                 unit=unit, sample=low_s, source=source, derivation=derivation or "minimum across samples")
        self.add(f"{prefix}.max", group, f"highest {label}", high, f"{fmt(high)} ({high_s})",
                 unit=unit, sample=high_s, source=source, derivation=derivation or "maximum across samples")
        self.add(f"{prefix}.median", group, f"median {label}", median(values), fmt(median(values)),
                 unit=unit, source=source, derivation=derivation or "median across samples")
        self.add(f"{prefix}.range", group, f"range of {label}", [low, high],
                 f"{fmt(low)} to {fmt(high)}", unit=unit, source=source,
                 derivation=derivation or "minimum to maximum across samples")


# ---------------------------------------------------------------------------
#  locating files
# ---------------------------------------------------------------------------
def report_root(path: Path) -> Path:
    path = Path(path)
    if (path / "tables/read_counts.tsv").is_file():
        return path
    candidates = [p for p in sorted((path / "nodes").glob("*")) if (p / "tables/read_counts.tsv").is_file()]
    if len(candidates) != 1:
        raise ValueError("expected exactly one report node with tables/read_counts.tsv")
    return candidates[0]


def _find(root: Path, pattern: str) -> list[Path]:
    """Look inside the report root, then in sibling workflow nodes."""
    found = sorted(root.glob(f"qc/*/{pattern}")) or sorted(root.glob(f"**/{pattern}"))
    if not found and root.parent.name == "nodes":
        found = sorted(root.parent.glob(f"*/*/*/{pattern}"))
    return found


# ---------------------------------------------------------------------------
#  sections of the ledger
# ---------------------------------------------------------------------------
def _qc(ledger: Ledger, root: Path) -> list[str]:
    path = root / "tables/read_counts.tsv"
    table = list(_rows(path))
    samples = [row["sample_id"].strip() for row in table]
    ledger.add("COHORT.sample_count", "cohort", "number of samples", len(samples), str(len(samples)),
               unit="samples", source=path)
    ledger.add("COHORT.sample_ids", "cohort", "sample accessions", samples, ", ".join(samples), source=path)
    columns: dict[str, dict[str, float]] = {c: {} for c in QC_COLUMNS}
    for row in table:
        sample = row["sample_id"].strip()
        for column in QC_COLUMNS:
            if row.get(column) not in (None, ""):
                columns[column][sample] = float(row[column])
    for column, (label, unit) in QC_COLUMNS.items():
        per = columns[column]
        for sample, value in per.items():
            ledger.add(f"QC.{column}.{sample}", "qc_sample", f"{label} in {sample}", int(value),
                       fmt_int(value), unit=unit, sample=sample, source=path)
        ledger.range_facts(f"QC.{column}", "qc_summary", label, per, fmt_int, unit, path)
        if per:
            total = sum(per.values())
            ledger.add(f"QC.{column}.total", "qc_summary", f"total {label}", int(total), fmt_int(total),
                       unit=unit, source=path, derivation="sum across samples")
    raw, kept = columns["raw_reads"], columns["post_fastp_reads"]
    retained = {s: kept[s] / raw[s] for s in raw if s in kept and raw[s]}
    ledger.range_facts("QC.fastp_retained_pct", "qc_summary", "percentage of reads retained by fastp",
                       retained, fmt_pct, "%", path, "post_fastp_reads / raw_reads")
    pairs, removed = columns["post_fastp_pairs"], columns["host_removed_pairs"]
    host = {s: removed[s] / pairs[s] for s in pairs if s in removed and pairs[s]}
    ledger.range_facts("QC.host_removed_pct", "qc_summary", "percentage of read pairs removed as host",
                       host, lambda x: fmt_pct(x, 4), "%", path, "host_removed_pairs / post_fastp_pairs")
    return samples


def _fastp(ledger: Ledger, root: Path) -> None:
    files = _find(root, "*.fastp.json")
    if not files:
        ledger.notes.append("fastp JSON reports not found; per-base quality facts omitted.")
        return
    q30_before, q30_after, gc, dup, insert, length = {}, {}, {}, {}, {}, {}
    sequencing = ""
    for path in files:
        sample = path.name.split(".fastp.json")[0]
        data = json.loads(path.read_text(encoding="utf-8"))
        summary = data.get("summary", {})
        before, after = summary.get("before_filtering", {}), summary.get("after_filtering", {})
        sequencing = summary.get("sequencing", sequencing)
        if "q30_rate" in before:
            q30_before[sample] = float(before["q30_rate"])
        if "q30_rate" in after:
            q30_after[sample] = float(after["q30_rate"])
        if "gc_content" in after:
            gc[sample] = float(after["gc_content"])
        if "read1_mean_length" in after:
            length[sample] = float(after["read1_mean_length"])
        if isinstance(data.get("duplication"), dict) and "rate" in data["duplication"]:
            dup[sample] = float(data["duplication"]["rate"])
        if isinstance(data.get("insert_size"), dict) and "peak" in data["insert_size"]:
            insert[sample] = float(data["insert_size"]["peak"])
        for key, per in (("q30_before", q30_before), ("q30_after", q30_after)):
            if sample in per:
                ledger.add(f"READQ.{key}.{sample}", "read_quality_sample",
                           f"Q30 base fraction {'before' if key.endswith('before') else 'after'} filtering in {sample}",
                           per[sample], fmt_pct(per[sample]), unit="%", sample=sample, source=path)
    if sequencing:
        ledger.add("READQ.sequencing_mode", "read_quality", "sequencing mode reported by fastp",
                   sequencing, sequencing, source=files[0])
    ledger.range_facts("READQ.q30_before", "read_quality", "Q30 base fraction before filtering",
                       q30_before, fmt_pct, "%", files[0], "fastp summary.before_filtering.q30_rate")
    ledger.range_facts("READQ.q30_after", "read_quality", "Q30 base fraction after filtering",
                       q30_after, fmt_pct, "%", files[0], "fastp summary.after_filtering.q30_rate")
    ledger.range_facts("READQ.gc_after", "read_quality", "GC content after filtering",
                       gc, fmt_pct, "%", files[0], "fastp summary.after_filtering.gc_content")
    ledger.range_facts("READQ.duplication", "read_quality", "duplication rate estimated by fastp",
                       dup, fmt_pct, "%", files[0], "fastp duplication.rate")
    ledger.range_facts("READQ.insert_peak", "read_quality", "insert size peak",
                       insert, fmt_int, "bp", files[0], "fastp insert_size.peak")
    ledger.range_facts("READQ.mean_length_after", "read_quality", "mean read 1 length after filtering",
                       length, fmt_int, "bp", files[0], "fastp summary.after_filtering.read1_mean_length")


def _host(ledger: Ledger, root: Path) -> None:
    files = _find(root, "*.host_depletion.metrics.json")
    if not files:
        return
    data = json.loads(files[0].read_text(encoding="utf-8"))
    prefix = str(data.get("host_index_prefix") or "")
    parts = [p for p in prefix.replace("\\", "/").split("/") if p]
    reference = ""
    if "host" in parts and parts.index("host") + 1 < len(parts):
        reference = parts[parts.index("host") + 1]
    elif parts:
        reference = parts[-1]
    if reference:
        ledger.add("HOST.reference", "host", "host reference genome index", reference, reference,
                   source=files[0], derivation="directory name of the bowtie2 host index (path redacted)")
    if data.get("filter_mode"):
        ledger.add("HOST.filter_mode", "host", "host read filter rule", data["filter_mode"],
                   "read pairs were retained only when both mates were unmapped"
                   if data["filter_mode"] == "strict_both_unmapped" else str(data["filter_mode"]),
                   source=files[0])
    rates = {}
    for path in files:
        item = json.loads(path.read_text(encoding="utf-8"))
        if "bowtie2_overall_alignment_rate_pct" in item:
            rates[item.get("sample_id") or path.name.split(".")[0]] = float(item["bowtie2_overall_alignment_rate_pct"]) / 100
    ledger.range_facts("HOST.alignment_rate", "host", "bowtie2 overall alignment rate to the host index",
                       rates, lambda x: fmt_pct(x, 2), "%", files[0])


def _taxonomy(ledger: Ledger, root: Path, samples: list[str]) -> None:
    path = root / "tables/species_abundance.tsv"
    if not path.is_file():
        ledger.notes.append("species_abundance.tsv not found; taxonomy facts omitted.")
        return
    per: dict[str, dict[str, float]] = {s: {} for s in samples}
    source_name = release = ""
    for row in _rows(path):
        if (row.get("taxonomy_rank") or "").strip() != "species":
            continue
        if (row.get("abundance_unit") or "").strip() != "fraction_of_reads":
            continue
        sample = (row.get("sample_id") or "").strip()
        if sample not in per:
            continue
        try:
            value = float(row.get("abundance") or "")
        except ValueError:
            continue
        if not math.isfinite(value) or value <= 0:
            continue
        name = (row.get("taxonomy") or "").strip()
        per[sample][name] = per[sample].get(name, 0.0) + value
        source_name = source_name or (row.get("classification_source") or "").strip()
        release = release or (row.get("database_release") or "").strip()
    if source_name:
        ledger.add("TAX.classifier", "taxonomy", "source of species abundances", source_name, source_name, source=path)
    richness, shannon, simpson = {}, {}, {}
    for sample, taxa in per.items():
        if not taxa:
            continue
        ranked = sorted(taxa.items(), key=lambda kv: kv[1], reverse=True)
        top, value = ranked[0]
        ledger.add(f"TAX.top1.{sample}", "taxonomy_sample", f"most abundant species in {sample}",
                   {"taxon": top, "fraction": value}, f"{top} ({fmt_pct(value)} of reads)",
                   unit="fraction_of_reads", sample=sample, source=path,
                   derivation="highest Bracken species fraction within the sample")
        ledger.add(f"TAX.top3.{sample}", "taxonomy_sample", f"three most abundant species in {sample}",
                   [{"taxon": t, "fraction": v} for t, v in ranked[:3]],
                   "; ".join(f"{t} ({fmt_pct(v)})" for t, v in ranked[:3]),
                   unit="fraction_of_reads", sample=sample, source=path)
        total = sum(taxa.values())
        props = [v / total for v in taxa.values()]
        richness[sample] = float(sum(1 for v in taxa.values() if v >= PREVALENCE_THRESHOLD))
        shannon[sample] = -sum(p * math.log(p) for p in props if p > 0)
        simpson[sample] = 1 - sum(p * p for p in props)
        ledger.add(f"DIV.shannon.{sample}", "diversity_sample", f"Shannon index in {sample}",
                   shannon[sample], fmt_float(shannon[sample], 2), sample=sample, source=path,
                   derivation="computed by the writing module from Bracken species fractions renormalised to 1")
    ledger.range_facts("DIV.shannon", "diversity", "Shannon index", shannon, lambda x: fmt_float(x, 2), "",
                       path, "natural-log Shannon index from Bracken species fractions, computed by the writing module")
    ledger.range_facts("DIV.simpson", "diversity", "Gini-Simpson index", simpson, lambda x: fmt_float(x, 3), "",
                       path, "1 - sum(p^2) from Bracken species fractions, computed by the writing module")
    ledger.range_facts("DIV.richness", "diversity", f"number of species above {fmt_pct(PREVALENCE_THRESHOLD, 1)} of reads",
                       richness, fmt_int, "species", path, "count of species with fraction >= 0.001")
    # Cohort view: mean fraction (absent = 0) and prevalence across samples.
    present = [s for s in per if per[s]]
    if present:
        names = {name for s in present for name in per[s]}
        means = {n: sum(per[s].get(n, 0.0) for s in present) / len(present) for n in names}
        ranked = sorted(means.items(), key=lambda kv: kv[1], reverse=True)[:10]
        for rank, (name, value) in enumerate(ranked, 1):
            prevalence = sum(1 for s in present if per[s].get(name, 0.0) >= PREVALENCE_THRESHOLD)
            ledger.add(f"TAX.cohort_top.{rank}", "taxonomy_cohort", f"species ranked {rank} by mean fraction",
                       {"taxon": name, "mean_fraction": value, "prevalence": prevalence},
                       f"{name} (mean {fmt_pct(value)} of reads; above {fmt_pct(PREVALENCE_THRESHOLD, 1)} in {prevalence} of {len(present)} samples)",
                       unit="fraction_of_reads", source=path,
                       derivation="mean of per-sample fractions, absent species counted as 0")
        dominant: dict[str, list[str]] = {}
        for s in present:
            top = max(per[s].items(), key=lambda kv: kv[1])[0]
            dominant.setdefault(top, []).append(s)
        ledger.add("TAX.dominant_summary", "taxonomy_cohort", "species that were most abundant in each sample",
                   dominant, "; ".join(f"{t} in {len(v)} sample{'s' if len(v) > 1 else ''} ({', '.join(v)})"
                                      for t, v in sorted(dominant.items(), key=lambda kv: -len(kv[1]))),
                   source=path)


def _function(ledger: Ledger, root: Path, samples: list[str]) -> None:
    path = root / "tables/pathway_abundance.tsv"
    if not path.is_file():
        return
    detected: dict[str, int] = {}
    unmapped: dict[str, float] = {}
    totals: dict[str, float] = {}
    for row in _rows(path):
        sample = (row.get("sample_id") or "").strip()
        fid = (row.get("function_id") or "").strip()
        if sample not in samples or "|" in fid:
            continue
        try:
            value = float(row.get("abundance") or "")
        except ValueError:
            continue
        totals[sample] = totals.get(sample, 0.0) + value
        if fid in NON_PATHWAYS:
            if fid == "UNMAPPED":
                unmapped[sample] = value
            continue
        if value > 0:
            detected[sample] = detected.get(sample, 0) + 1
    ledger.range_facts("FUNC.pathways_detected", "function", "number of MetaCyc pathways with non-zero HUMAnN abundance",
                       {s: float(v) for s, v in detected.items()}, fmt_int, "pathways", path,
                       "unstratified rows excluding UNMAPPED/UNINTEGRATED")
    share = {s: unmapped[s] / totals[s] for s in unmapped if totals.get(s)}
    ledger.range_facts("FUNC.unmapped_share", "function", "UNMAPPED share of the unstratified HUMAnN pathway total",
                       share, fmt_pct, "%", path, "UNMAPPED / sum of unstratified pathway rows")


def _provenance(ledger: Ledger, root: Path) -> None:
    prov = root / "provenance"
    path = prov / "software_versions.tsv"
    if path.is_file():
        for row in _rows(path):
            tool, raw = (row.get("tool") or "").strip(), (row.get("version") or "").strip()
            match = _VERSION.search(raw)
            if not tool:
                continue
            if not match:
                ledger.notes.append(f"version of {tool} could not be parsed from {raw!r}")
                continue
            ledger.add(f"SW.{tool}", "software", f"{tool} version", match.group(1), match.group(1), source=path)
    path = prov / "database_versions.tsv"
    if path.is_file():
        for row in _rows(path):
            section = (row.get("section") or "").strip()
            name, release = (row.get("database_name") or "").strip(), (row.get("release") or "").strip()
            if section and name:
                ledger.add(f"DB.{section}", "database", f"database used by {section}",
                           {"name": name, "release": release}, f"{name} (release {release})" if release else name,
                           source=path)
    path = prov / "parameters.json"
    if path.is_file():
        params = json.loads(path.read_text(encoding="utf-8"))
        wanted = [k for k in params if k.startswith("fastp_") or k.startswith("host_")] + \
                 ["read_length", "threads", "enable_mags", "enable_reassembly"]
        for key in wanted:
            value = params.get(key)
            if value is None or (isinstance(value, str) and "[HOME]" in value):
                continue
            ledger.add(f"PARAM.{key}", "parameter", key.replace("_", " "), value,
                       str(value).lower() if isinstance(value, bool) else str(value), source=path)
    path = prov / "run.json"
    if path.is_file():
        run = json.loads(path.read_text(encoding="utf-8"))
        if run.get("final_status"):
            ledger.add("RUN.status", "run", "workflow final status", run["final_status"], run["final_status"], source=path)


def build_ledger(package: Path | str) -> dict[str, Any]:
    root = report_root(Path(package)).resolve()
    ledger = Ledger(root)
    samples = _qc(ledger, root)
    _fastp(ledger, root)
    _host(ledger, root)
    _taxonomy(ledger, root, samples)
    _function(ledger, root, samples)
    _provenance(ledger, root)
    missing = [
        {"id": "study_design", "message": "Study design, sample origin, groups and confounders are not in the package."},
        {"id": "wet_lab", "message": "Sample collection, storage, DNA extraction and library preparation are not in the package."},
        {"id": "ethics", "message": "Ethics approval and consent are not in the package."},
        {"id": "statistics", "message": "No group comparison or statistical test was run; do not report differences or significance."},
    ]
    if not any(f["id"].startswith("FIG.") for f in ledger.facts) and not list((root / "figures").glob("*")):
        missing.append({"id": "figures", "message": "No manuscript figures were produced by the workflow."})
    return {"schema_version": 1, "package_root": str(root), "sample_ids": samples,
            "facts": ledger.facts, "missing": missing, "notes": ledger.notes}


def render_facts(facts: Iterable[dict[str, Any]]) -> str:
    """One fact per line, id first, for prompts."""
    return "\n".join(f"[{f['id']}] {f['label']}: {f['text']}" for f in facts)


def select(ledger: dict[str, Any], groups: Iterable[str]) -> list[dict[str, Any]]:
    wanted = set(groups)
    return [f for f in ledger.get("facts", []) if f.get("group") in wanted]


@register_skill
class BioFactLedgerSkill(BaseSkill):
    spec = SkillSpec(
        name="bio-fact-ledger",
        version="1.0.0",
        category=SkillCategory.WRITING,
        description="从宏基因组结果包中用代码计算论文可引用的全部数值，生成带编号和来源的事实台账；大模型只引用、不计算。",
        tags=("writing", "metagenomics", "facts", "deterministic"),
        input_keys=("package_root",),
        output_keys=("facts", "missing", "notes", "sample_ids"),
    )

    def run(self, skill_input: SkillInput):
        package = skill_input.require("package_root")["package_root"]
        data = build_ledger(package)
        return self.success(data=data, summary=f"事实台账：{len(data['facts'])} 条事实，{len(data['sample_ids'])} 个样本。")
