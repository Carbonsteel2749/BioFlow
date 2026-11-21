"""Section skills for Data Descriptor articles (Scientific Data style).

Deterministic (no model): data-records-writer, usage-notes-writer, code-availability-writer,
data-availability-writer — they only restate the fact ledger and the result-package
inventory, and leave AUTHOR_INPUT_NEEDED where the system has no information.

Model-written with checking: background-summary-writer, technical-validation-writer —
section-planner + paragraph_loop (write, verify sentence by sentence, revise).

Payload contract: see article_writing/section_library/SECTION_SKILLS.md.
"""

from __future__ import annotations

import re
from typing import Any

try:  # 标准包调用
    from ...core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from ...registry import register_skill
except ImportError:  # 兼容早期顶层脚本调用
    from core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from registry import register_skill

from .paragraph_loop import write_plan
from .section_planner import build_plan

AUTHOR_INPUT = "AUTHOR_INPUT_NEEDED"
PROJECT = re.compile(r"\b(PRJ(?:EB|NA|DB)\d+|[EDS]RP\d{5,})\b")
ARCHIVES = {"PRJEB": "the European Nucleotide Archive (ENA)", "ERP": "the European Nucleotide Archive (ENA)",
            "PRJNA": "the NCBI Sequence Read Archive (SRA)", "SRP": "the NCBI Sequence Read Archive (SRA)",
            "PRJDB": "the DDBJ Sequence Read Archive (DRA)", "DRP": "the DDBJ Sequence Read Archive (DRA)"}
TABLES = {
    "read_counts.tsv": "per-sample read counts after each processing step (raw reads, reads and read pairs after "
                       "fastp, read pairs after host removal and host read pairs removed)",
    "species_abundance.tsv": "species-level taxonomic abundances for every sample, one row per sample and taxon",
    "pathway_abundance.tsv": "MetaCyc pathway abundances from HUMAnN for every sample",
    "gene_families.tsv": "UniRef90 gene-family abundances from HUMAnN for every sample",
}
FOLDERS = {
    "pathway_coverage": "MetaCyc pathway coverage tables",
    "ko_abundance": "KEGG ortholog abundance tables",
    "ec_abundance": "Enzyme Commission number abundance tables",
    "by_sample": "per-sample copies of the taxonomic and functional tables",
}
PROVENANCE = {"software_versions.tsv": "the software versions", "database_versions.tsv": "the reference database "
              "releases", "parameters.json": "the run parameters", "input_checksums.tsv": "the input file checksums"}


def _facts(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["id"]: f for f in (payload.get("ledger") or {}).get("facts", [])}


def _text(facts, fid, default=""):
    return str(facts[fid]["text"]) if fid in facts else default


def _project(payload: dict[str, Any]) -> str:
    brief = payload.get("brief") or {}
    for value in (brief.get("title"), brief.get("research_question")):
        match = PROJECT.search(str(value or ""))
        if match:
            return match.group(1)
    return ""


def _archive(project: str) -> str:
    for prefix, name in ARCHIVES.items():
        if project.startswith(prefix):
            return name
    return "a public sequence archive"


def _size(n: int) -> str:
    for unit, scale in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= scale:
            return f"{n / scale:.1f} {unit}"
    return f"{n} bytes"


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def raw_data_sentence(payload: dict[str, Any]) -> str:
    facts = _facts(payload)
    runs = _text(facts, "COHORT.sample_ids")
    project = _project(payload)
    mode = _text(facts, "READQ.sequencing_mode")
    reads = "paired-end shotgun metagenomic reads" if "paired" in mode else "shotgun metagenomic reads"
    if project:
        text = f"The raw {reads} are available from {_archive(project)} under project accession {project}"
    else:
        text = f"The raw {reads} are available under [{AUTHOR_INPUT}: archive and project accession]"
    if runs:
        text += f", with run accessions {runs}"
    return text + "."


def data_records(payload: dict[str, Any]) -> dict[str, Any]:
    materials = payload.get("materials") or {}
    tables = materials.get("tables") or []
    paragraphs = [raw_data_sentence(payload)]
    main, folders, provenance = [], {}, []
    for table in tables:
        path = str(table.get("file") or "")
        name = path.rsplit("/", 1)[-1]
        parts = path.split("/")
        if len(parts) == 2 and name in TABLES:
            main.append(f"{name} ({_size(int(table.get('size_bytes') or 0))}) contains {TABLES[name]}")
        elif len(parts) > 2 and parts[1] in FOLDERS:
            folders.setdefault(parts[1], 0)
            folders[parts[1]] += 1
    for item in materials.get("facts") or []:
        source = str((item.get("source") or {}).get("file") or "")
        name = source.rsplit("/", 1)[-1]
        if name in PROVENANCE and name not in provenance:
            provenance.append(name)
    if main:
        paragraphs.append("The processed results are tab-separated text files. " + " ".join(
            f"The file {s}." for s in main))
    if folders:
        paragraphs.append("In addition, the result package includes " + _join(
            [f"{FOLDERS[k]} (folder {k}, {n} files)" for k, n in folders.items()]) + ".")
    if provenance:
        paragraphs.append("Provenance files record " + _join(
            [f"{PROVENANCE[name]} ({name})" for name in provenance]) + ".")
    paragraphs.append(f"[{AUTHOR_INPUT}: repository and accession or DOI where the processed tables are deposited.]")
    warnings = [] if _project(payload) else ["data_records: no project accession found in the brief"]
    return {"markdown": "\n\n".join(paragraphs), "warnings": warnings}


