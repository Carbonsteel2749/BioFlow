"""Full historical metadata synchronization workflow."""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from typing import List, Optional, Dict

from article_repository.classification.tagger import MockTagger
from article_repository.ingestion.sources.pubmed import fetch_pubmed_articles
from article_repository.integration.ai_localbase import (
    clear_knowledge_bases,
    ensure_knowledge_base,
    upload_article_document,
)

SYNC_STATE_FILE = Path(os.getenv("PUBMED_SYNC_STATE_FILE", "data/pubmed_sync_state.json"))


def _load_sync_state() -> Optional[Dict]:
    if not SYNC_STATE_FILE.exists():
        return None
    try:
        return json.loads(SYNC_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_sync_state(sync_type: str, articles: List[Dict], keywords: List[str]) -> None:
    SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SYNC_STATE_FILE.write_text(
        json.dumps({
            "sync_type": sync_type,
            "last_sync_date": datetime.now().isoformat(),
            "latest_publish_time": max(
                (item.get("publish_time", datetime.min) for item in articles),
                default=datetime.min,
            ).isoformat(),
            "articles_saved": len(articles),
            "keywords": keywords,
        }, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _upload_articles_to_ai_localbase(articles: List[Dict], knowledge_base_id: str, base_url: Optional[str] = None) -> int:
    """Push fetched articles to the configured ai-localbase knowledge base."""
    uploaded = 0
    for article in articles:
        try:
            if upload_article_document(article, knowledge_base_id=knowledge_base_id, base_url=base_url) is not None:
                uploaded += 1
        except Exception as exc:  # noqa: BLE001 - indexing must not lose crawl data
            doi = article.get("doi") or article.get("title") or "unknown article"
            print(f"ai-localbase 上传失败 ({doi}): {exc}")
    if articles:
        print(f"ai-localbase 文献上传完成：{uploaded}/{len(articles)}")
    return uploaded


def _tag_articles(articles: List[Dict]) -> None:
    """Attach structured metadata tags before the article is sent to ai-localbase."""
    tagger = MockTagger()
    for article in articles:
        try:
            article["llm_tags"] = tagger.tag(
                article.get("title", ""), article.get("abstract", "")
            ).to_dict()
        except Exception as exc:
            article["llm_tags"] = {}
            doi = article.get("doi") or article.get("title") or "unknown article"
            print(f"自动打标签失败 ({doi}): {exc}")


def run_full_sync(
    repo=None,
    keywords: Optional[List[str]] = None,
    mode: str = "broad",
    years_back: int = 20,
    retmax: Optional[int] = 50,
    knowledge_base_id: str = "",
) -> List[Dict]:
    """Compatibility wrapper for a full ai-localbase-only PubMed sync."""
    return run_pubmed_sync(
        sync_type="full",
        keywords=keywords,
        years_back=years_back,
        retmax=retmax,
        knowledge_base_id=knowledge_base_id,
    )["articles"]


def run_incremental_sync(
    repo=None,
    keywords: Optional[List[str]] = None,
    mode: str = "broad",
    years_back: int = 0,
    retmax: int = 200,
    knowledge_base_id: str = "",
) -> List[Dict]:
    """Compatibility wrapper for incremental ai-localbase-only PubMed sync."""
    return run_pubmed_sync(
        sync_type="incremental",
        keywords=keywords,
        retmax=retmax,
        knowledge_base_id=knowledge_base_id,
    )["articles"]


def run_pubmed_sync(
    sync_type: str,
    keywords: Optional[List[str]] = None,
    retmax: Optional[int] = None,
    years_back: int = 0,
    knowledge_base_id: str = "",
    reset_ai_localbase: bool = False,
    kb_name: str = "自闭症-肠道菌群文献库",
    base_url: Optional[str] = None,
) -> Dict:
    """Run one PubMed ingestion mode and upload directly to ai-localbase.

    Supported sync_type values:
      - test_full: latest 200 matching articles, for current test-stage bootstrapping
      - official_full: all matching articles from the last 20 years
      - incremental: articles published after the latest synced publication date
      - full: compatibility alias using provided years_back/retmax
    """
    if keywords is None:
        keywords = ["自闭症", "肠道菌群"]

    normalized = sync_type.strip().lower()
    if normalized == "test_full":
        years_back = 0
        retmax = 200 if retmax is None else retmax
        date_from = None
        state_type = "test_full"
    elif normalized == "official_full":
        years_back = 20
        retmax = None
        date_from = None
        state_type = "official_full"
    elif normalized == "incremental":
        state = _load_sync_state()
        if not state:
            print("未找到同步状态，增量模式自动回退为测试版全量入库。")
            return run_pubmed_sync(
                "test_full",
                keywords=keywords,
                retmax=200,
                knowledge_base_id=knowledge_base_id,
                reset_ai_localbase=reset_ai_localbase,
                kb_name=kb_name,
                base_url=base_url,
            )
        latest_publish_time = state.get("latest_publish_time") or state.get("last_sync_date")
        date_from = datetime.fromisoformat(latest_publish_time) - timedelta(days=1)
        years_back = 0
        retmax = 200 if retmax is None else retmax
        state_type = "incremental"
    elif normalized == "full":
        date_from = None
        retmax = 50 if retmax is None else retmax
        state_type = "full"
    else:
        raise ValueError("sync_type 必须是 test_full、official_full、incremental 或 full")

    if reset_ai_localbase:
        deleted = clear_knowledge_bases(base_url=base_url)
        print(f"ai-localbase 历史知识库已清空：删除 {deleted} 个知识库")
        knowledge_base_id = ""

    kb_id = knowledge_base_id or ensure_knowledge_base(
        name=kb_name,
        description="BioFLow autism + gut microbiota literature knowledge base",
        base_url=base_url,
    )
    if not kb_id:
        raise RuntimeError("无法解析或创建 ai-localbase 知识库")

    articles = fetch_pubmed_articles(
        keywords=keywords,
        years_back=years_back,
        retmax=retmax,
        date_from=date_from,
    )
    _tag_articles(articles)
    uploaded = _upload_articles_to_ai_localbase(articles, kb_id, base_url=base_url)
    _save_sync_state(state_type, articles, keywords)

    print(
        f"PubMed {state_type} 同步完成：抓取 {len(articles)} 篇，"
        f"上传 ai-localbase {uploaded} 篇，知识库 {kb_id}"
    )
    return {
        "articles": articles,
        "fetched": len(articles),
        "uploaded": uploaded,
        "knowledge_base_id": kb_id,
        "sync_type": state_type,
    }


if __name__ == "__main__":
    run_full_sync(retmax=5)