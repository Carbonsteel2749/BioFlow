"""Article metadata model placeholder."""
from sqlalchemy import Column, Integer, String, Text, Date, BigInteger
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class Article(Base):
    __tablename__ = "articles"

    # 主键
    id = Column(Integer, primary_key=True, autoincrement=True, index=True)

    # PubMed原生基础字段（原有保留）
    title = Column(Text, nullable=False, index=True)
    abstract = Column(Text, nullable=True)
    full_text = Column(Text, nullable=True)  # 全文（无全文时回退为摘要）
    authors = Column(Text, nullable=True)
    journal = Column(String(512), nullable=True)
    publish_date = Column(Date, nullable=True)
    doi = Column(String(512), unique=True, nullable=True, index=True)
    pmid = Column(String(256), unique=True, nullable=True, index=True)
    keywords = Column(Text, nullable=True)
    source = Column(String(128), default="pubmed")

    # ========== 新增需求扩展字段 ==========
    # 期刊分区：Q1/Q2/Q3/Q4/未知
    jcr_quartile = Column(String(32), nullable=True)
    # 文献总引用量
    citation_count = Column(BigInteger, default=0, nullable=True)
    # 大模型生成半结构化JSON标签（主题、疾病、菌群、研究类型等）
    llm_tags = Column(JSONB, nullable=True)

    def __repr__(self):
        return f"<Article(id={self.id}, doi={self.doi}, title={self.title[:30]})>"
