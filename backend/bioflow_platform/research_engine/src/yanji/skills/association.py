"""Exploratory continuous-variable skills: matched real observations only."""

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import false_discovery_control, spearmanr

from ..data import Blocker, DataError, load_frame


def numeric_variable(ctx, samples=None):
    dataset = ctx.find("metadata")
    frame = (
        ctx.prepared.frame if getattr(ctx, "prepared", None) is not None else load_frame(dataset)
    )
    column = ctx.params.variable
    if not column or column not in frame:
        raise Blocker("请明确选择当前样本信息表中的连续指标列。", "variable_missing")
    fields = {f["name"]: f for f in dataset.get("understanding", {}).get("fields", [])}
    if fields.get(column, {}).get("role") in {
        "identifier",
        "subject",
        "categorical",
        "ordinal",
        "time",
    }:
        raise Blocker(
            "所选字段已声明为非连续指标，请检查字段说明或选择其他指标。",
            "variable_unusable",
        )
    if samples is not None:
        missing = set(samples) - set(frame.index)
        if missing:
            raise Blocker(
                f"{len(missing)} 个丰度样本缺少信息对应，请先确认样本映射。",
                "samples_unmatched",
            )
        frame = frame.loc[samples]
    else:
        frame = frame.loc[
            ~frame.index.isin(ctx.snapshot.get("study", {}).get("excluded_samples", []))
        ]
    values = pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    if values.notna().sum() < 5 or values.nunique() < 3:
        raise Blocker(
            "连续指标至少需要 5 个有效观测和 3 个不同数值；分类变量不能替代连续指标。",
            "variable_unusable",
        )
    # Repeated observations cannot be treated as independent correlations.
    for name in frame:
        if (
            name.lower() in {"subject_id", "participant_id", "patient_id", "受试者编号"}
            or fields.get(name, {}).get("role") == "subject"
        ):
            ids = frame.loc[values.notna(), name].astype(str).str.strip()
            if ids[ids.ne("")].duplicated().any():
                raise Blocker(
                    "检测到重复受试者，当前关联方法不支持重复测量。",
                    "repeated_subjects",
                )
    return values


def distribution(ctx):
    from .analysis import result

    values = numeric_variable(ctx)
    valid = values.dropna()
    stats = valid.describe().to_dict()
    stats["missing"] = int(values.isna().sum())
    ctx.table(pd.DataFrame([stats]), "variable_statistics", f"{ctx.params.variable} 描述统计")
    ctx.table(values.to_frame(ctx.params.variable), "variable_values", "参与分析的指标记录")
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.hist(valid, bins="auto", color=ctx.colors[0], edgecolor="white")
    ax.set_xlabel(ctx.params.variable)
    ax.set_ylabel("样本数")
    ax.set_title(f"{ctx.params.variable} · {len(valid)} 个有效观测")
    ctx.chart(fig, "distribution", f"{ctx.params.variable} 分布")
    summary = f"{ctx.params.variable}：有效样本 {len(valid)}，缺失 {stats['missing']}，中位数 {valid.median():.4g}，四分位区间 {valid.quantile(0.25):.4g}–{valid.quantile(0.75):.4g}。"
    return result(
        ctx,
        "variable_distribution",
        summary,
        "不填补缺失，使用原始数值字段进行描述统计。",
        stats,
        [ctx.evidence("distribution", summary, stats)],
    )


