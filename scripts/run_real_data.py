"""将 GEO series matrix (.txt.gz) 转换为标准 CSV，然后运行质控模块。

用法:
    python scripts/run_real_data.py
    python scripts/run_real_data.py --input data/other_file.txt.gz
"""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pandas as pd

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


def geo_matrix_to_csv(gz_path: Path) -> Path:
    """读取 GEO series matrix .txt.gz，跳过 ! 注释行，导出为 CSV。

    等价于用户提供的 R 代码:
        raw <- read.table(..., comment.char="!", quote="", fill=TRUE)
        raw[] <- lapply(raw, function(x) gsub('"', '', x))
        rownames(raw) <- raw[, 1]
        raw <- raw[, -1]
        write.csv(raw, "xxx_cleaned.csv", row.names=TRUE)
    """
    if not gz_path.exists():
        sys.exit(f"文件不存在: {gz_path}")

    print(f"读取: {gz_path}")

    # 用 pandas 直接读 .gz，跳过 ! 开头的注释行
    # GEO series matrix 格式: ! 开头是注释，之后是 ID_REF \t GSMxxx \t GSMxxx ...
    open_func = gzip.open if gz_path.suffix == ".gz" else open

    # 先提取数据部分（跳过 ! 行）
    data_lines: list[str] = []
    with open_func(gz_path, "rt", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not line.startswith("!"):
                data_lines.append(line)

    if not data_lines:
        sys.exit("文件中没有非注释行，请检查格式")

    # 用 pandas 解析（等价 R 的 read.table + comment.char="!"）
    from io import StringIO

    raw = pd.read_csv(
        StringIO("".join(data_lines)),
        sep="\t",
        header=0,
        quotechar='"',
    )

    # 清除所有列中残留的引号（等价 R 的 gsub('"', '', x)）
    for col in raw.columns:
        if raw[col].dtype == object:
            raw[col] = raw[col].str.replace('"', "", regex=False)

    # 第一列作为行名（基因 ID），然后删除该列
    id_col = raw.columns[0]
    raw.index = raw[id_col].astype(str)
    raw = raw.drop(columns=[id_col])

    # 只保留数值列（丢弃非数值的 annotation 列）
    numeric_raw = raw.apply(pd.to_numeric, errors="coerce")
    numeric_raw = numeric_raw.dropna(how="all", axis=1)
    numeric_raw = numeric_raw.dropna(how="all", axis=0)

    # 导出 CSV
    csv_path = gz_path.with_name(gz_path.stem.replace(".txt", "") + "_cleaned.csv")
    numeric_raw.to_csv(csv_path)
    print(f"导出: {csv_path}")
    print(f"维度: {numeric_raw.shape[0]} 基因 × {numeric_raw.shape[1]} 样本")

    return csv_path


def run_pipeline(csv_path: Path, data_kind: str | None = None) -> None:
    """对 CSV 运行 ExpressionMatrixPipeline 的 QC → 归一化 → PCA 全流程。

    如果 data_kind 为 None，则自动检测数据类型。
    """
    from bioflow.core.models import DatasetSpec, DatasetType
    from bioflow.modules.analysis.pipelines import ExpressionMatrixPipeline

    print(f"\n{'='*60}")
    print("运行质控模块")
    print(f"{'='*60}")

    dataset = DatasetSpec(
        dataset_type=DatasetType.expression_matrix,
        primary_path=str(csv_path),
        format="csv",
        confidence=0.95,
        metadata={"separator": ","},
    )

    pipeline = ExpressionMatrixPipeline()
    out_dir = csv_path.parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    # data_kind=None → 自动检测
    params: dict = {}
    if data_kind:
        params["data_kind"] = data_kind

    result = pipeline.run(
        dataset=dataset,
        params=params,
        out_dir=out_dir,
    )

    # 打印结果
    print(f"\n--- QC 报告 ---")
    for key in sorted(result.metrics):
        if key.startswith("qc_"):
            val = result.metrics[key]
            if isinstance(val, list) and len(val) > 10:
                print(f"  {key}: [{val[0]}, ...] (共 {len(val)} 项)")
            else:
                print(f"  {key}: {val}")

    print(f"\n--- 归一化 ---")
    norm_method = result.metrics.get("normalization_method")
    if "pre-normalized" in str(norm_method):
        print(f"  状态: 已跳过（数据已归一化）")
    else:
        print(f"  方法: {norm_method}")
    print(f"  矩阵维度: {result.metrics.get('norm_shape')}")
    print(f"  样本均值范围: {result.metrics.get('norm_sample_mean_range')}")
    print(f"  Top 变异基因: {result.metrics.get('norm_top_variable_features', [])[:5]}")

    print(f"\n--- PCA ---")
    ratios = result.metrics.get("pca_variance_ratio", [])
    print(f"  主成分数: {result.metrics.get('pca_n_components')}")
    if len(ratios) >= 2:
        print(f"  PC1: {ratios[0]:.2%}  PC2: {ratios[1]:.2%}  合计: {sum(ratios[:2]):.2%}")
    print(f"  全部方差解释率: {[f'{r:.4f}' for r in ratios[:5]]}...")

    print(f"\n--- 输出文件 ---")
    print(f"  归一化矩阵: {out_dir}/normalized/normalized_matrix.csv")
    print(f"  PCA 坐标:   {out_dir}/pca/pca_coordinates.csv")
    print(f"  汇总报告:   {out_dir}/summary/pipeline_summary.json")

    print(f"\n--- 备注 ---")
    for note in result.notes:
        print(f"  {note}")


def xlsx_to_csv(xlsx_path: Path) -> Path:
    """读取 .xlsx 表达矩阵，导出为标准 CSV。"""
    if not xlsx_path.exists():
        sys.exit(f"文件不存在: {xlsx_path}")

    print(f"读取: {xlsx_path}")

    raw = pd.read_excel(xlsx_path, sheet_name=0)

    # 第一列作为行名（基因 ID），然后删除该列
    id_col = raw.columns[0]
    raw.index = raw[id_col].astype(str)
    raw = raw.drop(columns=[id_col])

    # 只保留数值列（丢弃非数值的 annotation 列）
    numeric_raw = raw.apply(pd.to_numeric, errors="coerce")
    numeric_raw = numeric_raw.dropna(how="all", axis=1)
    numeric_raw = numeric_raw.dropna(how="all", axis=0)

    if numeric_raw.empty:
        sys.exit("Excel 文件中未找到数值列，请检查格式")

    csv_path = xlsx_path.with_suffix(".cleaned.csv")
    numeric_raw.to_csv(csv_path)
    print(f"导出: {csv_path}")
    print(f"维度: {numeric_raw.shape[0]} 基因 × {numeric_raw.shape[1]} 样本")

    return csv_path


def csv_direct_to_csv(csv_input: Path) -> Path:
    """直接读取已有 CSV 表达矩阵，清理后导出。"""
    if not csv_input.exists():
        sys.exit(f"文件不存在: {csv_input}")

    print(f"读取: {csv_input}")

    raw = pd.read_csv(csv_input, index_col=0)

    # 只保留数值列
    numeric_raw = raw.apply(pd.to_numeric, errors="coerce")
    numeric_raw = numeric_raw.dropna(how="all", axis=1)
    numeric_raw = numeric_raw.dropna(how="all", axis=0)

    if numeric_raw.empty:
        sys.exit("CSV 文件中未找到数值列，请检查格式")

    csv_path = csv_input.with_suffix(".cleaned.csv")
    numeric_raw.to_csv(csv_path)
    print(f"导出: {csv_path}")
    print(f"维度: {numeric_raw.shape[0]} 基因 × {numeric_raw.shape[1]} 样本")

    return csv_path


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="表达矩阵 → CSV → 质控")
    parser.add_argument(
        "--input", "-i",
        default="data/GSE182373_series_matrix.txt.gz",
        help="输入文件路径 (.txt.gz / .xlsx / .csv)",
    )
    parser.add_argument(
        "--data-kind", "-k",
        default=None,
        choices=["counts", "tpm", "fpkm", "microarray", "relative_abundance"],
        help="数据类型 (默认: 自动检测)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = PROJECT_ROOT / input_path

    suffix = input_path.suffix.lower()
    if suffix == ".xlsx":
        csv_path = xlsx_to_csv(input_path)
    elif suffix in (".csv",):
        csv_path = csv_direct_to_csv(input_path)
    else:
        csv_path = geo_matrix_to_csv(input_path)

    run_pipeline(csv_path, data_kind=args.data_kind)


if __name__ == "__main__":
    main()
