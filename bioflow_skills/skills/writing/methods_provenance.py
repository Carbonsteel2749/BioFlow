"""methods-from-provenance: write Methods paragraphs from recorded provenance.

Every sentence is assembled from fact-ledger entries (software versions,
database releases and run parameters). A clause is written only when its facts
exist, so nothing undocumented reaches the Methods. Wet-lab steps, study design
and ethics are never inferred; they are returned as explicit author-input
placeholders.

Each paragraph lists the fact ids it used and the tokens (versions, numbers)
that any later language polish must keep verbatim.
"""

from __future__ import annotations

import re
from typing import Any, Callable

try:  # 标准包调用
    from ...core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from ...registry import register_skill
except ImportError:  # 兼容早期顶层脚本调用
    from core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from registry import register_skill

from .fact_ledger import build_ledger


AUTHOR_INPUT = "AUTHOR_INPUT_NEEDED"


class _Facts:
    def __init__(self, ledger: dict[str, Any]) -> None:
        self.by_id = {f["id"]: f for f in ledger.get("facts", [])}
        self.used: list[str] = []
        self.keep: list[str] = []

    def has(self, *ids: str) -> bool:
        return all(i in self.by_id for i in ids)

    def text(self, fid: str, keep: bool = True) -> str:
        fact = self.by_id[fid]
        self.used.append(fid)
        if keep:
            self.keep.append(str(fact["text"]))
        return str(fact["text"])

    def value(self, fid: str) -> Any:
        self.used.append(fid)
        return self.by_id[fid]["value"]

    def flag(self, fid: str) -> bool:
        return fid in self.by_id and str(self.by_id[fid]["value"]).lower() in {"true", "1"}

    def release(self, fid: str) -> str:
        fact = self.by_id[fid]
        self.used.append(fid)
        value = fact["value"]
        release = value.get("release", "") if isinstance(value, dict) else ""
        if release:
            self.keep.append(release)
        return release

    def take(self) -> tuple[list[str], list[str]]:
        used, keep = list(dict.fromkeys(self.used)), list(dict.fromkeys(self.keep))
        self.used, self.keep = [], []
        return used, keep


def _join(parts: list[str]) -> str:
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _samples(f: _Facts) -> str:
    if not f.has("COHORT.sample_count"):
        return ""
    n = f.text("COHORT.sample_count")
    text = f"The analysis comprised {n} shotgun metagenomic samples"
    if f.has("READQ.sequencing_mode"):
        mode = f.text("READQ.sequencing_mode", keep=False)
        cycles = re.match(r"paired end \((\d+) cycles \+ (\d+) cycles\)", mode)
        if cycles and cycles.group(1) == cycles.group(2):
            text += f" sequenced as paired-end 2 × {cycles.group(1)} bp reads"
            f.keep.append(cycles.group(1))
        elif mode:
            text += f" sequenced in {mode} mode"
    if f.has("COHORT.sample_ids"):
        text += f" (run accessions {f.text('COHORT.sample_ids')})"
    return text + ". All samples were processed with the same versioned workflow."


