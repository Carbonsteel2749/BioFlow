# pubmed_search.py
"""
PubMed E-utilities 联网检索模块
功能：将中文查询翻译为英文关键词 → 调用 PubMed API → 返回结构化摘要
"""

import re
import time
import datetime
import requests
import xml.etree.ElementTree as ET
from typing import List, Dict, Tuple

# ── 基础配置 ──────────────────────────────────────────────
PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

NCBI_EMAIL = "your_email@example.com"   # 建议填写真实邮箱
NCBI_TOOL  = "autism_rag_assistant"

# NCBI 限速：无 API Key 时每秒不超过 3 次请求
REQUEST_INTERVAL = 0.4


# ── 中文 → 英文关键词映射表 ────────────────────────────────
_ZH_TO_EN_KEYWORDS: Dict[str, List[str]] = {
    # 核心疾病
    "自闭症":     ["autism spectrum disorder", "ASD", "autism"],
    "孤独症":     ["autism spectrum disorder", "ASD", "autism"],
    "阿斯伯格":   ["Asperger syndrome"],

    # 饮食类型
    "无麸质":     ["gluten-free diet", "GFCF diet", "gluten free"],
    "无酪蛋白":   ["casein-free diet", "GFCF diet", "casein free"],
    "生酮饮食":   ["ketogenic diet"],
    "地中海饮食": ["Mediterranean diet"],

    # 食物相关
    "食物":       ["food", "foods", "dietary"],
    "食谱":       ["meal plan", "dietary pattern", "diet"],
    "饮食":       ["diet", "dietary intake"],
    "营养":       ["nutrition", "nutritional status"],
    "避免":       ["avoidance", "avoid", "restriction"],
    "禁忌":       ["contraindication", "food restriction", "avoid"],
    "不能吃":     ["food restriction", "food avoidance"],
    "过敏":       ["food allergy", "food intolerance", "hypersensitivity"],
    "挑食":       ["food selectivity", "picky eating", "food refusal"],
    "偏食":       ["food selectivity", "restricted eating"],
    "添加剂":     ["food additives", "artificial additives", "preservatives"],
    "糖":         ["sugar", "refined sugar", "sucrose"],
    "加工食品":   ["processed food", "ultra-processed food"],

    # 肠道相关
    "肠道菌群":   ["gut microbiota", "gut microbiome", "intestinal microbiota"],
    "益生菌":     ["probiotics", "probiotic supplementation"],
    "益生元":     ["prebiotics"],
    "粪菌移植":   ["fecal microbiota transplantation", "FMT"],
    "肠脑轴":     ["gut-brain axis"],
    "短链脂肪酸": ["short-chain fatty acids", "SCFAs", "butyrate"],
    "双歧杆菌":   ["Bifidobacterium"],
    "乳酸杆菌":   ["Lactobacillus"],
    "阿克曼菌":   ["Akkermansia muciniphila"],
    "肠漏":       ["leaky gut", "intestinal permeability"],
    "便秘":       ["constipation", "gastrointestinal"],
    "腹泻":       ["diarrhea", "gastrointestinal symptoms"],
    "胃肠":       ["gastrointestinal", "GI symptoms"],

    # 营养素
    "维生素D":    ["vitamin D", "vitamin D deficiency", "cholecalciferol"],
    "维生素B":    ["vitamin B", "B vitamins", "folate"],
    "叶酸":       ["folate", "folic acid"],
    "铁":         ["iron", "iron deficiency"],
    "锌":         ["zinc", "zinc deficiency"],
    "镁":         ["magnesium"],
    "钙":         ["calcium"],
    "omega-3":    ["omega-3 fatty acids", "DHA", "fish oil"],
    "DHA":        ["DHA", "docosahexaenoic acid", "omega-3"],

    # 干预方式
    "饮食干预":   ["dietary intervention", "nutritional intervention"],
    "营养干预":   ["nutritional intervention", "dietary supplementation"],
    "补充剂":     ["supplementation", "dietary supplement"],

    # 症状行为
    "行为":       ["behavior", "behavioral symptoms"],
    "睡眠":       ["sleep", "sleep disturbance"],
    "感觉敏感":   ["sensory sensitivity", "sensory processing"],
    "焦虑":       ["anxiety"],
    "注意力":     ["attention", "ADHD"],
    "语言":       ["language", "communication"],
    "社交":       ["social behavior", "social communication"],

    # 人群
    "儿童":       ["children", "pediatric", "child"],
    "幼儿":       ["toddler", "young children", "preschool"],
    "青少年":     ["adolescent", "teenager"],
}


