"""PMC 开放获取(OA)专抓源。

普通 PubMed 检索到的文章大多不是 PMC 开放获取子集成员，拿不到全文。
本模块直接从 PubMed Central 的 open access 子集检索，并通过 E-utilities
的 efetch(db=pmc) 拉取完整的 JATS 全文 XML，从而入库真实全文。

与 pubmed.py 的关系：两者产物都是归一化后的文献字典（含 full_text），
区别是本模块保证 full_text 是真实全文（非摘要回退）。
"""

import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _request_json(url: str) -> Dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": "BioFLow-ArticleRepo/PMC-OA/0.1"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _local_name(tag) -> str:
    return str(tag).rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _find_local(element, name: str):
    for child in element.iter():
        if _local_name(child.tag) == name:
            return child
    return None


def _find_all_local(element, name: str):
    return [c for c in element.iter() if _local_name(c.tag) == name]


def _safe_text(element) -> str:
    return (element.text or "").strip() if element is not None else ""


def _body_text(root) -> str:
    """从 JATS <body> 抽取完整正文，保留段落内部的内联格式文本。"""
    body = _find_local(root, "body")
    if body is None:
        return ""
    parts = []
    for node in body.iter():
        if _local_name(node.tag) in ("p", "title", "sec-title"):
            text = "".join(node.itertext()).strip()
            if text:
                parts.append(text)
    # 兜底：没取到任何块时退化为全部文本
    if not parts:
        parts = [t.strip() for t in body.itertext() if t and t.strip()]
    return "\n".join(parts)


def _abstract_text(root) -> str:
    abs_elem = _find_local(root, "abstract")
    if abs_elem is None:
        return ""
    parts = []
    for node in abs_elem.iter():
        if _local_name(node.tag) == "p":
            text = "".join(node.itertext()).strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


def _authors(root) -> List[Dict]:
    authors = []
    for contrib in _find_all_local(root, "contrib"):
        if contrib.attrib.get("contrib-type") != "author":
            continue
        surname = _safe_text(_find_local(contrib, "surname"))
        given = _safe_text(_find_local(contrib, "given-names"))
        name = f"{surname} {given}".strip() if given else surname
        if name and not any(a["name"] == name for a in authors):
            authors.append({"name": name, "affiliation": ""})
    return authors


def _keywords(root) -> List[str]:
    kws = []
    for kwd in _find_all_local(root, "kwd"):
        text = "".join(kwd.itertext()).strip()
        if text and text not in kws:
            kws.append(text)
    return kws


def _pub_date(root, article) -> Optional[datetime]:
    pub_date = _find_local(article, "pub-date")
    year = _safe_text(_find_local(pub_date, "year")) if pub_date is not None else ""
    if not year or not year.isdigit():
        return None
    month = _safe_text(_find_local(pub_date, "month")) if pub_date is not None else ""
    day = _safe_text(_find_local(pub_date, "day")) if pub_date is not None else ""
    month_num = int(month) if month.isdigit() else 1
    day_num = int(day) if day.isdigit() else 1
    try:
        return datetime(int(year), month_num, day_num)
    except Exception:
        return None


def _identifiers(article) -> Dict[str, str]:
    ids = {}
    for node in _find_all_local(article, "article-id"):
        kind = node.attrib.get("pub-id-type")
        if kind and node.text:
            ids[kind] = node.text.strip()
    return ids


def _parse_pmc_article(root) -> Dict:
    article = _find_local(root, "article")
    if article is None:
        raise RuntimeError("PMC XML 中未找到 <article> 节点")
    ids = _identifiers(article)
    article_title = _find_local(article, "article-title")
    title = "".join(article_title.itertext()).strip() if article_title is not None else ""
    journal = ""
    jtitle = _find_local(article, "journal-title")
    journal = "".join(jtitle.itertext()).strip() if jtitle is not None else ""
    return {
        "title": title,
        "abstract": _abstract_text(root),
        "body": _body_text(root),
        "authors": _authors(article),
        "keywords": _keywords(article),
        "journal": journal,
        "publish_time": _pub_date(root, article),
        "pmid": ids.get("pmid", ""),
        "doi": ids.get("doi", ""),
        "pmc": ids.get("pmc", ""),
    }


