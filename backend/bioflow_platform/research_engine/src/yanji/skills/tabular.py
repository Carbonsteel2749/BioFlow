"""User-selected table tools share the same validated planner and worker."""

import re

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import PathPatch, Rectangle
from matplotlib.path import Path
from scipy.stats import kruskal, mannwhitneyu, spearmanr

from ..data import Blocker, DataError, load_frame


def _distribution_columns(params):
    columns = [name for name in params.fields if name]
    if params.variable and params.variable not in columns:
        columns.append(params.variable)
    return columns


def validate_tabular(datasets, skill, params, study=None):
    d = next(
        (
            d
            for d in datasets
            if d["id"] == params.dataset_id and d["data_type"] in {"metadata", "table"}
        ),
        None,
    )
    if not d:
        raise Blocker("请选择当前研究的一份样本表或普通表格。", "table_missing")
    from ..preparation import prepare

    prepared = prepare(datasets, params.preparation, study)
    if prepared is not None and params.preparation[0].dataset_id != d["id"]:
        raise DataError("表格分析必须使用该准备队列所绑定的表。")
    frame = prepared.frame if prepared is not None else load_frame(d)
    if skill == "table_overview":
        return d, frame
    if skill == "field_distribution":
        columns = _distribution_columns(params)
        if (
            not columns
            or len(columns) > 8
            or len(columns) != len(set(columns))
            or any(name not in frame for name in columns)
        ):
            raise Blocker(
                "请选择 1 到 8 个存在且不重复的字段来查看分布。",
                "fields_invalid",
                dataset_id=d["id"],
            )
        return d, frame
    if skill == "group_numeric_compare":
        group_by = params.group_by
        variable = params.variable
        if not group_by or group_by not in frame:
            raise Blocker(
                "请选择用于分组的分类字段。",
                "group_by_missing",
                dataset_id=d["id"],
                params=["group_by"],
            )
        if not variable or variable not in frame or variable == group_by:
            raise Blocker(
                "请选择要按组比较的数值字段。",
                "variable_missing",
                dataset_id=d["id"],
                params=["variable"],
            )
        fields = {f["name"]: f for f in d.get("understanding", {}).get("fields", [])}
        group_role = fields.get(group_by, {}).get("role")
        if group_role in {"identifier", "subject", "time"}:
            raise Blocker(
                "编号、受试者或时间字段不能作为分组列。",
                "group_by_invalid",
                dataset_id=d["id"],
                params=["group_by"],
            )
        value_role = fields.get(variable, {}).get("role")
        if value_role in {"identifier", "subject", "categorical", "time"}:
            raise Blocker(
                "类别或编号字段不能作为组间比较的数值指标。",
                "not_continuous",
                dataset_id=d["id"],
                params=["variable"],
            )
        if value_role == "ordinal" and _ordinal_codes(frame[variable], fields[variable]) is None:
            raise Blocker(
                f"「{variable}」是有序等级，需要先确认至少三个等级的顺序。",
                "needs_order",
                dataset_id=d["id"],
                field=variable,
            )
        subject_cols = [
            c
            for c in frame
            if c.lower() in {"subject_id", "participant_id", "patient_id", "受试者编号"}
            or fields.get(c, {}).get("role") == "subject"
        ]
        for c in subject_cols:
            ids = frame[c].astype(str).str.strip()
            if ids[ids.ne("")].duplicated().any():
                raise Blocker(
                    "存在重复受试者，本工具不支持配对或重复测量组间检验。",
                    "repeated_subjects",
                )
        return d, frame
    names = ["x", "y"] if skill == "table_correlation" else ["source", "target", "weight"]
    cols = [getattr(params, name) for name in names]
    unusable, seen = [], set()
    for name, c in zip(names, cols):
        if not c or c not in frame or c in seen:
            unusable.append(name)
        seen.add(c)
    if unusable:
        raise Blocker(
            "请选择存在且不同的分析字段。",
            "columns_invalid",
            dataset_id=d["id"],
            params=unusable,
        )
    fields = {f["name"]: f for f in d.get("understanding", {}).get("fields", [])}
    if skill == "table_correlation":
        discrete = [
            name
            for name, c in zip(names, cols)
            if fields.get(c, {}).get("role") in {"identifier", "subject", "categorical", "time"}
        ]
        if discrete:
            raise Blocker(
                "编号、类别或时间字段不能直接作为连续数值进行相关分析。",
                "not_continuous",
                dataset_id=d["id"],
                params=discrete,
            )
        for c in cols:
            if fields.get(c, {}).get("role") == "ordinal" and (
                _ordinal_codes(frame[c], fields[c]) is None
            ):
                raise Blocker(
                    f"「{c}」是有序等级，需要先确认至少三个等级的顺序，才能参与相关分析。",
                    "needs_order",
                    dataset_id=d["id"],
                    field=c,
                )
        subject_cols = [
            c
            for c in frame
            if c.lower() in {"subject_id", "participant_id", "patient_id", "受试者编号"}
            or fields.get(c, {}).get("role") == "subject"
        ]
        for c in subject_cols:
            ids = frame[c].astype(str).str.strip()
            if ids[ids.ne("")].duplicated().any():
                raise Blocker(
                    "存在重复受试者，本工具不支持重复测量相关分析。",
                    "repeated_subjects",
                )
    else:
        weights = pd.to_numeric(frame[params.weight], errors="coerce")
        if weights.isna().any() or not np.isfinite(weights).all() or (weights < 0).any():
            raise Blocker(
                "桑基图权重必须是有限非负数；负相关需先明确其展示方式。",
                "columns_unusable",
                dataset_id=d["id"],
                params=["weight"],
            )
        if (
            frame[[params.source, params.target]]
            .astype(str)
            .apply(lambda s: s.str.strip().eq(""))
            .any()
            .any()
        ):
            raise DataError("桑基图的起点或终点存在空值。")
    return d, frame


