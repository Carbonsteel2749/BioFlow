"""
PubMed 检索式生成器
支持 strict / balanced / broad 三种模式，按字段限定符分层渐进检索，
并在 broad 模式使用 MeSH 受控词表和 All Fields 全字段检索最大化召回。

检索策略：
  strict   → [Title] 字段 + 核心词         → 高精度，快速验证
  balanced → [Title/Abstract] + 扩展词      → 精度与召回平衡
  broad    → [Title/Abstract] + [MeSH] + [All Fields] + 全部词 → 最大召回
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional

# ── 同义词库：按 core / extended / mesh 三级组织 ──────────────────
MEDICAL_TERMS: Dict[str, Dict[str, List[str]]] = {
    "自闭症": {
        "core": [
            "autism",
            "ASD",
            "autism spectrum disorder",
            "autistic disorder",
        ],
        "extended": [
            "autistic",
            "pervasive developmental disorder",
            "Asperger syndrome",
            "neurodevelopmental disorder",
            "child development disorder",
            "social communication disorder",
        ],
        "mesh": [
            "Autism Spectrum Disorder",
            "Autistic Disorder",
            "Child Development Disorders, Pervasive",
        ],
    },
    "肠道菌群": {
        "core": [
            "gut microbiota",
            "gut microbiome",
            "intestinal flora",
            "intestinal microbiota",
        ],
        "extended": [
            "gut flora",
            "microbiome",
            "microbiota",
            "fecal microbiota",
            "gastrointestinal microbiome",
            "enteric microbiota",
            "microbial community",
            "dysbiosis",
            "gut-brain axis",
            "probiotics",
            "prebiotics",
            "gut bacteria",
            "intestinal bacteria",
        ],
        "mesh": [
            "Gastrointestinal Microbiome",
            "Microbiota",
            "Dysbiosis",
            "Probiotics",
            "Fecal Microbiota Transplantation",
        ],
    },
    # 英文关键词回退映射
    "autism": {
        "core": [
            "autism",
            "ASD",
            "autism spectrum disorder",
            "autistic disorder",
        ],
        "extended": [
            "autistic",
            "pervasive developmental disorder",
            "Asperger syndrome",
            "neurodevelopmental disorder",
        ],
        "mesh": [
            "Autism Spectrum Disorder",
            "Autistic Disorder",
        ],
    },
    "gut microbiota": {
        "core": [
            "gut microbiota",
            "gut microbiome",
            "intestinal flora",
            "intestinal microbiota",
        ],
        "extended": [
            "gut flora",
            "microbiome",
            "microbiota",
            "fecal microbiota",
            "gastrointestinal microbiome",
            "enteric microbiota",
            "dysbiosis",
            "gut-brain axis",
            "probiotics",
            "prebiotics",
        ],
        "mesh": [
            "Gastrointestinal Microbiome",
            "Microbiota",
            "Dysbiosis",
        ],
    },
}


def _build_group(keyword: str, mode: str) -> str:
    """根据模式生成不同字段限定符的 OR 子查询组。

    strict:   仅 [Title] 字段 + 核心词
    balanced: [Title/Abstract] 字段 + 核心词 + 部分扩展词
    broad:    [Title/Abstract] + [MeSH Terms] + [All Fields] + 全部词
    """
    term_data = MEDICAL_TERMS.get(
        keyword,
        {"core": [keyword], "extended": [], "mesh": []},
    )

    if mode == "strict":
        # 仅用 Title 字段 + 核心词（3-4个）
        terms = term_data["core"][:3]
        parts = [f'"{t}"[Title]' for t in terms]

    elif mode == "balanced":
        # Title/Abstract 字段 + 核心词 + 扩展词前几个
        terms = term_data["core"] + term_data["extended"][:4]
        parts = [f'"{t}"[Title/Abstract]' for t in terms]

    else:  # broad
        parts: List[str] = []
        # Title/Abstract: 全部核心 + 扩展词
        ta_terms = term_data["core"] + term_data["extended"]
        parts += [f'"{t}"[Title/Abstract]' for t in ta_terms]
        # MeSH Terms: 受控词表（不加引号，MeSH 是精确匹配）
        mesh_terms = term_data.get("mesh", [])
        parts += [f'{t}[MeSH Terms]' for t in mesh_terms]
        # All Fields: 核心词兜底（全字段检索）
        parts += [f'"{t}"[All Fields]' for t in term_data["core"][:3]]

    return "(" + " OR ".join(parts) + ")"


def _build_date_filter(years_back: int) -> str:
    """生成 PubMed 日期范围过滤子句"""
    start = (datetime.now() - timedelta(days=years_back * 365)).strftime("%Y/%m/%d")
    end = datetime.now().strftime("%Y/%m/%d")
    return f' AND ("{start}"[Date - Publication] : "{end}"[Date - Publication])'


def build_query(
    keywords: List[str],
    mode: str = "balanced",
    years_back: int = 10,
) -> str:
    """生成一个主检索式。

    Args:
        keywords: 关键词列表，如 ["自闭症", "肠道菌群"]
        mode: 检索模式 strict/balanced/broad
        years_back: 往前回溯的年数

    Returns:
        PubMed 检索式字符串
    """
    groups = [_build_group(k, mode) for k in keywords]
    query = " AND ".join(groups)

    if years_back and years_back > 0:
        query += _build_date_filter(years_back)

    # strict 和 balanced 模式限制英文文献提高精度
    if mode != "broad":
        query += ' AND "english"[Language]'

    return query


def build_query_with_date_from(
    keywords: List[str],
    mode: str = "broad",
    date_from: Optional[datetime] = None,
    years_back: int = 0,
) -> str:
    """生成带起始日期过滤的检索式，用于增量同步。

    Args:
        keywords: 关键词列表
        mode: 检索模式
        date_from: 增量同步的起始日期（仅拉取该日期之后的文章）
        years_back: 若 date_from 为 None 时的回退年数

    Returns:
        PubMed 检索式字符串
    """
    groups = [_build_group(k, mode) for k in keywords]
    query = " AND ".join(groups)

    if date_from is not None:
        start = date_from.strftime("%Y/%m/%d")
        end = datetime.now().strftime("%Y/%m/%d")
        query += f' AND ("{start}"[Date - Publication] : "{end}"[Date - Publication])'
    elif years_back > 0:
        query += _build_date_filter(years_back)

    if mode != "broad":
        query += ' AND "english"[Language]'

    return query


def build_query_variants(
    keywords: List[str],
    years_back: int = 10,
    date_from: Optional[datetime] = None,
) -> List[str]:
    """生成 strict → balanced → broad 三组检索式。

    date_from 存在时用于增量同步；否则使用 years_back 生成全量/回溯检索式。
    """
    variants: List[str] = []
    for mode in ["strict", "balanced", "broad"]:
        if date_from is not None:
            variants.append(
                build_query_with_date_from(
                    keywords,
                    mode=mode,
                    date_from=date_from,
                    years_back=years_back,
                )
            )
        else:
            variants.append(build_query(keywords, mode=mode, years_back=years_back))
    return variants


def get_query_versions() -> Dict[str, str]:
    """返回三种检索模式的实际检索式，便于调试和验证"""
    return {
        "strict（精准版 - Title）": build_query(["自闭症", "肠道菌群"], mode="strict"),
        "balanced（平衡版 - Title/Abstract）": build_query(["自闭症", "肠道菌群"], mode="balanced"),
        "broad（宽泛版 - All Fields + MeSH）": build_query(["自闭症", "肠道菌群"], mode="broad"),
    }


# 测试入口
if __name__ == "__main__":
    print("=" * 80)
    print("PubMed 检索式生成器 - 多级渐进式检索")
    print("=" * 80)
    for name, query in get_query_versions().items():
        print(f"\n【{name}】")
        print("-" * 80)
        print(query)
        print(f"  查询长度: {len(query)} 字符")

    print("\n" + "=" * 80)
    print("增量同步检索式示例（date_from=2025-01-01）")
    print("-" * 80)
    inc_query = build_query_with_date_from(
        ["自闭症", "肠道菌群"],
        mode="broad",
        date_from=datetime(2025, 1, 1),
    )
    print(inc_query)
    print(f"  查询长度: {len(inc_query)} 字符")
    print("\n检索式生成成功！")