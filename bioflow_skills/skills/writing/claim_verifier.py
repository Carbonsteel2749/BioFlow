"""claim-verifier: check one paragraph sentence by sentence.

Two layers, in the spirit of FActScore and Chain-of-Verification:

1. Deterministic checks: numbers absent from the paragraph's facts and notes,
   over-claiming vocabulary, leaked fact ids, length.
2. Model judgement (optional, needs ``payload['model']`` with ``generate_json``
   or ``generate``): each numbered sentence is labelled supported or not
   against the same facts and notes.

The result lists issues as plain sentences a writer can act on, plus the
indexes of sentences that failed, so a caller can drop them as a last resort.
"""

from __future__ import annotations

import json
import re
from typing import Any

try:  # 标准包调用
    from ...core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from ...registry import register_skill
except ImportError:  # 兼容早期顶层脚本调用
    from core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from registry import register_skill


OVERCLAIM = re.compile(
    r"\b(significant(?:ly)?|enriched|enrichment|depleted|depletion|associated with|correlat\w*|"
    r"caus(?:e|ed|es|al)|due to|reflect(?:s|ing|ed)?|typical(?:ly)?|normal range|as expected|"
    r"consistent with (?:previous|prior|other)|previous(?:ly)? (?:studies|reported)|"
    r"highly effective|effective(?:ly)?|successful(?:ly)?|dysbiosis|biomarker|healthy|disease)\b",
    re.I,
)
LEDGER_ID = re.compile(r"\[(?:COHORT|QC|READQ|HOST|TAX|DIV|FUNC|SW|DB|PARAM|RUN)\.[^\]\s]+\]")
NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])\d+(?:\.\d+)?")
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")

JUDGE_SYSTEM = (
    "You check a paragraph of a scientific paper against its evidence. You are strict and literal."
)
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "supported": {"type": "boolean"},
                    "problem": {"type": "string"},
                },
                "required": ["index", "supported", "problem"],
            },
        }
    },
    "required": ["sentences"],
}


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE.split(text.strip()) if s.strip()]


def _numbers(text: str) -> list[str]:
    out = []
    for token in NUMBER.findall(text):
        try:
            value = float(token.replace(",", ""))
        except ValueError:
            continue
        if value < 20 and value.is_integer():
            continue
        out.append(token)
    return out


def _evidence_numbers(paragraph: dict[str, Any]) -> set[float]:
    blob = " ".join([*paragraph.get("facts", []), *paragraph.get("notes", []), paragraph.get("purpose", "")])
    values = set()
    for token in NUMBER.findall(blob):
        try:
            values.add(round(float(token.replace(",", "")), 6))
        except ValueError:
            pass
    return values


IDENTIFIER = re.compile(r"\b(?:PRJ[A-Z]{1,2}\d+|[SED]RR\d+|SAM[A-Z]{1,2}\d+|GC[AF]_\d+(?:\.\d+)?)\b")
DOMINANCE_CUE = re.compile(r"most abundant|dominat|predominant|most prevalent", re.I)
PREVALENCE_CUE = re.compile(r"(above|present|detected|prevalen|exceed|more than|at least)[^;]{0,40}$", re.I)
COUNT = re.compile(r"\bin (?:(all|each of the|every one of the)\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
                   r"eleven|twelve)\b(?: of the \w+)?\s+(?:samples?|of the samples)", re.I)
WORDS = {w: i for i, w in enumerate(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                                     "ten", "eleven", "twelve"])}


def _aliases(taxon: str) -> list[str]:
    parts = taxon.split()
    names = [taxon]
    if len(parts) >= 2:
        names.append(f"{parts[0][0]}. {' '.join(parts[1:])}")
    return names


def _dominance_issues(sentence: str, dominance: dict[str, int], index: int,
                      species_names: list[str] | None = None) -> list[str]:
    """Check "X was the most abundant species in N samples" against computed counts."""

    if not dominance or not DOMINANCE_CUE.search(sentence):
        return []
    known = {}
    for taxon in dominance:
        for alias in _aliases(taxon):
            known[alias.lower()] = taxon
    vocabulary = {alias.lower() for taxon in (species_names or []) for alias in _aliases(taxon)}
    species = re.compile(r"\b[A-Z][a-z]+ [a-z]{3,}\b|\b[A-Z]\. [a-z]{3,}\b")
    hits = [(m.start(), m.group(0)) for m in species.finditer(sentence)]
    issues = []
    for n, (start, name) in enumerate(hits):
        end = hits[n + 1][0] if n + 1 < len(hits) else len(sentence)
        segment = sentence[start:end]
        for match in COUNT.finditer(segment):
            if PREVALENCE_CUE.search(segment[: match.start()]):
                continue
            token = match.group(2).lower()
            count = int(token) if token.isdigit() else WORDS.get(token)
            taxon = known.get(name.lower())
            if taxon is None:
                if name.lower() in vocabulary:
                    issues.append(f"Sentence {index + 1} says {name} was the most abundant species in {token} sample(s), "
                                  "but it was not the most abundant species in any sample. Use the species listed in the facts.")
            elif count is not None and count != dominance[taxon]:
                issues.append(f"Sentence {index + 1} says {name} was the most abundant species in {token} sample(s); "
                              f"the facts say {dominance[taxon]}.")
            break
    return issues


