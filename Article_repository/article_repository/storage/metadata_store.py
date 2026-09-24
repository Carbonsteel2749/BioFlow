"""Metadata storage placeholder."""
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_FILE = os.getenv("SQLITE_DB_FILE", str(PROJECT_ROOT / "literature_db.sqlite3"))

# 文献元数据模型，和之前MongoDB版本完全一致，上层无需改动
class LiteratureMetadata(BaseModel):
    doi: str = Field(..., description="文献唯一DOI，主键判重")
    title: str
    abstract: Optional[str] = None
    full_text: Optional[str] = None  # 全文（无全文时回退为摘要）
    authors: List[Dict]  # 嵌套作者列表[{name: xxx, affiliation: xxx}]
    publish_time: datetime
    journal: str
    journal_division: Optional[str] = None
    citation_count: int = 0
    keywords: List[str] = []
    llm_tags: Dict  # LLM生成的结构化JSON标签：菌种、模型、干预手段等
    source: str  # 数据源：PubMed/OpenAlex等
    create_at: datetime = Field(default_factory=datetime.now)
    update_at: datetime = Field(default_factory=datetime.now)

class MetadataStore:
    def __init__(self):
        self.conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._init_table()

    def _init_table(self):
        # 建表：doi唯一主键，嵌套数据全部存JSON字符串
        create_sql = """
        CREATE TABLE IF NOT EXISTS literature_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doi TEXT UNIQUE NOT NULL,
            title TEXT,
            abstract TEXT,
            full_text TEXT,
            authors TEXT,
            publish_time TIMESTAMP,
            journal TEXT,
            journal_division TEXT,
            citation_count INTEGER DEFAULT 0,
            keywords TEXT,
            llm_tags TEXT,
            source TEXT,
            create_at TIMESTAMP,
            update_at TIMESTAMP
        )
        """
        self.cursor.execute(create_sql)

        # 存量库迁移：若旧表没有 full_text 列则补上
        existing_cols = [row[1] for row in self.cursor.execute("PRAGMA table_info(literature_metadata)").fetchall()]
        if "full_text" not in existing_cols:
            self.cursor.execute("ALTER TABLE literature_metadata ADD COLUMN full_text TEXT")

        # 同步追踪表：记录每次同步的元信息
        sync_tracking_sql = """
        CREATE TABLE IF NOT EXISTS sync_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_type TEXT NOT NULL,
            last_sync_date TIMESTAMP NOT NULL,
            articles_fetched INTEGER DEFAULT 0,
            articles_saved INTEGER DEFAULT 0,
            keywords TEXT,
            date_from TEXT,
            message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        self.cursor.execute(sync_tracking_sql)
        self.conn.commit()

    def insert_one_meta(self, meta: LiteratureMetadata):
        """单篇写入，DOI重复自动更新（增量更新逻辑不变）"""
        data = meta.model_dump()
        data["update_at"] = datetime.now()
        # 嵌套列表/字典转为JSON字符串存储
        insert_sql = """
        INSERT INTO literature_metadata 
        (doi, title, abstract, full_text, authors, publish_time, journal, journal_division,
        citation_count, keywords, llm_tags, source, create_at, update_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(doi) DO UPDATE SET
            title=excluded.title,
            abstract=excluded.abstract,
            full_text=excluded.full_text,
            authors=excluded.authors,
            publish_time=excluded.publish_time,
            journal=excluded.journal,
            journal_division=excluded.journal_division,
            citation_count=excluded.citation_count,
            keywords=excluded.keywords,
            llm_tags=excluded.llm_tags,
            source=excluded.source,
            update_at=excluded.update_at
        """
        params = (
            data["doi"],
            data["title"],
            data["abstract"],
            data.get("full_text"),
            json.dumps(data["authors"], ensure_ascii=False),
            data["publish_time"],
            data["journal"],
            data["journal_division"],
            data["citation_count"],
            json.dumps(data["keywords"], ensure_ascii=False),
            json.dumps(data["llm_tags"], ensure_ascii=False),
            data["source"],
            data["create_at"],
            data["update_at"]
        )
        self.cursor.execute(insert_sql, params)
        self.conn.commit()

    def batch_insert_meta(self, meta_list: List[LiteratureMetadata]):
        """批量全量入库，逻辑和MongoDB版本完全一致"""
        for meta in meta_list:
            self.insert_one_meta(meta)

    def get_by_doi(self, doi: str) -> Optional[Dict]:
        """按DOI查询单篇文献"""
        self.cursor.execute("SELECT * FROM literature_metadata WHERE doi = ?", (doi,))
        row = self.cursor.fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def search_by_keyword(self, keyword: str, page=1, page_size=20):
        """关键词模糊检索：标题/摘要/关键词"""
        skip = (page - 1) * page_size
        sql = """
        SELECT * FROM literature_metadata
        WHERE title LIKE ? OR abstract LIKE ? OR keywords LIKE ?
        LIMIT ? OFFSET ?
        """
        like_val = f"%{keyword}%"
        self.cursor.execute(sql, (like_val, like_val, like_val, page_size, skip))
        rows = self.cursor.fetchall()
        return [self._row_to_dict(r) for r in rows]

    def search_by_llm_tag(self, tag_key: str, tag_value: str, page=1, page_size=20):
        """按LLM标签精准筛选，兼容嵌套JSON"""
        skip = (page - 1) * page_size
        sql = """
        SELECT * FROM literature_metadata
        WHERE llm_tags LIKE ?
        LIMIT ? OFFSET ?
        """
        like_val = f'%"{tag_key}": "{tag_value}"%'
        self.cursor.execute(sql, (like_val, page_size, skip))
        rows = self.cursor.fetchall()
        return [self._row_to_dict(r) for r in rows]

    def custom_query(self, sql: str, params: tuple):
        """通用查询接口，供repository统一检索调用"""
        self.cursor.execute(sql, params)
        rows = self.cursor.fetchall()
        return [self._row_to_dict(r) for r in rows]

    def _row_to_dict(self, row):
        """数据库行数据反序列化，JSON字符串转回列表/字典"""
        cols = [desc[0] for desc in self.cursor.description]
        res = dict(zip(cols, row))
        # JSON字段反序列化
        res["authors"] = json.loads(res["authors"])
        res["keywords"] = json.loads(res["keywords"])
        res["llm_tags"] = json.loads(res["llm_tags"])
        return res

    def close(self):
        self.conn.close()

    # ── 同步追踪方法 ──────────────────────────────────────────

    def get_last_sync_date(self) -> Optional[datetime]:
        """获取最近一次同步的时间，用于增量同步的起始日期过滤"""
        self.cursor.execute(
            "SELECT last_sync_date FROM sync_tracking ORDER BY last_sync_date DESC LIMIT 1"
        )
        row = self.cursor.fetchone()
        if row and row[0]:
            try:
                return datetime.fromisoformat(row[0])
            except (ValueError, TypeError):
                return None
        return None

    def record_sync(
        self,
        sync_type: str,
        articles_fetched: int,
        articles_saved: int,
        keywords: str = "",
        date_from: Optional[str] = None,
        message: str = "",
    ) -> None:
        """记录一次同步操作到 sync_tracking 表"""
        self.cursor.execute(
            """INSERT INTO sync_tracking 
               (sync_type, last_sync_date, articles_fetched, articles_saved, keywords, date_from, message)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (sync_type, datetime.now(), articles_fetched, articles_saved, keywords, date_from, message),
        )
        self.conn.commit()

    def get_sync_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取最近的同步历史记录"""
        self.cursor.execute(
            "SELECT * FROM sync_tracking ORDER BY last_sync_date DESC LIMIT ?",
            (limit,),
        )
        rows = self.cursor.fetchall()
        cols = [desc[0] for desc in self.cursor.description]
        return [dict(zip(cols, row)) for row in rows]