def output(ctx, skill, summary, method, metrics):
    from .analysis import result

    return result(ctx, skill, summary, method, metrics, [ctx.evidence(skill, summary, metrics)])


def overview(ctx):
    d, frame = validate_tabular(
        ctx.datasets, "table_overview", ctx.params, ctx.snapshot.get("study", {})
    )
    from ..understanding import describe

    roles = {f["name"]: f["role"] for f in describe(d)["fields"]}
    rows = []
    for column in frame:
        text = frame[column].astype(str).str.strip().replace("", np.nan)
        numeric = pd.to_numeric(text, errors="coerce").replace([np.inf, -np.inf], np.nan)
        is_numeric = (
            roles.get(column) not in {"identifier", "subject", "categorical", "ordinal", "time"}
            and numeric.notna().sum() == text.notna().sum()
            and numeric.notna().any()
        )
        rows.append(
            {
                "field": column,
                "missing": int(text.isna().sum()),
                "unique": int(text.nunique()),
                "type": "numeric" if is_numeric else "other",
                "median": float(numeric.median()) if is_numeric else None,
                "q25": float(numeric.quantile(0.25)) if is_numeric else None,
                "q75": float(numeric.quantile(0.75)) if is_numeric else None,
            }
        )
    stats = pd.DataFrame(rows).set_index("field")
    ctx.table(stats, "table_profile", "列与数值概况")
    missing = stats.missing.sort_values(ascending=False).head(25).sort_values()
    fig, ax = plt.subplots(figsize=(8, max(4, len(missing) * 0.3)))
    ax.barh(missing.index, missing.values, color=ctx.colors[0])
    ax.set(xlabel="缺失记录数", title=ctx.params.title or "表格完整度")
    fig.tight_layout()
    ctx.chart(fig, "table_profile", "表格完整度")
    return output(
        ctx,
        "table_overview",
        f"{d['filename']}：{len(frame)} 行、{len(frame.columns)} 列，逐列保留缺失与实际数值概况。",
        "原始列的缺失、唯一值与适用的数值分位数；不插补、不推测分组、不进行假设检验。",
        {"rows": len(frame), "fields": len(frame.columns)},
    )


def _level_order(encoding, present):
    if not encoding or encoding == "未指定顺序":
        return None
    parts = [
        item.strip()
        for item in re.split(r"\s*(?:>|＞|→|->|、|,|，|;|；|\n)\s*", encoding)
        if item.strip()
    ]
    ordered = [item for item in parts if item in present]
    return ordered or None


