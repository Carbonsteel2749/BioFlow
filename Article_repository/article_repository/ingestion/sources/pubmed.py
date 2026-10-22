"""PubMed adapter for fetching and normalizing literature metadata."""

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Dict, List, Optional

from article_repository.search.query_builder import build_query_variants

def _request_json(url: str) -> Dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "BioFLow-ArticleRepo/0.1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))

def _safe_text(element, tag_name: str) -> str:
    child = element.find(tag_name) if element is not None else None
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _parse_date(pub_date_elem) -> Optional[datetime]:
    if pub_date_elem is None:
        return None
    year = _safe_text(pub_date_elem, "Year")
    month = _safe_text(pub_date_elem, "Month")
    day = _safe_text(pub_date_elem, "Day")
    if not year:
        return None
    month_map = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    try:
        month_num = int(month) if month.isdigit() else month_map.get(month[:3].lower(), 1)
        day_num = int(day) if day.isdigit() else 1
        return datetime(int(year), month_num, day_num)
    except Exception:
        return None


def _extract_publication_date(pubmed_article, article) -> Optional[datetime]:
    if article is not None:
        journal_date = article.find("Journal/JournalIssue/PubDate")
        parsed = _parse_date(journal_date)
        if parsed is not None:
            return parsed
    pubmed_data = pubmed_article.find("PubmedData") if pubmed_article is not None else None
    if pubmed_data is not None:
        for pub_date in pubmed_data.findall("./History/PubMedPubDate"):
            parsed = _parse_date(pub_date)
            if parsed is not None:
                return parsed
    return None


def _extract_abstract(article) -> str:
    abstract_text = []
    for abstract_elem in article.findall("./Abstract/AbstractText"):
        if abstract_elem.text:
            abstract_text.append(abstract_elem.text.strip())
    return "\n".join(abstract_text)


def _extract_authors(article) -> List[Dict]:
    authors = []
    for author in article.findall("./AuthorList/Author"):
        last_name = _safe_text(author, "LastName")
        initials = _safe_text(author, "Initials")
        name = f"{last_name} {initials}".strip()
        if not name:
            name = _safe_text(author, "CollectiveName")
        if name:
            authors.append({"name": name, "affiliation": ""})
    return authors


def _extract_keywords(medline) -> List[str]:
    """从 MedlineCitation 中提取关键词。

    注意：PubMed XML 的 <KeywordList> 是 <MedlineCitation> 的直接子节点，
    而不是 <Article> 的子节点，因此必须传入 medline 元素。
    """
    keywords = []
    for keyword in medline.findall("./KeywordList/Keyword"):
        value = (keyword.text or "").strip()
        if value and value not in keywords:
            keywords.append(value)
    # MeSH 兜底：若作者关键词为空，用 MeSH 主题词补齐
    if not keywords:
        for mesh in medline.findall("./MeshHeadingList/MeshHeading/DescriptorName"):
            value = (mesh.text or "").strip()
            if value and value not in keywords:
                keywords.append(value)
    return keywords


def _extract_article_id(pubmed_article, id_type: str) -> str:
    """从 PubmedData/ArticleIdList 中提取 DOI/PMC 等编号。"""
    pubmed_data = pubmed_article.find("PubmedData") if pubmed_article is not None else None
    article_id_list = pubmed_data.find("ArticleIdList") if pubmed_data is not None else None
    if article_id_list is None:
        return ""
    for article_id in article_id_list.findall("ArticleId"):
        if article_id.attrib.get("IdType") == id_type:
            return (article_id.text or "").strip()
    return ""


def _extract_pmc_id(pubmed_article) -> str:
    """提取 PMC 编号（如 PMC123456）。"""
    return _extract_article_id(pubmed_article, "pmc")


def _extract_pmc_body_text(root) -> str:
    """从 PMC 全文 JATS XML 的 <body> 中抽取可读正文。"""
    body = root.find(".//body")
    if body is None:
        return ""
    parts = []
    for node in body.iter():
        tag = str(node.tag).lower().rsplit("}", 1)[-1]
        if tag in ("p", "title", "sec-title") and node.text and node.text.strip():
            parts.append(node.text.strip())
    # 兜底：上面没取到任何段落时，退化为拼接全部文本
    if not parts:
        parts = [t.strip() for t in body.itertext() if t and t.strip()]
    return "\n".join(parts)


def _fetch_fulltext(pmc_id: str) -> str:
    """通过 E-utilities 从 PMC 抓取开放获取文献的全文。

    仅开放获取（OA）文章能拿到全文；拿不到时返回空字符串，
    由调用方回退到摘要。
    """
    if not pmc_id:
        return ""
    if not pmc_id.startswith("PMC"):
        pmc_id = "PMC" + pmc_id
    fetch_url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        "?db=pmc&id={id}&retmode=xml"
    ).format(id=urllib.parse.quote(pmc_id))
    try:
        with urllib.request.urlopen(
            urllib.request.Request(fetch_url, headers={"User-Agent": "BioFLow-ArticleRepo/0.1"}),
            timeout=40,
        ) as response:
            xml_text = response.read().decode("utf-8", errors="ignore")
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_text)
        return _extract_pmc_body_text(root)
    except Exception:
        return ""


