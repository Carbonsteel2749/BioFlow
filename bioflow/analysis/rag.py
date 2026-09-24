from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

from bioflow.core.models import EvidenceRef


_QUALIFIER_WORDS = {
    "significant",
    "significantly",
    "improve",
    "improves",
    "improved",
    "improvement",
    "increase",
    "increased",
    "decrease",
    "decreased",
    "higher",
    "lower",
    "better",
    "worse",
    "markedly",
    "strongly",
    "weakly",
    "trend",
    "association",
    "associated",
    "effect",
    "effects",
    "promote",
    "promotes",
    "inhibit",
    "inhibits",
    "statistically",
    "significance",
}


@dataclass
class RagResult:
    answer: str
    sources: List[str]
    references: List[str]
    conflict_note: Optional[str] = None


class RagClient:
    def __init__(self, url: Optional[str] = None, timeout: int = 60) -> None:
        self.url = url or os.getenv("BIOFLOW_RAG_URL", "")
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.url)

    def query(self, query: str) -> Optional[RagResult]:
        if not self.url:
            return None
        response = requests.post(
            self.url,
            json={"query": query, "top_k": 5, "temperature": 0.2, "source_scope": "all"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        return RagResult(
            answer=str(data.get("answer", "")),
            sources=list(data.get("sources", []) or []),
            references=list(data.get("references", []) or []),
            conflict_note=data.get("conflict_note"),
        )


def build_rag_query_terms(text: str) -> str:
    cleaned = text.lower()
    cleaned = re.sub(r"[\(\)\[\]\{\},.;:!?/\\\"'`~@#$%^&*_+=<>|]", " ", cleaned)
    cleaned = re.sub(r"\b\d+(?:\.\d+)?\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    tokens = []
    for token in cleaned.split():
        if token in _QUALIFIER_WORDS:
            continue
        if len(token) < 2:
            continue
        tokens.append(token)
    return " ".join(tokens[:12])


def evidence_to_query(evidence: List[EvidenceRef], fallback_text: str) -> str:
    parts: List[str] = []
    for item in evidence:
        parts.extend([item.title, item.snippet, item.locator])
    parts.append(fallback_text)
    return build_rag_query_terms(" ".join(parts))
