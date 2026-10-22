"""Thin LiteraturePort tool wrappers for the ReAct loop (no PaperQA/STORM dependency)."""

from __future__ import annotations

from article_writing.adapters.base import LiteraturePort
from article_writing.contracts import LiteratureHit


def tool_search(
    port: LiteraturePort,
    query: str,
    *,
    top_k: int = 3,
) -> tuple[list[LiteratureHit], str]:
    """Action ``search``: retrieve ranked hits and a short observation string."""

    hits = port.search(query, top_k=top_k)
    if not hits:
        return [], f'search({query!r}) returned no hits'
    lines = [
        f"- [{hit.paper_id}] {hit.title} (score={hit.score:.2f})" for hit in hits
    ]
    observation = f"search({query!r}) -> {len(hits)} hit(s):\n" + "\n".join(lines)
    return hits, observation


def tool_get(
    port: LiteraturePort,
    paper_id: str,
) -> tuple[LiteratureHit | None, str]:
    """Action ``get``: fetch one record for evidence inspection."""

    hit = port.get(paper_id)
    if hit is None:
        return None, f"get({paper_id!r}) returned nothing"
    snippet = " ".join((hit.abstract or "").split())
    if len(snippet) > 240:
        snippet = snippet[:239].rsplit(" ", 1)[0] + "…"
    observation = (
        f"get({paper_id!r}) -> title={hit.title!r}; "
        f"year={hit.year}; snippet={snippet!r}"
    )
    return hit, observation
