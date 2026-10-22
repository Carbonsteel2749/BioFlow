"""Map Article_repository rows / API payloads -> LiteratureHit (V1).

也包含 ai-localbase 后端的映射工具：向量库里的文献以
``标题(Title): ...`` + ``向量正文(Vector Body): ...`` 两段存储，
文档名是 DOI（``/`` 被替换成 ``_``），据此还原 LiteratureHit。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping, Optional

from article_writing.contracts import LiteratureHit


def _authors_to_str(authors: Any) -> str:
    if authors is None:
        return ""
    if isinstance(authors, str):
        return authors.strip()
    if isinstance(authors, list):
        names: list[str] = []
        for item in authors:
            if isinstance(item, Mapping):
                name = item.get("name") or item.get("author") or item.get("full_name") or ""
                names.append(str(name).strip())
            else:
                names.append(str(item).strip())
        return "; ".join(n for n in names if n)
    return str(authors).strip()


def _year_from_publish_time(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.year
    if hasattr(value, "year"):
        try:
            return int(value.year)
        except (TypeError, ValueError):
            return None
    text = str(value).strip()
    if len(text) >= 4 and text[:4].isdigit():
        year = int(text[:4])
        if 1900 <= year <= 2100:
            return year
    return None


def _tags_from_row(row: Mapping[str, Any]) -> list[str]:
    tags: list[str] = []
    keywords = row.get("keywords") or []
    if isinstance(keywords, list):
        tags.extend(str(k).strip() for k in keywords if str(k).strip())
    elif isinstance(keywords, str) and keywords.strip():
        tags.append(keywords.strip())

    llm_tags = row.get("llm_tags") or {}
    if isinstance(llm_tags, Mapping):
        for key, value in llm_tags.items():
            key_s = str(key).strip()
            if isinstance(value, list):
                for item in value:
                    item_s = str(item).strip()
                    if item_s:
                        tags.append(f"{key_s}:{item_s}" if key_s else item_s)
            else:
                value_s = str(value).strip()
                if value_s and value_s not in {"None", "null"}:
                    tags.append(f"{key_s}:{value_s}" if key_s else value_s)
    return tags


def normalize_doi_paper_id(paper_id: str) -> str:
    """Strip optional ``doi:`` prefix; paper_id is DOI for this integration."""
    text = (paper_id or "").strip()
    lower = text.lower()
    if lower.startswith("doi:"):
        return text[4:].strip()
    return text


def repository_row_to_hit(row: Mapping[str, Any]) -> LiteratureHit:
    """Convert a MetadataStore / API article dict into LiteratureHit.

    ``paper_id`` is set to DOI (Phase-1 wiring; pmid lookup deferred).
    """
    doi = str(row.get("doi") or "").strip()
    if not doi:
        raise ValueError("article row missing doi; cannot build LiteratureHit.paper_id")

    citation = row.get("citation_count")
    try:
        score = float(citation) if citation is not None else 0.0
    except (TypeError, ValueError):
        score = 0.0

    return LiteratureHit(
        paper_id=doi,
        title=str(row.get("title") or "").strip(),
        abstract=str(row.get("abstract") or "").strip(),
        authors=_authors_to_str(row.get("authors")),
        year=_year_from_publish_time(row.get("publish_time")),
        doi=doi,
        pmid=None,
        score=score,
        tags=_tags_from_row(row),
    )


# ── ai-localbase 后端映射 ───────────────────────────────────────────────────

# 上传原文的两段式格式（见 Article_repository/integration/ai_localbase.py）
_TITLE_RE = re.compile(r"标题\s*\(Title\)\s*[:：]\s*(?P<title>.+)")
_BODY_RE = re.compile(r"向量正文\s*\(Vector Body\)\s*[:：]\s*(?P<body>.*)", re.S)
_MEDIA_EXT_RE = re.compile(r"\.(txt|md|markdown|pdf|csv|tsv|xlsx?|json)$", re.IGNORECASE)
_DOI_PREFIX_RE = re.compile(r"^10\.\d{4,9}[._/]")
_YEAR_RE = re.compile(r"(?<!\d)(19[89]\d|20[0-4]\d)(?!\d)")


def doi_from_document_name(name: str) -> str:
    """从 ai-localbase 文档名还原 DOI（``10.1016_j.phrs.2026.108284.txt``）。

    上传时把 DOI 里的 ``/`` 换成了 ``_``，只需还原第一处分隔符；
    不是 DOI 命名的文档返回空串。
    """
    stem = _MEDIA_EXT_RE.sub("", (name or "").strip())
    if not _DOI_PREFIX_RE.match(stem):
        return ""
    return stem.replace("_", "/", 1)


def doi_to_document_stem(doi: str) -> str:
    """DOI → 上传文档名的去扩展名形式（用于按 DOI 定位文档）。"""
    clean = normalize_doi_paper_id(doi)
    return clean.replace("/", "_")


def year_from_doi(doi: str) -> Optional[int]:
    """从 DOI 中提取年份。

    向量库不保存作者/年份，多数期刊 DOI 的字面量里带出版年份
    （如 ``10.1016/j.phrs.2026.108284`` → 2026）；取不到就返回 None，
    不做臆测。
    """
    match = _YEAR_RE.search(normalize_doi_paper_id(doi))
    if not match:
        return None
    year = int(match.group(1))
    return year if 1900 <= year <= 2100 else None


def parse_uploaded_article_text(text: str) -> tuple[str, str]:
    """从上传原文（或 chunk 文本）解析 ``(title, body)``。

    chunk 0 含标题行；其余 chunk 只有正文，title 为空串。
    """
    raw = (text or "").strip()
    if not raw:
        return "", ""

    title = ""
    title_match = _TITLE_RE.search(raw)
    if title_match:
        title = title_match.group("title").strip().splitlines()[0].strip()

    body = ""
    body_match = _BODY_RE.search(raw)
    if body_match:
        body = body_match.group("body").strip()
    elif not title:
        body = raw
    else:
        # 有标题行但没有正文标记：标题行之后的内容都算正文
        body = raw[title_match.end() :].strip() if title_match else raw

    return title, body


def ai_localbase_document_to_hit(
    *,
    document_id: str,
    document_name: str,
    raw_content: str = "",
    chunk_texts: Optional[list[str]] = None,
    score: float = 0.0,
    snippet_chars: int = 1200,
) -> LiteratureHit:
    """ai-localbase 文档（原文或命中 chunk）→ LiteratureHit。

    - ``paper_id`` / ``doi``：文档名还原出的 DOI
    - ``title``：chunk 或原文里的 ``标题(Title):`` 行
    - ``abstract``：``向量正文(Vector Body):`` 内容（截断作为 snippet）
    - ``year``：由 DOI 字面量推导；``authors`` 向量库未保存，留空
    """
    doi = doi_from_document_name(document_name)
    if not doi:
        raise ValueError(f"document {document_id} is not DOI-named; cannot build LiteratureHit.paper_id")

    title = ""
    body = ""

    candidates = [text for text in (chunk_texts or []) if (text or "").strip()]
    if raw_content.strip():
        candidates.insert(0, raw_content)

    for text in candidates:
        candidate_title, candidate_body = parse_uploaded_article_text(text)
        if not title and candidate_title:
            title = candidate_title
        if not body and candidate_body:
            body = candidate_body
        if title and body:
            break

    if not title:
        title = doi

    abstract = body[:snippet_chars].strip()

    return LiteratureHit(
        paper_id=doi,
        title=title,
        abstract=abstract,
        authors="",
        year=year_from_doi(doi),
        doi=doi,
        pmid=None,
        score=float(score or 0.0),
        tags=["ai-localbase", f"doc:{document_id}"],
    )

