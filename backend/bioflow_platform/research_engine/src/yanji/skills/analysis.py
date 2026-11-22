"""Reviewed deterministic numerical operations; no model-generated computation."""

from __future__ import annotations

import warnings

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.spatial.distance import pdist, squareform
from scipy.stats import false_discovery_control, kruskal, mannwhitneyu
from skbio import DistanceMatrix
from skbio.stats.distance import permanova, permdisp
from skbio.stats.ordination import pcoa

from ..contracts import SkillResult
from ..data import DataError
from .context import clean_json
from .registry import CATALOG


def result(ctx, skill, summary, method, metrics=None, evidence=None, status="success"):
    return SkillResult(
        skill=skill,
        version=CATALOG[skill].version,
        status=status,
        summary=summary,
        method=method,
        metrics=clean_json(metrics or {}),
        evidence=evidence or [],
        artifacts=ctx.artifacts,
        warnings=ctx.warnings,
    ).model_dump()


def label(name):
    last = name.split("|")[-1]
    for prefix in ("s__", "g__", "f__", "p__", "o__", "c__", "k__", "d__", "t__"):
        last = last.replace(prefix, "")
    return last.replace("_", " ")


def alpha_values(matrix):
    values = matrix.div(matrix.sum(axis=1).replace(0, np.nan), axis=0)
    p = values.where(values > 0)
    shannon = -(p * np.log(p)).sum(axis=1, min_count=1)
    observed = (values > 0).sum(axis=1)
    return pd.DataFrame(
        {
            "Shannon": shannon,
            "Simpson 1-D": 1 - (values**2).sum(axis=1, min_count=1),
            "检出特征数": observed,
            "Pielou": shannon / np.log(observed.where(observed > 1)),
        }
    )


def data_check(ctx):
    rows = [
        {
            "文件": d["filename"],
            "类型": d["data_type"],
            "样本数": len(d["samples"]),
            "特征数": d["features"],
            "单位": d["unit"],
        }
        for d in ctx.datasets
    ]
    frame = pd.DataFrame(rows)
    ctx.table(frame, "data_check", "数据体检")
    for d in ctx.datasets:
        ctx.warnings.extend(d["warnings"])
    summary = f"已登记 {len(rows)} 份数据；分析使用已确认的数据版本。"
    return result(
        ctx,
        "data_check",
        summary,
        "逐文件校验、数据版本和样本匹配检查。",
        {"datasets": rows},
        [ctx.evidence("data", summary, {"files": len(rows)})],
    )


def metadata_overview(ctx):
    from ..data import load_frame

    dataset = ctx.find("metadata")
    frame = load_frame(dataset).copy()
    roles = {f["name"]: f["role"] for f in dataset.get("understanding", {}).get("fields", [])}
    excluded = ctx.snapshot.get("study", {}).get("excluded_samples", [])
    frame = frame.loc[~frame.index.isin(excluded)].replace(r"^\s*$", np.nan, regex=True)
    if frame.empty:
        raise DataError("没有可用的样本信息。")
    profile = []
    categories = []
    for column in frame:
        values = frame[column]
        numeric = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
        is_numeric = (
            roles.get(column) not in {"categorical", "ordinal", "identifier", "subject", "time"}
            and numeric.notna().sum() == values.notna().sum()
            and numeric.notna().any()
        )
        profile.append(
            {
                "field": column,
                "type": "numeric" if is_numeric else "categorical",
                "missing": int(values.isna().sum()),
                "missing_fraction": float(values.isna().mean()),
                "unique": int(values.nunique()),
                "median": float(numeric.median()) if is_numeric else None,
                "q25": float(numeric.quantile(0.25)) if is_numeric else None,
                "q75": float(numeric.quantile(0.75)) if is_numeric else None,
            }
        )
        if not is_numeric and 1 < values.nunique() <= 12:
            categories.extend(
                {"field": column, "category": str(k), "count": int(v)}
                for k, v in values.value_counts().items()
            )
    stats = pd.DataFrame(profile).set_index("field")
    ctx.table(stats, "metadata_profile", "样本字段与缺失率")
    if categories:
        ctx.table(pd.DataFrame(categories), "metadata_categories", "样本分类构成")
    missing = (
        stats.missing_fraction.sort_values(ascending=False).head(ctx.params.top_n).sort_values()
    )
    fig, ax = plt.subplots(figsize=(9, max(4, len(missing) * 0.3)))
    ax.barh(missing.index, missing.values, color=ctx.colors[0])
    ax.set_xlim(0, 1)
    ax.set_xlabel("缺失比例（空白字段，未填补）")
    ax.set_title("样本信息完整度")
    fig.tight_layout()
    ctx.chart(fig, "metadata_missing", "样本信息完整度")
    summary = f"已检查 {len(frame)} 个样本、{len(frame.columns)} 个信息字段；保留缺失值，未自动指定研究分组。"
    return result(
        ctx,
        "metadata_overview",
        summary,
        "逐字段计算非缺失数、唯一值数、数值中位数及四分位数，分类字段统计频数。",
        {"samples": len(frame), "fields": len(frame.columns)},
        [
            ctx.evidence(
                "metadata",
                summary,
                {"samples": len(frame), "fields": len(frame.columns)},
            )
        ],
    )