def _ordinal_codes(series, info):
    """Rank codes 1..k following the confirmed level order; unlisted values stay missing."""
    text = series.astype(str).str.strip()
    order = _level_order(info.get("encoding") or "", set(text))
    if not order or len(order) < 3:
        return None
    return text.map({level: rank for rank, level in enumerate(order, start=1)})


def _present(series):
    text = series.astype(str).str.strip()
    return text[~text.isin(["", "nan", "None", "NaN", "none"])]


def field_distribution(ctx):
    d, frame = validate_tabular(
        ctx.datasets, "field_distribution", ctx.params, ctx.snapshot.get("study", {})
    )
    if d["data_type"] == "metadata":
        frame = frame.loc[
            ~frame.index.isin(ctx.snapshot.get("study", {}).get("excluded_samples", []))
        ]
    from ..understanding import describe

    meanings = {field["name"]: field for field in describe(d)["fields"]}
    summaries, counts = [], []
    drawn = 0
    for name in _distribution_columns(ctx.params):
        info = meanings.get(name, {})
        label = (
            f"{info['meaning']}（{name}）"
            if info.get("meaning") and info["meaning"] != name
            else name
        )
        unit = (info.get("unit") or "").strip()
        text = _present(frame[name])
        numeric = pd.to_numeric(text, errors="coerce").replace([np.inf, -np.inf], np.nan)
        kind = (
            "category"
            if info.get("role") in {"categorical", "ordinal", "time"}
            else "numeric"
            if info.get("role") == "numeric"
            or (
                info.get("role") not in {"categorical", "ordinal"}
                and numeric.notna().sum() == len(text)
                and numeric.notna().sum() >= 1
                and numeric.nunique(dropna=True) >= 1
            )
            else "category"
        )
        if kind == "numeric":
            valid = numeric.dropna()
            if valid.empty:
                ctx.warnings.append(f"「{name}」没有可用数值，已跳过。")
                continue
            stats = {
                "field": name,
                "label": label,
                "kind": "numeric",
                "n": int(valid.shape[0]),
                "missing": int(len(frame) - len(text)) + int(numeric.isna().sum()),
                "median": float(valid.median()),
                "q25": float(valid.quantile(0.25)),
                "q75": float(valid.quantile(0.75)),
                "unit": unit,
            }
            summaries.append(stats)
            bins = min(12, max(4, int(np.sqrt(len(valid)))))
            histogram, edges = np.histogram(valid, bins=bins)
            tick_labels = [f"{edges[i]:.3g}–{edges[i + 1]:.3g}" for i in range(len(histogram))]
            fig, ax = plt.subplots(figsize=(7.2, 3.6))
            ax.hist(valid, bins=edges, color=ctx.colors[0], edgecolor="white")
            xlabel = f"{label}（{unit}）" if unit and unit != "未说明" else label
            ax.set(xlabel=xlabel, ylabel="频数", title=f"{label} 分布")
            fig.tight_layout()
            option = {
                "tooltip": {"trigger": "axis"},
                "xAxis": {"type": "category", "name": xlabel, "data": tick_labels},
                "yAxis": {"type": "value", "name": "频数"},
                "series": [
                    {
                        "type": "bar",
                        "data": [int(value) for value in histogram],
                        "itemStyle": {"color": ctx.colors[0]},
                    }
                ],
            }
            ctx.chart(fig, f"distribution_{drawn}", f"{label} 分布", option)
            if unit == "未说明":
                ctx.warnings.append(f"{label}未提供单位，按原始数值描述。")
        else:
            if text.empty:
                ctx.warnings.append(f"「{name}」没有可用记录，已跳过。")
                continue
            observed = text.value_counts()
            ordered = _level_order(info.get("encoding") or "", set(observed.index))
            if ordered:
                levels = ordered + [value for value in observed.index if value not in ordered]
            else:
                levels = list(observed.index)
                if info.get("encoding") and info.get("encoding") != "未指定顺序":
                    ctx.warnings.append(f"{label}的编码没能对应到实际取值，图中按频数排列。")
            if len(levels) > 12:
                head, tail = levels[:11], levels[11:]
                levels = head + ["其余"]
                level_counts = [int(observed[value]) for value in head] + [
                    int(observed[tail].sum())
                ]
                ctx.warnings.append(f"{label}的取值较多，图中仅展示前 11 项，其余合并。")
            else:
                level_counts = [int(observed.get(value, 0)) for value in levels]
            total = int(sum(level_counts))
            summaries.append(
                {
                    "field": name,
                    "label": label,
                    "kind": "ordinal" if info.get("role") == "ordinal" else "category",
                    "n": int(text.shape[0]),
                    "missing": int(len(frame) - len(text)),
                    "levels": levels,
                    "counts": level_counts,
                    "unit": unit,
                }
            )
            for level, count in zip(levels, level_counts):
                counts.append(
                    {
                        "field": name,
                        "level": level,
                        "count": count,
                        "percent": float(count / total) if total else 0,
                    }
                )
            fig_height = max(3.2, 0.42 * len(levels) + 1.2)
            fig, ax = plt.subplots(figsize=(7.6, fig_height))
            ax.barh(levels[::-1], level_counts[::-1], color=ctx.colors[1])
            ax.set(xlabel="频数", title=f"{label} 分布")
            fig.tight_layout()
            option = {
                "tooltip": {"trigger": "axis"},
                "xAxis": {
                    "type": "category",
                    "data": levels,
                    "axisLabel": {
                        "interval": 0,
                        "rotate": 25 if len(levels) > 6 else 0,
                    },
                },
                "yAxis": {"type": "value", "name": "频数"},
                "series": [
                    {
                        "type": "bar",
                        "data": level_counts,
                        "itemStyle": {"color": ctx.colors[1]},
                    }
                ],
            }
            ctx.chart(fig, f"distribution_{drawn}", f"{label} 分布", option)
        drawn += 1
    if not summaries:
        raise DataError("所选字段都没有可用记录。")
    ctx.table(pd.DataFrame(summaries).set_index("field"), "distribution_summary", "分布摘要")
    if counts:
        ctx.table(pd.DataFrame(counts), "distribution_counts", "分布频数")
    bits = []
    for item in summaries:
        if item["kind"] == "numeric":
            bits.append(
                f"{item['label']}：有效 {item['n']}，缺失 {item['missing']}，中位数 {item['median']:.4g}"
            )
        else:
            preview = "、".join(
                f"{level} {count}" for level, count in zip(item["levels"], item["counts"])
            )
            bits.append(f"{item['label']}：有效 {item['n']}，{preview}")
    summary = "。".join(bits) + "。"
    return output(
        ctx,
        "field_distribution",
        summary,
        "逐字段描述当前表。数值字段不填补缺失，给出分位数和直方图；类别字段给出频数，已确认且能对上的等级顺序按其排列。不进行组间检验。",
        {"rows": len(frame), "fields": summaries},
    )