def _fetch_pmc_xml(pmc_id: str) -> ET.Element:
    if not pmc_id.startswith("PMC"):
        pmc_id = "PMC" + pmc_id
    fetch_url = (
        f"{_EUTILS}/efetch.fcgi?db=pmc&id={urllib.parse.quote(pmc_id)}&retmode=xml"
    )
    with urllib.request.urlopen(
        urllib.request.Request(fetch_url, headers={"User-Agent": "BioFLow-ArticleRepo/PMC-OA/0.1"}),
        timeout=40,
    ) as response:
        return ET.fromstring(response.read().decode("utf-8", errors="ignore"))


def esearch_oa_pmc_ids(term: str, retmax: int = 100, sort: str = "pub_date") -> List[str]:
    """在 PMC 开放获取子集内检索，返回 PMC 编号列表。"""
    query = f"{term} AND open access[filter]"
    search_url = (
        f"{_EUTILS}/esearch.fcgi?db=pmc&retmode=json&retmax={retmax}"
        f"&sort={urllib.parse.quote(sort)}&term={urllib.parse.quote(query)}"
    )
    result = _request_json(search_url)
    return result.get("esearchresult", {}).get("idlist", [])


def fetch_oa_articles(
    term: str,
    retmax: int = 100,
    sleep: float = 0.4,
    include_nonoa: bool = False,
) -> List[Dict]:
    """从 PMC 开放获取子集抓取带全文的文献。

    Args:
        term: 检索式（如 "autism AND gut microbiota"）
        retmax: 最大篇数
        sleep: 请求间隔秒数（E-utilities 限流）
        include_nonoa: 若 True，则对拿不到全文的文章也保留（full_text 回退摘要）；
                       默认只返回拿到真实全文的文章。

    Returns:
        归一化文献列表（full_text 为真实全文）。
    """
    pmc_ids = esearch_oa_pmc_ids(term, retmax=retmax)
    articles: List[Dict] = []
    for pmc_id in pmc_ids:
        time.sleep(sleep)
        try:
            root = _fetch_pmc_xml(pmc_id)
            a = _parse_pmc_article(root)
        except Exception:
            continue

        full_text = a["body"]
        # 拿不到正文（如非 OA 或无 body）：回退摘要
        if not full_text:
            full_text = a["abstract"]
            if not include_nonoa:
                continue

        payload = {
            "doi": a["doi"] or (f"pubmed:{a['pmid']}" if a["pmid"] else f"pmc:{pmc_id}"),
            "title": a["title"],
            "abstract": a["abstract"] or None,
            "full_text": full_text,
            "authors": a["authors"],
            "publish_time": a["publish_time"] or datetime.now(),
            "journal": a["journal"],
            "journal_division": None,
            "citation_count": 0,
            "keywords": a["keywords"],
            "llm_tags": {},
            "source": "PMC-OA",
        }
        articles.append(payload)

    return articles


def save_oa_articles(repo, articles: List[Dict]) -> int:
    """SQLite persistence is disabled; ai-localbase is the active repository."""
    print("SQLite 已停用：跳过本地 SQLite OA 文献写入。")
    return len(articles)


if __name__ == "__main__":
    import sys

    term = sys.argv[1] if len(sys.argv) > 1 else "autism AND gut microbiota"
    retmax = int(sys.argv[2]) if len(sys.argv) > 2 else 50

    fetched = fetch_oa_articles(term, retmax=retmax)
    if not fetched:
        print("未抓取到任何 PMC-OA 文献，请检查检索式/网络。")
        raise SystemExit(1)
    save_oa_articles(None, fetched)
    has_full = sum(1 for a in fetched if len(a["full_text"]) > len(a["abstract"] or ""))
    print(f"PMC-OA 抓取完成：共 {len(fetched)} 篇")
    print(f"  拿到真实全文: {has_full} / {len(fetched)}，命中率 {has_full / len(fetched):.0%}")
    for a in fetched[:10]:
        print("  -", a["doi"], "| 全文", len(a["full_text"]), "| 标题:", a["title"][:55])