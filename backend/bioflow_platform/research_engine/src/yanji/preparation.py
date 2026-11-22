"""Composable, versioned cohort recipes. No eval, arbitrary Python, or source mutation.

Every consumer recomputes the same recipe from immutable input versions. Quantiles
are calculated on eligible matched samples, after exclusions and preceding filters.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .data import DataError, load_frame

VERSION = "1.0.0"


@dataclass
class Prepared:
    frame: pd.DataFrame
    membership: pd.DataFrame
    group_column: str | None
    operations: list[dict]


def prepare(datasets, operations, study=None):
    if not operations:
        return None
    ids = {o.dataset_id for o in operations}
    if len(ids) != 1:
        raise DataError("同一队列的准备操作须使用同一份样本表；跨表计算需要先明确连接关系。")
    dataset = next((d for d in datasets if d["id"] in ids), None)
    if not dataset or dataset["data_type"] not in {"metadata", "table"}:
        raise DataError("数据准备必须绑定本次所选的一份样本表或普通表。")
    original = load_frame(dataset).copy()
    if not original.index.is_unique:
        raise DataError("样本编号重复，不能建立可复现的分析队列。")
    excluded = set((study or {}).get("excluded_samples") or [])
    eligible = ~original.index.isin(excluded)
    # An abundance analysis derives thresholds on samples actually represented in
    # every selected matrix; unmatched records are reported, never imputed.
    matrices = [set(d["samples"]) for d in datasets if d["data_type"] in {"taxonomy", "functional"}]
    if matrices:
        matched = set.intersection(*matrices)
        eligible &= original.index.isin(matched)
    frame = original.loc[eligible].copy()
    membership = pd.DataFrame(index=original.index)
    membership["status"] = np.where(original.index.isin(excluded), "用户排除", "未匹配丰度样本")
    membership.loc[frame.index, "status"] = "纳入"
    group_column, audit = None, []
    for op in operations:
        if op.column not in frame:
            raise DataError(f"准备操作引用了不存在的列：{op.column}")
        if op.op != "filter" and (not op.output or op.output in frame):
            raise DataError("派生列必须有新名称，不能覆盖原始列或已有派生列。")
        values = frame[op.column]
        numeric = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
        info = {
            "op": op.op,
            "column": op.column,
            "output": op.output,
            "before": len(frame),
        }
        if op.op == "filter":
            if op.operator == "not_missing":
                keep = values.notna() & values.astype(str).str.strip().ne("")
            elif op.operator in {"in", "not_in", "eq", "ne"}:
                if not op.values or (op.operator in {"eq", "ne"} and len(op.values) != 1):
                    raise DataError("筛选条件缺少明确的值。")
                keep = values.astype(str).isin(op.values)
                if op.operator in {"ne", "not_in"}:
                    keep = ~keep
            else:
                if len(op.values) != 1:
                    raise DataError("数值筛选需要一个阈值。")
                try:
                    threshold = float(op.values[0])
                except ValueError:
                    raise DataError("数值筛选阈值无效。") from None
                if not np.isfinite(threshold):
                    raise DataError("数值筛选阈值必须有限。")
                keep = getattr(numeric, op.operator)(threshold) & numeric.notna()
            membership.loc[frame.index[~keep], "status"] = (
                f"筛选排除：{op.column} {op.operator} {op.values}"
            )
            frame = frame.loc[keep].copy()
        elif op.op in {"quantile_group", "threshold_group"}:
            if op.low_label == op.high_label:
                raise DataError("高低组名称不能相同。")
            if op.op == "quantile_group":
                if not 0 < op.lower < op.upper < 1:
                    raise DataError("分位点须满足 0 < 下分位点 < 上分位点 < 1。")
                low, high = numeric.quantile([op.lower, op.upper], interpolation="linear")
                if not np.isfinite(low) or not np.isfinite(high) or low >= high:
                    raise DataError(
                        "上下分位阈值重合或无有效数值，不能建立互斥高低组；请调整规则。"
                    )
                low_mask, high_mask = numeric.le(low), numeric.ge(high)
                info.update(lower_quantile=op.lower, upper_quantile=op.upper)
            else:
                low = high = op.threshold
                if not np.isfinite(low):
                    raise DataError("分组阈值必须是有限数值。")
                low_mask, high_mask = numeric.le(low), numeric.gt(high)
            membership.loc[frame.index[numeric.isna()], "status"] = f"缺失或非数值：{op.column}"
            membership.loc[frame.index[numeric.notna() & ~(low_mask | high_mask)], "status"] = (
                "中间区间排除"
            )
            frame[op.output] = np.where(
                low_mask, op.low_label, np.where(high_mask, op.high_label, "")
            )
            membership.loc[frame.index, op.output] = frame[op.output]
            membership.loc[frame.index, op.column] = values
            frame = frame.loc[low_mask | high_mask].copy()
            group_column = op.output
            info.update(
                low_threshold=float(low),
                high_threshold=float(high),
                boundary="低组 ≤ 下阈值；高组 ≥ 上阈值，同值不拆分"
                if op.op == "quantile_group"
                else "低组 ≤ 阈值；高组 > 阈值",
                counts={str(k): int(v) for k, v in frame[op.output].value_counts().items()},
            )
            if len(info["counts"]) != 2:
                raise DataError("分组规则未生成两个非空组。")
        elif op.op == "recode":
            if not op.mapping:
                raise DataError("重编码必须提供映射。")
            mapped = values.astype(str).map(op.mapping)
            membership.loc[frame.index[mapped.isna()], "status"] = "重编码未覆盖的值"
            frame[op.output] = mapped
            frame = frame.loc[mapped.notna()].copy()
            group_column = op.output
            membership.loc[frame.index, op.output] = frame[op.output]
            info["counts"] = frame[op.output].value_counts().to_dict()
        elif op.op == "arithmetic":
            if op.operand_column:
                if op.operand_column not in frame:
                    raise DataError("计算引用了不存在的第二列。")
                rhs = pd.to_numeric(frame[op.operand_column], errors="coerce")
            else:
                rhs = op.operand
            if op.arithmetic == "log1p":
                with np.errstate(invalid="ignore", divide="ignore"):
                    computed = np.log1p(numeric)
            else:
                with np.errstate(invalid="ignore", divide="ignore"):
                    computed = getattr(
                        numeric,
                        {
                            "add": "add",
                            "subtract": "sub",
                            "multiply": "mul",
                            "divide": "div",
                        }[op.arithmetic],
                    )(rhs)
            frame[op.output] = computed.replace([np.inf, -np.inf], np.nan)
            info["invalid"] = int(frame[op.output].isna().sum())
        if frame.empty:
            raise DataError("按当前准备规则筛选后，没有可分析的样本。")
        info["after"] = len(frame)
        audit.append(info)
    membership.loc[frame.index, "status"] = "纳入"
    if matrices:
        missing_metadata = sorted(set.union(*matrices) - set(original.index))
        if missing_metadata:
            membership = membership.reindex(list(membership.index) + missing_metadata)
            membership.loc[missing_metadata, "status"] = "缺少样本信息"
    membership["included"] = membership.index.isin(frame.index)
    return Prepared(frame, membership, group_column, audit)


def compile_cohorts(plan):
    """Resolve explicit upstream references to immutable recipes in each step."""
    by_id = {}
    for step in plan.steps:
        if step.cohort_from:
            source = by_id.get(step.cohort_from)
            if not source or source.skill != "prepare_cohort" or not source.parameters.preparation:
                raise DataError("cohort_from 必须指向前面的队列准备步骤。")
            if step.cohort_from not in step.depends_on:
                raise DataError("使用派生队列的步骤必须依赖该准备步骤。")
            if (
                step.parameters.preparation
                and step.parameters.preparation != source.parameters.preparation
            ):
                raise DataError("下游队列规则与引用的上游步骤不一致。")
            step.parameters.preparation = source.parameters.preparation
        if step.skill != "prepare_cohort" and step.parameters.preparation and not step.cohort_from:
            raise DataError("分析步骤的准备规则必须通过 cohort_from 引用，不能脱离准备步骤。")
        if step.skill == "prepare_cohort" and not step.parameters.preparation:
            raise DataError("队列准备步骤缺少可执行的准备规则。")
        by_id[step.id] = step


def preview(plan, datasets, study):
    rows = []
    for step in plan.steps:
        if step.skill == "prepare_cohort":
            prepared = prepare(datasets, step.parameters.preparation, study)
            rows.append(
                {
                    "step_id": step.id,
                    "operations": prepared.operations,
                    "included": len(prepared.frame),
                    "excluded": int((~prepared.membership.included).sum()),
                    "exclusion_reasons": prepared.membership.loc[
                        ~prepared.membership.included, "status"
                    ]
                    .value_counts()
                    .to_dict(),
                }
            )
    return rows


def assert_intent(plan, objective):
    """Fail closed on a known semantic substitution; model can repair its plan."""
    import re

    if any(s.skill == "custom_analysis" for s in plan.steps):
        return  # The program reviewer checks the complete objective against actual computation.
    if plan.questions or plan.clarifications:
        return
    quantiles = bool(
        re.search(r"四分位|分位数|分位点|百分位|quartile|quantile|percentile", objective, re.I)
    )
    grouped = bool(re.search(r"分组|高.{0,8}组|低.{0,8}组|group", objective, re.I))
    if quantiles and grouped:
        recipes = [
            s
            for s in plan.steps
            if s.skill == "prepare_cohort"
            and any(o.op == "quantile_group" for o in s.parameters.preparation)
        ]
        if not recipes:
            raise DataError(
                "用户要求按分位数新建分组，必须准备派生队列；不能改成连续相关或要求用户手工增加分组列。"
            )
        if re.search(r"富集|差异|enrich|differential", objective, re.I):
            if not any(
                s.skill in {"differential", "functional_differential"}
                and s.cohort_from in {r.id for r in recipes}
                for s in plan.steps
            ):
                raise DataError("分位组富集目标必须在派生队列上做组间差异，相关分析不能代替。")
        if re.search(r"上四分位|下四分位", objective):
            if any(
                (o.lower, o.upper) != (0.25, 0.75)
                for r in recipes
                for o in r.parameters.preparation
                if o.op == "quantile_group"
            ):
                raise DataError("上下四分位组要求 P25/P75，不能替换为其他分位点。")

    top = re.search(r"top\s*(\d+)", objective, re.I)
    if quantiles and grouped and top and re.search(r"每.{0,3}组|各组|分别", objective):
        matching = [
            s
            for s in plan.steps
            if s.skill == "group_feature_summary" and s.cohort_from in {r.id for r in recipes}
        ]
        if not matching or any(s.parameters.top_n != int(top.group(1)) for s in matching):
            raise DataError(
                "用户要求各组分别计算 Top N，须在派生队列上调用 group_feature_summary，不能用全样本排名代替。"
            )