def correlation(ctx):
    d, frame = validate_tabular(
        ctx.datasets, "table_correlation", ctx.params, ctx.snapshot.get("study", {})
    )
    frame = (
        frame.loc[~frame.index.isin(ctx.snapshot.get("study", {}).get("excluded_samples", []))]
        if d["data_type"] == "metadata"
        else frame
    )
    fields = {f["name"]: f for f in d.get("understanding", {}).get("fields", [])}
    columns, direction = {}, []
    for name in (ctx.params.x, ctx.params.y):
        info = fields.get(name, {})
        if info.get("role") == "ordinal":
            columns[name] = _ordinal_codes(frame[name], info)
            order = _level_order(
                info.get("encoding") or "", set(frame[name].astype(str).str.strip())
            )
            direction.append(
                f"「{name}」的序号从「{order[0]}」（1）到「{order[-1]}」（{len(order)}）"
            )
            ctx.warnings.append(
                f"「{name}」按已确认的等级顺序编码为 1 起的序号（1 为顺序中的第一项）后参与秩相关：{info.get('encoding')}。"
            )
        else:
            columns[name] = pd.to_numeric(frame[name], errors="coerce")
    pair = pd.DataFrame(columns).replace([np.inf, -np.inf], np.nan)
    valid = pair.dropna()
    if len(valid) < 5 or min(valid.nunique()) < 3:
        raise DataError("相关分析至少需要五个有效观测，每个指标至少三个不同数值。")
    rho, p = spearmanr(valid.iloc[:, 0], valid.iloc[:, 1])
    ctx.table(valid, "observations", "参与计算的观测")
    ctx.table(
        pd.DataFrame(
            [
                {
                    "x": ctx.params.x,
                    "y": ctx.params.y,
                    "n": len(valid),
                    "rho": rho,
                    "p": p,
                }
            ]
        ),
        "correlation",
        "相关分析结果",
    )
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.scatter(valid.iloc[:, 0], valid.iloc[:, 1], color=ctx.colors[0], alpha=0.7)
    ax.set(
        xlabel=ctx.params.x,
        ylabel=ctx.params.y,
        title=ctx.params.title or f"Spearman ρ = {rho:.3f}, p = {p:.3g}",
    )
    option = {
        "tooltip": {"trigger": "item"},
        "xAxis": {"type": "value", "name": ctx.params.x, "scale": True},
        "yAxis": {"type": "value", "name": ctx.params.y, "scale": True},
        "series": [
            {
                "type": "scatter",
                "symbolSize": 9,
                "data": valid.values.tolist(),
                "itemStyle": {"color": ctx.colors[0]},
            }
        ],
    }
    ctx.chart(fig, "correlation", "两字段相关", option)
    ctx.warnings.append(
        f"排除 {len(pair) - len(valid)} 个缺失或非数值观测。单次两字段探索性相关，渐近 p 值，未控制协变量，不代表因果。"
    )
    summary = (
        f"{ctx.params.x}与{ctx.params.y}：有效观测 {len(valid)}，Spearman ρ={rho:.3f}，p={p:.3g}。"
    )
    if direction:
        summary += f"{'；'.join(direction)}，ρ 的正负按这个方向解读。"
    return output(
        ctx,
        "table_correlation",
        summary,
        "完整观测双侧 Spearman 相关；单个预先指定检验，未作多重检验校正。",
        {"samples": len(valid), "rho": float(rho), "p": float(p)},
    )


