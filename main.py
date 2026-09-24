from fastapi import FastAPI, Query, Depends
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel
from database import SessionLocal, get_db
from models.article import Article

# 实例化接口应用
app = FastAPI(
    title="BioFlow 文献检索接口",
    description="PubMed文献数据库查询API",
    version="1.0"
)

# 定义返回数据模型（格式化输出）
class ArticleResp(BaseModel):
    id: int
    title: str
    abstract: Optional[str]
    authors: Optional[str]
    journal: Optional[str]
    publish_date: Optional[str]
    doi: Optional[str]
    pmid: Optional[str]
    keywords: Optional[str]
    source: str

    class Config:
        from_attributes = True

# 接口1：分页关键词模糊检索文献
@app.get("/api/article/search", response_model=List[ArticleResp], summary="关键词检索文献（分页）")
def search_article(
    keyword: Optional[str] = Query(None, description="检索关键词，匹配标题/摘要"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(10, ge=1, le=50, description="每页条数"),
    db: Session = Depends(get_db)
):
    offset = (page - 1) * page_size
    query = db.query(Article)
    if keyword:
        query = query.filter(
            Article.title.ilike(f"%{keyword}%") |
            Article.abstract.ilike(f"%{keyword}%")
        )
    data_list = query.limit(page_size).offset(offset).all()
    return data_list

# 接口2：根据DOI精准查询单篇文献
@app.get("/api/article/doi/{doi_str}", response_model=ArticleResp, summary="DOI精准查询文献")
def get_by_doi(doi_str: str, db: Session = Depends(get_db)):
    art = db.query(Article).filter(Article.doi == doi_str).first()
    return art

# 接口3：根据PMID精准查询单篇文献
@app.get("/api/article/pmid/{pmid_str}", response_model=ArticleResp, summary="PMID精准查询文献")
def get_by_pmid(pmid_str: str, db: Session = Depends(get_db)):
    art = db.query(Article).filter(Article.pmid == pmid_str).first()
    return art

# 接口4：获取数据库全部文献总数
@app.get("/api/article/count", summary="统计文献总数量")
def get_article_count(keyword: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Article)
    if keyword:
        query = query.filter(
            Article.title.ilike(f"%{keyword}%") |
            Article.abstract.ilike(f"%{keyword}%")
        )
    total = query.count()
    return {"total_articles": total}
