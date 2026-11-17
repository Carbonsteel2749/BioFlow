"""reference-cards: turn the author's chosen papers into citable evidence cards.

Input is a list of references (DOI plus whatever bibliographic fields are
known). For each DOI the skill fetches title, authors, journal, year and
abstract from Europe PMC, falling back to Crossref, and caches the record on
disk. The abstract is split into numbered sentences.

With a model (``payload['model']``) each abstract is reduced to a few claim
cards. Every claim must name the abstract sentences it rests on, and a claim
whose wording drifts too far from those sentences is dropped. Without a model
the first sentences are used verbatim.

Only references the author selected are used. Nothing is searched for.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

try:  # 标准包调用
    from ...core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from ...registry import register_skill
except ImportError:  # 兼容早期顶层脚本调用
    from core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from registry import register_skill


EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={query}&resultType=core&format=json"
CROSSREF = "https://api.crossref.org/works/{doi}"
USER_AGENT = "BioFlow-writing/1.0 (reference cards)"
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")
TAG = re.compile(r"<[^>]+>")
STOP = set("a an the of and or in on to for with by from as at is are was were be been that this these those "
           "which we our it its their than into via can may also both".split())
CLAIM_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "sentences": {"type": "array", "items": {"type": "integer"}},
                    "kind": {"type": "string", "enum": ["background", "finding", "method", "limitation"]},
                },
                "required": ["text", "sentences", "kind"],
            },
        }
    },
    "required": ["claims"],
}
CLAIM_SYSTEM = "You extract claims from a scientific abstract. You never add information."
MIN_OVERLAP = 0.4
MAX_CLAIMS = 5


def normalise_doi(doi: str) -> str:
    doi = (doi or "").strip()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.I)
    return doi.lower()


def clean_text(text: str) -> str:
    return " ".join(html.unescape(TAG.sub(" ", text or "")).split())


def _get_json(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_europe_pmc(doi: str, timeout: float = 20.0) -> dict[str, Any] | None:
    query = urllib.parse.quote(f'DOI:"{doi}"')
    data = _get_json(EUROPE_PMC.format(query=query), timeout)
    results = (data.get("resultList") or {}).get("result") or []
    match = next((r for r in results if normalise_doi(r.get("doi", "")) == doi), results[0] if results else None)
    if not match:
        return None
    journal = ((match.get("journalInfo") or {}).get("journal") or {}).get("title") or match.get("journalTitle") or ""
    authors = [a.get("lastName", "") + (" " + a.get("initials", "") if a.get("initials") else "")
               for a in ((match.get("authorList") or {}).get("author") or []) if a.get("lastName")]
    return {
        "doi": doi, "title": clean_text(match.get("title", "")).rstrip("."),
        "authors": authors or [s.strip() for s in (match.get("authorString") or "").rstrip(".").split(",") if s.strip()],
        "year": str(match.get("pubYear") or ""), "journal": clean_text(journal),
        "pmid": str(match.get("pmid") or ""), "abstract": clean_text(match.get("abstractText", "")),
        "source": "Europe PMC",
    }


def fetch_crossref(doi: str, timeout: float = 20.0) -> dict[str, Any] | None:
    data = _get_json(CROSSREF.format(doi=urllib.parse.quote(doi)), timeout).get("message") or {}
    if not data:
        return None
    year = ""
    for key in ("published-print", "published-online", "issued"):
        parts = (data.get(key) or {}).get("date-parts") or []
        if parts and parts[0]:
            year = str(parts[0][0])
            break
    authors = [f"{a.get('family', '')} {''.join(p[0] for p in a.get('given', '').split() if p)}".strip()
               for a in data.get("author") or [] if a.get("family")]
    return {
        "doi": doi, "title": clean_text(" ".join(data.get("title") or [])).rstrip("."), "authors": authors,
        "year": year, "journal": clean_text(" ".join(data.get("container-title") or [])), "pmid": "",
        "abstract": re.sub(r"^Abstract\s*", "", clean_text(data.get("abstract", ""))), "source": "Crossref",
    }


def fetch_record(doi: str, cache_dir: Path | None, fetchers: list[Callable[[str], dict | None]],
                 offline: bool = False) -> tuple[dict[str, Any] | None, str]:
    cache = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache = cache_dir / (hashlib.sha256(doi.encode()).hexdigest()[:24] + ".json")
        if cache.is_file():
            return json.loads(cache.read_text(encoding="utf-8")), "cache"
    if offline:
        return None, "offline"
    errors = []
    best = None
    for fetch in fetchers:
        try:
            record = fetch(doi)
        except Exception as error:  # noqa: BLE001 — try the next source
            errors.append(f"{getattr(fetch, '__name__', 'source')}: {error}")
            continue
        if record and record.get("title"):
            if best is None:
                best = record
            elif not best.get("abstract") and record.get("abstract"):
                best = {**best, "abstract": record["abstract"], "source": f"{best['source']} + {record['source']}"}
            if best.get("abstract"):
                break
    if best and cache is not None:
        cache.write_text(json.dumps(best, ensure_ascii=False, indent=1), encoding="utf-8")
    return best, "; ".join(errors)


def cite_key(authors: list[str], year: str, title: str, used: set[str]) -> str:
    surname = (authors[0].split()[0] if authors else (title.split()[0] if title else "ref"))
    surname = unicodedata.normalize("NFKD", surname).encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z]", "", surname).capitalize() or "Ref"
    key = f"{base}{year or 'nd'}"
    suffix = 0
    while key in used:
        suffix += 1
        key = f"{base}{year or 'nd'}{chr(96 + suffix)}"
    used.add(key)
    return key


def short_authors(authors: list[str]) -> str:
    if not authors:
        return ""
    return authors[0] + (" et al." if len(authors) > 2 else (f" and {authors[1]}" if len(authors) == 2 else ""))


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 2}


def overlap(claim: str, source: str) -> float:
    claim_words = _words(claim)
    return len(claim_words & _words(source)) / len(claim_words) if claim_words else 0.0


def _claim_prompt(card: dict[str, Any]) -> str:
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(card["sentences"]))
    return (
        f"Title: {card['title']}\n\nAbstract sentences:\n{numbered}\n\n"
        f"Extract up to {MAX_CLAIMS} claims a later paper could cite from this abstract. Each claim is one plain "
        "sentence that restates what the abstract says, using its own words where possible, and lists the "
        "numbers of the abstract sentences it comes from. Do not combine claims from different studies, do not "
        "generalise, and do not add anything. kind is background (what is known), finding (what this study "
        "found), method (what this study did) or limitation."
    )


def extract_claims(card: dict[str, Any], model: Any = None) -> tuple[list[dict[str, Any]], str]:
    sentences = card["sentences"]
    if not sentences:
        return [], "no abstract"
    raw: list[dict[str, Any]] = []
    note = "verbatim fallback"
    if model is not None:
        try:
            if hasattr(model, "generate_json"):
                data = model.generate_json(_claim_prompt(card), CLAIM_SYSTEM, CLAIM_SCHEMA)
            else:
                text = model.generate(_claim_prompt(card), system=CLAIM_SYSTEM)
                data = json.loads(text[text.find("{"): text.rfind("}") + 1])
            raw = list((data or {}).get("claims") or [])
            note = "model"
        except Exception as error:  # noqa: BLE001
            note = f"model failed ({error}); verbatim fallback"
            raw = []
    claims = []
    for item in raw:
        ids = sorted({int(i) for i in item.get("sentences") or [] if isinstance(i, int) and 0 <= i < len(sentences)})
        text = " ".join(str(item.get("text") or "").split())
        if not ids or not text:
            continue
        source = " ".join(sentences[i] for i in ids)
        if overlap(text, source) < MIN_OVERLAP:
            continue
        kind = item.get("kind") if item.get("kind") in {"background", "finding", "method", "limitation"} else "finding"
        claims.append({"text": text, "sentences": ids, "kind": kind, "quote": source})
    if not claims:
        claims = [{"text": s, "sentences": [i], "kind": "background" if i == 0 else "finding", "quote": s}
                  for i, s in enumerate(sentences[:3])]
    for n, claim in enumerate(claims[:MAX_CLAIMS], 1):
        claim["id"] = f"{card['key']}.C{n}"
    return claims[:MAX_CLAIMS], note


def build_cards(refs: list[dict[str, Any]], *, model: Any = None, cache_dir: str | Path | None = None,
                offline: bool = False, fetchers: list[Callable[[str], dict | None]] | None = None) -> dict[str, Any]:
    fetchers = fetchers if fetchers is not None else [fetch_europe_pmc, fetch_crossref]
    cache = Path(cache_dir) if cache_dir else None
    used: set[str] = set()
    seen: set[str] = set()
    cards, skipped = [], []
    for ref in refs:
        doi = normalise_doi(str(ref.get("doi") or ""))
        if not doi:
            skipped.append({"title": ref.get("title", ""), "reason": "no DOI"})
            continue
        if doi in seen:
            continue
        seen.add(doi)
        record, error = fetch_record(doi, cache, fetchers, offline=offline)
        record = dict(record or {})
        title = record.get("title") or str(ref.get("title") or "")
        authors = record.get("authors") or [a.strip() for a in str(ref.get("authors") or "").split(",") if a.strip()]
        year = record.get("year") or str(ref.get("year") or "")
        if not title:
            skipped.append({"doi": doi, "reason": f"not found ({error or 'no record'})"})
            continue
        abstract = record.get("abstract", "")
        card = {
            "key": cite_key(authors, year, title, used), "doi": doi, "title": title, "authors": authors,
            "short_authors": short_authors(authors), "year": year, "journal": record.get("journal", ""),
            "pmid": record.get("pmid", ""), "source": record.get("source", "author metadata"),
            "abstract": abstract, "sentences": [s.strip() for s in SENTENCE.split(abstract) if s.strip()],
        }
        card["claims"], card["claim_extraction"] = extract_claims(card, model)
        card["status"] = "ok" if card["claims"] else "no_abstract"
        cards.append(card)
    return {"cards": cards, "skipped": skipped}


@register_skill
class ReferenceCardsSkill(BaseSkill):
    spec = SkillSpec(
        name="reference-cards",
        version="1.0.0",
        category=SkillCategory.LITERATURE,
        description="按作者所选文献的 DOI 获取题录与摘要（Europe PMC，Crossref 备用，本地缓存），并提炼为带原句出处的可引用论据卡片。",
        tags=("literature", "citation", "evidence", "writing"),
        input_keys=("refs", "model", "cache_dir", "offline"),
        output_keys=("cards", "skipped"),
    )

    def run(self, skill_input: SkillInput):
        refs = list(skill_input.require("refs")["refs"] or [])
        data = build_cards(refs, model=skill_input.get("model"), cache_dir=skill_input.get("cache_dir"),
                           offline=bool(skill_input.get("offline")), fetchers=skill_input.get("fetchers"))
        return self.success(data=data, summary=f"{len(data['cards'])} 张文献卡片，{len(data['skipped'])} 篇跳过。")