def sankey(ctx):
    _, frame = validate_tabular(ctx.datasets, "sankey", ctx.params, ctx.snapshot.get("study", {}))
    p = ctx.params
    edges = frame[[p.source, p.target, p.weight]].copy()
    edges.columns = ["source", "target", "value"]
    edges["source"] = edges["source"].astype(str).str.strip()
    edges["target"] = edges["target"].astype(str).str.strip()
    edges["value"] = pd.to_numeric(edges["value"])
    ctx.table(edges, "edges", "原始关系边表")
    edges = edges.groupby(["source", "target"], sort=False, as_index=False)["value"].sum()
    edges = edges[edges.value > 0]
    if edges.empty:
        raise DataError("没有正权重关系，无法绘制桑基图。")
    if len(edges) > 200 or edges.source.nunique() + edges.target.nunique() > 80:
        raise DataError("关系超过 200 条或节点超过 80 个，请明确筛选后再绘图。")
    left = edges.groupby("source", sort=False).value.sum()
    right = edges.groupby("target", sort=False).value.sum()
    total = float(edges.value.sum())

    def positions(totals):
        start, result = 0.0, {}
        for name, weight in totals.items():
            height = 0.8 * weight / total
            result[name] = [start, height]
            start += height + 0.2 / len(totals)
        return result

    lp, rp = positions(left), positions(right)
    offsets_l = {k: v[0] for k, v in lp.items()}
    offsets_r = {k: v[0] for k, v in rp.items()}
    fig, ax = plt.subplots(figsize=(11, max(5, max(len(left), len(right)) * 0.32)))
    for i, e in enumerate(edges.itertuples(index=False)):
        y1, y2, h = offsets_l[e.source], offsets_r[e.target], 0.8 * e.value / total
        verts = [
            (0.08, y1),
            (0.42, y1),
            (0.58, y2),
            (0.92, y2),
            (0.92, y2 + h),
            (0.58, y2 + h),
            (0.42, y1 + h),
            (0.08, y1 + h),
            (0.08, y1),
        ]
        codes = [
            Path.MOVETO,
            Path.CURVE4,
            Path.CURVE4,
            Path.CURVE4,
            Path.LINETO,
            Path.CURVE4,
            Path.CURVE4,
            Path.CURVE4,
            Path.CLOSEPOLY,
        ]
        ax.add_patch(
            PathPatch(
                Path(verts, codes),
                facecolor=ctx.colors[i % len(ctx.colors)],
                alpha=0.3,
                edgecolor="none",
            )
        )
        offsets_l[e.source] += h
        offsets_r[e.target] += h
    for x, pos in [(0.04, lp), (0.92, rp)]:
        for name, (y, h) in pos.items():
            ax.add_patch(Rectangle((x, y), 0.04, h, facecolor=ctx.colors[0]))
            ax.text(
                x - 0.01 if x < 0.5 else x + 0.05,
                y + h / 2,
                str(name)[:60],
                ha="right" if x < 0.5 else "left",
                va="center",
                fontsize=8,
            )
    ax.set(xlim=(-0.35, 1.35), ylim=(-0.03, 1.03), title=p.title or "关系桑基图")
    ax.axis("off")
    nodes = [{"name": "L:" + str(n), "label": {"formatter": str(n)}} for n in left.index] + [
        {"name": "R:" + str(n), "label": {"formatter": str(n)}} for n in right.index
    ]
    links = [
        {"source": "L:" + e.source, "target": "R:" + e.target, "value": e.value}
        for e in edges.itertuples(index=False)
    ]
    ctx.chart(
        fig,
        "sankey",
        "关系桑基图",
        {
            "tooltip": {"trigger": "item"},
            "series": [
                {
                    "type": "sankey",
                    "data": nodes,
                    "links": links,
                    "lineStyle": {"color": "source", "opacity": 0.3},
                    "emphasis": {"focus": "adjacency"},
                }
            ],
        },
    )
    ctx.warnings.append(
        "带宽来自用户指定的非负权重；展示已有关系，不证明因果或实际守恒流量。重复起终点按权重相加，原始边表保留。"
    )
    return output(
        ctx,
        "sankey",
        f"绘制 {len(edges)} 条关系，{len(nodes)} 个分侧节点。",
        "两列节点加非负权重构成两层桑基图；左右同名节点分开。",
        {"edges": len(edges), "nodes": len(nodes)},
    )


