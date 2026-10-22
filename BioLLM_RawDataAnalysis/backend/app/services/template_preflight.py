from __future__ import annotations

import os
import runpy
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import Settings
from ..workflows.graph import WorkflowGraph
from ..workflows.registry import NodeRegistry
from .workflow_templates import validate_workflow_document


# Executables invoked by the existing basic node scripts; availability is
# checked by their shared require_command helper, not a simulated status.
_BASIC_TOOLS = {
    "fastq_input": ("python3", "gzip"),
    "fastqc": ("fastqc", "gzip", "python3"),
    "fastp": ("fastp", "gzip", "python3"),
    "host_depletion": ("bowtie2", "samtools", "gzip", "python3"),
    "taxonomy": ("kraken2", "bracken", "python3"),
}


@lru_cache(maxsize=4)
def _database_validator(path: str) -> dict[str, Any]:
    return runpy.run_path(path)


def preflight_workflow(
    workflow: WorkflowGraph | dict[str, Any],
    registry: NodeRegistry,
    settings: Settings,
) -> dict[str, Any]:
    graph, validation = validate_workflow_document(workflow, registry)
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, message: str) -> None:
        checks.append({"name": name, "status": status, "message": message})

    tools = {settings.nextflow_bin, "java"}
    for node in graph.nodes:
        if node.type not in _BASIC_TOOLS:
            add(node.id, "unknown", "此节点暂无独立模板预检适配，不能确认环境就绪。")
        tools.update(_BASIC_TOOLS.get(node.type, ()))
    common = settings.project_root / "workflow/bin/lib/common.sh"
    for command in sorted(tools):
        try:
            result = subprocess.run(
                ["bash", "-c", 'source "$1"; require_command "$2"',
                 "template-preflight", str(common), command],
                capture_output=True, text=True, timeout=10, check=False,
                env={**os.environ, "PIPELINE_TASK_ID": "template-preflight",
                     "PIPELINE_SAMPLE_ID": "global", "STEP_NAME": "preflight",
                     "STEP_LOG_FILE": os.devnull},
            )
            add(command, "passed" if result.returncode == 0 else "blocked",
                f"{command} 可用" if result.returncode == 0 else f"现有依赖预检未通过：{command}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            add(command, "unknown", f"无法完成依赖预检：{exc}")

    types = {node.type for node in graph.nodes}
    if "host_depletion" in types:
        prefix = settings.default_host_index
        shards = ('1', '2', '3', '4', 'rev.1', 'rev.2')
        ready = prefix is not None and any(all(
            Path(f'{prefix}.{part}.{extension}').is_file() and
            Path(f'{prefix}.{part}.{extension}').stat().st_size > 0
            for part in shards) for extension in ('bt2', 'bt2l'))
        add("host.grch38_bowtie2", "passed" if ready else "blocked",
            "动态流程已绑定服务器宿主索引，六个索引分片可用。" if ready else
            "服务器宿主索引未配置或分片不完整，不能运行去宿主。")

    if "taxonomy" in types:
        if not settings.default_database_registry or not settings.default_database_profile:
            add("database_registry", "blocked", "未配置数据库 registry/profile，无法调用现有数据库预检。")
        else:
            try:
                validator = _database_validator(str(settings.project_root / "workflow/bin/core/validate_databases.py"))
                resolved = validator["resolve_registry"](
                    settings.default_database_registry, settings.default_database_profile, False
                )
                add("database_registry", "passed",
                    "现有 reads 数据库预检通过（包含功能数据库；当前预检尚不支持仅检查物种分析）。")
                if not settings.default_database_manifest:
                    add("database_manifest", "blocked", "动态执行尚未配置 resolved database manifest。")
                else:
                    runtime = validator["load_json"](settings.default_database_manifest)
                    if runtime.get("databases") != resolved["databases"]:
                        add("database_manifest", "blocked", "执行所用数据库清单与本次预检结果不一致，请重新生成清单。")
                    else:
                        add("database_manifest", "passed", "执行数据库清单与现有预检结果一致。")
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                add("database_registry", "blocked", str(exc))

    if not validation.can_execute:
        add("workflow_risks", "blocked", "工作流仍有未确认的风险，请在现有编辑器中确认。")
    return {
        "ready": bool(checks) and all(check["status"] == "passed" for check in checks),
        "scope": "工具与数据库预检；不运行分析、不验证本次输入文件，也不保证完整任务成功。",
        "checks": checks,
    }
