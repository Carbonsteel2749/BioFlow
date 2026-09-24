"""Live LiteraturePort backed by ai-localbase (Qdrant + HTTP).

背景：Article_repository 已把文献从本地 SQLite 迁到 ai-localbase 向量库，
``article_literature.sqlite3`` 行数为 0，因此论文模块不能再从 SQLite 读文献。

本适配器只用 ai-localbase 现有 HTTP 接口：

- ``POST /api/knowledge-bases/{kb}/retrieval/debug``  语义+BM25+稀疏三路召回（服务端自动中→英翻译）
- ``GET  /api/knowledge-bases/{kb}/documents``        文档清单（文档名即 DOI）
- ``GET  /api/knowledge-bases/{kb}/documents/{id}``   原文（标题 + 正文）

映射：文档名 ``10.1016_j.phrs.2026.108284.txt`` → DOI ``10.1016/j.phrs.2026.108284`` 作 paper_id；
``标题(Title):`` 行 → title；``向量正文(Vector Body):`` → abstract。
向量库未保存作者，故 ``authors`` 留空；``year`` 由 DOI 字面量推导（取不到则 None）。

注意：服务端每次检索固定只回 ``RETRIEVAL_TOPK_KNOWLEDGE_BASE``（默认 3）个结果，
因此本适配器在结果不足 top_k 时用**查询变体**多次检索再按文档合并去重，
以免论文只拿到 2~3 篇文献。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from article_writing.adapters.base import LiteraturePort
from article_writing.adapters.literature_mapping import (
    ai_localbase_document_to_hit,
    doi_from_document_name,
    doi_to_document_stem,
    normalize_doi_paper_id,
)
from article_writing.contracts import LiteratureHit

DEFAULT_AI_LOCALBASE_URL = "http://127.0.0.1:8080"
_CROSSREF_WORK_URL = "https://api.crossref.org/works/{doi}"
_ENRICH_WORKERS = 4
_ENRICH_TIMEOUT = 6.0

# 更正/勘误/撤稿类条目不是可引用的研究文献，避免进入参考文献
_CORRECTION_TITLE_RE = re.compile(
    r"^\s*(author\s+correction|correction\s+to|corrigendum|erratum|retraction|withdrawn)\b",
    re.IGNORECASE,
)

_QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9\-]{3,}")
_QUERY_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "were",
    "how", "what", "why", "does", "did", "into", "between", "among", "via",
    "using", "used", "based", "study", "studies", "effect", "effects",
    "role", "analysis", "associated", "association", "relationship",
}


def _crossref_metadata(doi: str, timeout: float = _ENRICH_TIMEOUT) -> Tuple[str, Optional[int]]:
    """按 DOI 从 Crossref 取 ``(authors, year)``；失败返回空值，绝不抛错。

    向量库只保存标题与正文，作者/年份需外部补齐；Crossref 单次请求即可拿到两者。
    """
    mailto = os.getenv("CROSSREF_MAILTO", "").strip()
    url = _CROSSREF_WORK_URL.format(doi=urllib.parse.quote(doi, safe=""))
    if mailto:
        url = f"{url}?mailto={urllib.parse.quote(mailto)}"

    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - 补全失败不影响检索
        return "", None

    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, dict):
        return "", None

    names: List[str] = []
    for author in message.get("author") or []:
        if not isinstance(author, dict):
            continue
        family = str(author.get("family") or "").strip()
        given = str(author.get("given") or "").strip()
        if family and given:
            names.append(f"{given} {family}")
        elif family or given:
            names.append(family or given)

    year: Optional[int] = None
    for key in ("published-print", "published-online", "published", "issued", "created"):
        block = message.get(key)
        if not isinstance(block, dict):
            continue
        parts = block.get("date-parts") or []
        if parts and isinstance(parts[0], list) and parts[0]:
            try:
                candidate = int(parts[0][0])
            except (TypeError, ValueError):
                continue
            if 1900 <= candidate <= 2100:
                year = candidate
                break

    return "; ".join(names), year


class AILocalBaseLiteraturePort(LiteraturePort):
    """ai-localbase → LiteratureHit adapter."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_AI_LOCALBASE_URL,
        knowledge_base_id: str = "",
        knowledge_base_name: str = "",
        timeout: float = 30.0,
        snippet_chars: int = 1200,
        expand_queries: bool = True,
        max_queries: int = 4,
        max_details: int = 8,
        enrich_metadata: Optional[bool] = None,
    ) -> None:
        self.base_url = (base_url or DEFAULT_AI_LOCALBASE_URL).rstrip("/")
        self.knowledge_base_id = (knowledge_base_id or "").strip()
        self.knowledge_base_name = (knowledge_base_name or "").strip()
        self.timeout = timeout
        self.snippet_chars = max(200, int(snippet_chars))
        self.expand_queries = expand_queries
        self.max_queries = max(1, int(max_queries))
        self.max_details = max(0, int(max_details))

        if enrich_metadata is None:
            env_value = os.getenv("ARTICLE_WRITING_DOI_ENRICH", "1").strip().lower()
            enrich_metadata = env_value not in {"0", "false", "no", "off"}
        self.enrich_metadata = bool(enrich_metadata)

        self._kb_id: Optional[str] = None
        self._documents: Optional[List[Dict[str, Any]]] = None
        self._details: Dict[str, Dict[str, Any]] = {}
        self._crossref_cache: Dict[str, Tuple[str, Optional[int]]] = {}

    # ── HTTP ────────────────────────────────────────────────────────────────

    def _request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                detail = error.read().decode("utf-8", errors="replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"ai-localbase {method} {url} failed: HTTP {error.code} {detail}") from error
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(f"ai-localbase {method} {url} failed: {error}") from error

        if not body.strip():
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"ai-localbase {url} returned non-JSON body") from error

    def _resolve_knowledge_base_id(self) -> str:
        """确定检索用知识库：优先显式 id → 名称匹配 → 第一个知识库。"""
        if self.knowledge_base_id:
            return self.knowledge_base_id
        if self._kb_id:
            return self._kb_id

        payload = self._request_json("/api/knowledge-bases")
        items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(items, list) or not items:
            raise RuntimeError("ai-localbase has no knowledge base; index literature first")

        chosen = None
        if self.knowledge_base_name:
            for item in items:
                if str(item.get("name") or "").strip() == self.knowledge_base_name:
                    chosen = item
                    break
        if chosen is None:
            chosen = items[0]

        knowledge_base_id = str(chosen.get("id") or "").strip()
        if not knowledge_base_id:
            raise RuntimeError("ai-localbase returned a knowledge base without id")
        self._kb_id = knowledge_base_id
        return knowledge_base_id

    def _list_documents(self, *, refresh: bool = False) -> List[Dict[str, Any]]:
        if self._documents is not None and not refresh:
            return self._documents
        kb_id = self._resolve_knowledge_base_id()
        payload = self._request_json(f"/api/knowledge-bases/{urllib.parse.quote(kb_id)}/documents")
        items = payload.get("items") if isinstance(payload, dict) else payload
        self._documents = [item for item in (items or []) if isinstance(item, dict)]
        return self._documents

    def _document_detail(self, document_id: str) -> Dict[str, Any]:
        if document_id in self._details:
            return self._details[document_id]
        kb_id = self._resolve_knowledge_base_id()
        path = (
            f"/api/knowledge-bases/{urllib.parse.quote(kb_id)}"
            f"/documents/{urllib.parse.quote(document_id)}"
        )
        payload = self._request_json(path)
        detail = payload if isinstance(payload, dict) else {}
        self._details[document_id] = detail
        return detail

    # ── 检索 ────────────────────────────────────────────────────────────────

    def _search_once(self, query: str) -> List[LiteratureHit]:
        """单次检索：同一文档的多个 chunk 合并，保留最高分。"""
        kb_id = self._resolve_knowledge_base_id()
        path = f"/api/knowledge-bases/{urllib.parse.quote(kb_id)}/retrieval/debug"
        payload = self._request_json(path, method="POST", payload={"query": query})

        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []

        grouped: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            document_id = str(item.get("documentId") or "").strip()
            document_name = str(item.get("documentName") or "").strip()
            if not document_id or not document_name:
                continue
            try:
                score = float(item.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            entry = grouped.get(document_id)
            if entry is None:
                grouped[document_id] = {
                    "document_name": document_name,
                    "score": score,
                    "chunk_texts": [str(item.get("text") or "")],
                }
                order.append(document_id)
            else:
                entry["score"] = max(entry["score"], score)
                entry["chunk_texts"].append(str(item.get("text") or ""))

        hits: List[LiteratureHit] = []
        for index, document_id in enumerate(order):
            entry = grouped[document_id]
            raw_content = ""
            if index < self.max_details:
                try:
                    detail = self._document_detail(document_id)
                    raw_content = str(detail.get("rawContent") or "")
                except Exception:  # noqa: BLE001
                    raw_content = ""
            try:
                hit = ai_localbase_document_to_hit(
                    document_id=document_id,
                    document_name=entry["document_name"],
                    raw_content=raw_content,
                    chunk_texts=entry["chunk_texts"],
                    score=entry["score"],
                    snippet_chars=self.snippet_chars,
                )
            except ValueError:
                continue
            # 更正/勘误类条目不适合作为文献引用
            if _CORRECTION_TITLE_RE.match(hit.title or ""):
                continue
            hits.append(hit)
        return hits

    def _expand_queries(self, query: str) -> List[str]:
        """生成查询变体：服务端每次只回 3 篇，变体检索可提高可覆盖文献数。"""
        base = " ".join((query or "").split())
        variants: List[str] = [base] if base else []

        tokens: List[str] = []
        for token in _QUERY_TOKEN_RE.findall(base):
            if token.lower() in _QUERY_STOPWORDS:
                continue
            if token not in tokens:
                tokens.append(token)

        if len(tokens) >= 2:
            variants.append(" ".join(tokens[:2]))
            variants.append(" ".join(tokens[-2:]))
        if len(tokens) >= 4:
            variants.append(" ".join(tokens[::2]))

        unique: List[str] = []
        seen: set[str] = set()
        for variant in variants:
            key = variant.casefold()
            if not variant or key in seen:
                continue
            seen.add(key)
            unique.append(variant)
        return unique[: self.max_queries]

    def _enrich_hits(self, hits: List[LiteratureHit]) -> List[LiteratureHit]:
        """按 DOI 补齐 ``authors`` / ``year``（Crossref，带缓存、可关闭、失败不影响检索）。"""
        if not self.enrich_metadata or not hits:
            return hits

        pending = [
            hit
            for hit in hits
            if (not hit.authors or hit.year is None)
            and hit.doi
            and hit.doi not in self._crossref_cache
        ]
        if pending:
            workers = min(_ENRICH_WORKERS, len(pending))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for doi, meta in pool.map(
                    lambda hit: (hit.doi or "", _crossref_metadata(hit.doi or "")), pending
                ):
                    if doi:
                        self._crossref_cache[doi] = meta

        for hit in hits:
            meta = self._crossref_cache.get(hit.doi or "")
            if not meta:
                continue
            authors, year = meta
            if not hit.authors and authors:
                hit.authors = authors
            if hit.year is None and year:
                hit.year = year
        return hits

    def search(self, query: str, top_k: int = 5) -> List[LiteratureHit]:
        if top_k <= 0:
            return []
        query = (query or "").strip()
        if not query:
            return []

        queries = self._expand_queries(query) if self.expand_queries else [query]
        merged: Dict[str, LiteratureHit] = {}

        for index, current in enumerate(queries):
            # 首个查询跑完已够用就不再多打接口
            if index > 0 and len(merged) >= top_k:
                break
            try:
                hits = self._search_once(current)
            except Exception:  # noqa: BLE001
                if index == 0:
                    raise
                continue
            for hit in hits:
                existing = merged.get(hit.paper_id)
                if existing is None or hit.score > existing.score:
                    merged[hit.paper_id] = hit

        ranked = sorted(merged.values(), key=lambda item: item.score, reverse=True)
        return self._enrich_hits(ranked[:top_k])

    def get(self, paper_id: str) -> Optional[LiteratureHit]:
        doi = normalize_doi_paper_id(paper_id)
        if not doi:
            return None

        stem = doi_to_document_stem(doi).casefold()
        for document in self._list_documents():
            name = str(document.get("name") or "")
            if not name:
                continue
            document_stem = re.sub(r"\.[A-Za-z0-9]{1,6}$", "", name).casefold()
            if document_stem != stem and doi_from_document_name(name).casefold() != doi.casefold():
                continue
            document_id = str(document.get("id") or "")
            raw_content = ""
            try:
                raw_content = str(self._document_detail(document_id).get("rawContent") or "")
            except Exception:  # noqa: BLE001
                raw_content = ""
            try:
                hit = ai_localbase_document_to_hit(
                    document_id=document_id,
                    document_name=name,
                    raw_content=raw_content,
                    snippet_chars=self.snippet_chars,
                )
            except ValueError:
                return None
            self._enrich_hits([hit])
            return hit
        return None
