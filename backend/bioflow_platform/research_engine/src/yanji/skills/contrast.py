"""Descriptive sample- and set-level microbiome contrasts; no inferential claims."""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..data import Blocker, DataError
from .analysis import label, result


def _named_samples(ctx):
    samples = [s for s in ctx.params.samples if s]
    if len(samples) != len(set(samples)):
        raise DataError("指定的样本列表含有重复项。")
    return samples


def _scope_title(ctx, fallback):
    samples = _named_samples(ctx)
    if samples:
        shown = "、".join(samples[:4]) + (" 等" if len(samples) > 4 else "")
        return ctx.params.title or f"{shown} · {fallback}"
    return ctx.params.title or fallback


def sample_profile(ctx):
    samples = _named_samples(ctx)
    if not samples:
        raise Blocker(
            "请指定要查看组成的样本编号。",
            "samples_required",
            params=["samples"],
        )
    matrix = ctx.matrix()
    n = ctx.params.top_n
    union = matrix.mean().sort_values(ascending=False).head(n).index
    table = matrix[union].copy()
    table.columns = [label(c) for c in table.columns]
    ctx.table(table, "sample_profiles", "指定样本的优势特征相对丰度")
    long = (
        matrix[union]
        .reset_index()
        .melt(id_vars=matrix.index.name or "index", var_name="feature", value_name="abundance")
    )
    long.columns = ["sample", "feature", "abundance"]
    long["feature"] = long["feature"].map(label)
    ctx.table(long, "sample_profile_long", "指定样本组成长表")
    display = matrix[union].copy()
    others = (1 - display.sum(axis=1)).clip(lower=0)
    display["Others"] = others
    fig, ax = plt.subplots(figsize=(max(7, len(samples) * 0.9), 5))
    colors = ctx.colors + ["#cfd6d3"]
    bottom = np.zeros(len(display))
    for i, feature in enumerate(display.columns):
        values = display[feature].values
        ax.bar(
            display.index.astype(str),
            values,
            bottom=bottom,
            color=colors[i % len(colors)],
            width=0.72,
            label=label(feature) if feature != "Others" else "Others",
        )
        bottom = bottom + values
    ax.set_ylabel("相对丰度")
    ax.set_xlabel("样本")
    ax.set_ylim(0, 1)
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left", frameon=False, fontsize=8)
    ax.set_title(_scope_title(ctx, f"{ctx.params.rank} 组成剖面"))
    fig.tight_layout()
    option = {
        "tooltip": {"trigger": "axis"},
        "legend": {"type": "scroll", "bottom": 0},
        "xAxis": {"type": "category", "data": display.index.astype(str).tolist()},
        "yAxis": {"type": "value", "name": "相对丰度", "max": 1},
        "series": [
            {
                "name": label(feature) if feature != "Others" else "Others",
                "type": "bar",
                "stack": "total",
                "data": display[feature].tolist(),
            }
            for feature in display.columns
        ],
    }
    ctx.chart(fig, "sample_profile", _scope_title(ctx, "样本组成剖面"), option)
    summary = (
        f"已描述 {len(matrix)} 个指定样本在 {ctx.params.rank} 层级的组成；"
        f"图中展示这些样本合计平均丰度前 {min(n, matrix.shape[1])} 项。"
    )
    return result(
        ctx,
        "sample_profile",
        summary,
        "按指定样本子集做层内归一化组成描述，不做组间检验。",
        {"samples": list(matrix.index), "features": int(matrix.shape[1]), "rank": ctx.params.rank},
        [ctx.evidence("profile", summary, {"samples": list(matrix.index)})],
    )


