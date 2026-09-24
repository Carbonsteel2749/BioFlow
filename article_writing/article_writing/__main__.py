"""CLI entry: python -m article_writing --run-id demo --out outputs/demo."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

from article_writing.adapters import validate_fixtures_dir
from article_writing.orchestrator import WritingPipeline


def _load_yaml_config(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _read_text_arg(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    path = Path(text)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return text


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    default_config = repo_root / "configs" / "example.yaml"

    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", default=str(default_config))
    pre_args, _ = pre.parse_known_args()
    cfg = _load_yaml_config(Path(pre_args.config))
    llm_cfg = cfg.get("llm") if isinstance(cfg.get("llm"), dict) else {}
    app_cfg = cfg.get("app") if isinstance(cfg.get("app"), dict) else {}
    writing_cfg = cfg.get("writing") if isinstance(cfg.get("writing"), dict) else {}
    react_cfg = writing_cfg.get("react") if isinstance(writing_cfg.get("react"), dict) else {}
    evidence_cfg = (
        writing_cfg.get("evidence") if isinstance(writing_cfg.get("evidence"), dict) else {}
    )
    consistency_cfg = (
        writing_cfg.get("consistency")
        if isinstance(writing_cfg.get("consistency"), dict)
        else {}
    )
    skills_cfg = writing_cfg.get("skills") if isinstance(writing_cfg.get("skills"), dict) else {}

    parser = argparse.ArgumentParser(description="BioFLow article_writing pipeline")
    parser.add_argument(
        "--config",
        default=str(default_config),
        help="YAML config (default: configs/example.yaml); llm.enabled is honored",
    )
    parser.add_argument("--run-id", default=str(app_cfg.get("run_id") or "writing-demo-001"))
    parser.add_argument("--out", default=None, help="Output directory (default: outputs/<run-id>)")
    parser.add_argument(
        "--fixtures",
        default=None,
        help="Optional fixtures directory (defaults to package fixtures/)",
    )
    parser.add_argument(
        "--query",
        default="autism gut microbiota differential expression",
        help="Literature search query (mock or live)",
    )
    parser.add_argument(
        "--adapter-mode",
        default="mock",
        choices=["mock", "live"],
        help="mock=fixtures; live=literature repo + optional BioLLM analysis/brief",
    )
    parser.add_argument(
        "--evidence-mode",
        default=str(evidence_cfg.get("mode") or "warn"),
        choices=["warn", "strict"],
        help="Phase-2 evidence binding: warn (default) or strict",
    )
    parser.add_argument(
        "--react",
        dest="react_enabled",
        action="store_true",
        default=bool(react_cfg.get("enabled", True)),
        help="Enable Related Work ReAct loop (default: on)",
    )
    parser.add_argument(
        "--no-react",
        dest="react_enabled",
        action="store_false",
        help="Disable ReAct; use a single LiteraturePort.search",
    )
    parser.add_argument(
        "--react-max-steps",
        type=int,
        default=int(react_cfg.get("max_steps") or 8),
        help="Max Thought/Action/Observation steps for ReAct",
    )
    parser.add_argument(
        "--consistency-mode",
        default=str(consistency_cfg.get("mode") or "warn"),
        choices=["warn", "strict"],
        help="Phase-4 PaperState consistency: warn (default) or strict",
    )
    parser.add_argument(
        "--literature-url",
        default="",
        help="If set, live literature uses HTTP GET /api/articles; else in-process SQLite",
    )
    parser.add_argument(
        "--literature-db",
        default="",
        help="SQLite path for Article_repository (default: Article_repository/article_literature.sqlite3)",
    )
    parser.add_argument(
        "--analysis-bundle",
        default="",
        help="Live AnalysisBundle JSON, BioLLM .tar.gz, or extracted package directory",
    )
    parser.add_argument(
        "--analysis-run-dir",
        default="",
        help="Extracted BioLLM package or task output directory",
    )
    parser.add_argument(
        "--analysis-url",
        default="",
        help="BioLLM API base URL (use with --analysis-task-id)",
    )
    parser.add_argument(
        "--analysis-task-id",
        default="",
        help="Completed BioLLM task id (downloads /api/tasks/{id}/results)",
    )
    parser.add_argument("--brief-path", default="", help="Live PaperBrief JSON path")
    parser.add_argument(
        "--llm",
        dest="llm_enabled",
        action="store_true",
        default=None,
        help="Force-enable LLM polish",
    )
    parser.add_argument(
        "--no-llm",
        dest="llm_enabled",
        action="store_false",
        help="Force-disable LLM polish (overrides config)",
    )
    parser.add_argument(
        "--llm-provider",
        default=str(llm_cfg.get("provider") or "ollama"),
        choices=["ollama", "openai", "openai_compat", "deepseek", "vllm", "fake"],
        help="LLM provider (recommended: ollama + qwen3:14b)",
    )
    parser.add_argument(
        "--llm-model",
        default=str(llm_cfg.get("model") or "qwen:7b"),
        help="Model name (default: qwen:7b; must exist in `ollama list`)",
    )
    parser.add_argument(
        "--llm-url",
        default=str(llm_cfg.get("url") or "http://127.0.0.1:11434"),
        help="Ollama base URL or OpenAI-compatible API base (…/v1)",
    )
    parser.add_argument(
        "--llm-api-key",
        default=None,
        help="API key for openai/deepseek/vllm (or set OPENAI_API_KEY)",
    )
    parser.add_argument(
        "--llm-timeout",
        type=float,
        default=float(llm_cfg.get("timeout") or 180.0),
        help="LLM HTTP timeout in seconds",
    )
    parser.add_argument(
        "--llm-language",
        default=str(llm_cfg.get("language") or writing_cfg.get("language") or "en+zh"),
        help="Target languages for polish (default: en+zh bilingual)",
    )
    parser.add_argument(
        "--llm-instructions",
        default=str(llm_cfg.get("instructions") or ""),
        help="Extra writing requirements appended to default rules",
    )
    parser.add_argument(
        "--skills",
        dest="skills_enabled",
        action="store_true",
        default=bool(skills_cfg.get("enabled", False)),
        help="Use bioflow_skills playbooks (nature-writing/polishing/reviewer)",
    )
    parser.add_argument(
        "--no-skills",
        dest="skills_enabled",
        action="store_false",
        help="Disable skill playbooks",
    )
    parser.add_argument(
        "--editor-letter",
        default="",
        help="Editor letter text or path; with --skills runs nature-response",
    )
    parser.add_argument(
        "--reviewer-comments",
        default="",
        help="Reviewer comments text or path; with --skills runs nature-response",
    )
    parser.add_argument(
        "--validate-fixtures",
        action="store_true",
        help="Only validate fixtures and exit",
    )
    args = parser.parse_args()

    if args.llm_enabled is None:
        args.llm_enabled = bool(llm_cfg.get("enabled", False))

    run_id = args.run_id
    out = Path(args.out) if args.out else Path(str(app_cfg.get("output_dir") or "outputs")) / run_id

    fixtures = Path(args.fixtures) if args.fixtures else None
    if args.validate_fixtures or args.adapter_mode == "mock":
        if args.adapter_mode == "mock" or args.validate_fixtures:
            summary = validate_fixtures_dir(fixtures)
            if args.validate_fixtures:
                print(summary)
                return

    pipeline = WritingPipeline(
        fixtures_dir=fixtures,
        literature_query=args.query,
        adapter_mode=args.adapter_mode,
        literature_url=args.literature_url,
        literature_db=args.literature_db or None,
        analysis_bundle_path=args.analysis_bundle,
        analysis_run_dir=args.analysis_run_dir,
        analysis_url=args.analysis_url,
        analysis_task_id=args.analysis_task_id,
        brief_path=args.brief_path,
        evidence_mode=args.evidence_mode,
        react_enabled=args.react_enabled,
        react_max_steps=args.react_max_steps,
        consistency_mode=args.consistency_mode,
        llm_enabled=bool(args.llm_enabled),
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
        llm_url=args.llm_url,
        llm_api_key=args.llm_api_key,
        llm_timeout=args.llm_timeout,
        llm_language=args.llm_language,
        llm_instructions=args.llm_instructions,
        skills_enabled=bool(args.skills_enabled),
        editor_letter=_read_text_arg(args.editor_letter),
        reviewer_comments=_read_text_arg(args.reviewer_comments),
    )
    bundle_path = pipeline.run_and_export(run_id=run_id, output_dir=out)
    print(f"exported: {bundle_path}")
    print(f"adapter_mode: {pipeline.adapters.mode}")
    print(f"react_enabled: {pipeline.react_enabled}")
    print(f"evidence_mode: {pipeline.evidence_mode.value}")
    print(f"consistency_mode: {pipeline.consistency_mode.value}")
    print(
        f"llm: enabled={getattr(pipeline.llm, 'enabled', False)} "
        f"provider={getattr(pipeline.llm, 'provider', 'n/a')} "
        f"model={getattr(pipeline.llm, 'model', 'n/a')}"
    )
    if pipeline.last_consistency_report is not None:
        print(f"consistency_ok: {pipeline.last_consistency_report.ok}")
    print(f"skills_enabled: {pipeline.skills_enabled}")
    if pipeline.skill_outputs:
        print("skill_outputs: " + ", ".join(k for k in pipeline.skill_outputs if k.endswith(".md")))


if __name__ == "__main__":
    main()