def usage_notes(payload: dict[str, Any]) -> dict[str, Any]:
    facts = _facts(payload)
    notes = []
    if "TAX.classifier" in facts:
        notes.append(f"Species abundances in species_abundance.tsv were estimated with "
                     f"{_text(facts, 'TAX.classifier')} and are given as fractions of classified reads within each "
                     "sample, so they should be compared as relative values.")
    if any(k.startswith("FUNC.") for k in facts):
        notes.append("HUMAnN pathway and gene-family values are reported in the units written by HUMAnN and are not "
                     "percentages; the rows UNMAPPED and UNINTEGRATED summarise reads that could not be assigned "
                     "and are not biological pathways.")
    notes.append("read_counts.tsv reports single reads for the raw and fastp-filtered data and read pairs after "
                 "pairing and host removal; these units should not be mixed.")
    db = facts.get("DB.taxonomy_reads.kraken2")
    if db and "8 GB" in str(db["value"].get("name", "")):
        notes.append("Taxonomic classification used the size-capped Kraken2 Standard 8 GB database; users "
                     "interested in rare taxa may want to reclassify the reads against a larger database.")
    notes.append("The result package contains no participant metadata or group labels. Samples can be linked to "
                 "external metadata through their run accessions.")
    notes.append("Software versions, database releases and parameters in the provenance files allow the processing "
                 "to be repeated or compared with other pipelines.")
    return {"markdown": "\n\n".join(notes)}


def code_availability(payload: dict[str, Any]) -> dict[str, Any]:
    facts = _facts(payload)
    tools = [f"{name} v{_text(facts, 'SW.' + key)}" for key, name in (
        ("fastqc", "FastQC"), ("fastp", "fastp"), ("bowtie2", "Bowtie2"), ("samtools", "SAMtools"),
        ("kraken2", "Kraken2"), ("bracken", "Bracken"), ("metaphlan", "MetaPhlAn"), ("humann", "HUMAnN"),
        ("multiqc", "MultiQC")) if f"SW.{key}" in facts]
    text = "The data were processed with a Nextflow workflow"
    if tools:
        text += " that ran " + _join(tools)
    text += (". All run parameters were recorded in the provenance file parameters.json released with the results. "
             f"[{AUTHOR_INPUT}: public repository URL, version or commit, and licence of the workflow code.]")
    return {"markdown": text}


def data_availability(payload: dict[str, Any]) -> dict[str, Any]:
    text = (raw_data_sentence(payload) + " " +
            f"[{AUTHOR_INPUT}: where the processed tables are deposited, and any access conditions.]")
    return {"markdown": text}


def _planned(payload: dict[str, Any], section: str, use_figures: bool) -> dict[str, Any]:
    model = payload.get("model")
    if model is None:
        raise ValueError(f"{section} needs a language model")
    plan = build_plan(payload.get("ledger") or {}, section, payload.get("brief") or {},
                      payload.get("cards") or [], payload.get("figures") if use_figures else [])
    result = write_plan(plan, model, section=section, title=str((payload.get("brief") or {}).get("title") or ""),
                        style_rules=str(payload.get("style_rules") or ""))
    return {"markdown": result["markdown"], "warnings": result["warnings"], "citations": result["citations"],
            "figures": [{"figure_id": f["figure_id"], "number": f["number"]} for f in result["figures"]],
            "paragraphs": result["paragraphs"]}


def _skill(name: str, description: str, builder, *, tags=("writing", "data-descriptor")):
    class _Section(BaseSkill):
        spec = SkillSpec(name=name, version="1.0.0", category=SkillCategory.WRITING, description=description,
                         tags=tags, input_keys=("section", "ledger", "materials", "brief", "cards", "model", "figures"),
                         output_keys=("markdown", "warnings"))

        def run(self, skill_input: SkillInput):
            data = builder(dict(skill_input.payload))
            return self.success(data=data, summary=f"{name}: {len(data['markdown'].split())} words")

    _Section.__name__ = "".join(part.capitalize() for part in name.split("-")) + "Skill"
    return register_skill(_Section)


DataRecordsSkill = _skill("data-records-writer", "数据记录：按结果包清单和登录号描述原始数据与处理后表格（代码生成）。", data_records)
UsageNotesSkill = _skill("usage-notes-writer", "使用说明：数据单位、不可比较的字段、数据库局限和元数据缺失（代码生成）。", usage_notes)
CodeAvailabilitySkill = _skill("code-availability-writer", "代码可用性：流程与软件版本，仓库地址待作者补充（代码生成）。", code_availability)
DataAvailabilitySkill = _skill("data-availability-writer", "数据可用性：原始数据登录号，处理后数据存放位置待作者补充（代码生成）。", data_availability)
BackgroundSummarySkill = _skill("background-summary-writer", "背景与概述：数据描述论文开篇，逐段写作并逐句核对。",
                                lambda p: _planned(p, "background_summary", False))
TechnicalValidationSkill = _skill("technical-validation-writer", "技术验证：从数据质量角度逐段写作并逐句核对，可插入绘图模块的图。",
                                  lambda p: _planned(p, "technical_validation", True))