def sample_comparison(ctx):
    samples = _named_samples(ctx)
    if len(samples) < 2:
        raise Blocker(
            "描述性组成对比至少需要两个样本编号。",
            "samples_required",
            params=["samples"],
        )
    matrix = ctx.matrix()
    if len(matrix) < 2:
        raise DataError("指定样本中可计算的不足两个。")
    names = list(matrix.index)
    a, b = names[0], names[1]
    # Top features by max abundance across the selected samples, not the full cohort.
    top = matrix.max().sort_values(ascending=False).head(ctx.params.top_n).index
    present = {name: set(matrix.columns[matrix.loc[name] > 0]) for name in names}
    shared = set.intersection(*present.values()) if present else set()
    only = {
        name: sorted(present[name] - set.union(*(present[o] for o in names if o != name)))
        for name in names
    }
    membership = []
    for feature in matrix.columns:
        hosts = [name for name in names if matrix.loc[name, feature] > 0]
        membership.append(
            {
                "feature": feature,
                "label": label(feature),
                "detected_in": "、".join(hosts) if hosts else "无",
                "n_detected": len(hosts),
                **{name: float(matrix.loc[name, feature]) for name in names},
            }
        )
    members = pd.DataFrame(membership).set_index("feature")
    members["difference"] = members[a] - members[b] if len(names) == 2 else np.nan
    # Pseudocount keeps log2 ratios defined; recorded in the method text.
    pseudo = 1e-6
    if len(names) == 2:
        members["log2_ratio"] = np.log2((members[a] + pseudo) / (members[b] + pseudo))
    ctx.table(
        members.sort_values("difference", key=lambda s: s.abs(), ascending=False),
        "abundance_contrast",
        "指定样本丰度对照",
    )
    sets = pd.DataFrame(
        [
            {
                "set": "共有检出",
                "count": len(shared),
                "examples": "、".join(label(x) for x in list(shared)[:8]),
            },
            *[
                {
                    "set": f"仅 {name}",
                    "count": len(only[name]),
                    "examples": "、".join(label(x) for x in only[name][:8]),
                }
                for name in names
            ],
        ]
    )
    ctx.table(sets.set_index("set"), "shared_unique", "共有与特有检出")
    # Side-by-side composition of the union top features.
    display = matrix[top].copy()
    display["Others"] = (1 - display.sum(axis=1)).clip(lower=0)
    fig, ax = plt.subplots(figsize=(max(7, len(names) * 1.1), 5))
    colors = ctx.colors + ["#cfd6d3"]
    x = np.arange(len(names))
    bottom = np.zeros(len(names))
    for i, feature in enumerate(display.columns):
        values = display[feature].values
        ax.bar(
            x,
            values,
            bottom=bottom,
            color=colors[i % len(colors)],
            width=0.7,
            label=label(feature) if feature != "Others" else "Others",
        )
        bottom = bottom + values
    ax.set_xticks(x, names)
    ax.set_ylabel("相对丰度")
    ax.set_ylim(0, 1)
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left", frameon=False, fontsize=8)
    ax.set_title(_scope_title(ctx, "组成描述性对比"))
    fig.tight_layout()
    ctx.chart(fig, "composition_contrast", _scope_title(ctx, "组成描述性对比"))
    # Largest absolute differences between the first two samples.
    if len(names) == 2:
        ranked = members.reindex(
            members["difference"].abs().sort_values(ascending=False).index
        ).head(ctx.params.top_n)
        fig, ax = plt.subplots(figsize=(9, max(4, len(ranked) * 0.32)))
        y = np.arange(len(ranked))
        ax.barh(y - 0.18, ranked[a].values, height=0.35, color=ctx.colors[0], label=a)
        ax.barh(y + 0.18, ranked[b].values, height=0.35, color=ctx.colors[1], label=b)
        ax.set_yticks(y, [label(i)[:55] for i in ranked.index], fontsize=8)
        ax.set_xlabel("相对丰度")
        ax.legend(frameon=False)
        ax.set_title(f"{a} 与 {b} 丰度差异最大的特征")
        fig.tight_layout()
        ctx.chart(fig, "abundance_bars", f"{a} 与 {b} 丰度对比")
    summary = (
        f"对 {len(names)} 个指定样本做了 {ctx.params.rank} 层级描述性对比："
        f"共有检出 {len(shared)} 个特征"
        + ("；" + "；".join(f"仅 {n} {len(only[n])} 个" for n in names) if names else "")
        + "。未做统计检验。"
    )
    ctx.warnings.append(
        "这是指定样本的描述性组成对比。每个样本只有一次观测时不能给出显著性，也不能代表组间差异。"
    )
    return result(
        ctx,
        "sample_comparison",
        summary,
        "指定样本子集层内归一化；共有/特有按检出（丰度>0）；log2 比值使用伪计数 1e-6，仅作描述。",
        {
            "samples": names,
            "shared": len(shared),
            "unique": {name: len(only[name]) for name in names},
            "rank": ctx.params.rank,
        },
        [ctx.evidence("contrast", summary, {"samples": names, "shared": len(shared)})],
    )


