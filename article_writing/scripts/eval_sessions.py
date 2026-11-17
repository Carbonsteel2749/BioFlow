"""Score writing sessions against the fact ledger of their analysis package.

Usage: python eval_sessions.py PACKAGE_ROOT SESSION_ID [SESSION_ID ...]
Reads sessions from the writing API on 127.0.0.1:8766.
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path("/home/xh/BioFlow-writing")
sys.path[:0] = [str(ROOT), str(ROOT / "article_writing")]
from bioflow_skills.skills.writing.fact_ledger import build_ledger  # noqa: E402

NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])\d+(?:\.\d+)?")
RANGE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*%?\s*(?:to|and|-|–)\s*(\d[\d,]*(?:\.\d+)?)")
OVERCLAIM = re.compile(r"\b(significant(?:ly)?|enriched|depleted|associated with|caus(?:e|ed|al)|"
                       r"higher in|lower in|dysbiosis|biomarker)\b", re.I)
SECTIONS = ["abstract", "introduction", "methods", "results", "discussion", "conclusion", "back_matter"]


def norm(token: str) -> float | None:
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def _raw_values(value, out):
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        out.add(round(float(value), 6))
        out.add(round(float(value), 5))
    elif isinstance(value, dict):
        for item in value.values():
            _raw_values(item, out)
    elif isinstance(value, list):
        for item in value:
            _raw_values(item, out)


def ledger_numbers(ledger):
    values = set()
    for fact in ledger["facts"]:
        _raw_values(fact.get("value"), values)
        for token in NUMBER.findall(f"{fact['text']} {fact['label']}"):
            v = norm(token)
            if v is not None:
                values.add(round(v, 6))
    return values


def ledger_ranges(ledger):
    pairs = set()
    for fact in ledger["facts"]:
        if fact["id"].endswith(".range"):
            nums = [norm(t) for t in NUMBER.findall(fact["text"])]
            nums = [n for n in nums if n is not None]
            if len(nums) >= 2:
                pairs.add((round(nums[0], 6), round(nums[1], 6)))
    return pairs


def english(markdown: str) -> str:
    return markdown.split("\n中文\n")[0]


def score(text, known, ranges):
    nums = [norm(t) for t in NUMBER.findall(text)]
    nums = [n for n in nums if n is not None and not (n < 20 and float(n).is_integer())]
    unsourced = sorted({n for n in nums if round(n, 6) not in known
                        and round(n / 100, 6) not in known and round(n * 100, 6) not in known})
    bad_ranges = []
    for a, b in RANGE.findall(text):
        pa, pb = norm(a), norm(b)
        if pa is None or pb is None or pa < 20 and pb < 20 and float(pa).is_integer():
            continue
        if (round(pa, 6), round(pb, 6)) not in ranges:
            bad_ranges.append(f"{a} to {b}")
    return {"words": len(text.split()), "numbers": len(nums), "unsourced": unsourced[:10],
            "bad_ranges": bad_ranges[:6], "overclaim": sorted({m.lower() for m in OVERCLAIM.findall(text)})}


def main():
    package, sessions = sys.argv[1], sys.argv[2:]
    ledger = build_ledger(package)
    known, ranges = ledger_numbers(ledger), ledger_ranges(ledger)
    for sid in sessions:
        with urllib.request.urlopen(f"http://127.0.0.1:8766/api/sessions/{sid}") as response:
            data = json.load(response)
        print(f"\n######## session {sid} status={data.get('status')}")
        totals = {"numbers": 0, "unsourced": 0, "bad_ranges": 0, "overclaim": 0}
        for name in SECTIONS:
            section = (data.get("sections") or {}).get(name)
            if not section:
                continue
            s = score(english(section.get("markdown") or ""), known, ranges)
            totals["numbers"] += s["numbers"]
            totals["unsourced"] += len(s["unsourced"])
            totals["bad_ranges"] += len(s["bad_ranges"])
            totals["overclaim"] += len(s["overclaim"])
            print(f"{name:13s} words={s['words']:4d} numbers={s['numbers']:3d} "
                  f"unsourced={s['unsourced']} bad_ranges={s['bad_ranges']} overclaim={s['overclaim']}")
        traced = totals["numbers"] - totals["unsourced"]
        rate = 100 * traced / totals["numbers"] if totals["numbers"] else 100
        print(f"TOTAL numbers={totals['numbers']} traceable={rate:.1f}% wrong_ranges={totals['bad_ranges']} "
              f"overclaim_terms={totals['overclaim']}")


if __name__ == "__main__":
    main()