ALL_SAMPLES = re.compile(r"\b(?:above|present|detected|exceed\w*|more than|at least)\b[^;]*?"
                         r"\bin (?:all|every|each of the)\b(?: (?:the )?(?:\d+|\w+))? samples?\b", re.I)
CLAUSE_START = re.compile(r",\s*(?:with|while|whereas|but)\s|;|\b(?:while|whereas|however)\b", re.I)


def _prevalence_issues(sentence: str, prevalence: dict[str, int], total: int, index: int) -> list[str]:
    """Check "X, Y and Z were above 0.1% in all samples" against computed prevalence."""

    if not prevalence or not total:
        return []
    aliases = {alias.lower(): taxon for taxon in prevalence for alias in _aliases(taxon)}
    issues = []
    for match in ALL_SAMPLES.finditer(sentence):
        starts = [m.end() for m in CLAUSE_START.finditer(sentence, 0, match.start())]
        clause = sentence[starts[-1] if starts else 0: match.end()]
        for alias, taxon in aliases.items():
            if re.search(rf"\b{re.escape(alias)}\b", clause, re.I) and prevalence[taxon] < total:
                issues.append(f"Sentence {index + 1} says {alias} was above the threshold in all samples; "
                              f"the facts say {prevalence[taxon]} of {total} samples.")
    return list(dict.fromkeys(issues))


CITATION = re.compile(r"\[(@[^\]]+)\]")
CITE_KEY = re.compile(r"@([A-Za-z0-9_:.-]+)")
SELF_REFERENCE = re.compile(r"\b(this study|the present (?:study|analysis|samples|data|work)|here,? we|we |our |"
                            r"these (?:nine |\d+ )?samples|in this work|the samples (?:analy[sz]ed|profiled) here)",
                            re.I)
NAMED_AUTHOR = re.compile(r"\b[A-Z][a-z]+ et al\.?|\b[A-Z][a-z]+ and [A-Z][a-z]+ \(\d{4}\)")


def citation_keys(text: str) -> list[str]:
    return [key for inner in CITATION.findall(text) for key in CITE_KEY.findall(inner)]


def _citation_issues(sentence: str, paragraph: dict[str, Any], index: int) -> list[str]:
    allowed = set(paragraph.get("citations") or [])
    keys = citation_keys(sentence)
    issues = []
    for key in keys:
        if key not in allowed:
            issues.append(f"Sentence {index + 1} cites [@{key}], which is not a reference for this paragraph. "
                          "Use only the citation markers in the facts.")
    if NAMED_AUTHOR.search(CITATION.sub("", sentence)):
        issues.append(f"Sentence {index + 1} names authors in the text. Use the citation marker instead.")
    if paragraph.get("require_citations") and not keys and not SELF_REFERENCE.search(sentence):
        issues.append(f"Sentence {index + 1} reports earlier work without a citation. End it with the citation "
                      "marker of the reference it comes from, or remove it.")
    return issues


def deterministic_issues(text: str, paragraph: dict[str, Any]) -> tuple[list[str], set[int]]:
    issues: list[str] = []
    bad: set[int] = set()
    allowed = _evidence_numbers(paragraph)
    evidence_raw = " ".join([*paragraph.get("facts", []), *paragraph.get("notes", []),
                             paragraph.get("purpose", ""), paragraph.get("context", "")])
    evidence = evidence_raw.lower()
    identifiers = set(IDENTIFIER.findall(evidence_raw))
    checks = paragraph.get("checks") or {}
    dominance = checks.get("dominance") or {}
    species_names = checks.get("species") or []
    for index, sentence in enumerate(split_sentences(text)):
        for token in IDENTIFIER.findall(sentence):
            if token not in identifiers:
                issues.append(f"Sentence {index + 1} names the identifier {token}, which is not in the facts. "
                              "Copy identifiers exactly or leave them out.")
                bad.add(index)
        citation_problems = _citation_issues(sentence, paragraph, index)
        if citation_problems:
            issues.extend(citation_problems)
            bad.add(index)
        dominance_problems = _dominance_issues(sentence, dominance, index, species_names)
        dominance_problems += _prevalence_issues(sentence, checks.get("prevalence") or {},
                                                 int(checks.get("sample_count") or 0), index)
        if dominance_problems:
            issues.extend(dominance_problems)
            bad.add(index)
        for token in _numbers(sentence):
            if round(float(token.replace(",", "")), 6) not in allowed:
                issues.append(f'Sentence {index + 1} uses the number {token}, which is not in the facts. Remove it or copy a number from the facts.')
                bad.add(index)
        for match in OVERCLAIM.finditer(sentence):
            word = match.group(0)
            if word.lower() in evidence:
                continue
            issues.append(f'Sentence {index + 1} uses "{word}", which claims more than the evidence supports. Rephrase it descriptively.')
            bad.add(index)
        if LEDGER_ID.search(sentence):
            issues.append(f"Sentence {index + 1} contains a fact id in brackets. Remove the bracketed id.")
            bad.add(index)
    words = len(text.split())
    if words > int(paragraph.get("max_words", 150) * 1.4):
        issues.append(f"The paragraph has {words} words; shorten it to at most {paragraph.get('max_words')} words.")
    return issues, bad


