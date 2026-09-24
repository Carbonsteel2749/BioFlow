#!/usr/bin/env python3
"""
测试脚本：插入测试文献 + 跑打标签流程
"""

import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from article_repository.storage.repository import LiteratureRepository
from article_repository.storage.metadata_store import LiteratureMetadata


TEST_ARTICLES = [
    {
        "doi": "10.1038/test2025001",
        "title": "肠道菌群干预对自闭症模型小鼠行为的影响",
        "abstract": "目的：探究双歧杆菌对自闭症小鼠社交行为的改善作用及其机制。方法：采用BTBR自闭症模型小鼠，随机分为对照组和双歧杆菌干预组，持续灌胃4周。通过16S rRNA测序分析肠道菌群组成，结合行为学检测（三箱社交实验、旷场实验）评估社交能力。结果：双歧杆菌干预显著改善了BTBR小鼠的社交缺陷，肠道菌群多样性增加，拟杆菌门/厚壁菌门比例发生改变。结论：双歧杆菌可能通过调节肠道菌群-脑轴改善自闭症模型小鼠的社交行为。",
        "authors": [{"name": "张三", "affiliation": "北京大学生命科学学院"}, {"name": "李四", "affiliation": "清华大学医学院"}],
        "publish_time": datetime(2025, 3, 15),
        "journal": "Nature Microbiology",
        "journal_division": "一区",
        "citation_count": 128,
        "keywords": ["自闭症", "肠道菌群", "双歧杆菌", "BTBR小鼠", "社交行为"],
        "llm_tags": {},
        "source": "PubMed",
    },
    {
        "doi": "10.1016/test2025002",
        "title": "粪菌移植治疗自闭症儿童的随机对照试验",
        "abstract": "背景：肠道菌群失调与自闭症谱系障碍（ASD）密切相关。本研究评估粪菌移植（FMT）对自闭症儿童胃肠道症状和行为学的改善效果。方法：纳入60名4-12岁ASD儿童，随机分为FMT组和安慰剂组，进行为期12周的干预。采用ATEC量表评估行为学变化，同时检测粪便菌群组成和血清代谢组。结果：FMT组儿童胃肠道症状显著改善，ATEC总分下降25%，社交沟通能力提升明显。菌群分析显示双歧杆菌和乳酸杆菌丰度显著增加。结论：粪菌移植可改善自闭症儿童的胃肠道症状和部分行为学表现。",
        "authors": [{"name": "王五", "affiliation": "复旦大学附属儿科医院"}, {"name": "赵六", "affiliation": "上海交通大学医学院"}],
        "publish_time": datetime(2025, 5, 20),
        "journal": "Gastroenterology",
        "journal_division": "一区",
        "citation_count": 89,
        "keywords": ["自闭症", "粪菌移植", "儿童", "随机对照试验", "肠道菌群"],
        "llm_tags": {},
        "source": "PubMed",
    },
    {
        "doi": "10.1002/test2025003",
        "title": "基于宏基因组测序的自闭症患者肠道菌群特征分析",
        "abstract": "目的：系统分析自闭症谱系障碍（ASD）患者肠道菌群的宏基因组学特征。方法：收集150名ASD患者和150名健康对照的粪便样本，进行全基因组鸟枪法宏基因组测序。采用差异丰度分析和功能通路注释，鉴定与ASD相关的菌群特征。结果：ASD患者肠道菌群多样性显著降低，双歧杆菌属和粪杆菌属丰度下降，拟杆菌属升高。功能分析显示短链脂肪酸合成通路和神经递质代谢通路存在显著差异。结论：本研究揭示了ASD患者肠道菌群的宏基因组学特征，为基于菌群的干预策略提供了理论依据。",
        "authors": [{"name": "钱七", "affiliation": "中国科学院微生物研究所"}],
        "publish_time": datetime(2025, 1, 10),
        "journal": "Microbiome",
        "journal_division": "一区",
        "citation_count": 256,
        "keywords": ["自闭症", "宏基因组", "肠道菌群", "差异丰度分析", "短链脂肪酸"],
        "llm_tags": {},
        "source": "PubMed",
    },
]


def insert_test_data():
    print("=" * 50)
    print("插入测试文献数据")
    print("=" * 50)
    
    repo = LiteratureRepository()
    count = 0
    for art_data in TEST_ARTICLES:
        meta = LiteratureMetadata(**art_data)
        repo.store.insert_one_meta(meta)
        count += 1
        print(f"  已插入: {art_data['title'][:40]}...")
    
    print(f"\n共插入 {count} 篇测试文献")
    return repo


if __name__ == "__main__":
    insert_test_data()
    print("\n测试数据插入完成！接下来运行：")
    print("  USE_MOCK_TAGGER=true python run_tagging.py")