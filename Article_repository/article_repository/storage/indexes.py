"""Index management placeholder."""
from .metadata_store import MetadataStore

def create_all_indexes():
    store = MetadataStore()
    cursor = store.cursor
    # 1. DOI唯一索引（建表时已设置UNIQUE，补充索引加速查询）
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_doi ON literature_metadata(doi)")
    # 2. 发表时间索引（时间筛选、增量更新）
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_publish_time ON literature_metadata(publish_time)")
    # 3. 期刊索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal ON literature_metadata(journal)")
    # 4. 同步追踪时间索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sync_date ON sync_tracking(last_sync_date)")
    store.conn.commit()
    store.close()
    print("所有检索索引创建完成")

if __name__ == "__main__":
    create_all_indexes()
