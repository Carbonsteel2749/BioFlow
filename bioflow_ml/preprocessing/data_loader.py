"""用于读取表达矩阵和样本元数据的文件加载器。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_table(path: str | Path, *, sheet_name: int | str = 0) -> pd.DataFrame:
    """读取 CSV、TSV、TXT、XLS 或 XLSX 文件，返回原始表格。"""

    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(source)
    suffix = source.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(source)
    if suffix == ".tsv":
        return pd.read_csv(source, sep="\t")
    if suffix == ".txt":
        return pd.read_csv(source, sep=None, engine="python")
    if suffix in {".xls", ".xlsx"}:
        return pd.read_excel(source, sheet_name=sheet_name)
    raise ValueError(f"unsupported input format '{suffix}'; use CSV, TSV, TXT, XLS or XLSX")
