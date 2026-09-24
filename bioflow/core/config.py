from __future__ import annotations

from pathlib import Path
from typing import List

import yaml
from pydantic import BaseModel, Field


class NextflowConfig(BaseModel):
    binary: str = "nextflow"
    pipeline: str = ""


class MockConfig(BaseModel):
    fixture: str = "data/mock/mock_result.json"


class RunnerConfig(BaseModel):
    mode: str = "mock"
    nextflow: NextflowConfig = Field(default_factory=NextflowConfig)
    mock: MockConfig = Field(default_factory=MockConfig)


class OllamaConfig(BaseModel):
    url: str = "http://127.0.0.1:11434/api/generate"
    model: str = "qwen3:14b"


class ModelConfig(BaseModel):
    default_provider: str = "ollama"
    allow_external: bool = False
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)


class RagConfig(BaseModel):
    url: str = "http://127.0.0.1:8008/rag/query"
    timeout: int = 60


class SanitizationConfig(BaseModel):
    allow_external: bool = False
    banned_terms: List[str] = Field(default_factory=list)
    allowlist_fields: List[str] = Field(default_factory=list)


class AppConfig(BaseModel):
    run_id: str = "run-0001"
    workspace: str = "runs"
    runners: RunnerConfig = Field(default_factory=RunnerConfig)
    models: ModelConfig = Field(default_factory=ModelConfig)
    rag: RagConfig = Field(default_factory=RagConfig)
    sanitization: SanitizationConfig = Field(default_factory=SanitizationConfig)


def load_config(path: str) -> AppConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    app_cfg = data.get("app", {})
    merged = {
        "run_id": app_cfg.get("run_id", "run-0001"),
        "workspace": app_cfg.get("workspace", "runs"),
        "runners": data.get("runners", {}),
        "models": data.get("models", {}),
        "rag": data.get("rag", {}),
        "sanitization": data.get("sanitization", {}),
    }
    return AppConfig.model_validate(merged)
