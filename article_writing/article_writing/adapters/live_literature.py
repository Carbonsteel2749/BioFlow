"""Live LiteraturePort for the literature plate.

后端选择（``backend`` 参数 / 环境变量 ``ARTICLE_WRITING_LITERATURE_BACKEND``）：

- ``ai-localbase``：向量库 HTTP（**当前默认**，Article_repository 已迁移到 ai-localbase，
  本地 SQLite 行数为 0）
- ``http``：Article_repository 旧 HTTP ``GET /api/articles``（该接口已下线，仅兼容保留）
- ``sqlite``：进程内读 ``article_literature.sqlite3``（已停用，仅兼容保留）
- ``auto``：先探测 ai-localbase，可用就用它；否则按 base_url 走 http，再退回 sqlite

章节与编排器只依赖 ``LiteraturePort``，切换后端不影响上游代码。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, List, Optional

from article_writing.adapters.ai_localbase_literature import (
    DEFAULT_AI_LOCALBASE_URL,
    AILocalBaseLiteraturePort,
)
from article_writing.adapters.base import LiteraturePort
from article_writing.adapters.literature_mapping import (
    normalize_doi_paper_id,
    repository_row_to_hit,
)
from article_writing.contracts import LiteratureHit

_BIOFLOW_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_REPO_ROOT = _BIOFLOW_ROOT / "Article_repository"
# Has real rows in this workspace; literature_db.sqlite3 may be empty.
_DEFAULT_DB = _DEFAULT_REPO_ROOT / "article_literature.sqlite3"
_DEFAULT_KB_NAME = "自闭症-肠道菌群文献库"


def _ensure_article_repository_importable(repo_root: Path | None = None) -> Path:
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    return root


class LiveLiteraturePort(LiteraturePort):
    """Article_repository / ai-localbase → LiteratureHit adapter."""

    def __init__(
        self,
        *,
        base_url: str = "",
        timeout: float = 30.0,
        db_path: str | Path | None = None,
        repo_root: str | Path | None = None,
        backend: str = "",
        knowledge_base_id: str = "",
        knowledge_base_name: str = "",
        expand_queries: bool = True,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self.repo_root = Path(repo_root) if repo_root else _DEFAULT_REPO_ROOT
        if db_path is not None:
            self.db_path = Path(db_path)
        else:
            env_db = os.getenv("SQLITE_DB_FILE", "").strip()
            self.db_path = Path(env_db) if env_db else _DEFAULT_DB

        self.backend = (
            (backend or os.getenv("ARTICLE_WRITING_LITERATURE_BACKEND", "auto")).strip().lower()
            or "auto"
        )
        self.knowledge_base_id = (knowledge_base_id or os.getenv("AI_LOCALBASE_KB_ID", "")).strip()
        self.knowledge_base_name = (
            knowledge_base_name or os.getenv("AI_LOCALBASE_KB_NAME", _DEFAULT_KB_NAME)
        ).strip()
        self.expand_queries = expand_queries

        self._store: Any = None
        self._repo: Any = None
        self._resolved_backend: str = ""
        self._ai_localbase: Optional[AILocalBaseLiteraturePort] = None

    # ── 后端解析 ────────────────────────────────────────────────────────────

    def _make_ai_localbase(self) -> AILocalBaseLiteraturePort:
        if self._ai_localbase is None:
            self._ai_localbase = AILocalBaseLiteraturePort(
                base_url=self.base_url or DEFAULT_AI_LOCALBASE_URL,
                knowledge_base_id=self.knowledge_base_id,
                knowledge_base_name=self.knowledge_base_name,
                timeout=self.timeout,
                expand_queries=self.expand_queries,
            )
        return self._ai_localbase

    def _ai_localbase_available(self) -> bool:
        try:
            self._make_ai_localbase()._resolve_knowledge_base_id()  # noqa: SLF001 - 探测用
            return True
        except Exception:  # noqa: BLE001
            return False

    def _resolved(self) -> str:
        """解析实际使用的后端（结果缓存）。"""
        if self._resolved_backend:
            return self._resolved_backend

        backend = self.backend
        if backend == "ai-localbase":
            self._resolved_backend = "ai-localbase"
        elif backend in {"sqlite", "http"}:
            self._resolved_backend = backend
        else:  # auto
            if self._ai_localbase_available():
                self._resolved_backend = "ai-localbase"
            elif self.base_url:
                self._resolved_backend = "http"
            else:
                self._resolved_backend = "sqlite"
        return self._resolved_backend

    def _backend(self) -> str:
        """兼容旧调用：返回 ``http`` 或 ``repository``。"""
        resolved = self._resolved()
        if resolved == "ai-localbase":
            return "ai-localbase"
        return "http" if resolved == "http" else "repository"

    # ── SQLite 后端（兼容保留） ─────────────────────────────────────────────

    def _get_store(self) -> Any:
        if self._store is not None:
            return self._store
        _ensure_article_repository_importable(self.repo_root)
        from article_repository.storage import metadata_store as ms

        ms.DB_FILE = str(self.db_path.resolve())
        self._store = ms.MetadataStore()
        return self._store

    def _get_repo(self) -> Any:
        if self._repo is not None:
            return self._repo
        store = self._get_store()
        from article_repository.storage.repository import LiteratureRepository

        repo = LiteratureRepository()
        # Reuse the store bound to our db_path (LiteratureRepository() would reopen default).
        repo.store = store
        self._repo = repo
        return self._repo

    # ── 公共 API ────────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5) -> List[LiteratureHit]:
        if top_k <= 0:
            return []
        query = (query or "").strip()

        resolved = self._resolved()
        if resolved == "ai-localbase":
            return self._make_ai_localbase().search(query, top_k=top_k)
        if resolved == "http":
            return self._search_http(query, top_k)
        return self._search_repository(query, top_k)

    def get(self, paper_id: str) -> Optional[LiteratureHit]:
        doi = normalize_doi_paper_id(paper_id)
        if not doi:
            return None

        resolved = self._resolved()
        if resolved == "ai-localbase":
            return self._make_ai_localbase().get(doi)
        if resolved == "http":
            # No by-doi HTTP yet; fall back to in-process get_by_doi when db is available.
            try:
                return self._get_by_doi_repository(doi)
            except Exception:  # noqa: BLE001
                return None
        return self._get_by_doi_repository(doi)

    def _search_repository(self, query: str, top_k: int) -> List[LiteratureHit]:
        repo = self._get_repo()
        if query:
            rows = repo.retrieve_literature({"keyword": query}, page=1, page_size=top_k)
        else:
            rows = self._get_store().search_by_keyword("", page=1, page_size=top_k)
        hits: List[LiteratureHit] = []
        for row in rows or []:
            try:
                hits.append(repository_row_to_hit(row))
            except ValueError:
                continue
        return hits[:top_k]

    def _get_by_doi_repository(self, doi: str) -> Optional[LiteratureHit]:
        row = self._get_store().get_by_doi(doi)
        if not row:
            return None
        try:
            return repository_row_to_hit(row)
        except ValueError:
            return None

    def _search_http(self, query: str, top_k: int) -> List[LiteratureHit]:
        import json
        from urllib.error import HTTPError, URLError
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen

        params = urlencode(
            {
                "keyword": query,
                "page": 1,
                "page_size": max(top_k, 1),
            }
        )
        url = f"{self.base_url}/api/articles?{params}"
        req = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"literature HTTP search failed: {url} ({exc})") from exc

        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []
        hits: List[LiteratureHit] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                hits.append(repository_row_to_hit(item))
            except ValueError:
                continue
        return hits[:top_k]
