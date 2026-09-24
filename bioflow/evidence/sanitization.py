from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from pydantic import BaseModel, Field


class SanitizationRules(BaseModel):
    allow_external: bool = False
    banned_terms: List[str] = Field(default_factory=list)
    allowlist_fields: List[str] = Field(default_factory=list)


@dataclass
class SanitizationFinding:
    rule: str
    count: int


@dataclass
class SanitizationResult:
    text: str
    findings: List[SanitizationFinding]


_DEFAULT_PATTERNS: Dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "phone": re.compile(r"\b(?:\+?\d[\d\-\s]{8,}\d)\b"),
    "id_number": re.compile(r"\b\d{6,}\b"),
    "ipv4": re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
}


def _apply_patterns(text: str) -> Tuple[str, List[SanitizationFinding]]:
    findings: List[SanitizationFinding] = []
    redacted = text
    for name, pattern in _DEFAULT_PATTERNS.items():
        matches = pattern.findall(redacted)
        if matches:
            redacted = pattern.sub("[REDACTED]", redacted)
            findings.append(SanitizationFinding(rule=name, count=len(matches)))
    return redacted, findings


def _apply_banned_terms(text: str, banned_terms: Iterable[str]) -> Tuple[str, List[SanitizationFinding]]:
    findings: List[SanitizationFinding] = []
    redacted = text
    for term in banned_terms:
        if not term:
            continue
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        matches = pattern.findall(redacted)
        if matches:
            redacted = pattern.sub("[REDACTED]", redacted)
            findings.append(SanitizationFinding(rule=f"term:{term}", count=len(matches)))
    return redacted, findings


def sanitize_text(text: str, rules: SanitizationRules) -> SanitizationResult:
    cleaned, findings = _apply_patterns(text)
    cleaned, term_findings = _apply_banned_terms(cleaned, rules.banned_terms)
    return SanitizationResult(text=cleaned, findings=findings + term_findings)


def sanitize_payload(payload: Dict, rules: SanitizationRules) -> Dict:
    sanitized: Dict = {}
    for key, value in payload.items():
        if rules.allowlist_fields and key not in rules.allowlist_fields:
            continue
        if isinstance(value, str):
            result = sanitize_text(value, rules)
            sanitized[key] = result.text
        else:
            sanitized[key] = value
    return sanitized