def fetch_pubmed_articles(
    keywords: Optional[List[str]] = None,
    mode: str = "balanced",
    years_back: int = 10,
    retmax: Optional[int] = 50,
    date_from: Optional[datetime] = None,
    sort: str = "pub_date",
) -> List[Dict]:
    """Fetch PubMed literature metadata for the given keywords and normalize it.

    Args:
        keywords: 搜索关键词列表，默认 ["自闭症", "肠道菌群"]
        mode: 检索模式 strict/balanced/broad
        years_back: 往前回溯的年数（date_from 为 None 时生效）
        retmax: 最大返回数量
        date_from: 增量同步的起始日期，仅拉取该日期之后发表的文章

    Returns:
        归一化后的文献元数据列表
    """
    if keywords is None:
        keywords = ["自闭症", "肠道菌群"]

    # 全量和增量都按 strict → balanced → broad 三组检索式依次执行，
    # 然后全局去重、按发表时间排序，最后再截断到 retmax。
    query_variants = build_query_variants(
        keywords,
        years_back=0 if date_from is not None else years_back,
        date_from=date_from,
    )

    seen_ids = set()
    articles: List[Dict] = []
    last_error: Optional[Exception] = None

    target_count = retmax
    page_size = 200
    for query in query_variants:
        retstart = 0
        fetched_for_query = 0
        while target_count is None or fetched_for_query < target_count:
            request_size = page_size if target_count is None else min(page_size, target_count - fetched_for_query)
            time.sleep(0.4)
            search_url = (
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
                "?db=pubmed&retmode=json&retmax={retmax}&retstart={retstart}"
                "&sort={sort}&term={term}"
            ).format(
                retmax=request_size,
                retstart=retstart,
                sort=urllib.parse.quote(sort),
                term=urllib.parse.quote(query),
            )
            try:
                search_result = _request_json(search_url)
            except Exception as exc:
                last_error = exc
                break
            id_list = search_result.get("esearchresult", {}).get("idlist", [])
            if not id_list:
                break
            time.sleep(0.4)
            fetch_url = (
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
                "?db=pubmed&id={ids}&retmode=xml&rettype=abstract"
            ).format(ids=",".join(id_list))
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(fetch_url, headers={"User-Agent": "BioFLow-ArticleRepo/0.1"}),
                    timeout=30,
                ) as fetch_result:
                    xml_text = fetch_result.read().decode("utf-8", errors="ignore")
            except Exception as exc:
                last_error = exc
                break
            try:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(xml_text)
            except Exception as exc:
                raise RuntimeError(f"PubMed XML 解析失败: {exc}") from exc

            fetched_for_query += len(id_list)
            for pubmed_article in root.findall("./PubmedArticle"):
                medline = pubmed_article.find("MedlineCitation")
                if medline is None:
                    continue
                article = medline.find("Article")
                pmid = _safe_text(medline, "PMID")
                if not pmid or pmid in seen_ids:
                    continue
                seen_ids.add(pmid)
                title = _safe_text(article, "ArticleTitle") if article is not None else ""
                abstract = _extract_abstract(article) if article is not None else ""
                journal = article.find("Journal") if article is not None else None
                journal_title = _safe_text(journal, "Title")
                pub_date = _extract_publication_date(pubmed_article, article)
                doi = _extract_article_id(pubmed_article, "doi")
                keywords = _extract_keywords(medline)
                authors = _extract_authors(article) if article is not None else []
                pmc_id = _extract_pmc_id(pubmed_article)
                full_text = _fetch_fulltext(pmc_id) if pmc_id else ""
                time.sleep(0.4)
                content_type = "full_text" if full_text else "abstract"
                if not full_text:
                    full_text = abstract
                articles.append({
                    "doi": doi or f"pubmed:{pmid}",
                    "title": title,
                    "abstract": abstract,
                    "full_text": full_text,
                    "authors": authors,
                    "publish_time": pub_date or datetime.now(),
                    "journal": journal_title,
                    "journal_division": None,
                    "citation_count": 0,
                    "keywords": keywords,
                    "llm_tags": {},
                    "source": "PubMed",
                    "pmid": pmid,
                    "pmc_id": pmc_id,
                    "content_type": content_type,
                })
            retstart += len(id_list)
            if len(id_list) < request_size:
                break

    articles.sort(
        key=lambda item: item.get("publish_time") or datetime.min,
        reverse=True,
    )
    if target_count is not None:
        articles = articles[:target_count]

    if not articles and last_error is not None:
        raise RuntimeError(f"PubMed 检索失败: {last_error}") from last_error

    return articles


def save_pubmed_articles(repo, articles: List[Dict], incremental: bool = False) -> int:
    """SQLite persistence is disabled; ai-localbase is the active repository."""
    print("SQLite 已停用：跳过本地 SQLite 文献元数据库写入。")
    return len(articles)
