"""section-planner: paragraph plans for descriptive metagenomic chapters.

A small model writes better when each call has one job. This skill decides, in
code, how a chapter is split into paragraphs, which ledger facts each paragraph
may use (at most a handful), and which interpretations are allowed. The
interpretations are derived from the facts with explicit thresholds, so the
writer never has to judge what a number means.

The plans are descriptive: no group comparison, no literature comparison and no
causal statement is allowed until study design and references are supplied.
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


COMMON_FORBID = (
    "Do not compare samples as groups, and do not use the words significant, enriched, depleted or associated.",
    "Do not compare with other studies or call a value typical, normal or expected; no literature is provided.",
    "Do not explain what caused a value (for example library preparation, diet or disease).",
    "Do not add any number, sample name, species or tool that is not in the facts.",
)
LITERATURE_FORBID = (
    "Every sentence that reports what other work found must end with that work's citation marker, written "
    "exactly as in the facts, for example [@Key]. Cite only the markers listed in the facts.",
    "State only what the cited claims say. Do not generalise beyond them or merge them into a new conclusion.",
    "Do not state or imply the health status or diagnosis of the people who provided the present samples.",
    "Do not use the words significant, enriched, depleted or associated for the present samples.",
    "Do not add any number, sample name, species or tool that is not in the facts.",
)
SECTIONS = ("introduction", "results", "discussion", "abstract", "conclusion")
_ANIMAL = re.compile(r"\b(mouse|mice|murine|rat|rats|animal model)\b", re.I)


class _Ledger:
    def __init__(self, ledger: dict[str, Any]) -> None:
        self.by_id = {f["id"]: f for f in ledger.get("facts", [])}
        self.missing = ledger.get("missing", [])
        self.samples = ledger.get("sample_ids", [])

    def ids(self, *wanted: str) -> list[str]:
        return [i for i in wanted if i in self.by_id]

    def text(self, fid: str) -> str:
        return str(self.by_id[fid]["text"])

    def value(self, fid: str) -> Any:
        return self.by_id[fid]["value"]

    def sample(self, fid: str) -> str:
        return str(self.by_id[fid].get("sample_id") or "")

    def prefix(self, prefix: str) -> list[str]:
        return [i for i in self.by_id if i.startswith(prefix)]


def _pct_text(fraction: float, digits: int = 2) -> str:
    return f"{fraction * 100:.{digits}f}%"


# ---------------------------------------------------------------------------
#  interpretations derived from facts (explicit thresholds)
# ---------------------------------------------------------------------------
def quality_notes(led: _Ledger) -> tuple[list[str], list[str]]:
    notes, used = [], []
    if "READQ.q30_after.min" in led.by_id and led.value("READQ.q30_after.min") >= 0.90:
        notes.append(f"After filtering, at least {led.text('READQ.q30_after.min').split(' ')[0]} of bases reached Q30 "
                     "in every sample, which indicates high base-call quality.")
        used.append("READQ.q30_after.min")
    if "QC.fastp_retained_pct.min" in led.by_id and led.value("QC.fastp_retained_pct.min") >= 0.85:
        notes.append(f"fastp kept at least {led.text('QC.fastp_retained_pct.min').split(' ')[0]} of reads in every "
                     "sample, so filtering removed only a small part of the data.")
        used.append("QC.fastp_retained_pct.min")
    return notes, used


def host_notes(led: _Ledger) -> tuple[list[str], list[str]]:
    if "QC.host_removed_pct.max" not in led.by_id:
        return [], []
    top = led.value("QC.host_removed_pct.max")
    if top < 0.01:
        return ([f"Host read pairs were at most {led.text('QC.host_removed_pct.max').split(' ')[0]} of read pairs in any "
                 "sample. This shows the samples contained very little human DNA, so host removal changed the read "
                 "counts very little. It does not measure how well host removal worked."],
                ["QC.host_removed_pct.max"])
    return ([f"Host read pairs reached {led.text('QC.host_removed_pct.max')} of read pairs in one sample."],
            ["QC.host_removed_pct.max"])


def composition_notes(led: _Ledger) -> tuple[list[str], list[str]]:
    notes, used = [], []
    n = len(led.samples)
    if "TAX.dominant_summary" in led.by_id:
        dominant = led.value("TAX.dominant_summary")
        if isinstance(dominant, dict) and len(dominant) > 1:
            notes.append(f"The most abundant species was not the same in every sample: {len(dominant)} different "
                         f"species were the most abundant in at least one of the {n} samples.")
            used.append("TAX.dominant_summary")
    for fid in led.prefix("TAX.cohort_top."):
        value = led.value(fid)
        if isinstance(value, dict) and value.get("prevalence") == n and n > 1:
            notes.append(f"{value['taxon']} was above 0.1% of reads in all {n} samples.")
            used.append(fid)
    if "DIV.shannon.min" in led.by_id:
        sample = led.sample("DIV.shannon.min")
        top = f"TAX.top1.{sample}"
        if top in led.by_id and led.value(top).get("fraction", 0) >= 0.2:
            taxon = led.value(top)["taxon"]
            notes.append(f"The sample with the lowest Shannon index ({sample}) was also the sample in which one species "
                         f"made up a large share of reads ({taxon}, {_pct_text(led.value(top)['fraction'])}).")
            used.extend(["DIV.shannon.min", top])
    return notes, used


def function_notes(led: _Ledger) -> tuple[list[str], list[str]]:
    if "FUNC.unmapped_share.range" not in led.by_id:
        return [], []
    return ([f"Between {led.text('FUNC.unmapped_share.range')} of the HUMAnN pathway total was UNMAPPED, meaning those "
             "reads could not be assigned to a known pathway."], ["FUNC.unmapped_share.range"])


def limitation_notes(led: _Ledger) -> tuple[list[str], list[str]]:
    notes = [m["message"] for m in led.missing]
    used = []
    db = led.by_id.get("DB.taxonomy_reads.kraken2")
    if db and "8 GB" in str(db["value"].get("name", "")):
        notes.append("Classification used the size-capped Kraken2 Standard 8 GB database, which can miss species that "
                     "are rare or poorly represented in the reference collection.")
        used.append("DB.taxonomy_reads.kraken2")
    notes.append("The Shannon and Gini-Simpson indices were computed from species fractions and describe single "
                 "samples; they were not compared statistically.")
    return notes, used


# ---------------------------------------------------------------------------
#  plans
# ---------------------------------------------------------------------------
def _para(key: str, purpose: str, led: _Ledger, fact_ids: list[str], *, notes=(), note_ids=(),
          words=(70, 120), forbid=()) -> dict[str, Any] | None:
    fact_ids = list(dict.fromkeys([*fact_ids, *note_ids]))
    fact_ids = [i for i in fact_ids if i in led.by_id]
    if not fact_ids and not notes:
        return None
    return {
        "key": key,
        "purpose": purpose,
        "fact_ids": fact_ids,
        "facts": [f"{led.by_id[i]['label']}: {led.by_id[i]['text']}" for i in fact_ids],
        "notes": list(notes),
        "forbid": [*COMMON_FORBID, *forbid],
        "min_words": words[0],
        "max_words": words[1],
    }


def plan_results(led: _Ledger) -> list[dict[str, Any]]:
    top = led.ids(*(f"TAX.cohort_top.{i}" for i in range(1, 6)))
    paragraphs = [
        _para("depth", "Report sequencing depth and how many reads remained after quality filtering.", led,
              led.ids("COHORT.sample_count", "QC.raw_reads.min", "QC.raw_reads.max", "QC.raw_reads.total",
                      "QC.fastp_retained_pct.range", "QC.post_fastp_pairs.range")),
        _para("base_quality", "Report base quality and duplication of the filtered reads.", led,
              led.ids("READQ.q30_before.range", "READQ.q30_after.range", "READQ.duplication.range",
                      "READQ.gc_after.range"),
              notes=quality_notes(led)[0], words=(50, 100)),
        _para("host", "Report how many read pairs were removed as host and how many remained.", led,
              led.ids("HOST.reference", "QC.host_removed_pairs.min", "QC.host_removed_pairs.max",
                      "QC.post_host_pairs.range"),
              notes=host_notes(led)[0], note_ids=host_notes(led)[1], words=(50, 100),
              forbid=("Do not say host removal was effective, successful or complete.",)),
        _para("composition", "Describe the species that dominated the communities across and within samples.", led,
              [*top, *led.ids("TAX.dominant_summary")],
              notes=composition_notes(led)[0], words=(80, 140)),
        _para("diversity", "Report within-sample diversity and richness.", led,
              led.ids("DIV.shannon.min", "DIV.shannon.max", "DIV.simpson.range", "DIV.richness.range"),
              notes=[n for n in composition_notes(led)[0] if "Shannon" in n],
              note_ids=[i for i in composition_notes(led)[1] if i.startswith(("TAX.top1.", "DIV."))],
              words=(50, 100)),
        _para("function", "Report the functional profiles.", led,
              led.ids("FUNC.pathways_detected.range", "FUNC.unmapped_share.range"),
              notes=function_notes(led)[0], words=(40, 90)),
    ]
    return [p for p in paragraphs if p]


def plan_technical_validation(led: _Ledger) -> list[dict[str, Any]]:
    """Data Descriptor: the same evidence as Results, framed as checks of data quality."""

    framing = {
        "depth": "Validate sequencing depth: report reads per sample and how many passed quality filtering.",
        "base_quality": "Validate base-call quality of the filtered reads (Q30, duplication, GC content).",
        "host": "Validate host contamination: report how many read pairs matched the human reference.",
        "composition": "Show that taxonomic profiles are plausible for human stool: which species dominated and "
                       "how consistent they were across samples.",
        "diversity": "Report within-sample diversity and richness as a check of profile complexity.",
        "function": "Report functional profile coverage, including the share that could not be assigned.",
    }
    plan = plan_results(led)
    for paragraph in plan:
        paragraph["purpose"] = framing.get(paragraph["key"], paragraph["purpose"])
        paragraph["forbid"] = [*paragraph["forbid"], "Do not run or report statistical hypothesis tests."]
    return plan


def plan_background_summary(led: _Ledger, brief: dict[str, Any], cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Data Descriptor opening: context, what the dataset is, why it is reusable."""

    paragraphs = []
    background = _claims(cards, ("background",), 4) or _claims(cards, ("finding",), 3)
    if background:
        paragraphs.append(_lit_para("context", "Introduce why data of this kind are needed, using only the cited "
                                    "literature.", background, notes=_evidence_notes(background)))
    else:
        paragraphs.append(_para(
            "context", "Introduce in general terms what shotgun metagenomic sequencing of stool samples measures. "
            "No literature is provided, so do not describe or cite earlier studies.", led, [],
            notes=["Shotgun metagenomic sequencing of stool samples can profile both the taxonomic and the functional "
                   "composition of the gut microbiome from the same reads."], words=(40, 80)))
    aim = str(brief.get("research_question") or brief.get("title") or "").strip()
    purpose = ("Summarise what this dataset contains and how it was generated: the samples, the sequencing, the "
               "processing steps and the outputs. Do not report results beyond the facts.")
    if aim:
        purpose += f" The dataset is: {aim}"
    paragraphs.append(_para(
        "dataset", purpose, led,
        led.ids("COHORT.sample_count", "READQ.sequencing_mode", "QC.raw_reads.range", "SW.fastp", "SW.bowtie2",
                "SW.kraken2", "SW.bracken", "SW.humann"),
        notes=_tool_roles(led), words=(90, 160),
        forbid=("Do not state or imply the health status or diagnosis of the people who provided the samples.",)))
    paragraphs.append(_para(
        "reuse", "Explain what the released data provide and how others can reuse them.", led, [],
        notes=["The released tables provide per-sample read counts, read-quality metrics, species-level abundances "
               "and MetaCyc pathway profiles together with the software versions and parameters used.",
               "Other researchers can reuse these profiles to compare processing choices or to combine them with "
               "other gut metagenome datasets.",
               "The samples are described individually; the package contains no group labels, so no group "
               "comparison is included."], words=(60, 110)))
    return [p for p in paragraphs if p]


