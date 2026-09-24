"""Integration helpers to push documents into ai-localbase knowledge base.

This module uses the ai-localbase MCP JSON-RPC `upload_text_document` tool
for small text payloads and falls back to the HTTP `/api/uploads` +
`register_staged_upload` flow for larger content when possible.
"""
from typing import Optional
import os
import json
import logging

from dotenv import load_dotenv

load_dotenv()

try:
    import requests
except Exception:  # pragma: no cover - requests should be installed in dev env
    requests = None

AI_LOCALBASE_URL = os.getenv("AI_LOCALBASE_URL", "http://localhost:8080")
AI_LOCALBASE_MCP_KEY = os.getenv("AI_LOCALBASE_MCP_KEY", "")
AI_LOCALBASE_KB_ID = os.getenv("AI_LOCALBASE_KB_ID", "")
AI_LOCALBASE_KB_NAME = os.getenv("AI_LOCALBASE_KB_NAME", "")

_INLINE_LIMIT = 256 * 1024  # 256 KB inline limit (matches ai-localbase doc guidance)

logger = logging.getLogger("article_repository.integration.ai_localbase")


def _knowledge_base_id(knowledge_base_id: Optional[str] = None, base_url: Optional[str] = None) -> str:
    """Resolve the configured ID, or find the configured knowledge base by name."""
    explicit = (knowledge_base_id or AI_LOCALBASE_KB_ID).strip()
    if explicit:
        return explicit
    if not AI_LOCALBASE_KB_NAME:
        return ""
    try:
        matches = [
            item for item in list_knowledge_bases(base_url)
            if item.get("name") == AI_LOCALBASE_KB_NAME
        ]
    except Exception as exc:  # pragma: no cover - depends on live ai-localbase
        logger.warning("无法按名称查找 ai-localbase 知识库: %s", exc)
        return ""
    return str(matches[0].get("id") or "") if matches else ""


def _mcp_call(payload: dict) -> dict:
    url = AI_LOCALBASE_URL.rstrip("/") + "/mcp"
    headers = {"Content-Type": "application/json"}
    if AI_LOCALBASE_MCP_KEY:
        headers["Authorization"] = f"Bearer {AI_LOCALBASE_MCP_KEY}"
    text = json.dumps(payload)
    if requests is None:
        raise RuntimeError("requests package not available; please install requests")
    resp = requests.post(url, headers=headers, data=text, timeout=30)
    resp.raise_for_status()
    return resp.json()


def upload_text_document(file_name: str, content: str, knowledge_base_id: Optional[str] = None) -> Optional[dict]:
    """Upload a text document via MCP `upload_text_document` tool.

    Returns JSON-RPC response dict on success, or None on failure.
    """
    kb = _knowledge_base_id(knowledge_base_id)
    if not kb:
        logger.debug("no AI_LOCALBASE_KB_ID configured; skipping upload")
        return None

    if len(content.encode("utf-8")) > _INLINE_LIMIT:
        # Caller should handle large content (staging + register). Let caller decide.
        logger.debug("content too large for inline upload; size=%d", len(content))
        return None

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "upload_text_document",
            "arguments": {
                "knowledgeBaseId": kb,
                "fileName": file_name,
                "content": content,
            },
        },
    }
    try:
        return _mcp_call(payload)
    except Exception as exc:
        logger.exception("upload_text_document failed: %s", exc)
        return None


def stage_and_register_file(file_name: str, content_bytes: bytes, knowledge_base_id: Optional[str] = None) -> Optional[dict]:
    """Upload large file bytes to `/api/uploads` then call MCP `register_staged_upload`.

    Returns MCP response or None.
    """
    kb = _knowledge_base_id(knowledge_base_id)
    if not kb:
        logger.debug("no AI_LOCALBASE_KB_ID configured; skipping upload")
        return None
    if requests is None:
        raise RuntimeError("requests package not available; please install requests")

    upload_url = AI_LOCALBASE_URL.rstrip("/") + "/api/uploads"
    headers = {}
    if AI_LOCALBASE_MCP_KEY:
        headers["Authorization"] = f"Bearer {AI_LOCALBASE_MCP_KEY}"

    files = {"file": (file_name, content_bytes)}
    try:
        r = requests.post(upload_url, headers=headers, files=files, timeout=60)
        r.raise_for_status()
        data = r.json()
        upload_id = data.get("uploadId") or data.get("id") or data.get("data", {}).get("uploadId")
        if not upload_id:
            logger.error("upload staging did not return uploadId: %s", data)
            return None

        # call register_staged_upload via MCP
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "register_staged_upload",
                "arguments": {
                    "uploadId": upload_id,
                    "knowledgeBaseId": kb,
                    "fileName": file_name,
                },
            },
        }
        return _mcp_call(payload)
    except Exception as exc:
        logger.exception("stage_and_register_file failed: %s", exc)
        return None


def upload_article(doi: str, title: str, text: str, knowledge_base_id: Optional[str] = None) -> Optional[dict]:
    """High-level helper: try inline upload first, otherwise stage+register when too large."""
    file_name = doi if doi else (title[:120].replace("/", "_") or "article.txt")
    if isinstance(text, str):
        text_bytes = text.encode("utf-8")
    else:
        text_bytes = bytes(text)

    # prefer inline small uploads
    if len(text_bytes) <= _INLINE_LIMIT:
        return upload_text_document(file_name, text, knowledge_base_id)
    else:
        return stage_and_register_file(file_name, text_bytes, knowledge_base_id)