def translate_query_to_pubmed_terms(query: str) -> Tuple[str, str]:
    """
    将中文查询转换为 PubMed 检索式。

    返回：
        (严格检索式, 宽松检索式) 两个版本
        严格版：主题词 AND 修饰词
        宽松版：只用核心词，OR 连接同义词
    """
    collected: List[List[str]] = []   # 每个中文词对应的英文同义词组
    query_lower = query.lower()

    for zh_word, en_list in _ZH_TO_EN_KEYWORDS.items():
        if zh_word.lower() in query_lower:
            collected.append(en_list[:3])   # 每个词最多取3个同义词

    # 保留原始查询中的英文单词
    stopwords = {
        "the", "a", "an", "is", "are", "in", "on", "of",
        "for", "to", "and", "or", "what", "how", "why",
        "should", "can", "does", "which",
    }
    existing_lower = {t.lower() for group in collected for t in group}
    for word in re.findall(r"[a-zA-Z]{3,}", query):
        w = word.lower()
        if w not in stopwords and w not in existing_lower:
            collected.append([word])
            existing_lower.add(w)

    # 兜底：什么都没匹配到
    if not collected:
        fallback = query.strip()
        return (
            f'"{fallback}"[Title/Abstract]',
            f'"{fallback}"[Title/Abstract]',
        )

    # ── 严格检索式：每组取第一个词，AND 连接（最多4组）──
    strict_parts = [
        f'"{group[0]}"[Title/Abstract]'
        for group in collected[:4]
    ]
    strict = " AND ".join(strict_parts)

    # ── 宽松检索式：每组内部 OR，组间 AND（最多3组）──
    loose_parts = []
    for group in collected[:3]:
        if len(group) == 1:
            loose_parts.append(f'"{group[0]}"[Title/Abstract]')
        else:
            inner = " OR ".join(f'"{t}"[Title/Abstract]' for t in group)
            loose_parts.append(f"({inner})")
    loose = " AND ".join(loose_parts)

    return strict, loose


def search_pubmed_ids(
    query: str,
    max_results: int = 5,
    min_year: int = 2015,
) -> List[str]:
    """
    第一步：用关键词搜索 PubMed，返回 PMID 列表。
    内置三级降级策略：严格 → 宽松 → 仅核心词。

    Args:
        query:       中文或英文查询
        max_results: 最多返回几篇
        min_year:    只要这一年之后的文献

    Returns:
        PMID 字符串列表
    """
    current_year = datetime.datetime.now().year
    date_filter  = f"{min_year}:{current_year}[pdat]"

    # 判断是否含中文
    has_chinese = any('\u4e00' <= ch <= '\u9fff' for ch in query)

    if has_chinese:
        strict_term, loose_term = translate_query_to_pubmed_terms(query)
    else:
        strict_term = query
        loose_term  = query

    # 三级检索策略
    strategies = [
        ("严格", f"({strict_term}) AND ({date_filter})"),
        ("宽松", f"({loose_term}) AND ({date_filter})"),
        ("仅核心词", f"({loose_term.split(' AND ')[0]}) AND ({date_filter})"),
    ]

    for level, search_term in strategies:
        params = {
            "db":      "pubmed",
            "term":    search_term,
            "retmax":  max_results,
            "retmode": "json",
            "sort":    "relevance",
            "tool":    NCBI_TOOL,
            "email":   NCBI_EMAIL,
        }
        try:
            print(f"🔍 [{level}] PubMed 检索式: {search_term}")
            resp = requests.get(PUBMED_ESEARCH, params=params, timeout=15)
            resp.raise_for_status()
            data  = resp.json()
            pmids = data.get("esearchresult", {}).get("idlist", [])
            print(f"   → 找到 {len(pmids)} 篇: {pmids}")

            if pmids:
                return pmids

            # 没找到则进入下一级
            print(f"   → 无结果，尝试下一级策略...")
            time.sleep(REQUEST_INTERVAL)

        except Exception as e:
            print(f"❌ PubMed esearch [{level}] 失败: {e}")
            time.sleep(REQUEST_INTERVAL)
            continue

    print("❌ 三级检索均无结果")
    return []


def fetch_pubmed_abstracts(pmids: List[str]) -> List[Dict]:
    """
    第二步：根据 PMID 列表获取摘要，返回结构化数据。

    Returns:
        列表，每项包含：
        {
            "pmid":     "38123456",
            "title":    "论文标题",
            "authors":  "Smith J, et al.",
            "journal":  "期刊名",
            "year":     "2023",
            "abstract": "摘要正文...",
            "url":      "https://pubmed.ncbi.nlm.nih.gov/38123456/"
        }
    """
    if not pmids:
        return []

    time.sleep(REQUEST_INTERVAL)

    params = {
        "db":      "pubmed",
        "id":      ",".join(pmids),
        "rettype": "abstract",
        "retmode": "xml",
        "tool":    NCBI_TOOL,
        "email":   NCBI_EMAIL,
    }

    try:
        resp = requests.get(PUBMED_EFETCH, params=params, timeout=20)
        resp.raise_for_status()
        return _parse_pubmed_xml(resp.text)
    except Exception as e:
        print(f"❌ PubMed efetch 失败: {e}")
        return []


