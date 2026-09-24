import requests
from xml.etree import ElementTree as ET
from database import SessionLocal
from models.article import Article
from datetime import datetime
import time

# PubMed 官方接口地址
ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# 全局请求超时、重试配置
REQUEST_TIMEOUT = 45
MAX_RETRY = 2

def safe_request(url, params):
    """带重试机制的请求函数，解决网络卡顿卡死"""
    retry = 0
    while retry < MAX_RETRY:
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except Exception as e:
            retry += 1
            print(f"请求失败，第{retry}次重试: {str(e)}")
            time.sleep(2)
    print("多次重试失败，跳过本次请求")
    return None

def search_pubmed_pmid_list(keyword: str, ret_max: int = 20):
    """检索关键词，获取PMID数字列表"""
    params = {
        "db": "pubmed",
        "term": keyword,
        "retmax": ret_max,
        "retmode": "json"
    }
    resp = safe_request(ESEARCH_URL, params)
    if not resp:
        return []
    data = resp.json()
    return data["esearchresult"]["idlist"]

def batch_fetch_full_info(pmid_list):
    """批量efetch一次性获取PMID、标题、摘要、作者，绕过esummary空ID问题"""
    params = {
        "db": "pubmed",
        "id": ",".join(pmid_list),
        "retmode": "xml"
    }
    resp = safe_request(EFETCH_URL, params)
    if not resp:
        return {}
    root = ET.fromstring(resp.text)
    art_dict = {}
    for pubmed_article in root.findall(".//PubmedArticle"):
        medline_citation = pubmed_article.find("MedlineCitation")
        pmid_node = medline_citation.find("PMID")
        pmid = pmid_node.text if pmid_node is not None else ""
        if not pmid:
            continue
        article_node = medline_citation.find("Article")
        # 标题
        title_node = article_node.find("ArticleTitle")
        title = title_node.text.strip() if title_node and title_node.text else ""
        # 摘要
        abstract_texts = article_node.findall(".//AbstractText")
        abstract = " ".join([x.text.strip() for x in abstract_texts if x.text])
        # 作者
        author_list = article_node.find("AuthorList")
        authors = []
        if author_list:
            for author in author_list.findall("Author"):
                last = author.find("LastName")
                fore = author.find("ForeName")
                if last and fore:
                    authors.append(f"{fore.text} {last.text}")
        author_str = ",".join(authors)
        # 期刊
        journal_node = article_node.find("Journal/Title")
        journal = journal_node.text.strip() if journal_node and journal_node.text else ""
        # 发表日期
        pub_date_node = medline_citation.find("DateCreated")
        pub_date = None
        try:
            if pub_date_node:
                y = pub_date_node.find("Year").text
                m = pub_date_node.find("Month").text.zfill(2)
                d = pub_date_node.find("Day").text.zfill(2)
                pub_date = datetime.strptime(f"{y}/{m}/{d}", "%Y/%m/%d").date()
        except:
            pass
        # DOI
        doi = ""
        article_id_list = medline_citation.findall(".//ArticleId")
        for aid in article_id_list:
            if aid.attrib.get("IdType") == "doi":
                doi = aid.text.strip()
                break
        art_dict[pmid] = {
            "pmid": pmid,
            "title": title,
            "abstract": abstract,
            "authors": author_str,
            "journal": journal,
            "publish_date": pub_date,
            "doi": doi,
            "keywords": "",
            "source": "pubmed"
        }
    return art_dict

def crawl_and_save(keyword: str, count: int = 10):
    """完整抓取流程：检索→批量获取完整信息→查重入库"""
    pmid_list = search_pubmed_pmid_list(keyword, count)
    if not pmid_list:
        print("未检索到任何相关文献")
        return
    print(f"检索到{len(pmid_list)}篇文献，批量拉取完整信息...")
    art_info_map = batch_fetch_full_info(pmid_list)
    if not art_info_map:
        print("批量拉取文献信息失败")
        return
    db = SessionLocal()
    total_insert = 0
    for pmid, art_data in art_info_map.items():
        if not art_data["title"]:
            continue
        print(f"待入库 PMID:{art_data['pmid']} DOI:{art_data['doi']}")
        # 查重逻辑
        exist_art = None
        if art_data["doi"]:
            exist_art = db.query(Article).filter(Article.doi == art_data["doi"]).first()
        elif art_data["pmid"]:
            exist_art = db.query(Article).filter(Article.pmid == art_data["pmid"]).first()
        if exist_art:
            print(f"已存在，跳过 | PMID:{art_data['pmid']} | {art_data['title'][:50]}...")
            continue
        new_article = Article(**art_data)
        db.add(new_article)
        db.commit()
        total_insert += 1
        print(f"新增入库({total_insert}) | PMID:{art_data['pmid']} | {art_data['title'][:50]}...")
        time.sleep(0.3)
    db.close()
    print(f"\n本轮抓取完成，新增入库文献总数：{total_insert}")

import sys

if __name__ == "__main__":
    # 命令行传参格式：python crawl_pubmed.py "检索词" 抓取数量
    if len(sys.argv) >= 3:
        search_keyword = sys.argv[1]
        fetch_num = int(sys.argv[2])
    else:
        # 默认兜底关键词
        search_keyword = "gut microbiota alzheimer disease"
        fetch_num = 4
    crawl_and_save(keyword=search_keyword, count=fetch_num)