def core_taxa(ctx):
    matrix = ctx.matrix()
    threshold = ctx.params.prevalence
    if threshold <= 0 or threshold > 1:
        raise DataError("核心菌检出率阈值须在 (0, 1] 内。")
    prevalence = (matrix > 0).mean()
    core = prevalence[prevalence >= threshold].sort_values(ascending=False)
    table = pd.DataFrame(
        {
            "prevalence": core,
            "mean_abundance": matrix[core.index].mean(),
            "label": [label(x) for x in core.index],
        }
    )
    ctx.table(table, "core_taxa", f"检出率 ≥ {threshold:g} 的核心特征")
    groups = ctx.groups(matrix.index)
    set_rows = []
    if groups is not None and groups.nunique() >= 2:
        by_group = {}
        for name, index in groups.groupby(groups).groups.items():
            prev = (matrix.loc[index] > 0).mean()
            by_group[name] = set(prev[prev >= threshold].index)
            set_rows.append(
                {
                    "group": name,
                    "core_count": len(by_group[name]),
                    "examples": "、".join(label(x) for x in list(by_group[name])[:8]),
                }
            )
        shared = set.intersection(*by_group.values()) if by_group else set()
        set_rows.insert(
            0,
            {
                "group": "各组共有核心",
                "core_count": len(shared),
                "examples": "、".join(label(x) for x in list(shared)[:8]),
            },
        )
        for name, features in by_group.items():
            unique = features - set.union(*(by_group[o] for o in by_group if o != name))
            set_rows.append(
                {
                    "group": f"仅 {name}",
                    "core_count": len(unique),
                    "examples": "、".join(label(x) for x in list(unique)[:8]),
                }
            )
        ctx.table(pd.DataFrame(set_rows).set_index("group"), "core_by_group", "分组核心与特有")
    fig, ax = plt.subplots(figsize=(9, max(4, min(20, len(table)) * 0.28)))
    show = table.head(ctx.params.top_n).iloc[::-1]
    ax.barh(show["label"].str[:55], show["prevalence"], color=ctx.colors[0])
    ax.set_xlim(0, 1)
    ax.axvline(threshold, color="#a0aaa5", lw=0.8, ls="--")
    ax.set_xlabel("检出率")
    ax.set_title(_scope_title(ctx, f"核心特征（检出率 ≥ {threshold:g}）"))
    fig.tight_layout()
    ctx.chart(fig, "core_taxa", _scope_title(ctx, "核心特征"))
    summary = (
        f"在 {len(matrix)} 个样本、{ctx.params.rank} 层级上，"
        f"检出率 ≥ {threshold:g} 的特征有 {len(table)} 个。"
    )
    return result(
        ctx,
        "core_taxa",
        summary,
        f"核心特征定义为所选样本中检出率不低于 {threshold:g}；分组时分别计算后再做集合运算。",
        {
            "samples": len(matrix),
            "core": len(table),
            "threshold": threshold,
            "rank": ctx.params.rank,
        },
        [ctx.evidence("core", summary, {"core": len(table), "threshold": threshold})],
    )