def _quality(f: _Facts) -> str:
    sentences = []
    if f.has("SW.fastqc"):
        sentences.append(f"Read quality was inspected with FastQC v{f.text('SW.fastqc')}.")
    if not f.has("SW.fastp"):
        return " ".join(sentences)
    clauses = []
    if f.flag("PARAM.fastp_detect_adapter_for_pe"):
        f.used.append("PARAM.fastp_detect_adapter_for_pe")
        clauses.append("paired-end adapter detection")
    ends = [e for e, key in (("5'", "PARAM.fastp_cut_front"), ("3'", "PARAM.fastp_cut_tail")) if f.flag(key)]
    if ends and f.has("PARAM.fastp_cut_window_size", "PARAM.fastp_cut_mean_quality"):
        f.used.extend(["PARAM.fastp_cut_front", "PARAM.fastp_cut_tail"])
        clauses.append(
            f"sliding-window trimming from the {' and '.join(ends)} ends (window size "
            f"{f.text('PARAM.fastp_cut_window_size')}, mean quality "
            f"{f.text('PARAM.fastp_cut_mean_quality')})")
    if f.has("PARAM.fastp_qualified_quality_phred", "PARAM.fastp_unqualified_percent_limit"):
        clauses.append(
            f"removal of reads with more than {f.text('PARAM.fastp_unqualified_percent_limit')}% of bases "
            f"below Phred {f.text('PARAM.fastp_qualified_quality_phred')}")
    if f.has("PARAM.fastp_n_base_limit"):
        clauses.append(f"a limit of {f.text('PARAM.fastp_n_base_limit')} N bases per read")
    if f.flag("PARAM.fastp_trim_poly_g"):
        f.used.append("PARAM.fastp_trim_poly_g")
        clauses.append("poly-G tail trimming")
    if f.has("PARAM.fastp_length_required"):
        clauses.append(f"a minimum read length of {f.text('PARAM.fastp_length_required')} bp")
    text = f"Reads were filtered and trimmed with fastp v{f.text('SW.fastp')}"
    if clauses:
        text += " using " + _join(clauses)
    text += "."
    if f.has("PARAM.fastp_correction") and not f.flag("PARAM.fastp_correction"):
        f.used.append("PARAM.fastp_correction")
        text += " Overlap-based base correction was not applied."
    sentences.append(text)
    return " ".join(sentences)


def _host(f: _Facts) -> str:
    if not f.has("SW.bowtie2"):
        return ""
    reference = f" against the {f.text('HOST.reference')} human reference index" if f.has("HOST.reference") else ""
    preset = f" in {f.text('PARAM.host_bowtie2_preset')} mode" if f.has("PARAM.host_bowtie2_preset") else ""
    text = f"Host-derived reads were identified by aligning the filtered read pairs{reference} with Bowtie2 v{f.text('SW.bowtie2')}{preset}"
    if f.has("SW.samtools"):
        text += f", and alignments were processed with SAMtools v{f.text('SW.samtools')}"
    text += "."
    if f.has("HOST.filter_mode"):
        rule = f.text("HOST.filter_mode", keep=False)
        text += f" {rule[0].upper() + rule[1:]}."
    return text


def _taxonomy(f: _Facts) -> str:
    if not f.has("SW.kraken2"):
        return ""
    text = f"Taxonomic classification was performed with Kraken2 v{f.text('SW.kraken2')}"
    if f.has("DB.taxonomy_reads.kraken2"):
        db = f.by_id["DB.taxonomy_reads.kraken2"]["value"]
        release = f.release("DB.taxonomy_reads.kraken2")
        text += f" against the {db.get('name')} database" + (f" (built {release})" if release else "")
        f.keep.append(str(db.get("name")))
    text += "."
    if f.has("SW.bracken"):
        text += f" Species-level abundances were re-estimated with Bracken v{f.text('SW.bracken')}"
        if f.has("PARAM.read_length"):
            text += f" for a read length of {f.text('PARAM.read_length')} bp"
        text += " and are reported as fractions of classified reads."
    return text


def _function(f: _Facts) -> str:
    if not f.has("SW.humann"):
        return ""
    text = f"Functional profiles were generated with HUMAnN v{f.text('SW.humann')}"
    resources = []
    if f.has("SW.metaphlan"):
        part = f"MetaPhlAn v{f.text('SW.metaphlan')}"
        if f.has("DB.function_reads.metaphlan"):
            part += f" with the {f.release('DB.function_reads.metaphlan')} marker database"
        resources.append(part)
    if f.has("DB.function_reads.humann_nucleotide"):
        resources.append(f"the ChocoPhlAn pangenome database ({f.release('DB.function_reads.humann_nucleotide')})")
    if f.has("DB.function_reads.humann_protein"):
        resources.append(f"the UniRef90 protein database ({f.release('DB.function_reads.humann_protein')})")
    if resources:
        text += " using " + _join(resources)
    text += "."
    mapped = [label for label, key in (("KEGG orthologs", "DB.function_reads.ko_mapping"),
                                       ("Enzyme Commission numbers", "DB.function_reads.ec_mapping")) if f.has(key)]
    if mapped:
        for key in ("DB.function_reads.ko_mapping", "DB.function_reads.ec_mapping"):
            if f.has(key):
                f.used.append(key)
        text += f" UniRef90 gene families were regrouped to {_join(mapped)} with the HUMAnN mapping files"
        if f.has("DB.function_reads.ko_mapping"):
            text += f" ({f.release('DB.function_reads.ko_mapping')})"
        text += ", and MetaCyc pathway abundance and coverage were reported."
    return text


