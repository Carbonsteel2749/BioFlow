"""API schema definitions for literature browsing and evaluation."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ArticleOut(BaseModel):
	doi: str
	title: str
	abstract: Optional[str] = None
	authors: List[Dict[str, Any]] = Field(default_factory=list)
	publish_time: Optional[datetime] = None
	journal: Optional[str] = None
	journal_division: Optional[str] = None
	citation_count: int = 0
	keywords: List[str] = Field(default_factory=list)
	llm_tags: Dict[str, Any] = Field(default_factory=dict)
	source: str = "PubMed"


class SearchRequest(BaseModel):
	keyword: Optional[str] = None
	tag_key: Optional[str] = None
	tag_value: Optional[str] = None
	page: int = 1
	page_size: int = 20


class SearchResponse(BaseModel):
	total: int
	page: int
	page_size: int
	items: List[ArticleOut]


class TagQualityResponse(BaseModel):
	total: int
	tagged: int
	untagged: int
	coverage: float
	field_fill_rate: Dict[str, float]


class SyncResponse(BaseModel):
	fetched: int
	saved: int
	message: str


class HealthResponse(BaseModel):
	status: str = "ok"