def _judge_prompt(text: str, paragraph: dict[str, Any]) -> str:
    sentences = split_sentences(text)
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences))
    facts = "\n".join(f"- {f}" for f in paragraph.get("facts", [])) or "- (none)"
    notes = "\n".join(f"- {n}" for n in paragraph.get("notes", [])) or "- (none)"
    return (
        "Evidence: facts\n" + facts + "\n\nEvidence: allowed interpretations\n" + notes +
        "\n\nParagraph sentences:\n" + numbered +
        "\n\nFor each sentence, decide whether every claim in it is stated by the evidence above. "
        "A sentence that only names what was measured, or restates a fact in other words, is supported. "
        "Species written with an abbreviated genus (for example F. prausnitzii) are the same as the full name in "
        "the evidence. Read the whole evidence list before deciding that something is missing. "
        "Facts that begin with a marker such as [@Key] are claims of that reference: a sentence ending with "
        "[@Key] is supported only if that reference's claims state it. "
        "A sentence is NOT supported if it adds a cause, a comparison with other studies, a judgement such as "
        "typical or effective, a group difference, or any detail that the evidence does not state. "
        "For unsupported sentences, write the problem in one short sentence; otherwise write an empty problem. "
        'Return JSON: {"sentences": [{"index": 0, "supported": true, "problem": ""}, ...]}'
    )


def model_issues(model: Any, text: str, paragraph: dict[str, Any]) -> tuple[list[str], set[int], str]:
    prompt = _judge_prompt(text, paragraph)
    try:
        if hasattr(model, "generate_json"):
            data = model.generate_json(prompt, JUDGE_SYSTEM, JUDGE_SCHEMA)
        else:
            raw = model.generate(prompt, system=JUDGE_SYSTEM)
            data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except Exception as error:  # noqa: BLE001 — the judge is advisory; deterministic checks still apply
        return [], set(), f"judge unavailable: {error}"
    count = len(split_sentences(text))
    issues, bad = [], set()
    for row in (data or {}).get("sentences", []):
        try:
            index = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        if 0 <= index < count and row.get("supported") is False:
            problem = str(row.get("problem") or "it is not supported by the facts").strip()
            issues.append(f"Sentence {index + 1} is not supported: {problem}")
            bad.add(index)
    return issues, bad, ""


def verify(text: str, paragraph: dict[str, Any], model: Any = None) -> dict[str, Any]:
    issues, bad = deterministic_issues(text, paragraph)
    judge_note = "judge skipped"
    judged: list[str] = []
    if model is not None:
        judged, judged_bad, judge_note = model_issues(model, text, paragraph)
        sentences = len(split_sentences(text))
        # A judge that rejects most of a paragraph is more likely wrong than the paragraph.
        if judged_bad and len(judged_bad) <= max(1, sentences // 2):
            issues.extend(judged)
            bad |= judged_bad
        elif judged_bad:
            judge_note = f"judge rejected {len(judged_bad)}/{sentences} sentences; ignored as unreliable"
            judged = []
    return {"ok": not issues, "issues": issues, "bad_sentences": sorted(bad),
            "judge": judge_note or "judge ran", "judge_issues": len(judged)}


def drop_sentences(text: str, indexes) -> str:
    drop = set(indexes)
    return " ".join(s for i, s in enumerate(split_sentences(text)) if i not in drop)


@register_skill
class ClaimVerifierSkill(BaseSkill):
    spec = SkillSpec(
        name="claim-verifier",
        version="1.0.0",
        category=SkillCategory.WRITING,
        description="逐句核对单段文字：代码检查数字来源、过度表述和编号泄漏，模型判断每句是否有事实或允许的解读支撑。",
        tags=("writing", "verification", "factuality"),
        input_keys=("text", "paragraph", "model"),
        output_keys=("ok", "issues", "bad_sentences", "judge"),
    )

    def run(self, skill_input: SkillInput):
        payload = skill_input.require("text", "paragraph")
        result = verify(str(payload["text"]), payload["paragraph"], skill_input.get("model"))
        return self.success(data=result, summary="通过" if result["ok"] else f"{len(result['issues'])} 个问题")
