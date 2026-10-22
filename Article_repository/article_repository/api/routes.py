"""API route registration for article browsing, sync, and evaluation."""

import sys
from pathlib import Path
from typing import Dict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import APIRouter, Query

from article_repository.api.schemas import (
	HealthResponse,
	SyncResponse,
)
from article_repository.ingestion.full_sync import run_pubmed_sync

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
	return HealthResponse()


@router.post("/api/sync/pubmed", response_model=SyncResponse)
def sync_pubmed(
	sync_type: str = Query(default="test_full"),
	retmax: int = Query(default=200, ge=1, le=10000),
	reset: bool = Query(default=False),
) -> SyncResponse:
	result = run_pubmed_sync(
		sync_type=sync_type,
		retmax=retmax if sync_type != "official_full" else None,
		reset_ai_localbase=reset,
	)
	return SyncResponse(
		fetched=result["fetched"],
		saved=result["uploaded"],
		message=(
			f"{result['sync_type']} 完成：抓取 {result['fetched']} 篇，"
			f"上传 ai-localbase {result['uploaded']} 篇，知识库 {result['knowledge_base_id']}"
		),
	)
