"""Phase 4 quality gates: STORMS-informed reporting check, mock peer review, targeted revision.

* ``storms-checklist`` (deterministic): checks the finished manuscript against reporting
  items for human shotgun metagenomic studies, adapted from the STORMS checklist
  (Mirzayi et al., Nat Med 2021). Each item is reported / author input needed / missing.
  It never fills anything in.
* ``manuscript-reviewer`` (model, JSON): a pre-submission review. Every issue must quote a
  sentence verbatim from the named section; issues whose quote is not found are discarded.
* ``targeted-reviser`` (model + code checks): rewrites only the paragraph holding the
  quoted sentence. The rewrite is kept only if it adds no number, citation or identifier,
  keeps every figure reference, and passes the claim-verifier's deterministic checks;
  otherwise the original paragraph stays and the issue is left for the author.
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Any

try:  # 标准包调用
    from ...core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from ...registry import register_skill
except ImportError:  # 兼容早期顶层脚本调用
    from core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from registry import register_skill

from .claim_verifier import NUMBER, citation_keys, deterministic_issues
from .paragraph_writer import clean

AUTHOR = re.compile(r"\[AUTHOR_INPUT_NEEDED:[^\]]*\]")
PENDING = re.compile(r"\[SKILL_PENDING:[^\]]*\][^\n]*")
FIGURE_REF = re.compile(r"\bFigure \d+\b")
ASSET_LINE = re.compile(r"^(?:!\[|\*\*(?:Figure|Table) \d+\.\*\*)")

# (id, label, reported pattern, author-placeholder pattern)
STORMS_ITEMS: tuple[tuple[str, str, str, str], ...] = (
    ("participants", "Number of participants / samples",
     r"\b(\d+|two|three|four|five|six|seven|eight|nine|ten|twelve)\b (?:human )?(?:stool|faecal|fecal|participants|"
     r"subjects|individuals|shotgun metagenomic samples|samples)", r"participants"),
    ("design", "Study design and population", r"cross-sectional|cohort study|case[- ]control|randomi[sz]ed|recruit",
     r"study design"),
    ("eligibility", "Eligibility and exclusion criteria (e.g. antibiotic use)", r"inclusion criteri|exclusion criteri|"
     r"eligib|antibiotic", r"participants"),
    ("ethics", "Ethics approval and informed consent", r"ethic(?:s|al) (?:committee|approval)|informed consent|"
     r"approved by", r"ethics"),
    ("collection", "Sample collection, transport and storage", r"stored at|−\s?80|-80 ?°|collected (?:at|in|by)|"
     r"collection kit", r"sample collection|storage"),
    ("extraction", "DNA extraction method", r"DNA (?:was )?extract|extraction kit|NucleoSpin|PowerSoil|QIAamp",
     r"DNA extraction"),
    ("library", "Library preparation and sequencing platform", r"library prep|Nextera|TruSeq|Illumina|HiSeq|"
     r"NovaSeq|MiSeq|BGISEQ|DNBSEQ", r"library preparation|sequencing platform"),
    ("layout", "Read layout and length", r"paired-end|single-end|\b\d+ ?bp\b|2 × \d+", ""),
    ("qc", "Read quality control tools and parameters", r"fastp|Trimmomatic|KneadData|Cutadapt", ""),
    ("host", "Host (human) read removal", r"host|human reads|GRCh38|hg38", ""),
    ("taxonomy", "Taxonomic profiling tool and reference database", r"Kraken2|MetaPhlAn|Bracken|mOTUs", ""),
    ("function", "Functional profiling tool and database", r"HUMAnN|eggNOG|MetaCyc|UniRef", ""),
    ("diversity", "Diversity metrics and how they were computed", r"Shannon|Simpson|richness|alpha diversity", ""),
    ("statistics", "Statistical methods, or an explicit statement that none were used",
     r"statistical (?:test|comparison|analysis|method)|Wilcoxon|PERMANOVA|regression|no group comparison", ""),
    ("confounders", "Metadata and potential confounders", r"confound|covariate|\bdiet\b|medication|BMI",
     r"metadata|confound"),
    ("contamination", "Negative controls / contamination assessment", r"negative control|blank (?:extraction|"
     r"control)|mock community|kit contamina", r"control"),
    ("versions", "Software versions", r"v\d+\.\d+", ""),
    ("raw_data", "Raw data accession", r"\bPRJ(?:EB|NA|DB)\d+|\b[EDS]R[PRX]\d{5,}", r"archive and project accession"),
    ("processed_data", "Processed data availability", r"processed (?:tables|data|results)[^.]{0,80}(?:deposited|"
     r"available|released)", r"processed tables"),
    ("code", "Code / workflow availability", r"(?:code|workflow)[^.]{0,60}(?:github|gitlab|zenodo|repository|"
     r"available at)", r"repository URL|code"),
)
STATUS_ZH = {"reported": "已报告", "author_input": "待作者补充", "missing": "缺失"}
# A sentence that says the information is absent does not report it.
ABSENT = re.compile(r"\b(?:not (?:available|included|provided|recorded|reported|described|known)|absence of|"
                    r"no information|missing|unavailable|lack(?:s|ing)? (?:of )?|do(?:es)? not (?:include|contain|"
                    r"provide|record)|not in the package)\b", re.I)
SENTENCES = re.compile(r"(?<=[.!?])\s+")


def _mentions(pattern: str, body: str) -> tuple[bool, bool]:
    """(reported, stated_absent) for one section body."""

    reported = absent = False
    for sentence in SENTENCES.split(body):
        if re.search(pattern, sentence, re.I):
            if ABSENT.search(sentence):
                absent = True
            else:
                reported = True
    return reported, absent


def storms_check(sections: dict[str, str]) -> dict[str, Any]:
    items = []
    for item_id, label, reported, author in STORMS_ITEMS:
        found_in, placeholder_in = [], []
        for name, text in sections.items():
            placeholders = " ".join(AUTHOR.findall(text))
            body = PENDING.sub("", AUTHOR.sub("", text))
            if item_id == "versions":
                if len(re.findall(reported, body)) >= 3:
                    found_in.append(name)
                continue
            said, absent = _mentions(reported, body)
            if said:
                found_in.append(name)
            elif absent:
                placeholder_in.append(name)
            if author and re.search(author, placeholders, re.I):
                placeholder_in.append(name)
        placeholder_in = list(dict.fromkeys(placeholder_in))
        status = "reported" if found_in else "author_input" if placeholder_in else "missing"
        items.append({"id": item_id, "label": label, "status": status,
                      "sections": found_in or placeholder_in})
    counts = {s: sum(1 for i in items if i["status"] == s) for s in STATUS_ZH}
    lines = ["# STORMS-informed reporting check", "",
             "Adapted from the STORMS checklist for human microbiome studies (Mirzayi et al., Nat Med 2021). "
             "Items are detected in the manuscript text; nothing was filled in automatically.", "",
             f"Reported {counts['reported']} · author input needed {counts['author_input']} · "
             f"missing {counts['missing']} (of {len(items)})", "",
             "| Item | Status | Section(s) |", "|---|---|---|"]
    for item in items:
        lines.append(f"| {item['label']} | {STATUS_ZH[item['status']]} ({item['status']}) | "
                     f"{', '.join(item['sections']) or '—'} |")
    return {"items": items, "counts": counts, "markdown": "\n".join(lines) + "\n"}


REVIEW_SYSTEM = ("You are a careful peer reviewer for a scientific data journal. You only point to problems that "
                 "are visible in the text you are given.")
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {"issues": {"type": "array", "items": {"type": "object", "properties": {
        "section": {"type": "string"}, "quote": {"type": "string"}, "problem": {"type": "string"},
        "severity": {"type": "string", "enum": ["major", "minor"]},
        "kind": {"type": "string", "enum": ["overclaim", "unsupported", "unclear", "redundant", "missing_information"]},
    }, "required": ["section", "quote", "problem", "severity", "kind"]}}},
    "required": ["issues"],
}
REVISABLE_KINDS = {"overclaim", "unsupported", "unclear", "redundant"}
MAX_ISSUES = 8


def _norm(text: str) -> str:
    return " ".join(text.split())


def review_prompt(sections: dict[str, str], titles: dict[str, str]) -> str:
    parts = []
    for name, text in sections.items():
        body = "\n".join(line for line in text.splitlines() if not ASSET_LINE.match(line.strip()))
        parts.append(f"### [{name}] {titles.get(name, name)}\n{body.strip()}")
    return ("Review this manuscript before submission. List at most "
            f"{MAX_ISSUES} concrete problems in the writing: claims stronger than the reported data, statements "
            "not supported by the numbers given, unclear or redundant sentences, or information a reader needs "
            "that is missing. Do not ask for new experiments or analyses. For each problem give the section id "
            "shown in square brackets, copy one sentence from that section exactly as it is written in 'quote', "
            "and explain the problem in one sentence. Placeholders such as AUTHOR_INPUT_NEEDED are known gaps; do "
            "not report them.\n\n" + "\n\n".join(parts))


def review(sections: dict[str, str], titles: dict[str, str], model: Any) -> dict[str, Any]:
    prompt = review_prompt(sections, titles)
    if hasattr(model, "generate_json"):
        data = model.generate_json(prompt, REVIEW_SYSTEM, REVIEW_SCHEMA)
    else:
        raw = model.generate(prompt, system=REVIEW_SYSTEM)
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    kept, discarded = [], []
    for issue in (data or {}).get("issues", [])[:MAX_ISSUES * 2]:
        section = str(issue.get("section") or "").strip().strip("[]")
        quote = _norm(str(issue.get("quote") or ""))
        if section not in sections or len(quote) < 20 or quote not in _norm(sections[section]):
            discarded.append({**issue, "reason": "quote not found in the named section"})
            continue
        if "AUTHOR_INPUT_NEEDED" in quote or "SKILL_PENDING" in quote:
            discarded.append({**issue, "reason": "known placeholder"})
            continue
        kept.append({"section": section, "quote": quote, "problem": str(issue.get("problem") or "").strip(),
                     "severity": issue.get("severity") if issue.get("severity") in {"major", "minor"} else "minor",
                     "kind": issue.get("kind") if issue.get("kind") in REVISABLE_KINDS | {"missing_information"}
                     else "unclear"})
        if len(kept) >= MAX_ISSUES:
            break
    return {"issues": kept, "discarded": discarded}


REVISE_SYSTEM = ("You revise one sentence of a scientific paper to fix one reviewer comment. "
                 "Output only the revised sentence as plain prose.")


def _numbers(text: str) -> set[float]:
    out = set()
    for token in NUMBER.findall(text):
        try:
            value = float(token.replace(",", ""))
        except ValueError:
            continue
        if not (value < 20 and value.is_integer()):
            out.add(round(value, 6))
    return out


def _paragraphs(markdown: str) -> list[str]:
    return re.split(r"\n\s*\n", markdown)


PRONOUN_START = re.compile(r"^(?:It|This|These|They|That|Those|Such|Its|Their)\b")
DELETE = "DELETE"


def revise(markdown: str, issue: dict[str, Any], model: Any) -> dict[str, Any]:
    """Rewrite only the quoted sentence; every other sentence of the paragraph stays as written."""

    blocks = _paragraphs(markdown)
    quote = issue["quote"]
    index = next((i for i, b in enumerate(blocks) if quote in _norm(b) and not ASSET_LINE.match(b.strip())), None)
    if index is None:
        return {"markdown": markdown, "status": "not_found"}
    paragraph = _norm(blocks[index])
    start = paragraph.find(quote)
    before, after = paragraph[:start].rstrip(), paragraph[start + len(quote):].lstrip()
    may_delete = issue.get("kind") == "redundant"
    prompt = (f"Paragraph (for context only):\n{paragraph}\n\nSentence to revise:\n{quote}\n\n"
              f"Reviewer comment: {issue['problem']}\n\n"
              "Rewrite only this sentence to address the comment with the smallest change. Keep every number, unit, "
              "sample name, citation marker such as [@Key] and figure reference such as (Figure 1) exactly as "
              "written. Do not add any new number, finding, citation, method or claim; where the sentence claims "
              "more than the data show, weaken or remove that part. Output only the rewritten sentence."
              + (f" If the sentence only repeats information given elsewhere, output {DELETE}." if may_delete else ""))
    new = clean(model.generate(prompt, system=REVISE_SYSTEM)).strip()
    problems = []
    if new.upper().strip(". ") == DELETE:
        if not may_delete:
            problems.append("deletion not allowed for this comment")
        if _numbers(quote) or citation_keys(quote) or FIGURE_REF.search(quote):
            problems.append("sentence carries numbers, citations or figure references")
        if PRONOUN_START.match(after):
            problems.append("next sentence depends on this one")
        if problems:
            return {"markdown": markdown, "status": "kept_original", "reasons": problems}
        blocks[index] = (before + " " + after).strip()
        return {"markdown": "\n\n".join(blocks), "status": "revised", "before": quote, "after": "(sentence removed)"}
    if not new:
        problems.append("empty rewrite")
    if len(new) > 2 * len(quote) + 40:
        problems.append("rewrite much longer than the sentence")
    if not _numbers(new) <= _numbers(quote):
        problems.append("rewrite added numbers")
    if not set(citation_keys(new)) <= set(citation_keys(quote)):
        problems.append("rewrite added citations")
    if set(FIGURE_REF.findall(quote)) - set(FIGURE_REF.findall(new)):
        problems.append("rewrite dropped a figure reference")
    hard, _ = deterministic_issues(new, {"facts": [paragraph], "notes": [], "max_words": 400,
                                         "citations": citation_keys(quote)})
    problems += hard
    if problems:
        return {"markdown": markdown, "status": "kept_original", "reasons": problems[:4]}
    if difflib.SequenceMatcher(None, quote, _norm(new)).ratio() > 0.97:
        return {"markdown": markdown, "status": "unchanged", "reasons": ["rewrite was practically identical"]}
    blocks[index] = " ".join(part for part in (before, new, after) if part)
    return {"markdown": "\n\n".join(blocks), "status": "revised", "before": quote, "after": new}


def ledger_numbers(ledger: dict[str, Any] | None) -> set[float]:
    """Every number the fact ledger can vouch for (display text and raw values)."""

    known: set[float] = set()

    def add(value):
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            known.add(round(float(value), 6))
        elif isinstance(value, dict):
            for item in value.values():
                add(item)
        elif isinstance(value, list):
            for item in value:
                add(item)

    for fact in (ledger or {}).get("facts", []):
        known |= _numbers(f"{fact.get('text', '')} {fact.get('label', '')}")
        add(fact.get("value"))
    return known


# Interpretive wording is judged on its reasoning, not on whether its numbers exist.
INTERPRETS = re.compile(r"\b(suggest\w*|indicat\w*|impl\w*|demonstrat\w*|reflect\w*|likely|probabl\w*|"
                        r"may|might|could|consistent with|due to|because)\b", re.I)
NOT_SUPPORTED = re.compile(r"not (?:supported|reported|provided|given|stated)|does not (?:provide|report|state)", re.I)


def review_and_revise(sections: dict[str, str], titles: dict[str, str], revisable: set[str], model: Any,
                      ledger: dict[str, Any] | None = None) -> dict:
    result = review(sections, titles, model)
    known = ledger_numbers(ledger)
    updated = dict(sections)
    log = []
    for issue in result["issues"]:
        entry = dict(issue)
        quoted = _numbers(issue["quote"])
        # A reviewer claiming a number is unsupported is overruled when the fact ledger holds every number.
        if (known and quoted and quoted <= known and not INTERPRETS.search(issue["quote"])
                and (issue["kind"] == "unsupported" or NOT_SUPPORTED.search(issue["problem"]))):
            entry["action"] = "rejected (numbers verified against the fact ledger)"
            log.append(entry)
            continue
        if issue["kind"] not in REVISABLE_KINDS:
            entry["action"] = "author"
        elif issue["section"] not in revisable:
            entry["action"] = "author (section not model-written)"
        else:
            outcome = revise(updated[issue["section"]], issue, model)
            updated[issue["section"]] = outcome["markdown"]
            entry["action"] = outcome["status"]
            entry.update({k: outcome[k] for k in ("reasons", "before", "after") if k in outcome})
        log.append(entry)
    lines = ["# Pre-submission review and targeted revision", "",
             "Simulated single-reviewer report on the English manuscript. Issues had to quote a sentence from the "
             f"manuscript; {len(result['discarded'])} issue(s) whose quote was not found were discarded. Revisions "
             "were kept only if they added no number, citation or claim and passed the automatic checks.", ""]
    for n, entry in enumerate(log, 1):
        lines.append(f"## {n}. [{entry['severity']}] {entry['section']} — {entry['kind']}")
        lines.append(f"> {entry['quote']}")
        lines.append("")
        lines.append(f"Problem: {entry['problem']}")
        lines.append(f"Action: {entry['action']}" + (f" ({'; '.join(entry['reasons'])})" if entry.get("reasons") else ""))
        if entry.get("after"):
            lines.append("")
            lines.append(f"Revised sentence: {entry['after']}")
        lines.append("")
    if not log:
        lines.append("No verifiable issues were reported.")
    return {"sections": updated, "issues": log, "discarded": result["discarded"], "markdown": "\n".join(lines) + "\n"}


@register_skill
class StormsChecklistSkill(BaseSkill):
    spec = SkillSpec(name="storms-checklist", version="1.0.0", category=SkillCategory.WRITING,
                     description="按 STORMS 报告规范（人体微生物组研究）逐项检查稿件：已报告 / 待作者补充 / 缺失，不自动补写。",
                     tags=("writing", "reporting", "quality", "deterministic"), input_keys=("sections",),
                     output_keys=("items", "counts", "markdown"))

    def run(self, skill_input: SkillInput):
        data = storms_check(dict(skill_input.require("sections")["sections"]))
        return self.success(data=data, summary=f"STORMS：已报告 {data['counts']['reported']} 项")


@register_skill
class ManuscriptReviewerSkill(BaseSkill):
    spec = SkillSpec(name="manuscript-reviewer", version="1.0.0", category=SkillCategory.WRITING,
                     description="模拟审稿：每条意见必须原文引用稿件中的句子，引用对不上的意见自动丢弃。",
                     tags=("writing", "review", "llm"), input_keys=("sections", "titles", "model"),
                     output_keys=("issues", "discarded"))

    def run(self, skill_input: SkillInput):
        p = skill_input.require("sections", "model")
        data = review(dict(p["sections"]), dict(skill_input.get("titles") or {}), p["model"])
        return self.success(data=data, summary=f"{len(data['issues'])} 条可核实意见")


@register_skill
class TargetedReviserSkill(BaseSkill):
    spec = SkillSpec(name="targeted-reviser", version="1.0.0", category=SkillCategory.WRITING,
                     description="按审稿意见只改相关段落；改后不得新增数字、引用或结论，并须通过自动核对，否则保留原文交作者处理。",
                     tags=("writing", "revision", "llm"), input_keys=("sections", "titles", "revisable", "model"),
                     output_keys=("sections", "issues", "markdown"))

    def run(self, skill_input: SkillInput):
        p = skill_input.require("sections", "model")
        data = review_and_revise(dict(p["sections"]), dict(skill_input.get("titles") or {}),
                                 set(skill_input.get("revisable") or []), p["model"], skill_input.get("ledger"))
        revised = sum(1 for i in data["issues"] if i["action"] == "revised")
        return self.success(data=data, summary=f"{len(data['issues'])} 条意见，修改 {revised} 处")
