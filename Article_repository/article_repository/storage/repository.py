"""Repository abstraction placeholder."""
from .metadata_store import MetadataStore, LiteratureMetadata
from .indexes import create_all_indexes
from typing import Any, Dict, List, Optional
import time
from datetime import datetime

try:
    import schedule  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - optional dependency in local env
    schedule = None

class LiteratureRepository:
    def __init__(self):
        self.store = MetadataStore()
        # 初始化时自动创建索引
        create_all_indexes()

    def full_batch_save(self, raw_meta_list: List[Dict]):
        """首次全量抓取后批量入库，raw_meta_list是爬虫返回的原始文献字典列表"""
        parse_list = [LiteratureMetadata(**item) for item in raw_meta_list]
        self.store.batch_insert_meta(parse_list)
        print(f"全量入库完成，共处理{len(parse_list)}篇文献")

    def increment_update_task(self, new_meta_list: List[Dict]):
        """增量更新任务：只写入新发表/更新的文献，DOI重复自动覆盖旧数据"""
        parse_list = [LiteratureMetadata(**item) for item in new_meta_list]
        self.store.batch_insert_meta(parse_list)
        print(f"月度增量更新完成，共新增/更新{len(parse_list)}篇文献")

    def schedule_monthly_increment(self, crawler_func):
        """设置每月定时增量更新调度，crawler_func是你上游爬虫抓取新文献的函数"""
        if schedule is None:
            raise RuntimeError("schedule package is not installed; install it to enable monthly scheduling")

        def job():
            print(f"开始执行月度增量抓取任务，时间：{datetime.now()}")
            new_datas = crawler_func()
            self.increment_update_task(new_datas)
        # 每月1号凌晨执行
        schedule.every().month.at("00:00").do(job)
        # 常驻循环
        while True:
            schedule.run_pending()
            time.sleep(3600)

    def retrieve_literature(self, search_params: Dict, page=1, page_size=20):
        """统一检索入口：兼容关键词、标签、时间、期刊多条件查询"""
        base_sql = "SELECT * FROM literature_metadata WHERE 1=1 "
        params = []
        if keyword := search_params.get("keyword"):
            normalized = keyword.strip().lower()
            base_sql += " AND (lower(title) LIKE ? OR lower(abstract) LIKE ? OR lower(keywords) LIKE ? OR lower(journal) LIKE ? OR lower(source) LIKE ?) "
            like_val = f"%{normalized}%"
            params.extend([like_val, like_val, like_val, like_val, like_val])
            if normalized != keyword.strip():
                like_val_original = f"%{keyword.strip()}%"
                params = [like_val_original, like_val_original, like_val_original, like_val_original, like_val_original] + params[5:]
        if tag_filter := search_params.get("tag_filter"):
            for k, v in tag_filter.items():
                # 兼容旧格式（字符串）和新格式（JSON 数组）
                base_sql += ' AND (llm_tags LIKE ? OR llm_tags LIKE ?) '
                params.append(f'%"{k}": "{v}"%')      # 字符串格式
                params.append(f'%"{k}": ["%{v}%"]%')  # JSON 数组格式
        if time_range := search_params.get("time_range"):
            start, end = time_range
            base_sql += " AND publish_time BETWEEN ? AND ? "
            params.extend([start, end])
        skip = (page - 1) * page_size
        base_sql += " LIMIT ? OFFSET ? "
        params.extend([page_size, skip])
        return self.store.custom_query(base_sql, tuple(params))

    # ── 同步追踪代理方法 ──────────────────────────────────────

    def get_last_sync_date(self) -> Optional[datetime]:
        """获取最近一次同步的时间"""
        return self.store.get_last_sync_date()

    def record_sync(
        self,
        sync_type: str,
        articles_fetched: int,
        articles_saved: int,
        keywords: str = "",
        date_from: Optional[str] = None,
        message: str = "",
    ) -> None:
        """记录一次同步操作"""
        self.store.record_sync(sync_type, articles_fetched, articles_saved, keywords, date_from, message)

    def get_sync_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取最近的同步历史"""
        return self.store.get_sync_history(limit)
