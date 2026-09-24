#!/usr/bin/env python3
"""
文献打标签流程脚本
流程：从数据库读取未打标的文献 → 调用大模型打标签 → 更新回数据库
"""

import os
import json
from typing import List, Dict

from article_repository.storage.repository import LiteratureRepository
from article_repository.classification import QwenTagger, MockTagger, ArticleTags


def get_untagged_articles(repo: LiteratureRepository, limit: int = 100) -> List[Dict]:
    """获取未打标签的文献（llm_tags为空或仅包含空字段）"""
    articles = repo.retrieve_literature({}, page=1, page_size=limit)
    untagged = []
    for art in articles:
        llm_tags = art.get("llm_tags", {})
        if not llm_tags or all(not v for v in llm_tags.values()):
            untagged.append(art)
    return untagged


def update_tags_by_doi(repo: LiteratureRepository, doi: str, tags: ArticleTags):
    """根据DOI更新文献的标签"""
    article = repo.store.get_by_doi(doi)
    if not article:
        print(f"未找到DOI: {doi}")
        return
    
    article["llm_tags"] = tags.to_dict()
    article["update_at"] = article.get("update_at")
    
    from article_repository.storage.metadata_store import LiteratureMetadata
    from datetime import datetime
    article["update_at"] = datetime.now()
    
    meta = LiteratureMetadata(**article)
    repo.store.insert_one_meta(meta)
    print(f"已更新标签: {doi}")


def run_batch_tagging(repo: LiteratureRepository, tagger, batch_size: int = 10):
    """批量打标签主流程"""
    print("=" * 50)
    print("文献打标签流程开始")
    print("=" * 50)
    
    untagged = get_untagged_articles(repo)
    print(f"\n发现 {len(untagged)} 篇未打标的文献")
    
    if not untagged:
        print("所有文献均已打标，无需处理")
        return
    
    print("\n开始批量打标签...")
    success_count = 0
    fail_count = 0
    
    for i, article in enumerate(untagged[:batch_size], 1):
        doi = article.get("doi")
        title = article.get("title", "")
        abstract = article.get("abstract", "")
        
        print(f"\n[{i}/{batch_size}] 处理: {title[:50]}...")
        
        try:
            tags = tagger.tag(title, abstract)
            update_tags_by_doi(repo, doi, tags)
            print(f"  标签: {tags.to_dict()}")
            success_count += 1
        except Exception as e:
            print(f"  失败: {e}")
            fail_count += 1
    
    print("\n" + "=" * 50)
    print(f"批量打标签完成")
    print(f"成功: {success_count} | 失败: {fail_count}")
    print("=" * 50)


def run_single_tagging(repo: LiteratureRepository, tagger, doi: str):
    """单篇文献打标签（测试用）"""
    print("=" * 50)
    print(f"单篇打标签: {doi}")
    print("=" * 50)
    
    article = repo.store.get_by_doi(doi)
    if not article:
        print(f"未找到文献: {doi}")
        return
    
    print(f"\n标题: {article['title']}")
    print(f"摘要: {article['abstract'][:200]}...")
    
    tags = tagger.tag(article["title"], article["abstract"])
    print(f"\n生成的标签:")
    print(json.dumps(tags.to_dict(), ensure_ascii=False, indent=2))
    
    update_tags_by_doi(repo, doi, tags)
    print("\n标签已更新到数据库")


if __name__ == "__main__":
    repo = LiteratureRepository()

    use_mock = os.getenv("USE_MOCK_TAGGER", "false").lower() == "true"
    if use_mock:
        print("使用 MockTagger（测试模式）")
        tagger = MockTagger()
    else:
        print("使用 QwenTagger（优先调用真实大模型）")
        try:
            tagger = QwenTagger(
                base_url=os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1"),
                model=os.getenv("VLLM_MODEL", "Qwen3-14B"),
            )
            if not getattr(tagger, "_server_ready", False):
                raise RuntimeError("模型服务不可达")
        except Exception as exc:
            if os.getenv("ALLOW_MOCK_FALLBACK", "true").lower() == "true":
                print(f"QwenTagger 初始化失败，回退到 MockTagger: {exc}")
                tagger = MockTagger()
            else:
                raise

    run_batch_tagging(repo, tagger, batch_size=10)