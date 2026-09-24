from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

import typer

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

app = typer.Typer(add_completion=False)


def build_context(cfg: AppConfig) -> RunContext:
    context = RunContext(run_id=cfg.run_id, workspace=Path(cfg.workspace))
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


def build_inputs(
    cfg: AppConfig,
    runner_mode: Optional[str],
    pipeline: Optional[str],
    dataset_path: Optional[str],
    user_request: Optional[str],
    metadata_path: Optional[str] = None,
    group_a: Optional[str] = None,
    group_b: Optional[str] = None,
    log2fc_threshold: Optional[float] = None,
    padj_threshold: Optional[float] = None,
    method: Optional[str] = None,
) -> Dict[str, ModuleInput]:
    payload = {
        "runner_mode": runner_mode or cfg.runners.mode,
        "pipeline": pipeline or cfg.runners.nextflow.pipeline,
        "params": {},
        "dataset_path": dataset_path or str(Path("data/demo/expression_matrix.csv")),
        "user_request": user_request or "",
    }
    differential_payload = {
        key: value
        for key, value in {
            "metadata_path": metadata_path,
            "group_a": group_a,
            "group_b": group_b,
            "log2fc_threshold": log2fc_threshold,
            "padj_threshold": padj_threshold,
            "method": method,
        }.items()
        if value is not None and value != ""
    }
    return {
        "data_processing": ModuleInput(run_id=cfg.run_id, module="data_processing", payload=payload),
        "differential_analysis": ModuleInput(
            run_id=cfg.run_id,
            module="differential_analysis",
            payload=differential_payload,
        ),
    }


@app.command("list-modules")
def list_modules(config: str = typer.Option("config/example.yaml", "--config")) -> None:
    cfg = load_config(config)
    registry = get_registry()
    for spec in registry.list_specs():
        typer.echo(f"{spec.name} ({spec.kind}) deps={','.join(spec.deps)}")


@app.command("run-module")
def run_module(
    module_name: str,
    config: str = typer.Option("config/example.yaml", "--config"),
    runner_mode: Optional[str] = typer.Option(None, "--runner"),
    pipeline: Optional[str] = typer.Option(None, "--pipeline"),
    dataset_path: Optional[str] = typer.Option(None, "--dataset-path"),
    user_request: Optional[str] = typer.Option(None, "--user-request"),
    metadata_path: Optional[str] = typer.Option(None, "--metadata-path"),
    group_a: Optional[str] = typer.Option(None, "--group-a"),
    group_b: Optional[str] = typer.Option(None, "--group-b"),
    log2fc_threshold: Optional[float] = typer.Option(None, "--log2fc-threshold"),
    padj_threshold: Optional[float] = typer.Option(None, "--padj-threshold"),
    method: Optional[str] = typer.Option(None, "--method"),
) -> None:
    cfg = load_config(config)
    context = build_context(cfg)
    registry = get_registry()
    scheduler = ModuleScheduler(registry)
    inputs = build_inputs(
        cfg,
        runner_mode,
        pipeline,
        dataset_path,
        user_request,
        metadata_path,
        group_a,
        group_b,
        log2fc_threshold,
        padj_threshold,
        method,
    )
    report = scheduler.run(context, module_names=[module_name], inputs=inputs)
    typer.echo(report.summary())


@app.command("run-all")
def run_all(
    config: str = typer.Option("config/example.yaml", "--config"),
    runner_mode: Optional[str] = typer.Option(None, "--runner"),
    pipeline: Optional[str] = typer.Option(None, "--pipeline"),
    dataset_path: Optional[str] = typer.Option(None, "--dataset-path"),
    user_request: Optional[str] = typer.Option(None, "--user-request"),
    metadata_path: Optional[str] = typer.Option(None, "--metadata-path"),
    group_a: Optional[str] = typer.Option(None, "--group-a"),
    group_b: Optional[str] = typer.Option(None, "--group-b"),
    log2fc_threshold: Optional[float] = typer.Option(None, "--log2fc-threshold"),
    padj_threshold: Optional[float] = typer.Option(None, "--padj-threshold"),
    method: Optional[str] = typer.Option(None, "--method"),
) -> None:
    cfg = load_config(config)
    context = build_context(cfg)
    registry = get_registry()
    scheduler = ModuleScheduler(registry)
    inputs = build_inputs(
        cfg,
        runner_mode,
        pipeline,
        dataset_path,
        user_request,
        metadata_path,
        group_a,
        group_b,
        log2fc_threshold,
        padj_threshold,
        method,
    )
    report = scheduler.run(context, inputs=inputs)
    typer.echo(report.summary())


if __name__ == "__main__":
    app()