def _parse_pubmed_xml(xml_text: str) -> List[Dict]:
    """解析 PubMed XML 响应，提取关键字段。"""
    results = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"❌ XML 解析失败: {e}")
        return []

    for article in root.findall(".//PubmedArticle"):
        try:
            # ── PMID ──
            pmid_el = article.find(".//PMID")
            pmid    = pmid_el.text.strip() if pmid_el is not None else "unknown"

            # ── 标题 ──
            title_el = article.find(".//ArticleTitle")
            title    = "".join(title_el.itertext()).strip() if title_el is not None else ""

            # ── 摘要（可能分多段，带 Label 的结构化摘要） ──
            abstract_texts = []
            for ab in article.findall(".//AbstractText"):
                label = ab.get("Label", "")
                text  = "".join(ab.itertext()).strip()
                if text:
                    abstract_texts.append(f"{label}: {text}" if label else text)
            abstract = " ".join(abstract_texts)

            # 没有摘要的跳过（通常是会议摘要或编辑信）
            if not abstract:
                continue

            # ── 作者（最多取前3位） ──
            authors = []
            for author in article.findall(".//Author")[:3]:
                last  = author.findtext("LastName", "")
                first = author.findtext("ForeName", "")
                if last:
                    authors.append(f"{last} {first[0]}." if first else last)
            author_str = ", ".join(authors)
            if len(article.findall(".//Author")) > 3:
                author_str += " et al."

            # ── 期刊 ──
            journal = (
                article.findtext(".//Journal/Title", "")
                or article.findtext(".//Journal/ISOAbbreviation", "")
            )

            # ── 年份（优先取 PubDate/Year，其次 MedlineDate） ──
            year_el = article.find(".//PubDate/Year")
            if year_el is not None:
                year = year_el.text.strip()
            else:
                medline_el = article.find(".//PubDate/MedlineDate")
                year = medline_el.text[:4] if medline_el is not None else ""

            results.append({
                "pmid":     pmid,
                "title":    title,
                "authors":  author_str,
                "journal":  journal,
                "year":     year,
                "abstract": abstract,
                "url":      f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            })

        except Exception as e:
            print(f"⚠️ 解析单篇文章失败 (pmid={pmid}): {e}")
            continue

    print(f"✅ 成功解析 {len(results)} 篇文献摘要")
    return results


def pubmed_search_context(
    query: str,
    max_results: int = 5,
    min_year: int = 2015,
) -> Tuple[str, List[Dict]]:
    """
    一站式接口：输入查询 → 输出 (上下文文本, 结构化文献列表)
    上下文文本可直接作为 LLM prompt 的一部分。

    Args:
        query:       用户原始查询（中文或英文均可）
        max_results: 最多获取几篇文献
        min_year:    文献年份下限

    Returns:
        (context_text, articles)
        context_text: 格式化好的字符串，空字符串表示未找到
        articles:     结构化文献列表
    """
    pmids = search_pubmed_ids(query, max_results=max_results, min_year=min_year)
    if not pmids:
        return "", []

    articles = fetch_pubmed_abstracts(pmids)
    if not articles:
        return "", []

    # 格式化为 LLM 可读的上下文
    context_parts = []
    for i, art in enumerate(articles, 1):
        context_parts.append(
            f"[PubMed {i}] {art['title']}\n"
            f"作者: {art['authors']} | 期刊: {art['journal']} ({art['year']})\n"
            f"摘要: {art['abstract']}\n"
            f"链接: {art['url']}"
        )

    context_text = "\n\n---\n\n".join(context_parts)
    return context_text, articles


# ── 本地测试入口 ───────────────────────────────────────────
if __name__ == "__main__":
    test_cases = [
        "自闭症儿童应该避免哪些食物",
        "自闭症儿童无麸质饮食干预效果",
        "自闭症肠道菌群益生菌",
    ]

    for test_query in test_cases:
        print(f"\n{'='*60}")
        print(f"测试查询: {test_query}")
        print('='*60)

        context, articles = pubmed_search_context(
            test_query, max_results=3, min_year=2018
        )

        if context:
            print(f"\n📄 获取到 {len(articles)} 篇文献，上下文预览：")
            print(context[:800])
            print("...")
        else:
            print("❌ 未获取到结果")
