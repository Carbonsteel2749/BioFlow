"""测试专用脚本：清空数据库 + 重新执行首次全量抓取。"""

import sqlite3
from pathlib import Path

from article_repository.ingestion.full_sync import run_full_sync
from article_repository.storage.repository import LiteratureRepository

DB_PATH = Path(__file__).resolve().parent / "literature_db.sqlite3"


def reset_database(db_path: Path = DB_PATH) -> None:
    """清空文献表和同步追踪表，用于反复测试首次抓取效果。"""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("DELETE FROM literature_metadata")
    cur.execute("DELETE FROM sync_tracking")
    conn.commit()
    conn.close()
    print(f"已清空数据库: {db_path}")


def run_initial_sync(
    keywords=None,
    mode: str = "broad",
    years_back: int = 20,
    retmax: int = 200,
):
    """执行首次全量抓取，返回抓取到的文献列表。"""
    if keywords is None:
        keywords = ["自闭症", "肠道菌群"]

    repo = LiteratureRepository()
    articles = run_full_sync(
        repo=repo,
        keywords=keywords,
        mode=mode,
        years_back=years_back,
        retmax=retmax,
    )
    print(f"首次抓取完成，共 {len(articles)} 篇文献")
    return articles


if __name__ == "__main__":
    reset_database()
    articles = run_initial_sync(
        keywords=["自闭症", "肠道菌群"],
        mode="broad",      # 可改为 strict / balanced 测试不同检索策略
        years_back=20,     # 回溯年数
        retmax=50,         # 抓取数量上限
    )

    # 简单预览前 5 篇标题
    print("\n预览前 5 篇：")
    for i, art in enumerate(articles[:5], 1):
        print(f"{i}. {art['title'][:80]}...")
