from storage import LiteratureRepository
from datetime import datetime

repo = LiteratureRepository()
# 测试单篇样例数据
test_data = [{
    "doi": "10.1038/test2025001",
    "title": "肠道菌群干预对自闭症模型小鼠行为的影响",
    "abstract": "探究双歧杆菌对自闭症小鼠社交行为的改善作用...",
    "authors": [{"name": "张三", "affiliation": "XX大学医学院"}],
    "publish_time": datetime(2025, 1, 10),
    "journal": "Nature Microbiology",
    "journal_division": "一区",
    "citation_count": 12,
    "keywords": ["自闭症", "肠道菌群", "双歧杆菌", "动物模型"],
    "llm_tags": {
        "core_bacteria": "双歧杆菌",
        "experiment_model": "小鼠自闭症模型",
        "intervention": "益生菌灌胃干预",
        "analysis_method": "16S rRNA测序、行为学检测"
    },
    "source": "PubMed"
}]
repo.full_batch_save(test_data)
# 测试检索
res = repo.retrieve_literature({"keyword":"双歧杆菌"})
print("检索结果：", res[0]["title"])