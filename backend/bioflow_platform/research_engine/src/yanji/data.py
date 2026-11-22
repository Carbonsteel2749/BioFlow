"""Bounded tabular import and immutable dataset versions."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import db
from .config import MAX_CELLS, MAX_UPLOAD, runtime_root
from .contracts import ImportOptions


class DataError(ValueError):
    pass


class Blocker(DataError):
    """A failed precondition that carries what is needed to resolve it.

    The planner turns each code into a question the user can answer in place, so
    a check never reaches the user as a sentence with nothing to act on.
    """

    def __init__(self, message, code, **data):
        super().__init__(message)
        self.code, self.data = code, data


def sample_key(name):
    match = re.match(r"^(F\d+)", name)
    return match.group(1) if match else re.sub(r"(?:_combined|_Abundance)$", "", name)


def project_dir(project_id):
    db.get(project_id, "project")
    path = runtime_root() / "projects" / project_id
    path.mkdir(parents=True, exist_ok=True)
    return path


SAMPLE_COLUMNS = {
    "sample_id",
    "sampleid",
    "sample",
    "样本编号",
    "样本名",
    "f号",
    "样本id",
    "编号",
}


def workbook_preview(raw: bytes):
    from openpyxl import load_workbook

    try:
        book = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        try:
            return [
                {
                    "name": s.title,
                    "columns": [str(c or "").strip() for c in next(s.values, ())],
                    "rows": max(0, (s.max_row or 1) - 1),
                }
                for s in book.worksheets
            ]
        finally:
            book.close()
    except Exception as exc:
        raise DataError("无法读取 Excel 工作簿，请确认文件为未加密的 .xlsx。") from exc


def _excel_table(raw: bytes, options: ImportOptions):
    from openpyxl import load_workbook

    try:
        book = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:
        raise DataError("无法读取 Excel 工作簿，请确认文件为未加密的 .xlsx。") from exc
    try:
        if options.sheet and options.sheet not in book.sheetnames:
            raise DataError("指定的工作表不存在。")
        sheet = book[options.sheet] if options.sheet else book.worksheets[0]
        rows = sheet.values
        for _ in range(options.header_row - 1):
            next(rows, ())
        header = [str(c).strip() if c is not None else "" for c in next(rows, ())]
        # Ignore genuinely empty trailing columns; never silently rename duplicate fields.
        while header and not header[-1]:
            header.pop()
        if len(header) < 2 or any(not h for h in header) or len(set(header)) != len(header):
            raise DataError("Excel 首行应为不重复的字段名，至少包含样本编号和一个信息字段。")
        records = []
        for row in rows:
            if not any(v is not None for v in row):
                continue
            records.append(["" if v is None else str(v) for v in row[: len(header)]])
            if len(records) * len(header) > MAX_CELLS:
                raise DataError("工作表超过 1,000 万个单元上限，请先筛选或拆分。")
        if not records:
            raise DataError("工作表只有表头，没有数据。")
        frame = pd.DataFrame(records, columns=header)
        if options.kind == "table" or (
            options.kind == "auto" and not any(h.lower() in SAMPLE_COLUMNS for h in header)
        ):
            frame.index = pd.Index([str(i + 1) for i in range(len(frame))], name="行号")
            return frame, sheet.title
        identifier = options.id_column or next(
            (h for h in header if h.lower() in SAMPLE_COLUMNS), header[0]
        )
        if identifier not in frame:
            raise DataError("指定的标识列不存在。")
        frame[identifier] = frame[identifier].str.strip()
        if frame[identifier].eq("").any() or frame[identifier].duplicated().any():
            raise DataError("样本标识列有空值或重复值，请选择正确的样本编号列。")
        return frame.set_index(identifier), sheet.title
    finally:
        book.close()


def _table(raw: bytes, options: ImportOptions) -> pd.DataFrame:
    text = None
    for encoding in [options.encoding] if options.encoding != "auto" else ["utf-8-sig", "gb18030"]:
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise DataError("无法读取文字编码，请在读取设置中选择编码。")
    lines = [line for line in text.splitlines()[options.header_row - 1 :] if line.strip()]
    # Preserve '# Pathway' headers. MetaPhlAn version comments are not data.
    lines = [
        line
        for line in lines
        if not (line.startswith("#") and "\t" not in line and "," not in line)
    ]
    if not lines:
        raise DataError("文件为空或没有可读取的表头。")
    if options.delimiter != "auto":
        delimiter = {"tab": "\t", "comma": ",", "semicolon": ";"}[options.delimiter]
    else:
        delimiter = (
            "\t"
            if "\t" in lines[0]
            else (";" if lines[0].count(";") > lines[0].count(",") else ",")
        )
    reader = csv.reader(lines, delimiter=delimiter)
    header = next(reader)
    header = [x.strip() for x in header]
    if len(header) < 2:
        raise DataError("至少需要两列：特征或样本标识，以及对应的数据。")
    if len(set(header)) != len(header) or any(not x for x in header):
        raise DataError("表头包含重复或空列名，请修改后重新上传。")
    rows = []
    for line_num, row in enumerate(reader, 2):
        if len(row) != len(header):
            raise DataError(f"第 {line_num} 行有 {len(row)} 列，表头有 {len(header)} 列。")
        rows.append(row)
        if len(rows) * (len(header) - 1) > MAX_CELLS:
            raise DataError("数据超过 1,000 万个单元上限，请先筛选或拆分。")
    if not rows:
        raise DataError("只有表头，没有数据。")
    frame = pd.DataFrame(rows, columns=header)
    unknown_table = (
        options.kind == "auto"
        and options.unit == "auto"
        and not any(h.lower() in SAMPLE_COLUMNS for h in header)
        and not any(str(x).startswith(("k__", "d__")) for x in frame.iloc[:30, 0])
        and "pathway" not in header[0].lower()
        and not any("|g__" in str(x) or str(x).startswith("PWY") for x in frame.iloc[:30, 0])
    )
    if options.kind == "table" or unknown_table:
        frame.index = pd.Index([str(i + 1) for i in range(len(frame))], name="行号")
        return frame
    if {"sample_id", "feature_id", "value"}.issubset(header):
        if options.kind not in {"taxonomy", "functional"}:
            raise DataError(
                "BioFlow 长表请明确选择物种丰度或功能通路类型，不能从数值猜测特征含义。"
            )
        if options.transpose or options.id_column:
            raise DataError("标准长表已包含样本与特征标识，不支持转置或另选标识列。")
        for column in ["sample_id", "feature_id"]:
            frame[column] = frame[column].str.strip()
            if frame[column].eq("").any():
                raise DataError("长表的样本或特征标识为空。")
        if frame.duplicated(["sample_id", "feature_id"]).any():
            raise DataError("长表存在重复的样本与特征组合，不能自动求和。")
        units = frame["unit"].str.strip().unique().tolist() if "unit" in frame else []
        if units and (
            len(units) != 1 or units[0] not in {"fraction", "percent", "counts", "other"}
        ):
            raise DataError(
                "长表 unit 必须为统一的 fraction、percent、counts 或 other；覆盖度不能作为丰度导入。"
            )
        if units and options.unit != "auto" and units[0] != options.unit:
            raise DataError("导入单位与长表 unit 列冲突。")
        samples = frame.sample_id.unique().tolist()
        features = frame.feature_id.unique().tolist()
        if len(samples) * len(features) > MAX_CELLS:
            raise DataError("长表展开后超过 1,000 万个单元上限。")
        if len(frame) != len(samples) * len(features):
            raise DataError("长表缺少样本与特征组合；请明确补齐记录，系统不会默认填零。")
        matrix = frame.pivot(index="feature_id", columns="sample_id", values="value").reindex(
            index=features, columns=samples
        )
        matrix.attrs["canonical_unit"] = units[0] if units else None
        matrix.attrs["canonical_long"] = True
        return matrix
    inferred_id = header[0]
    if options.kind in {"auto", "metadata"}:
        inferred_id = next((h for h in header if h.lower() in SAMPLE_COLUMNS), header[0])
    id_column = options.id_column or inferred_id
    if id_column not in header:
        raise DataError("指定的标识列不存在。")
    frame[id_column] = frame[id_column].str.strip()
    if (frame[id_column] == "").any() or frame[id_column].duplicated().any():
        duplicate = frame.loc[frame[id_column].duplicated(False), id_column].head(5).tolist()
        raise DataError(f"标识列有空值或重复值 {duplicate}，不能自动合并。")
    return frame.set_index(id_column)


def inspect(raw: bytes, filename: str, options: ImportOptions):
    sheet = None
    if Path(filename).suffix.lower() == ".json":
        try:
            rows = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError):
            raise DataError("JSON 文件无法读取，应为一组对象记录。") from None
        if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
            raise DataError('JSON 需要是对象数组，例如 [{"编号":"001","年龄":8}]。')
        if any(isinstance(v, (dict, list)) for row in rows for v in row.values()):
            raise DataError("嵌套 JSON 不能直接分析，请先展开为记录表。")
        keys = list(dict.fromkeys(k for r in rows for k in r))
        if len(keys) * len(rows) > MAX_CELLS:
            raise DataError("JSON 展开后超过数据单元上限。")
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=keys, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
        return inspect(
            output.getvalue().encode(),
            "records.tsv",
            options.model_copy(
                update={"header_row": 1, "encoding": "utf-8-sig", "delimiter": "tab"}
            ),
        )
    if Path(filename).suffix.lower() == ".xlsx":
        frame, sheet = _excel_table(raw, options)
    else:
        frame = _table(raw, options)
    kind = options.kind
    source = "standard"
    ids = frame.index.astype(str)
    if kind == "auto":
        if frame.index.name == "行号":
            kind = "table"
        elif frame.index.name.lower() in SAMPLE_COLUMNS:
            kind = "metadata"
        elif any(x.startswith(("k__", "d__")) for x in ids[:30]):
            kind, source = "taxonomy", "metaphlan"
        elif "pathway" in frame.index.name.lower() or "pathabundance" in filename.lower():
            kind, source = "functional", "humann"
        elif options.unit != "auto":
            kind = "taxonomy"
        else:
            # Preserve an unknown table instead of assigning biological meaning.
            kind = "table"
            options = options.model_copy(update={"kind": "table"})
            if sheet:
                frame, sheet = _excel_table(raw, options)
            else:
                frame = _table(raw, options)
    else:
        if kind == "taxonomy" and any(x.startswith(("k__", "d__")) for x in ids[:30]):
            source = "metaphlan"
        if kind == "functional" and (
            "pathway" in frame.index.name.lower() or "pathabundance" in filename
        ):
            source = "humann"
    if options.transpose:
        if kind == "metadata":
            raise DataError("样本信息表不支持转置，请以每行一个样本的格式上传。")
        frame = frame.T
    transforms = [{"operation": "import", **options.model_dump(), "source": source}]
    warnings = []
    if sheet:
        transforms[0]["sheet"] = sheet
        warnings.append(f"已读取工作表「{sheet}」，公式使用文件保存的缓存值；空缺字段保持为空。")
    unit = (
        frame.attrs.get("canonical_unit")
        if options.unit == "auto" and frame.attrs.get("canonical_unit")
        else options.unit
    )
    if frame.attrs.get("canonical_long"):
        transforms.append(
            {
                "operation": "canonical_long_to_matrix",
                "missing_policy": "reject",
                "duplicate_policy": "reject",
            }
        )
    if kind in {"metadata", "table"}:
        return frame, {
            "data_type": kind,
            "identifier_column": frame.index.name if kind == "metadata" else None,
            "source": source,
            "unit": kind,
            "samples": frame.index.tolist() if kind == "metadata" else [],
            "features": len(frame.columns),
            "columns": frame.columns.tolist(),
            "warnings": warnings,
            "transformations": transforms,
            "summary": {"rows": len(frame), "columns": len(frame.columns)},
        }
    numeric = frame.apply(pd.to_numeric, errors="coerce")
    missing = numeric.isna()
    if missing.any().any():
        i, j = np.argwhere(missing.to_numpy())[0]
        raise DataError(
            f"特征「{frame.index[i]}」样本「{frame.columns[j]}」存在缺失或非数值内容；请修复，系统不会填零。"
        )
    values = numeric.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise DataError("丰度必须是有限非负数；发现负数、无穷大或非法数值。")
    if numeric.index.duplicated().any() or numeric.columns.duplicated().any():
        raise DataError("转置后产生重复标识。")
    if unit == "auto":
        if source == "metaphlan":
            unit = "percent"
        elif source == "humann" and "relab" in filename.lower():
            unit = "fraction"
        else:
            raise DataError(
                "无法确定丰度单位，请选择比例、百分比、计数或其他标准化丰度后重新导入。"
            )
    if unit == "percent":
        if values.max() > 100.0001:
            raise DataError("百分比数值超过 100，请检查单位。")
        numeric = numeric / 100
        transforms.append({"operation": "percent_to_fraction", "factor": 0.01})
        unit = "fraction"
    elif unit == "fraction" and values.max() > 1.00001:
        raise DataError(
            "比例数值超过 1，请检查单位或 HUMAnN 归一化方式；必要时选择其他标准化丰度。"
        )
    elif unit == "counts" and not np.allclose(values, np.round(values), rtol=0, atol=1e-8):
        raise DataError("计数类型包含非整数，请确认单位。")
    empty = numeric.columns[numeric.sum(axis=0) == 0].tolist()
    if empty:
        warnings.append(f"{len(empty)} 个样本的全部丰度为零，相关分析将明确标记不可计算。")
    rank_counts = {}
    if kind == "taxonomy" and source == "metaphlan":
        for name in numeric.index:
            rank = taxonomy_rank(name)
            rank_counts[rank] = rank_counts.get(rank, 0) + 1
    stratified = sum("|" in x for x in numeric.index) if kind == "functional" else 0
    totals = (
        sum("|" not in x and not special(x) for x in numeric.index) if kind == "functional" else 0
    )
    if kind == "functional" and stratified and not totals:
        warnings.append("仅含物种分层通路，支持贡献分析；不能通过求和恢复群落总通路丰度。")
    if kind == "taxonomy" and source == "standard":
        warnings.append("标准矩阵不含分类层级；按用户提供的单一特征层级分析，不进行属级合并。")
    return numeric, {
        "data_type": kind,
        "identifier_column": frame.index.name,
        "source": source,
        "unit": unit,
        "samples": numeric.columns.tolist(),
        "features": len(numeric),
        "columns": [],
        "warnings": warnings,
        "transformations": transforms,
        "summary": {
            "rows": len(numeric),
            "columns": len(numeric.columns),
            "zero_samples": empty,
            "rank_counts": rank_counts,
            "stratified_rows": stratified,
            "total_pathways": totals,
            "zero_fraction": float((values == 0).mean()),
        },
    }


def taxonomy_rank(name):
    prefix = name.split("|")[-1].split("__")[0]
    return {
        "s": "species",
        "g": "genus",
        "f": "family",
        "o": "order",
        "c": "class",
        "p": "phylum",
        "k": "kingdom",
        "d": "domain",
        "t": "strain",
    }.get(prefix, "unknown")


def special(name):
    return name.split("|")[0].split(":")[0] in {
        "UNMAPPED",
        "UNINTEGRATED",
        "READS_UNMAPPED",
    }


def write_frame(directory, frame):
    directory.mkdir(parents=True, exist_ok=True)
    frame.to_json(
        directory / "matrix.json.gz",
        orient="split",
        compression="gzip",
        double_precision=15,
    )


def load_frame(dataset):
    path = runtime_root() / dataset["storage"] / "matrix.json.gz"
    if (
        dataset.get("content_sha256")
        and hashlib.sha256(path.read_bytes()).hexdigest() != dataset["content_sha256"]
    ):
        raise DataError("标准化数据校验失败，请重新导入；系统不会继续使用被改变的数据文件。")
    return pd.read_json(path, orient="split", compression="gzip", dtype=False, convert_axes=False)


def active(project_id):
    project = db.get(project_id, "project")
    return [db.get(i, "dataset") for i in project.get("active_datasets", {}).values()]


def materials(project_id):
    """All current project files, independent of the legacy one-per-type inputs."""
    project = db.get(project_id, "project")
    ids = project.get("material_ids")
    if ids is None:
        ids = list(project.get("active_datasets", {}).values())
    return [db.get(i, "dataset") for i in dict.fromkeys(ids)]


def selected_inputs(project_id, ids=None):
    if ids is None:
        return active(project_id)
    index = {d["id"]: d for d in materials(project_id)}
    if not ids or len(set(ids)) != len(ids) or any(i not in index for i in ids):
        raise DataError("本次分析的文件已变化，请重新选择项目文件。")
    return [index[i] for i in ids]


def import_bytes(project_id, filename, raw, options):
    if len(raw) > MAX_UPLOAD:
        raise DataError("单文件超过 100 MB 上限。")
    frame, info = inspect(raw, filename, options)
    source_id = db.uid()
    directory = project_dir(project_id) / "datasets" / source_id
    write_frame(directory, frame)
    (directory / "source.txt").write_bytes(raw)
    row = db.create(
        "dataset",
        project_id,
        filename=Path(filename).name,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        content_sha256=hashlib.sha256((directory / "matrix.json.gz").read_bytes()).hexdigest(),
        storage=str(directory.relative_to(runtime_root())),
        source_id=source_id,
        revision=1,
        **info,
    )
    _activate(project_id, row)
    return row


def _slot(dataset):
    if dataset["data_type"] == "table":
        return "table:" + dataset.get("source_id", dataset["id"])
    if dataset["data_type"] != "functional":
        return dataset["data_type"]
    return (
        "functional_stratified"
        if dataset["summary"]["stratified_rows"] and not dataset["summary"]["total_pathways"]
        else "functional"
    )


def _activate(project_id, dataset):
    p = db.get(project_id, "project")
    ids = p.get("material_ids", list(p.get("active_datasets", {}).values()))
    ids = [i for i in ids if i != dataset.get("parent_version")]
    if dataset["id"] not in ids:
        ids.append(dataset["id"])
    db.update(
        project_id,
        material_ids=ids,
        active_datasets={**p.get("active_datasets", {}), _slot(dataset): dataset["id"]},
    )


def mapping_suggestions(project_id, dataset):
    if dataset["data_type"] == "table":
        return {
            "reference": None,
            "mapping": {},
            "unmatched": [],
            "needs_confirmation": False,
        }
    candidates = active(project_id)
    reference = next((d for d in candidates if d["data_type"] == "taxonomy"), None)
    if reference is None:
        reference = next((d for d in candidates if d["data_type"] == "metadata"), None)
    if reference is None:
        reference = next(iter(candidates), None)
    if reference is None or reference["id"] == dataset["id"]:
        return {
            "reference": None,
            "mapping": {s: s for s in dataset["samples"]},
            "unmatched": [],
            "needs_confirmation": False,
        }

    key = sample_key
    lookup = {}
    for sample in reference["samples"]:
        lookup.setdefault(key(sample), []).append(sample)
    mapping = {}
    unmatched = []
    for sample in dataset["samples"]:
        if sample in reference["samples"]:
            mapping[sample] = sample
        elif len(lookup.get(key(sample), [])) == 1:
            mapping[sample] = lookup[key(sample)][0]
        else:
            unmatched.append(sample)
            mapping[sample] = sample
    return {
        "reference": reference["id"],
        "mapping": mapping,
        "unmatched": unmatched,
        "needs_confirmation": bool(
            (unmatched and dataset["data_type"] != "metadata")
            or any(k != v for k, v in mapping.items())
        ),
    }


def remap(dataset_id, mapping, excluded):
    old = db.get(dataset_id, "dataset")
    if old["data_type"] == "table":
        raise DataError("普通表格请通过数据类型设置选择样本编号。")
    if not set(mapping).issubset(old["samples"]) or not set(excluded).issubset(old["samples"]):
        raise DataError("映射或排除列表含未知样本。")
    remaining = [s for s in old["samples"] if s not in excluded]
    if not remaining:
        raise DataError("至少保留一个样本。")
    names = [mapping.get(s, s).strip() for s in remaining]
    if any(not s for s in names) or len(set(names)) != len(names):
        raise DataError("目标样本名为空或不唯一，请修复映射。")
    frame = load_frame(old)
    if old["data_type"] == "metadata":
        frame = frame.loc[remaining]
        frame.index = names
    else:
        frame = frame[remaining]
        frame.columns = names
    directory = project_dir(old["project_id"]) / "datasets" / db.uid()
    write_frame(directory, frame)
    info = {
        k: v
        for k, v in old.items()
        if k not in {"id", "created_at", "updated_at", "kind", "project_id", "storage"}
    }
    summary = dict(old["summary"])
    summary["rows"] = len(frame)
    summary["columns"] = len(frame.columns)
    if old["data_type"] != "metadata":
        summary["zero_samples"] = frame.columns[frame.sum(axis=0) == 0].tolist()
    info["summary"] = summary
    info["content_sha256"] = hashlib.sha256((directory / "matrix.json.gz").read_bytes()).hexdigest()
    info.update(
        samples=names,
        revision=old["revision"] + 1,
        transformations=old["transformations"]
        + [{"operation": "sample_mapping", "mapping": mapping, "excluded": excluded}],
        parent_version=old["id"],
    )
    row = db.create(
        "dataset",
        old["project_id"],
        storage=str(directory.relative_to(runtime_root())),
        **info,
    )
    _activate(old["project_id"], row)
    return row


def preview(dataset):
    frame = load_frame(dataset)
    if dataset["data_type"] == "table":
        return {
            "columns": frame.columns[:50].tolist(),
            "rows": [
                [None if pd.isna(x) else x for x in row]
                for row in frame.iloc[:12, :50].values.tolist()
            ],
        }
    return {
        "columns": [
            str(
                dataset.get("identifier_column")
                or ("样本编号" if dataset["data_type"] == "metadata" else "特征")
            )
        ]
        + frame.columns[:50].tolist(),
        "rows": [
            [str(i)] + [None if pd.isna(x) else x for x in row]
            for i, row in frame.iloc[:12, :50].iterrows()
        ],
    }
