from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from bioflow.core.config import AppConfig, load_config
from bioflow.core.context import RunContext
from bioflow.core.models import ModuleInput
from bioflow.evidence.index import JsonlEvidenceIndex
from bioflow.evidence.sanitization import SanitizationRules
from bioflow.llm.providers import OllamaProvider
from bioflow.llm.router import ModelRouter
from bioflow.modules.catalog import get_registry
from bioflow.orchestrator.scheduler import ModuleScheduler
from bioflow.runners import MockNextflowRunner, NextflowRunner

app = FastAPI(title="Bioflow API")


class RunRequest(BaseModel):
    run_id: Optional[str] = None
    payload: Dict = {}


def _build_context(cfg: AppConfig, run_id: Optional[str]) -> RunContext:
    context = RunContext(run_id=run_id or cfg.run_id, workspace=Path(cfg.workspace))
    context.runners["mock"] = MockNextflowRunner(cfg.runners.mock.fixture)
    context.runners["nextflow"] = NextflowRunner(cfg.runners.nextflow.binary)
    evidence_path = context.run_dir() / "evidence.jsonl"
    context.evidence_index = JsonlEvidenceIndex(str(evidence_path))
    context.sanitizer = SanitizationRules(
        allow_external=cfg.sanitization.allow_external,
        banned_terms=cfg.sanitization.banned_terms,
        allowlist_fields=cfg.sanitization.allowlist_fields,
    )
    provider = OllamaProvider(cfg.models.ollama.url, cfg.models.ollama.model)
    context.model_router = ModelRouter(
        providers={cfg.models.default_provider: provider},
        default_provider=cfg.models.default_provider,
        sanitization=context.sanitizer,
    )
    context.rag_url = cfg.rag.url or os.getenv("BIOFLOW_RAG_URL", "")
    context.rag_timeout = cfg.rag.timeout
    return context


def _load_cfg() -> AppConfig:
    cfg_path = os.getenv("BIOFLOW_CONFIG", "config/example.yaml")
    return load_config(cfg_path)


@app.get("/modules")
def list_modules():
    registry = get_registry()
    return [spec.model_dump() for spec in registry.list_specs()]


@app.post("/run/{module_name}")
def run_module(module_name: str, request: RunRequest):
    cfg = _load_cfg()
    context = _build_context(cfg, request.run_id)
    registry = get_registry()
    scheduler = ModuleScheduler(registry)

    payload = {
        "runner_mode": cfg.runners.mode,
        "pipeline": cfg.runners.nextflow.pipeline,
        "params": request.payload.get("params", {}),
        "dataset_path": request.payload.get("dataset_path", "data/demo/expression_matrix.csv"),
        "user_request": request.payload.get("user_request", ""),
    }
    inputs = {
        "data_processing": ModuleInput(run_id=context.run_id, module="data_processing", payload=payload)
    }
    report = scheduler.run(context, module_names=[module_name], inputs=inputs)
    return report.summary()
