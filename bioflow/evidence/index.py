from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List

from bioflow.core.models import EvidenceRef


class EvidenceIndex:
    def add(self, evidence: Iterable[EvidenceRef]) -> None:
        raise NotImplementedError

    def search(self, query: str, limit: int = 5) -> List[EvidenceRef]:
        raise NotImplementedError


class JsonlEvidenceIndex(EvidenceIndex):
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: List[EvidenceRef] = []
        if self.path.exists():
            self._cache = self._load()

    def _load(self) -> List[EvidenceRef]:
        items: List[EvidenceRef] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            items.append(EvidenceRef.model_validate(data))
        return items

    def add(self, evidence: Iterable[EvidenceRef]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            for item in evidence:
                handle.write(item.model_dump_json() + "\n")
                self._cache.append(item)

    def search(self, query: str, limit: int = 5) -> List[EvidenceRef]:
        if not query:
            return self._cache[:limit]
        keywords = [token.lower() for token in query.split() if token]
        scored: List[tuple[float, EvidenceRef]] = []
        for item in self._cache:
            text = f"{item.title} {item.snippet}".lower()
            score = sum(1 for key in keywords if key in text)
            if score:
                scored.append((float(score), item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]