def _article_metadata(article: dict) -> dict:
    authors = article.get("authors") or []
    return {
        "doi": article.get("doi") or "",
        "pmid": article.get("pmid") or "",
        "pmc_id": article.get("pmc_id") or article.get("pmc") or "",
        "title": article.get("title") or "",
        "authors": [a.get("name") for a in authors if a.get("name")],
        "journal": article.get("journal") or "",
        "publish_time": str(article.get("publish_time") or ""),
        "keywords": article.get("keywords") or [],
        "llm_tags": article.get("llm_tags") or {},
        "source": article.get("source") or "",
        "content_type": article.get("content_type") or ("full_text" if article.get("full_text") else "abstract"),
    }


def article_to_text(article: dict, knowledge_base_id: Optional[str] = None) -> str:
    """Build the ai-localbase document.

    只保留标题与向量正文（全文/摘要）。元数据标签（索引词）不进入向量正文，
    避免其作为 chunk 正文出现在召回结果中。
    """
    title = str(article.get("title") or "").strip()
    body = (article.get("full_text") or article.get("abstract") or "").strip()

    sections = []
    if title:
        sections.append("标题(Title): " + title)
    if body:
        sections.append("向量正文(Vector Body):\n" + body)
    return "\n\n".join(sections)


def upload_article_document(article: dict, knowledge_base_id: Optional[str] = None, base_url: Optional[str] = None) -> Optional[dict]:
    """上传文献到向量知识库，正文优先用全文（全文为空时回退摘要）。"""
    doi = article.get("doi") or ""
    title = article.get("title", "")
    text = article_to_text(article, knowledge_base_id)
    if not AI_LOCALBASE_MCP_KEY:
        try:
            return upload_article_http(article, knowledge_base_id=knowledge_base_id, base_url=base_url)
        except Exception as exc:
            logger.warning("ai-localbase direct REST upload failed: %s", exc)
            return None

    result = upload_article(doi, title, text, knowledge_base_id)
    if result is not None:
        return result
    # MCP may be disabled or protected by a scope token; REST upload is the
    # compatible fallback for the local ingestion pipeline.
    try:
        return upload_article_http(article, knowledge_base_id=knowledge_base_id, base_url=base_url)
    except Exception as exc:
        logger.warning("ai-localbase REST upload fallback failed: %s", exc)
        return None


def list_knowledge_bases(base_url: Optional[str] = None) -> list:
    """查询 ai-localbase 已有的知识库列表（返回 list[dict]，含 id/name）。"""
    if requests is None:
        raise RuntimeError("requests package not available; please install requests")
    url = (base_url or AI_LOCALBASE_URL).rstrip("/") + "/api/knowledge-bases"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json().get("items", [])


def create_knowledge_base(name: str, description: str = "", base_url: Optional[str] = None) -> dict:
    """Create an ai-localbase knowledge base and return its JSON object."""
    if requests is None:
        raise RuntimeError("requests package not available; please install requests")
    url = (base_url or AI_LOCALBASE_URL).rstrip("/") + "/api/knowledge-bases"
    r = requests.post(url, json={"name": name, "description": description}, timeout=30)
    r.raise_for_status()
    return r.json()


def delete_knowledge_base(knowledge_base_id: str, base_url: Optional[str] = None) -> bool:
    """Delete one ai-localbase knowledge base and its Qdrant collection."""
    if requests is None:
        raise RuntimeError("requests package not available; please install requests")
    if not knowledge_base_id:
        return False
    url = (base_url or AI_LOCALBASE_URL).rstrip("/") + f"/api/knowledge-bases/{knowledge_base_id}"
    r = requests.delete(url, timeout=60)
    if r.status_code == 404:
        return False
    r.raise_for_status()
    return True


def clear_knowledge_bases(base_url: Optional[str] = None) -> int:
    """Delete all ai-localbase knowledge bases exposed by the API."""
    deleted = 0
    for item in list_knowledge_bases(base_url):
        if delete_knowledge_base(str(item.get("id") or ""), base_url):
            deleted += 1
    return deleted


def ensure_knowledge_base(
    name: str = "自闭症-肠道菌群文献库",
    description: str = "BioFLow PubMed/PMC literature repository",
    base_url: Optional[str] = None,
) -> str:
    """Resolve the configured KB, find by name, or create a new one."""
    configured = _knowledge_base_id(None, base_url=base_url)
    if configured:
        return configured
    for item in list_knowledge_bases(base_url):
        if item.get("name") == name:
            return str(item.get("id") or "")
    created = create_knowledge_base(name, description, base_url)
    return str(created.get("id") or created.get("knowledgeBaseId") or "")


def upload_article_http(
    article: dict,
    knowledge_base_id: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: int = 180,
) -> dict:
    """通过 REST `/api/knowledge-bases/:id/documents` 上传文献到向量库。

    使用 HTTP multipart（字段名 `file`），无需启用/使用 MCP，正文优先全文、空则回退摘要。
    返回后端 JSON 响应（含 document id / status）。
    """
    if requests is None:
        raise RuntimeError("requests package not available; please install requests")
    kb = _knowledge_base_id(knowledge_base_id, base_url=base_url)
    if not kb:
        kb = ensure_knowledge_base(base_url=base_url)
    if not kb:
        raise ValueError("缺少知识库 ID：请传 knowledge_base_id、设置 AI_LOCALBASE_KB_ID，或确保可创建默认知识库")
    text = article_to_text(article, kb)
    file_name = (article.get("doi") or article.get("title", "")[:120] or "article").replace("/", "_") + ".txt"
    url = (base_url or AI_LOCALBASE_URL).rstrip("/") + f"/api/knowledge-bases/{kb}/documents"
    files = {"file": (file_name, text.encode("utf-8"), "text/plain")}
    r = requests.post(url, files=files, timeout=timeout)
    r.raise_for_status()
    return r.json()