def association(ctx, skill):
    from .analysis import alpha_values, label, result

    functional = skill == "functional_association"
    matrix = ctx.matrix("functional" if functional else "taxonomy")
    values = numeric_variable(ctx, matrix.index)
    valid = values.notna()
    matrix = matrix.loc[valid]
    values = values.loc[valid]
    if skill == "diversity_association":
        matrix = alpha_values(matrix)
    else:
        matrix = matrix.loc[:, (matrix > 0).mean() >= ctx.params.prevalence]
    rows = []
    for feature in matrix:
        x = matrix[feature]
        keep = x.notna() & np.isfinite(x)
        n = int(keep.sum())
        if n < 5 or x[keep].nunique() < 2 or values[keep].nunique() < 3:
            rows.append(
                {
                    "feature": feature,
                    "n": n,
                    "rho": np.nan,
                    "p": np.nan,
                    "reason": "常量或有效观测不足",
                }
            )
            continue
        rho, p = spearmanr(values[keep], x[keep])
        rows.append({"feature": feature, "n": n, "rho": float(rho), "p": float(p), "reason": ""})
    if not rows:
        raise DataError("检出率筛选后没有可用特征。")
    table = pd.DataFrame(rows).set_index("feature")
    table["q"] = np.nan
    tested = table.p.notna()
    if not tested.any():
        raise DataError("没有可计算的非常量特征。")
    table.loc[tested, "q"] = false_discovery_control(table.loc[tested, "p"].values, method="bh")
    table = table.sort_values(["q", "p"], na_position="last")
    table["predictor"] = table.index.astype(str)
    table["outcome"] = ctx.params.variable
    table["effect"] = table.rho
    table["metric"] = "spearman_rho"
    table["status"] = np.where(table.p.notna(), "estimated", "not_estimable")
    ctx.table(table, "associations", f"{ctx.params.variable} 关联检验完整结果")
    ctx.table(
        values.to_frame(ctx.params.variable).join(matrix),
        "matched_observations",
        "实际参与计算的匹配观测",
    )
    show = table.loc[table.q.notna()].head(ctx.params.top_n)
    fig, ax = plt.subplots(figsize=(9, max(4, len(show) * 0.32)))
    ax.barh(
        [label(x)[:65] for x in show.index][::-1],
        show.rho.values[::-1],
        color=ctx.colors[0],
    )
    ax.set_xlim(-1, 1)
    ax.set_xlabel("Spearman rho（按 BH q 值排序）")
    ax.set_title(f"{ctx.params.variable} 与特征关联")
    ctx.chart(fig, "association_effects", f"{ctx.params.variable} 关联效应")
    best = show.index[0]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.scatter(values, matrix[best], alpha=0.65, color=ctx.colors[0])
    ax.set_xlabel(ctx.params.variable)
    ax.set_ylabel(label(best)[:65])
    ax.set_title(f"rho={show.iloc[0].rho:.3g}, q={show.iloc[0].q:.3g}")
    option = {
        "tooltip": {"trigger": "item"},
        "xAxis": {"type": "value", "name": ctx.params.variable},
        "yAxis": {"type": "value", "name": label(best)[:35]},
        "series": [
            {
                "type": "scatter",
                "data": np.column_stack([values, matrix[best]]).tolist(),
            }
        ],
    }
    ctx.chart(fig, "association_scatter", "排序首位特征与连续指标的实际观测", option)
    significant = int((table.q < 0.05).sum())
    summary = (
        f"指标「{ctx.params.variable}」：{len(values)} 个匹配有效样本，检验 {int(tested.sum())} 个特征，"
        f"BH q<0.05 的特征 {significant} 个。排序首位为「{best}」（rho={show.iloc[0].rho:.4g}，q={show.iloc[0].q:.4g}）。"
    )
    ctx.warnings += [
        f"指标缺失或非数值观测排除 {int((~valid).sum())} 个，未填零。",
        "探索性双侧 Spearman 渐近检验；小样本解释需谨慎，未控制协变量。",
        "相对丰度关联受组成效应影响，不代表绝对数量变化或因果关系。",
        "散点图展示排序首位特征，即使未达到显著性；完整检验结果含不适用特征。",
    ]
    metrics = {
        "variable": ctx.params.variable,
        "samples": len(values),
        "tested": int(tested.sum()),
        "significant": significant,
        "top": table.loc[tested].head(5).reset_index().to_dict("records"),
    }
    return result(
        ctx,
        skill,
        summary,
        "以已确认样本编号精确对应；缺失成对排除；检出率过滤；双侧 Spearman，所有可检验特征统一 BH 校正。",
        metrics,
        [ctx.evidence("associations", summary, metrics)],
    )