def _derived(f: _Facts) -> str:
    if not f.has("DIV.shannon.range"):
        return ""
    f.used.extend(["DIV.shannon.range", "DIV.simpson.range", "DIV.richness.range"])
    return ("For a descriptive summary, the Shannon index (natural logarithm) and the Gini-Simpson index "
            "were computed from the Bracken species fractions after renormalisation within each sample, and "
            "species richness was counted as the number of species above 0.1% of reads. No statistical "
            "comparison between samples or groups was performed.")


def _workflow(f: _Facts) -> str:
    text = "The workflow was executed with Nextflow"
    if f.has("SW.multiqc"):
        text += f", quality-control reports were aggregated with MultiQC v{f.text('SW.multiqc')}"
    return text + ", and software versions, database releases and input checksums were recorded with the results."


def _author_inputs(ledger: dict[str, Any]) -> str:
    wanted = {m.get("id") for m in ledger.get("missing", [])}
    items = []
    if "study_design" in wanted:
        items.append("study design, participants and sample origin")
    if "wet_lab" in wanted:
        items.append("sample collection and storage, DNA extraction, library preparation and sequencing platform")
    if "ethics" in wanted:
        items.append("ethics approval and informed consent")
    if not items:
        return ""
    return f"[{AUTHOR_INPUT}: {'; '.join(items)}. These are not recorded in the analysis package.]"


SECTIONS: tuple[tuple[str, str, Callable[[_Facts], str]], ...] = (
    ("samples", "Samples and sequencing data", _samples),
    ("quality", "Read quality control", _quality),
    ("host", "Host read removal", _host),
    ("taxonomy", "Taxonomic profiling", _taxonomy),
    ("function", "Functional profiling", _function),
    ("derived", "Descriptive community metrics", _derived),
    ("workflow", "Workflow and provenance", _workflow),
)


def build_methods(ledger: dict[str, Any]) -> dict[str, Any]:
    facts = _Facts(ledger)
    paragraphs = []
    for key, heading, builder in SECTIONS:
        text = builder(facts)
        used, keep = facts.take()
        if text:
            paragraphs.append({"key": key, "heading": heading, "text": text,
                               "fact_ids": used, "keep_tokens": keep})
    placeholder = _author_inputs(ledger)
    if placeholder:
        paragraphs.insert(0, {"key": "author_input", "heading": "Study design and sample processing",
                              "text": placeholder, "fact_ids": [], "keep_tokens": [AUTHOR_INPUT]})
    markdown = "\n\n".join(f"**{p['heading']}.** {p['text']}" for p in paragraphs) + "\n"
    return {"paragraphs": paragraphs, "markdown": markdown,
            "missing": ledger.get("missing", []), "notes": ledger.get("notes", [])}


@register_skill
class MethodsFromProvenanceSkill(BaseSkill):
    spec = SkillSpec(
        name="methods-from-provenance",
        version="1.0.0",
        category=SkillCategory.WRITING,
        description="依据分析流程记录的软件版本、数据库版本和运行参数逐段生成 Methods；只写有记录的步骤，实验与伦理信息标为待作者补充。",
        tags=("writing", "methods", "provenance", "deterministic"),
        input_keys=("ledger", "package_root"),
        output_keys=("paragraphs", "markdown", "missing", "notes"),
    )

    def run(self, skill_input: SkillInput):
        ledger = skill_input.get("ledger")
        if not ledger:
            ledger = build_ledger(skill_input.require("package_root")["package_root"])
        data = build_methods(ledger)
        return self.success(data=data, summary=f"Methods：{len(data['paragraphs'])} 段，均来自分析记录。")
