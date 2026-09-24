"""ai-localbase-only 文献同步脚本。

默认执行测试版全量入库：抓取最近最新的 200 篇符合 PubMed 检索式的文章，
直接上传 ai-localbase。SQLite 已停用，不再作为持久化目标。

示例：
    python sync_and_upload.py
    python sync_and_upload.py --mode test_full --reset
    python sync_and_upload.py --mode official_full --reset
    python sync_and_upload.py --mode incremental
"""

import argparse
import sys
from typing import Dict, List

from article_repository.ingestion.full_sync import run_pubmed_sync
from article_repository.ingestion.sources.pmc_oa import fetch_oa_articles
from article_repository.integration.ai_localbase import (
    AI_LOCALBASE_URL,
    ensure_knowledge_base,
    upload_article_http,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取文献并上传到 ai-localbase（SQLite 已停用）")
    parser.add_argument(
        "--mode",
        choices=["test_full", "official_full", "incremental"],
        default="test_full",
        help="同步模式：测试全量、正式全量或增量",
    )
    parser.add_argument("--retmax", type=int, default=200, help="test_full/incremental 最大抓取篇数")
    parser.add_argument("--keywords", nargs="*", default=["自闭症", "肠道菌群"], help="PubMed 关键词")
    parser.add_argument("--kb", default="", help="目标知识库 ID；缺省时按名称查找或创建")
    parser.add_argument("--kb-name", default="自闭症-肠道菌群文献库", help="缺省知识库名称")
    parser.add_argument("--kb-url", default=AI_LOCALBASE_URL, help="ai-localbase 后端地址")
    parser.add_argument("--reset", action="store_true", help="先清空 ai-localbase 中现有知识库和向量集合")
    parser.add_argument("--oa", type=int, default=0, help="额外抓取 PMC-OA 全文篇数，0=跳过")
    parser.add_argument("--oa-term", default="autism AND gut microbiota", help="PMC-OA 检索式")
    return parser.parse_args()


def upload_oa_articles(args: argparse.Namespace, kb_id: str) -> int:
    if args.oa <= 0:
        return 0
    print(f"\n[PMC-OA] 额外抓取开放获取全文 {args.oa} 篇……")
    articles: List[Dict] = fetch_oa_articles(args.oa_term, retmax=args.oa)
    ok = 0
    for i, article in enumerate(articles, 1):
        try:
            resp = upload_article_http(article, kb_id, base_url=args.kb_url)
            status = resp.get("uploaded", {}).get("status")
            print(f"  {i}/{len(articles)} {article.get('doi','')} -> {status}")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  {i}/{len(articles)} {article.get('doi','')} 上传失败: {exc}")
    return ok


def main() -> int:
    args = parse_args()
    retmax = None if args.mode == "official_full" else args.retmax
    result = run_pubmed_sync(
        sync_type=args.mode,
        keywords=args.keywords,
        retmax=retmax,
        knowledge_base_id=args.kb,
        reset_ai_localbase=args.reset,
        kb_name=args.kb_name,
        base_url=args.kb_url,
    )
    kb_id = result["knowledge_base_id"] or ensure_knowledge_base(args.kb_name, base_url=args.kb_url)
    oa_uploaded = upload_oa_articles(args, kb_id)
    print(
        "\n完成："
        f"PubMed 抓取 {result['fetched']} 篇，上传 {result['uploaded']} 篇；"
        f"PMC-OA 额外上传 {oa_uploaded} 篇；知识库 {kb_id}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
