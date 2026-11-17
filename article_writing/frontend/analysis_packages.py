"""List finished analysis packages for the writing-page dropdown."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

DEFAULT_ROOT = Path("/home/xh/BioLLM_RawDataAnalysis/runtime/outputs")
ARCHIVE_SUFFIX = "_分析结果汇总.tar.gz"


def analysis_output_root() -> Path:
    configured = os.getenv("ARTICLE_WRITING_ANALYSIS_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_ROOT


def list_analysis_packages(root: Path | None = None) -> list[dict[str, str]]:
    """Newest package first. The option value is the task directory."""

    folder = root if root is not None else analysis_output_root()
    if not folder.is_dir():
        return []
    packages: list[dict[str, str]] = []
    for task in folder.iterdir():
        if not task.is_dir() or task.name.startswith("."):
            continue
        archive = _archive_for(task)
        if archive is None:
            continue
        packages.append(
            {
                "path": str(task),
                "label": _package_label(task, archive),
                "task_id": task.name,
            }
        )
    packages.sort(key=lambda item: Path(item["path"]).stat().st_mtime, reverse=True)
    return packages


def _archive_for(task: Path) -> Path | None:
    from article_writing.adapters.archive_catalog import deliverable_archive
    return deliverable_archive(task)



def _package_label(task: Path, archive: Path) -> str:
    """任务名 · 任务 ID · 打包日期，与分析模块的压缩包文件名一致。"""

    when = datetime.fromtimestamp(archive.stat().st_mtime).strftime("%Y-%m-%d")
    filename = archive.name
    if filename.endswith(ARCHIVE_SUFFIX) and "__" in filename:
        stem = filename[: -len(ARCHIVE_SUFFIX)]
        task_name, short_id = stem.rsplit("__", 1)
        task_name = task_name.strip()
        short_id = short_id.strip()
        if task_name and short_id:
            return " · ".join((task_name, short_id, when))
    from article_writing.adapters.materials import task_name
    return " · ".join((task_name(task), task.name[:8], when))
