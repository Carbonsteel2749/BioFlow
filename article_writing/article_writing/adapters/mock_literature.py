from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from article_writing.adapters.base import LiteraturePort
from article_writing.adapters.fixtures_loader import load_literature
from article_writing.contracts import LiteratureHit

_TOKEN_RE = re.compile(r"[a-z0-9\u4e00-\u9fff]+", re.IGNORECASE)


def _tokens(text: str) -> list[str]:
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]


class MockLiteraturePort(LiteraturePort):
    """Fixture-backed literature search. Swap via factory to a live LiteraturePort."""

    def __init__(self, fixtures_dir: Path | str | None = None) -> None:
        self._hits = load_literature(fixtures_dir)

    def search(self, query: str, top_k: int = 5) -> List[LiteratureHit]:
        if top_k <= 0:
            return []
        query = (query or "").strip()
        if not query:
            return sorted(self._hits, key=lambda h: h.score, reverse=True)[:top_k]

        query_l = query.lower()
        tokens = _tokens(query_l)
        ranked: List[tuple[float, LiteratureHit]] = []
        for hit in self._hits:
            blob = f"{hit.title} {hit.abstract} {' '.join(hit.tags)}".lower()
            score = float(hit.score)
            if query_l in blob:
                score += 2.0
            for tok in tokens:
                if tok in blob:
                    score += 1.0
            ranked.append((score, hit))
        ranked.sort(key=lambda item: (item[0], item[1].score), reverse=True)
        return [hit for _, hit in ranked[:top_k]]

    def get(self, paper_id: str) -> Optional[LiteratureHit]:
        for hit in self._hits:
            if hit.paper_id == paper_id:
                return hit
        return None