FIGURE_ROLE = {"stacked": "composition", "heatmap": "composition", "pca": "composition", "pcoa": "composition",
               "nmds": "composition", "shannon": "diversity", "alpha_diversity": "diversity",
               "rarefaction": "diversity", "enrichment": "function", "bubble": "function", "volcano": "function"}


def attach_figures(plan: list[dict[str, Any]], figures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give each figure to the paragraph that discusses it and number them in reading order."""

    if not plan or not figures:
        return plan
    keys = [p["key"] for p in plan]
    for figure in figures:
        role = FIGURE_ROLE.get(str(figure.get("chart") or "").lower())
        target = plan[keys.index(role)] if role in keys else plan[-1]
        target.setdefault("figures", []).append(dict(figure))
    number = 0
    for paragraph in plan:
        for figure in paragraph.get("figures", []):
            number += 1
            figure["number"] = number
            figure["embed"] = (f"![{figure['caption']}](figures/{figure['figure_id']}.png)\n\n"
                               f"**Figure {number}.** {figure['caption']}")
            paragraph["facts"].append(f"Figure {number} shows: {figure['caption']}")
        if paragraph.get("figures"):
            cited = ", ".join(f"Figure {f['number']}" for f in paragraph["figures"])
            paragraph["forbid"] = [*paragraph["forbid"],
                                   f"Refer to {cited} in parentheses, for example (Figure {paragraph['figures'][0]['number']}), "
                                   "in the sentence that describes what it shows. Do not describe anything the "
                                   "figure caption does not state."]
    return plan


def plan_discussion(led: _Ledger) -> list[dict[str, Any]]:
    q_notes, q_ids = quality_notes(led)
    h_notes, h_ids = host_notes(led)
    c_notes, c_ids = composition_notes(led)
    f_notes, f_ids = function_notes(led)
    l_notes, l_ids = limitation_notes(led)
    paragraphs = [
        _para("data_quality", "Interpret what the quality-control results mean for using these data.", led,
              [], notes=[*q_notes, *h_notes], note_ids=[*q_ids, *h_ids], words=(70, 130),
              forbid=("Do not repeat the full list of read counts.",)),
        _para("composition", "Interpret the community composition: which species dominated and how much samples differed.",
              led, led.ids("TAX.cohort_top.1", "TAX.cohort_top.2", "TAX.cohort_top.3"),
              notes=c_notes, note_ids=c_ids, words=(80, 140)),
        _para("function", "Interpret what the functional profiles can and cannot show.", led, [],
              notes=f_notes, note_ids=f_ids, words=(40, 90)),
        _para("limitations", "State the limitations of this analysis.", led, [], notes=l_notes, note_ids=l_ids,
              words=(70, 140)),
        _para("outlook", "State what information would allow further analysis, without promising results.", led, [],
              notes=["With confirmed study design and sample metadata, the same profiles could be used for planned "
                     "group comparisons with appropriate statistical tests.",
                     "Sample collection, DNA extraction and library preparation details are needed to report the study "
                     "fully."], words=(40, 80)),
    ]
    return [p for p in paragraphs if p]


def _tool_roles(led: _Ledger) -> list[str]:
    """Which tool produced which result, so summaries do not misattribute them."""

    roles = []
    if led.ids("SW.fastp"):
        roles.append("fastp filtered and trimmed the reads; the Q30 values come from fastp reports.")
    if led.ids("SW.bowtie2"):
        roles.append("Bowtie2 aligned reads to the human reference to remove host read pairs.")
    if led.ids("SW.kraken2", "SW.bracken"):
        roles.append("Kraken2 and Bracken produced the species abundances.")
        if led.ids("DIV.shannon.range"):
            roles.append("The Shannon index was computed afterwards from the species fractions as a separate step; "
                         "it was not produced by Kraken2, Bracken or HUMAnN, so never name a tool as its source.")
    if led.ids("SW.humann"):
        roles.append("HUMAnN produced the functional (MetaCyc pathway) profiles only.")
    return roles


# ---------------------------------------------------------------------------
#  literature (reference cards from the author's chosen papers)
# ---------------------------------------------------------------------------
def claim_line(card: dict[str, Any], claim: dict[str, Any]) -> str:
    who = f"{card.get('short_authors') or card['key']}, {card.get('year') or 'n.d.'}"
    return f"[@{card['key']}] {claim['text']} ({who})"


def _claims(cards: list[dict[str, Any]], kinds: tuple[str, ...], limit: int, skip: set[str] = frozenset()):
    """Round-robin across papers so one reference does not fill a paragraph."""

    queues = [[c for c in card.get("claims", []) if c["kind"] in kinds and c["id"] not in skip] for card in cards]
    picked: list[tuple[dict, dict]] = []
    while len(picked) < limit and any(queues):
        for card, queue in zip(cards, queues):
            if queue and len(picked) < limit:
                picked.append((card, queue.pop(0)))
    return picked


def _lit_para(key: str, purpose: str, picked, *, notes=(), led: _Ledger | None = None, fact_ids=(),
              words=(80, 150), require: bool = True) -> dict[str, Any] | None:
    if not picked and not notes:
        return None
    facts = [claim_line(card, claim) for card, claim in picked]
    if led is not None:
        facts += [f"{led.by_id[i]['label']}: {led.by_id[i]['text']}" for i in fact_ids if i in led.by_id]
    return {
        "key": key, "purpose": purpose, "fact_ids": [claim["id"] for _, claim in picked] + list(fact_ids),
        "facts": facts, "notes": list(notes), "forbid": list(LITERATURE_FORBID),
        "citations": sorted({card["key"] for card, _ in picked}), "require_citations": require,
        "min_words": words[0], "max_words": words[1],
    }


def _evidence_notes(picked) -> list[str]:
    animal = sorted({card["key"] for card, claim in picked if _ANIMAL.search(claim["text"] + " " + claim.get("quote", ""))})
    if not animal:
        return []
    return ["Part of this cited evidence comes from animal (mouse) studies: "
            + ", ".join(f"[@{k}]" for k in animal) + "."]


def plan_introduction(led: _Ledger, brief: dict[str, Any], cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    paragraphs = []
    background = _claims(cards, ("background",), 5) or _claims(cards, ("finding",), 4)
    used = {claim["id"] for _, claim in background}
    paragraphs.append(_lit_para(
        "background", "Introduce the research area from the cited literature: what is known so far.", background))
    findings = _claims(cards, ("finding", "limitation"), 4, skip=used)
    paragraphs.append(_lit_para(
        "open_questions", "Summarise what the cited studies found and which questions remain open, using only "
                          "the cited claims.", findings, notes=_evidence_notes(findings)))
    aim = str(brief.get("research_question") or brief.get("title") or "").strip()
    tools = led.ids("COHORT.sample_count", "SW.fastp", "SW.bowtie2", "SW.kraken2", "SW.bracken", "SW.humann")
    purpose = ("State the aim and approach of the present study in the final paragraph of the introduction: which "
               "samples were profiled and with which main tools. Do not report any results.")
    if aim:
        purpose += f" The aim is: {aim}"
    aim_para = _para("aim", purpose, led, tools,
                     notes=[*_tool_roles(led),
                            "The analysis package records no diagnosis or group labels for these samples, so they "
                            "are described sample by sample."],
                     words=(60, 120),
                     forbid=("Do not state or imply the health status or diagnosis of the people who provided "
                             "these samples.", "Do not report results numbers."))
    paragraphs.append(aim_para)
    return [p for p in paragraphs if p]


def literature_discussion(led: _Ledger, cards: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not cards:
        return None
    picked = _claims(cards, ("finding", "background"), 4)
    notes = []
    species = _checks(led).get("species", [])
    for card in cards:
        text = " ".join([card.get("abstract", ""), card.get("title", "")]).lower()
        shared = [s for s in species if s.lower() in text]
        if shared:
            notes.append(f"[@{card['key']}] discusses {', '.join(shared)}, which was also observed in the present samples.")
    notes.append("The present analysis package contains no diagnosis or group information, so these profiles cannot "
                 "test or confirm the findings of the cited studies.")
    notes += _evidence_notes(picked)
    return _lit_para("literature", "Place the present descriptive profiles in the context of the cited studies, "
                                   "and state what the present data can and cannot add to them.",
                     picked, notes=notes, words=(80, 150), require=False)


def plan_abstract(led: _Ledger, brief: dict[str, Any]) -> list[dict[str, Any]]:
    tools = led.ids("SW.fastp", "SW.bowtie2", "SW.kraken2", "SW.bracken", "SW.humann")
    facts = [*led.ids("COHORT.sample_count"), *tools,
             *led.ids("QC.raw_reads.range", "READQ.q30_after.range", "TAX.cohort_top.1", "DIV.shannon.range")]
    notes = [*host_notes(led)[0][:1], *_tool_roles(led),
             "No group comparison or statistical test was performed; the study is descriptive."]
    aim = str(brief.get("research_question") or brief.get("title") or "").strip()
    purpose = ("Write a single-paragraph abstract: the aim, what was done (name the main tools), the main "
               "results (use at most four numbers), and the main limitation.")
    if aim:
        purpose += f" The aim is: {aim}"
    para = _para("abstract", purpose, led, facts, notes=notes, note_ids=host_notes(led)[1], words=(120, 200),
                 forbid=("Do not list per-sample values.",))
    return [para] if para else []


def plan_conclusion(led: _Ledger) -> list[dict[str, Any]]:
    c_notes, c_ids = composition_notes(led)
    para = _para("conclusion", "Close the paper in one short paragraph: what these data show and what they cannot show yet.",
                 led, led.ids("COHORT.sample_count", "TAX.cohort_top.1"),
                 notes=[*quality_notes(led)[0][:1], *c_notes[:1],
                        "No group comparison or statistical test was performed."],
                 note_ids=[*quality_notes(led)[1][:1], *c_ids[:1]], words=(60, 110),
                 forbid=("Do not add new numbers beyond the facts.",))
    return [para] if para else []


def _checks(led: _Ledger) -> dict[str, Any]:
    """Structured facts the verifier checks deterministically."""

    checks: dict[str, Any] = {}
    dominant = led.by_id.get("TAX.dominant_summary")
    if dominant and isinstance(dominant["value"], dict):
        checks["dominance"] = {taxon: len(samples) for taxon, samples in dominant["value"].items()}
    names = set()
    prevalence: dict[str, int] = {}
    for fid, fact in led.by_id.items():
        value = fact["value"]
        if fid.startswith("TAX.cohort_top.") and isinstance(value, dict) and "prevalence" in value:
            prevalence[value.get("taxon", "")] = int(value["prevalence"])
        if fid.startswith(("TAX.top1.", "TAX.cohort_top.")) and isinstance(value, dict):
            names.add(value.get("taxon", ""))
        elif fid.startswith("TAX.top3.") and isinstance(value, list):
            names.update(item.get("taxon", "") for item in value if isinstance(item, dict))
    checks["species"] = sorted(n for n in names if n)
    checks["prevalence"] = {k: v for k, v in prevalence.items() if k}
    checks["sample_count"] = len(led.samples)
    return checks


def build_plan(ledger: dict[str, Any], section: str, brief: dict[str, Any] | None = None,
               cards: list[dict[str, Any]] | None = None,
               figures: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    led = _Ledger(ledger)
    cards = [c for c in (cards or []) if c.get("claims")]
    if section == "introduction":
        plan = plan_introduction(led, brief or {}, cards)
    elif section == "results":
        plan = plan_results(led)
    elif section == "technical_validation":
        plan = plan_technical_validation(led)
    elif section == "background_summary":
        plan = plan_background_summary(led, brief or {}, cards)
    elif section == "discussion":
        plan = plan_discussion(led)
        extra = literature_discussion(led, cards)
        if extra:
            at = next((i for i, p in enumerate(plan) if p["key"] == "limitations"), len(plan))
            plan.insert(at, extra)
    elif section == "abstract":
        plan = plan_abstract(led, brief or {})
    elif section == "conclusion":
        plan = plan_conclusion(led)
    else:
        raise ValueError(f"no plan for section {section!r}")
    checks = _checks(led)
    context = " ".join(str((brief or {}).get(k) or "") for k in ("title", "research_question")).strip()
    for paragraph in plan:
        paragraph["checks"] = checks
        paragraph["context"] = context
        paragraph.setdefault("citations", [])
        paragraph.setdefault("require_citations", False)
    return attach_figures(plan, figures or [])


@register_skill
class SectionPlannerSkill(BaseSkill):
    spec = SkillSpec(
        name="section-planner",
        version="1.0.0",
        category=SkillCategory.WRITING,
        description="把章节拆成段落：规定每段目的、可用事实（每段不超过数条）和由代码按阈值推出的可写解读，供逐段写作。",
        tags=("writing", "planning", "metagenomics", "deterministic"),
        input_keys=("ledger", "section", "brief", "cards"),
        output_keys=("section", "paragraphs"),
    )

    def run(self, skill_input: SkillInput):
        payload = skill_input.require("ledger", "section")
        section = str(payload["section"])
        paragraphs = build_plan(payload["ledger"], section, skill_input.get("brief") or {},
                                skill_input.get("cards") or [], skill_input.get("figures") or [])
        return self.success(data={"section": section, "paragraphs": paragraphs},
                            summary=f"{section}: {len(paragraphs)} 段计划。")