def composition(ctx, functional=False):
    matrix = ctx.matrix("functional" if functional else "taxonomy")
    groups = ctx.groups(matrix.index)
    n = ctx.params.top_n
    means = matrix.mean().sort_values(ascending=False)
    top = means.head(n).index
    table = pd.DataFrame(
        {
            "mean_abundance": means,
            "prevalence": (matrix > 0).mean().reindex(means.index),
        }
    )
    ctx.table(table, "feature_summary", "特征均值与检出率")
    display = matrix[top].copy()
    if not functional:
        display["Others"] = (matrix.sum(axis=1) - display.sum(axis=1)).clip(lower=0)
    if groups is not None:
        display = display.groupby(groups, sort=False).mean()
        order = [g for g in ctx.params.group_order if g in display.index]
        display = display.reindex(order + [g for g in display.index if g not in order])
    elif len(display) > 40:
        display = display.mean().to_frame("全部样本均值").T
        ctx.warnings.append("样本较多，组成图展示全部样本均值；原始结果表仍包含全部样本。")
    title = ctx.params.title or ("通路丰度概览" if functional else "微生物组成概览")
    colors = (ctx.colors * (len(display.columns) // len(ctx.colors) + 1))[: len(display.columns)]
    fig, ax = plt.subplots(figsize=(10, 5.7))
    if functional:
        vals = means.head(n).sort_values()
        ax.barh(range(len(vals)), vals.values, color=ctx.colors[0])
        ax.set_yticks(range(len(vals)), [label(x)[:65] for x in vals.index], fontsize=8)
        ax.set_xlabel("输入尺度的平均通路丰度")
        option = {
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 210, "right": 35, "bottom": 40},
            "xAxis": {"type": "value"},
            "yAxis": {"type": "category", "data": [label(x) for x in vals.index]},
            "series": [
                {
                    "type": "bar",
                    "data": vals.values.tolist(),
                    "itemStyle": {"color": ctx.colors[0]},
                }
            ],
        }
    else:
        display.rename(columns=label).plot.bar(stacked=True, color=colors, ax=ax, width=0.72)
        ax.set_ylabel("所选层级内归一化比例")
        ax.set_xlabel("分组" if groups is not None else "样本")
        ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left", frameon=False, fontsize=8)
        ax.tick_params(axis="x", rotation=30)
        option = {
            "color": colors,
            "tooltip": {"trigger": "axis"},
            "legend": {"type": "scroll", "bottom": 0},
            "grid": {"left": 55, "right": 25, "bottom": 90},
            "xAxis": {"type": "category", "data": display.index.tolist()},
            "yAxis": {"type": "value", "name": "比例"},
            "series": [
                {
                    "name": label(x),
                    "type": "bar",
                    "stack": "total",
                    "data": display[x].tolist(),
                }
                for x in display.columns
            ],
        }
    ax.set_title(title)
    fig.tight_layout()
    ctx.chart(fig, "composition", title, option)
    # Show per-sample variation without silently trimming samples from the data.
    heat = matrix[top].T
    if heat.shape[1] > 60:
        heat = (
            heat.T.groupby(groups, sort=False).mean().T
            if groups is not None
            else heat.mean(axis=1).to_frame("全部样本均值")
        )
    fig, ax = plt.subplots(figsize=(10, max(4, len(top) * 0.28)))
    image = ax.imshow(heat.values, aspect="auto", cmap="YlGnBu")
    ax.set_yticks(range(len(heat)), [label(x)[:55] for x in heat.index], fontsize=8)
    if heat.shape[1] <= 30:
        ax.set_xticks(range(heat.shape[1]), heat.columns, rotation=60, ha="right", fontsize=7)
    else:
        ax.set_xticks([])
    ax.set_title("优势特征丰度热图")
    fig.colorbar(image, ax=ax, label="丰度")
    fig.tight_layout()
    ctx.chart(fig, "heatmap", "优势特征丰度热图")
    summary = f"在 {len(matrix)} 个样本中分析 {matrix.shape[1]} 个特征，图中展示平均丰度前 {min(n, matrix.shape[1])} 项。"
    return result(
        ctx,
        "functional_overview" if functional else "composition",
        summary,
        "按指定分类层级内归一化计算组成及检出率。"
        if not functional
        else "使用群落总通路行，保留输入丰度尺度，不混入物种分层与特殊条目。",
        {
            "samples": len(matrix),
            "features": matrix.shape[1],
            "top": table.head(n).reset_index().to_dict("records"),
        },
        [
            ctx.evidence(
                "overview",
                summary,
                {"samples": len(matrix), "features": matrix.shape[1]},
            )
        ],
    )


def alpha_diversity(ctx):
    matrix = ctx.matrix()
    values = alpha_values(matrix)
    groups = ctx.groups(matrix.index)
    ctx.table(values, "alpha_diversity", "Alpha 多样性指标")
    fig, axes = plt.subplots(2, 2, figsize=(10, 7))
    rng = np.random.default_rng(ctx.params.seed)
    if groups is None:
        groups = pd.Series("全部样本", index=values.index)
    names = groups.unique().tolist()
    for ax, column in zip(axes.flat, values.columns):
        for j, group in enumerate(names):
            data = values.loc[groups == group, column].dropna()
            ax.scatter(
                j + rng.uniform(-0.14, 0.14, len(data)),
                data,
                s=18,
                alpha=0.7,
                color=ctx.colors[j % len(ctx.colors)],
            )
            if len(data):
                ax.plot([j - 0.2, j + 0.2], [data.median()] * 2, color="#263b38", lw=2)
        ax.set_xticks(range(len(names)), names, rotation=15)
        ax.set_title(column)
    fig.suptitle(ctx.params.title or "样本内多样性", fontsize=15)
    fig.tight_layout()
    ctx.chart(fig, "alpha", ctx.params.title or "样本内多样性")
    ctx.warnings.append(
        "检出特征数仅反映当前数据和过滤条件，不代表真实总丰富度；Pielou 在检出不足两个特征时为 NA。"
    )
    summary = (
        f"已计算 {len(values)} 个样本的四项 Alpha 多样性指标。"
        + "均值："
        + "；".join(f"{k}={v:.4g}" for k, v in values.mean().items())
        + "。"
    )
    return result(
        ctx,
        "alpha_diversity",
        summary,
        "所选层级内归一化；Shannon 使用自然对数，Simpson 为 1−Σp²，Pielou 为 Shannon/ln(S)。",
        {"samples": len(values), "means": values.mean().to_dict()},
        [
            ctx.evidence(
                "alpha",
                summary,
                {"samples": len(values), "means": values.mean().to_dict()},
            )
        ],
    )


def distance_matrix(matrix):
    if len(matrix) < 3:
        raise DataError("距离排序至少需要 3 个有效样本。")
    dm = DistanceMatrix(
        squareform(pdist(matrix.values, metric="braycurtis")), ids=matrix.index.tolist()
    )
    if np.allclose(dm.data, 0):
        raise DataError("所有样本组成完全相同，距离全为零，不能进行排序或群落检验。")
    return dm


def beta_diversity(ctx):
    matrix = ctx.matrix()
    dm = distance_matrix(matrix)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ordination = pcoa(dm, seed=ctx.params.seed)
    if any("negative eigenvalues" in str(w.message) for w in caught):
        ctx.warnings.append(
            "PCoA 存在负特征值，二维表示不能完全反映原始距离，请谨慎解释；原始警告保留在分析指标中。"
        )
    coords = ordination.samples.iloc[:, :2].copy()
    ctx.table(
        pd.DataFrame(dm.data, index=dm.ids, columns=dm.ids),
        "bray_curtis",
        "Bray–Curtis 距离矩阵",
    )
    ctx.table(coords, "pcoa_coordinates", "PCoA 坐标")
    groups = ctx.groups(matrix.index)
    if groups is None:
        groups = pd.Series("全部样本", index=matrix.index)
    fig, ax = plt.subplots(figsize=(8, 6))
    series = []
    for i, group in enumerate(groups.unique()):
        points = coords.loc[groups == group]
        ax.scatter(
            points.iloc[:, 0],
            points.iloc[:, 1],
            label=group,
            color=ctx.colors[i % len(ctx.colors)],
            s=42,
            alpha=0.8,
        )
        series.append(
            {
                "name": group,
                "type": "scatter",
                "symbolSize": 9,
                "data": [
                    [float(row.iloc[0]), float(row.iloc[1]), str(idx)]
                    for idx, row in points.iterrows()
                ],
            }
        )
    percentages = (ordination.proportion_explained.iloc[:2] * 100).tolist()
    ax.set_xlabel(f"PC1 ({percentages[0]:.1f}%)")
    ax.set_ylabel(f"PC2 ({percentages[1]:.1f}%)")
    title = ctx.params.title or "Bray–Curtis PCoA"
    ax.set_title(title)
    ax.legend(frameon=False)
    fig.tight_layout()
    ctx.chart(
        fig,
        "pcoa",
        title,
        {
            "color": ctx.colors,
            "tooltip": {"trigger": "item"},
            "legend": {"bottom": 0},
            "xAxis": {"type": "value", "name": f"PC1 ({percentages[0]:.1f}%)"},
            "yAxis": {"type": "value", "name": f"PC2 ({percentages[1]:.1f}%)"},
            "series": series,
        },
    )
    summary = f"已完成 {len(matrix)} 个样本的 Bray–Curtis 距离与 PCoA 排序。"
    ctx.warnings.append("排序图中的视觉分离不等于统计显著差异；需结合正式检验及研究设计。")
    return result(
        ctx,
        "beta_diversity",
        summary,
        "Bray–Curtis 距离，scikit-bio PCoA；记录负特征值警告。",
        {
            "samples": len(matrix),
            "variance_percent": percentages,
            "ordination_warnings": [str(w.message) for w in caught],
        },
        [ctx.evidence("beta", summary, {"variance_percent": percentages})],
    )


def grouped_matrix(ctx, kind="taxonomy", allow_multi=False):
    matrix = ctx.matrix(kind)
    groups = ctx.groups(matrix.index, required=True, allow_multi=allow_multi)
    matrix = matrix.loc[groups.index]
    chosen = [v for v in ctx.params.group_values if v]
    if not chosen and ctx.params.group_a and ctx.params.group_b:
        chosen = [ctx.params.group_a, ctx.params.group_b]
    names = chosen or groups.unique().tolist()
    return matrix, groups, names


def alpha_comparison(ctx):
    matrix, groups, names = grouped_matrix(ctx, allow_multi=True)
    values = alpha_values(matrix)
    rows = []
    multi = len(names) > 2
    for column in values:
        parts = [values.loc[groups == name, column].dropna() for name in names]
        medians = {f"median_{name}": part.median() for name, part in zip(names, parts)}
        sizes = {f"n_{name}": len(part) for name, part in zip(names, parts)}
        if any(len(part) < 3 for part in parts) or pd.concat(parts).nunique() < 2:
            rows.append(
                {
                    "metric": column,
                    "p": np.nan,
                    "status": "not_testable",
                    **medians,
                    **sizes,
                }
            )
            continue
        if multi:
            p = float(kruskal(*parts).pvalue)
            test_name = "Kruskal-Wallis"
        else:
            p = float(
                mannwhitneyu(parts[0], parts[1], alternative="two-sided", method="auto").pvalue
            )
            test_name = "Mann-Whitney"
        rows.append(
            {
                "metric": column,
                "p": p,
                "test": test_name,
                "status": "tested",
                **medians,
                **sizes,
            }
        )
    table = pd.DataFrame(rows).set_index("metric")
    table["q"] = np.nan
    mask = table.p.notna()
    if mask.any():
        table.loc[mask, "q"] = false_discovery_control(table.loc[mask, "p"].values, method="bh")
    if not multi and f"median_{names[0]}" in table and f"median_{names[1]}" in table:
        table["median_difference"] = table[f"median_{names[0]}"] - table[f"median_{names[1]}"]
    ctx.table(table, "alpha_comparison", "多样性组间检验")
    count = int((table.q < 0.05).sum())
    summary = f"比较 {'、'.join(names)}，{count} 项多样性指标通过 BH 校正阈值 q<0.05。"
    if (~mask).any():
        ctx.warnings.append("部分指标为常量或有效样本不足，标记为不可检验，未伪造 p 值。")
    return result(
        ctx,
        "alpha_comparison",
        summary,
        (
            "三组及以上用 Kruskal–Wallis；两组用双侧 Mann–Whitney U。"
            "本次有效 Alpha 指标统一 BH 校正。"
        ),
        {
            "groups": names,
            "significant": count,
            "results": table.reset_index().to_dict("records"),
        },
        [
            ctx.evidence(
                "alpha_test",
                summary,
                {"groups": names, "results": table.reset_index().to_dict("records")},
            )
        ],
    )


def community_comparison(ctx):
    matrix, groups, names = grouped_matrix(ctx, allow_multi=True)
    dm = distance_matrix(matrix)
    kwargs = {"permutations": ctx.params.permutations, "seed": ctx.params.seed}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pv = permanova(dm, groups, **kwargs)
        try:
            pdv = permdisp(dm, groups, **kwargs)
        except (ValueError, ZeroDivisionError, FloatingPointError):
            pdv = None
    ctx.warnings.extend(str(w.message) for w in caught)
    rows = [
        {
            "test": "PERMANOVA",
            "statistic": float(pv["test statistic"]),
            "p": float(pv["p-value"]),
        }
    ]
    if pdv is not None:
        rows.append(
            {
                "test": "PERMDISP",
                "statistic": float(pdv["test statistic"]),
                "p": float(pdv["p-value"]),
            }
        )
    if pdv is None or not np.isfinite(rows[-1]["p"]):
        ctx.warnings.append("PERMDISP 不可计算，无法排除组间离散度影响。")
    elif pdv["p-value"] < 0.05:
        ctx.warnings.append(
            "PERMDISP 提示组间离散度存在差异；PERMANOVA 结果不能单独解释为组中心差异。"
        )
    table = pd.DataFrame(rows).set_index("test")
    ctx.table(table, "community_tests", "群落整体检验")
    pvalue = rows[0]["p"]
    if not np.isfinite(rows[0]["statistic"]) or not np.isfinite(pvalue):
        raise DataError("群落检验为退化情况，无法获得有限统计量；请查看距离矩阵与分组。")
    summary = f"{'、'.join(names)} 的 PERMANOVA p={pvalue:.4g}；已同时检查离散度。"
    return result(
        ctx,
        "community_comparison",
        summary,
        "Bray–Curtis 距离上的 PERMANOVA 与 PERMDISP，支持两组或多组；" + str(kwargs),
        {"results": rows, "groups": names, **kwargs},
        [ctx.evidence("community", summary, {"results": rows, **kwargs})],
    )


def prepare_cohort(ctx):
    prepared = ctx.prepared
    if prepared is None:
        raise DataError("缺少队列准备规则。")
    ctx.table(prepared.membership, "cohort_membership", "逐样本分组、纳入与排除原因")
    ctx.table(prepared.frame, "prepared_metadata", "本次派生样本信息（原文件保留）")
    rows = []
    for item in prepared.operations:
        rows.append({**item, "counts": str(item.get("counts", {}))})
    ctx.table(pd.DataFrame(rows), "preparation_audit", "准备步骤与实际阈值")
    counts = (
        prepared.frame[prepared.group_column].value_counts().to_dict()
        if prepared.group_column
        else {}
    )
    summary = (
        f"按确认规则纳入 {len(prepared.frame)} 个样本；未纳入 {int((~prepared.membership.included).sum())} 个。"
        + "、".join(f"{g} {n} 个" for g, n in counts.items())
    )
    return result(
        ctx,
        "prepare_cohort",
        summary,
        "按方案依次筛选和派生；分位数采用线性插值，同值不拆分，缺失与中间区间按规则排除。",
        {
            "included": len(prepared.frame),
            "counts": counts,
            "operations": prepared.operations,
        },
        [ctx.evidence("cohort", summary, {"counts": counts, "operations": prepared.operations})],
    )


def group_feature_summary(ctx):
    matrix = ctx.matrix()
    groups = ctx.groups(
        matrix.index,
        required=True,
        allow_multi=True,
        min_per_group=1,
        inferential=False,
    )
    matrix = matrix.loc[groups.index]
    rows, tops = [], {}
    for i, name in enumerate(groups.unique()):
        values = matrix.loc[groups == name]
        table = pd.DataFrame(
            {
                "mean_abundance": values.mean(),
                "median_abundance": values.median(),
                "prevalence": (values > 0).mean(),
            }
        ).sort_values("mean_abundance", ascending=False)
        table["group"] = name
        table["rank"] = np.arange(1, len(table) + 1)
        rows.append(table)
        top = table.head(ctx.params.top_n)
        tops[str(name)] = top.reset_index().to_dict("records")
        view = top.sort_values("mean_abundance")
        fig, ax = plt.subplots(figsize=(9, max(4, len(view) * 0.32)))
        ax.barh(range(len(view)), view.mean_abundance, color=ctx.colors[i % len(ctx.colors)])
        ax.set_yticks(range(len(view)), [label(f)[:65] for f in view.index], fontsize=8)
        ax.set_xlabel("组内平均相对丰度")
        ax.set_title(f"{name} · Top {ctx.params.top_n}")
        fig.tight_layout()
        ctx.chart(fig, f"group_top_{i + 1}", f"{name} 优势特征")
    ctx.table(pd.concat(rows), "group_feature_ranks", "每组独立的完整特征排名")
    summary = f"分别计算 {len(tops)} 个组的丰度与检出率，各自展示最多 Top {ctx.params.top_n}（以实际特征数为限）；属于描述性排名。"
    return result(
        ctx,
        "group_feature_summary",
        summary,
        "在同一确认队列内逐组独立排序；优势丰度不等于统计显著富集。",
        {"groups": list(tops), "top_by_group": tops},
        [ctx.evidence("group_ranks", summary, {"groups": list(tops)})],
    )


def differential(ctx, functional=False):
    matrix, groups, names = grouped_matrix(
        ctx, "functional" if functional else "taxonomy", allow_multi=True
    )
    prevalence = (matrix > 0).mean()
    keep = prevalence >= ctx.params.prevalence
    matrix = matrix.loc[:, keep]
    if matrix.shape[1] == 0:
        raise DataError("没有特征通过检出率过滤，请检查数据或调整阈值。")
    multi = len(names) > 2
    rows = []
    for feature in matrix:
        parts = [matrix.loc[groups == name, feature] for name in names]
        medians = {f"median_{name}": part.median() for name, part in zip(names, parts)}
        means = {f"mean_{name}": part.mean() for name, part in zip(names, parts)}
        prevalences = {
            f"prevalence_{name}": float((part > 0).mean()) for name, part in zip(names, parts)
        }
        if matrix[feature].nunique() < 2:
            p = np.nan
            test_name = None
        elif multi:
            p = float(kruskal(*parts).pvalue)
            test_name = "Kruskal-Wallis"
        else:
            p = float(
                mannwhitneyu(parts[0], parts[1], alternative="two-sided", method="auto").pvalue
            )
            test_name = "Mann-Whitney"
        row = {
            "feature": feature,
            "label": label(feature),
            "prevalence": prevalence[feature],
            "p": p,
            "test": test_name,
            **medians,
            **means,
            **prevalences,
        }
        if not multi:
            row["median_difference"] = medians[f"median_{names[0]}"] - medians[f"median_{names[1]}"]
        rows.append(row)
    table = pd.DataFrame(rows).set_index("feature")
    table["q"] = np.nan
    mask = table.p.notna()
    if mask.any():
        table.loc[mask, "q"] = false_discovery_control(table.loc[mask, "p"].values, method="bh")
    table = table.sort_values(["q", "p"], na_position="last")
    enriched = {}
    if not multi:
        table["higher_group"] = np.where(
            table.median_difference > 0,
            names[0],
            np.where(table.median_difference < 0, names[1], "中位数相同"),
        )
        for i, name in enumerate(names):
            subset = table.loc[(table.q < 0.05) & table.higher_group.eq(name)]
            ctx.table(subset, f"enriched_group_{i + 1}", f"{name} 相对富集候选（q<0.05）")
            enriched[str(name)] = subset.reset_index().to_dict("records")
    ctx.table(table, "differential", "探索性差异筛查")
    view = table.loc[mask.reindex(table.index).fillna(False)].head(ctx.params.top_n)
    if len(view) and not multi:
        ordered = view.sort_values("median_difference")
        fig, ax = plt.subplots(figsize=(10, max(4, len(ordered) * 0.3)))
        ax.barh(
            range(len(ordered)),
            ordered.median_difference,
            color=[ctx.colors[0] if x > 0 else ctx.colors[3] for x in ordered.median_difference],
        )
        ax.set_yticks(range(len(ordered)), [label(x)[:60] for x in ordered.index], fontsize=8)
        ax.axvline(0, color="#a0aaa5", lw=0.8)
        ax.set_xlabel(f"中位数差：{names[0]} − {names[1]}")
        ax.set_title(ctx.params.title or f"{names[0]} 与 {names[1]} 相对丰度差异")
        fig.tight_layout()
        ctx.chart(fig, "differential", f"{names[0]} 与 {names[1]} 相对丰度差异")
        # Side-by-side abundance for the same top features.
        fig, ax = plt.subplots(figsize=(9, max(4, len(view) * 0.32)))
        y = np.arange(len(view))
        ax.barh(
            y - 0.18,
            view[f"median_{names[0]}"].values,
            height=0.35,
            color=ctx.colors[0],
            label=names[0],
        )
        ax.barh(
            y + 0.18,
            view[f"median_{names[1]}"].values,
            height=0.35,
            color=ctx.colors[1],
            label=names[1],
        )
        ax.set_yticks(y, [label(i)[:55] for i in view.index], fontsize=8)
        ax.set_xlabel("组内中位数相对丰度")
        ax.legend(frameon=False)
        ax.set_title(f"{names[0]} 与 {names[1]} 丰度对比")
        fig.tight_layout()
        ctx.chart(fig, "abundance_compare", f"{names[0]} 与 {names[1]} 丰度对比")
    elif len(view) and multi:
        fig, ax = plt.subplots(figsize=(9, max(4, len(view) * 0.35)))
        y = np.arange(len(view))
        width = 0.8 / len(names)
        for i, name in enumerate(names):
            ax.barh(
                y + (i - (len(names) - 1) / 2) * width,
                view[f"median_{name}"].values,
                height=width * 0.9,
                color=ctx.colors[i % len(ctx.colors)],
                label=name,
            )
        ax.set_yticks(y, [label(i)[:55] for i in view.index], fontsize=8)
        ax.set_xlabel("组内中位数相对丰度")
        ax.legend(frameon=False)
        ax.set_title("多组丰度对比（按 q 值排序）")
        fig.tight_layout()
        ctx.chart(fig, "abundance_compare", "多组丰度对比")
    count = int((table.q < 0.05).sum())
    tops = table.loc[table.q < 0.05].head(5)
    if len(tops):
        bits = []
        for feature, row in tops.iterrows():
            if multi:
                bits.append(f"{label(feature)}（q={row.q:.3g}）")
            else:
                if row.median_difference == 0:
                    bits.append(f"{label(feature)} 分布有差异但两组中位数相同（q={row.q:.3g}）")
                else:
                    direction = names[0] if row.median_difference > 0 else names[1]
                    bits.append(f"{label(feature)} 在 {direction} 更高（q={row.q:.3g}）")
        named = "；".join(bits)
        summary = f"过滤后 {len(table)} 个特征，{count} 个达到 q<0.05。例如：{named}。"
    else:
        summary = f"过滤后 {len(table)} 个特征，其中 {count} 个达到 q<0.05。"
    ctx.warnings.append(
        "探索性单变量相对丰度比较，未控制协变量或组成效应；不代表绝对丰度变化或因果关系。"
    )
    return result(
        ctx,
        "functional_differential" if functional else "differential",
        summary,
        (
            "检出率过滤后，两组用双侧 Mann–Whitney U，三组及以上用 Kruskal–Wallis；"
            "同次全部可检验特征 BH 校正；报告各组中位数与丰度对比图。"
        ),
        {
            "groups": names,
            "group_counts": {str(k): int(v) for k, v in groups.value_counts().items()},
            "enriched_by_group": enriched,
            "tested": int(mask.sum()),
            "significant": count,
            "top": table.head(15).reset_index().to_dict("records"),
        },
        [
            ctx.evidence(
                "differential",
                summary,
                {"tested": int(mask.sum()), "significant": count, "groups": names},
            )
        ],
    )


def pathway_contribution(ctx):
    matrix = ctx.matrix("functional", stratified=True)
    pathway = ctx.params.pathway
    if not pathway:
        # A deterministic descriptive default, recorded in output. It is not an inferred biological priority.
        means = matrix.mean().groupby(lambda x: x.split("|", 1)[0]).sum()
        pathway = means.idxmax()
        ctx.warnings.append("未指定通路：默认展示已观测分层丰度之和最大的通路，仅作为浏览入口。")
    matching = [
        x
        for x in matrix
        if x.split("|", 1)[0] == pathway or x.split("|", 1)[0].split(":")[0] == pathway
    ]
    if not matching:
        raise DataError("指定通路不存在，请从功能数据的通路列表选择。")
    values = matrix[matching].copy()
    values.columns = [x.split("|", 1)[1] for x in matching]
    means = values.mean().sort_values(ascending=False)
    total = float(means.sum())
    frame = pd.DataFrame(
        {
            "mean_stratified_abundance": means,
            "share_of_observed_contributions": means / total if total > 0 else np.nan,
        }
    )
    ctx.table(frame, "pathway_contributions", "通路物种贡献")
    show = means.head(ctx.params.top_n).sort_values()
    fig, ax = plt.subplots(figsize=(10, max(4, len(show) * 0.3)))
    ax.barh(range(len(show)), show.values, color=ctx.colors[0])
    ax.set_yticks(range(len(show)), [label(x)[:60] for x in show.index], fontsize=8)
    ax.set_xlabel("平均分层丰度（输入尺度）")
    ax.set_title(ctx.params.title or pathway[:85], fontsize=11)
    fig.tight_layout()
    ctx.chart(fig, "contribution", "通路物种贡献")
    ctx.warnings.extend(
        [
            "占比分母为本文件中该通路已观测分层贡献之和，不是群落总通路丰度。",
            "宏基因组 DNA 功能注释反映功能潜力，不等于表达量、活性或因果机制。",
        ]
    )
    summary = f"展示通路「{pathway}」的 {len(means)} 个物种分层贡献。"
    return result(
        ctx,
        "pathway_contribution",
        summary,
        "选择指定通路的物种分层行，计算跨样本平均丰度和已观测贡献内占比，不恢复群落总量。",
        {"pathway": pathway, "contributors": len(means), "samples": len(matrix)},
        [
            ctx.evidence(
                "contribution",
                summary,
                {"pathway": pathway, "contributors": len(means)},
            )
        ],
    )


from .association import association, distribution
from .contrast import core_taxa, sample_comparison, sample_profile
from .research import associations as research_associations
from .research import baseline_table, join_tables
from .research_plots import association_heatmap, association_network, effect_plot
from .tabular import (
    correlation,
    field_distribution,
    group_numeric_compare,
    sankey,
)
from .tabular import (
    overview as table_overview,
)

HANDLERS = {
    "join_tables": join_tables,
    "baseline_table": baseline_table,
    "partial_correlation": lambda ctx: research_associations(ctx, "partial_correlation"),
    "adjusted_regression": lambda ctx: research_associations(ctx, "adjusted_regression"),
    "association_heatmap": association_heatmap,
    "effect_plot": effect_plot,
    "association_network": association_network,
    "prepare_cohort": prepare_cohort,
    "group_feature_summary": group_feature_summary,
    "table_overview": table_overview,
    "field_distribution": field_distribution,
    "table_correlation": correlation,
    "group_numeric_compare": group_numeric_compare,
    "sankey": sankey,
    "taxa_association": lambda ctx: association(ctx, "taxa_association"),
    "diversity_association": lambda ctx: association(ctx, "diversity_association"),
    "functional_association": lambda ctx: association(ctx, "functional_association"),
    "variable_distribution": distribution,
    "data_check": data_check,
    "metadata_overview": metadata_overview,
    "composition": composition,
    "sample_profile": sample_profile,
    "sample_comparison": sample_comparison,
    "core_taxa": core_taxa,
    "alpha_diversity": alpha_diversity,
    "beta_diversity": beta_diversity,
    "alpha_comparison": alpha_comparison,
    "community_comparison": community_comparison,
    "differential": differential,
    "functional_overview": lambda ctx: composition(ctx, True),
    "functional_differential": lambda ctx: differential(ctx, True),
    "pathway_contribution": pathway_contribution,
}


def execute(ctx, skill):
    if skill not in HANDLERS:
        raise DataError("未知或不可直接执行的 Skill。")
    try:
        return HANDLERS[skill](ctx)
    except DataError as exc:
        # Statistically inapplicable steps remain explicit; other steps and report can finish.
        return result(ctx, skill, str(exc), "前置条件未满足，未执行统计推断。", status="skipped")