def group_numeric_compare(ctx):
    """Compare one numeric column across levels of a categorical column."""
    d, frame = validate_tabular(
        ctx.datasets, "group_numeric_compare", ctx.params, ctx.snapshot.get("study", {})
    )
    if d["data_type"] == "metadata":
        frame = frame.loc[
            ~frame.index.isin(ctx.snapshot.get("study", {}).get("excluded_samples", []))
        ]
    fields = {f["name"]: f for f in d.get("understanding", {}).get("fields", [])}
    group_col = ctx.params.group_by
    value_col = ctx.params.variable
    groups = _present(frame[group_col])
    info = fields.get(value_col, {})
    if info.get("role") == "ordinal":
        values = _ordinal_codes(frame[value_col], info)
        ctx.warnings.append(
            f"「{value_col}」按已确认等级顺序编码为序号后参与组间比较：{info.get('encoding')}。"
        )
    else:
        values = pd.to_numeric(frame[value_col], errors="coerce").replace([np.inf, -np.inf], np.nan)
    work = pd.DataFrame({"group": groups, "value": values}).dropna()
    chosen = [v for v in ctx.params.group_values if v]
    if not chosen and ctx.params.group_a and ctx.params.group_b:
        chosen = [ctx.params.group_a, ctx.params.group_b]
    if chosen:
        if len(set(chosen)) != len(chosen) or not set(chosen).issubset(set(work.group)):
            raise Blocker("指定的比较组无效或不完整。", "groups_unselected")
        work = work[work.group.isin(chosen)]
    names = list(dict.fromkeys(work.group.tolist()))
    if len(names) < 2:
        raise Blocker(
            "组间比较至少需要两个组。",
            "groups_unselected",
            dataset_id=d["id"],
            values={str(k): int(v) for k, v in work.group.value_counts().items()},
        )
    counts = work.group.value_counts()
    if counts.min() < 3:
        raise Blocker(
            f"每个比较组至少需要 3 个有效观测；当前最少一组只有 {int(counts.min())} 个。",
            "groups_too_small",
            dataset_id=d["id"],
            values={str(k): int(v) for k, v in counts.items()},
        )
    parts = [work.loc[work.group == name, "value"] for name in names]
    multi = len(names) > 2
    if multi:
        p = float(kruskal(*parts).pvalue)
        test_name = "Kruskal-Wallis"
    else:
        p = float(mannwhitneyu(parts[0], parts[1], alternative="two-sided", method="auto").pvalue)
        test_name = "Mann-Whitney"
    rows = []
    for name, part in zip(names, parts):
        rows.append(
            {
                "group": name,
                "n": int(len(part)),
                "median": float(part.median()),
                "q25": float(part.quantile(0.25)),
                "q75": float(part.quantile(0.75)),
                "mean": float(part.mean()),
            }
        )
    table = pd.DataFrame(rows).set_index("group")
    table["test"] = test_name
    table["p"] = p
    ctx.table(table, "group_numeric_compare", f"{value_col} 按 {group_col} 组间比较")
    fig, ax = plt.subplots(figsize=(max(6.5, len(names) * 1.4), 4.8))
    data = [part.values for part in parts]
    try:
        bp = ax.boxplot(
            data,
            tick_labels=names,
            patch_artist=True,
            showfliers=False,
            medianprops={"color": "#1f3b2c"},
        )
    except TypeError:
        bp = ax.boxplot(
            data,
            labels=names,
            patch_artist=True,
            showfliers=False,
            medianprops={"color": "#1f3b2c"},
        )
    for i, patch in enumerate(bp["boxes"]):
        patch.set_facecolor(ctx.colors[i % len(ctx.colors)])
        patch.set_alpha(0.65)
    for i, part in enumerate(parts):
        jitter = np.random.default_rng(ctx.params.seed + i).uniform(-0.12, 0.12, len(part))
        ax.scatter(
            np.full(len(part), i + 1) + jitter,
            part,
            s=14,
            color=ctx.colors[i % len(ctx.colors)],
            alpha=0.55,
            zorder=3,
        )
    ax.set_ylabel(value_col)
    ax.set_title(ctx.params.title or f"{value_col} · {group_col} 组间比较（{test_name}）")
    fig.tight_layout()
    option = {
        "tooltip": {"trigger": "item"},
        "xAxis": {"type": "category", "data": names},
        "yAxis": {"type": "value", "name": value_col},
        "series": [
            {
                "type": "boxplot",
                "data": [
                    [
                        float(part.min()),
                        float(part.quantile(0.25)),
                        float(part.median()),
                        float(part.quantile(0.75)),
                        float(part.max()),
                    ]
                    for part in parts
                ],
            }
        ],
    }
    ctx.chart(fig, "group_numeric_compare", f"{value_col} 组间比较", option)
    medians = "；".join(
        f"{name} 中位数 {table.loc[name, 'median']:.4g}（n={table.loc[name, 'n']}）"
        for name in names
    )
    summary = (
        f"{value_col} 按 {group_col} 比较（{'、'.join(names)}）：{test_name} p={p:.4g}。{medians}。"
    )
    ctx.warnings.append(
        "探索性组间秩和检验，未校正协变量，不代表因果关系；配对或重复测量设计不适用。"
    )
    return output(
        ctx,
        "group_numeric_compare",
        summary,
        (
            "完整观测上，两组用双侧 Mann–Whitney U，三组及以上用 Kruskal–Wallis；"
            "报告各组样本量、中位数与四分位数，不做多重协变量校正。"
        ),
        {
            "group_by": group_col,
            "variable": value_col,
            "groups": names,
            "test": test_name,
            "p": p,
            "results": table.reset_index().to_dict("records"),
        },
    )
