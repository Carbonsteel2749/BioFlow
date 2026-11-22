"""Effect-aware figures and three-stage association chains from real result tables."""

import textwrap

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import PathPatch, Rectangle
from matplotlib.path import Path

from ..data import DataError, load_frame


def result_frame(ctx, alias="data", right=False):
    if alias in ctx.inputs:
        return ctx.inputs[alias].copy()
    identity = ctx.params.right_dataset_id if right else ctx.params.dataset_id
    ds = next(
        (
            d
            for d in ctx.datasets
            if d["id"] == identity and d["data_type"] in {"table", "metadata"}
        ),
        None,
    )
    if not ds:
        raise DataError(f"请为{alias}绑定上游结果表，或选择一份已有结果文件。")
    return load_frame(ds).copy()


def standard_result(ctx, frame, require_pair=True):
    p = ctx.params
    if p.result_predictor not in frame and frame.index.name == p.result_predictor:
        frame = frame.reset_index()
    needed = [p.result_predictor, p.result_effect]
    if require_pair:
        needed.append(p.result_outcome)
    if any(c not in frame for c in needed):
        raise DataError("关联结果缺少指定的暴露、结局或效应字段。")
    f = frame.copy()
    f["predictor"] = f[p.result_predictor].astype(str)
    f["outcome"] = f[p.result_outcome].astype(str) if p.result_outcome in f else "效应"
    f["effect"] = pd.to_numeric(f[p.result_effect], errors="coerce")
    for target, source in [
        ("p", p.result_p),
        ("q", p.result_q),
        ("ci_low", p.result_ci_low),
        ("ci_high", p.result_ci_high),
    ]:
        f[target] = pd.to_numeric(f[source], errors="coerce") if source in f else np.nan
    metric = p.effect_metric
    if not metric and "metric" in f:
        metrics = f.loc[f.effect.notna(), "metric"].dropna().unique()
        if len(metrics) == 1:
            metric = str(metrics[0])
    if not metric:
        raise DataError(
            "请明确效应量含义（例如标准化beta、Spearman rho或LDA），不能混用不同统计量。"
        )
    f["metric"] = metric
    f = f.loc[np.isfinite(f.effect)].copy()
    if f.empty:
        raise DataError("没有可估计的效应可供绘图。")
    if (f.predictor.str.strip().eq("") | f.outcome.str.strip().eq("")).any():
        raise DataError("结果标签包含空值。")
    if p.significance != "all" and not f[p.significance].notna().any():
        # A heatmap may still display all available coefficients without stars;
        # a network needs actual qualifying edges and validates below.
        ctx.warnings.append(f"结果中没有有效{p.significance}，图中不标注显著性。")
    f = f.drop_duplicates(["predictor", "outcome", "effect", "p", "q"])
    if f.duplicated(["predictor", "outcome"]).any():
        raise DataError("同一关联有多份不同结果，请先明确使用哪个模型，不自动平均系数或p值。")
    return f


def stars(value):
    return "***" if value <= 0.001 else "**" if value <= 0.01 else "*" if value <= 0.05 else ""


def short(name):
    from .analysis import label

    return "\n".join(textwrap.wrap(label(str(name)), width=36))


def heatmap_frame(ctx, table, name="heatmap", emit_table=True):
    try:
        f = standard_result(ctx, table)
    except DataError:
        if not emit_table:
            ctx.warnings.append("本次没有可估计效应，保留完整结果表，不绘制空热图。")
            return
        raise
    top = (
        f.assign(strength=f.effect.abs())
        .groupby("predictor")
        .strength.max()
        .nlargest(ctx.params.top_n)
        .index
    )
    outcomes = list(dict.fromkeys(f.outcome))
    if len(outcomes) > 40:
        outcomes = list(
            f.assign(strength=f.effect.abs()).groupby("outcome").strength.max().nlargest(40).index
        )
        ctx.warnings.append("热图仅展示效应较大的40个结局；完整检验及BH校正仍覆盖原分析范围。")
        f = f.loc[f.outcome.isin(outcomes)]
    f = f.loc[f.predictor.isin(top)]
    values = f.pivot(index="predictor", columns="outcome", values="effect").reindex(
        index=top, columns=outcomes
    )
    significance = (
        f.pivot(index="predictor", columns="outcome", values=ctx.params.significance).reindex_like(
            values
        )
        if ctx.params.significance != "all"
        else None
    )
    limit = float(np.nanmax(np.abs(values.values))) or 1
    fig, ax = plt.subplots(
        figsize=(max(6, len(outcomes) * 0.75 + 3), max(3.5, len(top) * 0.43 + 1.8))
    )
    cmap = plt.get_cmap("RdBu_r").copy()
    cmap = cmap.with_extremes(bad="#e5e9e5")
    im = ax.imshow(
        np.ma.masked_invalid(values.values),
        cmap=cmap,
        vmin=-limit,
        vmax=limit,
        aspect="auto",
    )
    ax.set_xticks(range(len(outcomes)), outcomes, rotation=40, ha="right")
    ax.set_yticks(range(len(top)), [short(n) for n in top], fontsize=8)
    for i in range(len(top)):
        for j in range(len(outcomes)):
            value = values.iloc[i, j]
            if pd.notna(value):
                mark = (
                    stars(significance.iloc[i, j])
                    if significance is not None and pd.notna(significance.iloc[i, j])
                    else ""
                )
                ax.text(
                    j,
                    i,
                    f"{value:.2f}{mark}",
                    ha="center",
                    va="center",
                    fontsize=8,
                    color="white" if abs(value) > limit * 0.65 else "#263c34",
                )
    metric = str(f.metric.iloc[0])
    fig.colorbar(im, ax=ax, label=metric)
    ax.set(
        xlabel="结局 / Outcome",
        ylabel="暴露或特征 / Predictor",
        title=ctx.params.title or f"关联效应 · {metric}",
    )
    fig.text(
        0.5,
        0.01,
        f"颜色与数值：{metric}；星号：{ctx.params.significance}（未在展示子集重新校正）",
        ha="center",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    ctx.chart(fig, name, "关联效应热图")
    if emit_table:
        ctx.table(f, "plot_data", "热图实际使用的效应与显著性")


def association_heatmap(ctx):
    from .analysis import result

    f = result_frame(ctx)
    heatmap_frame(ctx, f)
    return result(
        ctx,
        "association_heatmap",
        "已按真实效应和原始校正结果生成热图。",
        "不重新检验；颜色对应效应量，星号对应明确选择的q/p。",
        {},
        [ctx.evidence("heatmap", "关联效应可视化", {})],
    )


def effect_plot(ctx):
    from .analysis import result

    f = standard_result(ctx, result_frame(ctx), require_pair=False)
    p = ctx.params
    if p.effect_direction:
        if p.effect_style == "forest":
            raise DataError("按富集组转换方向适用于棒棒糖图；森林图须使用原始效应及置信区间。")
        raw = result_frame(ctx)
        if p.result_predictor not in raw and raw.index.name == p.result_predictor:
            raw = raw.reset_index()
        if p.effect_direction not in raw or not p.positive_group:
            raise DataError("方向绘图需要指定组别字段及正方向组名。")
        if raw[p.effect_direction].nunique() != 2 or p.positive_group not in set(
            raw[p.effect_direction].astype(str)
        ):
            raise DataError("方向绘图必须明确两个组以及哪个组为正方向。")
        # Match source rows by explicit labels; never assume High/Low spellings.
        mapping = raw.set_index(p.result_predictor)[p.effect_direction].astype(str)
        if not mapping.index.is_unique:
            raise DataError("每个特征需要唯一的组方向。")
        f["effect"] = f.effect.abs() * np.where(
            f.predictor.map(mapping).eq(p.positive_group), 1, -1
        )
        f["metric"] = "signed_" + f.metric
    if p.significance != "all":
        if not f[p.significance].notna().any():
            raise DataError("结果缺少有效显著性值，请明确选择显示全部，不能将缺失当作不显著。")
        f = f.loc[f[p.significance].le(p.alpha)]
    if f.empty:
        ctx.table(f, "plot_data", "筛选后的空效应结果")
        return empty_findings(ctx, "effect_plot", "没有满足所选显著性阈值的效应。", {"features": 0})
    f = (
        f.reindex(f.effect.abs().sort_values(ascending=False).index)
        .head(p.top_n)
        .sort_values("effect")
    )
    if p.effect_style == "forest" and (f.ci_low.isna().any() or f.ci_high.isna().any()):
        raise DataError("森林图需要真实置信区间；没有区间时请选择效应棒棒糖图，不能编造区间。")
    if p.effect_style == "forest" and ((f.ci_low > f.effect) | (f.ci_high < f.effect)).any():
        raise DataError("置信区间未包含对应效应值，请核对字段、方向和尺度。")
    fig, ax = plt.subplots(figsize=(9, max(3.5, len(f) * 0.4 + 1.2)))
    y = np.arange(len(f))
    colors = [ctx.colors[0] if v >= 0 else ctx.colors[5] for v in f.effect]
    if p.effect_style == "forest":
        for i, row in enumerate(f.itertuples()):
            ax.plot([row.ci_low, row.ci_high], [i, i], color=colors[i], lw=1.5)
    else:
        ax.hlines(y, 0, f.effect, color=colors, lw=1.2)
    ax.scatter(f.effect, y, color=colors, s=35, zorder=3)
    ax.axvline(0, color="#a5afa9", lw=0.8)
    ax.set_yticks(
        y,
        [
            short(r.predictor) + (f" → {r.outcome}" if r.outcome != "效应" else "")
            for r in f.itertuples()
        ],
        fontsize=8,
    )
    ax.set(
        xlabel=f.metric.iloc[0],
        ylabel="关联或特征",
        title=p.title or ("效应与95%置信区间" if p.effect_style == "forest" else "带方向效应图"),
    )
    fig.tight_layout()
    ctx.chart(
        fig,
        "effect_plot",
        "效应量与区间图" if p.effect_style == "forest" else "效应棒棒糖图",
    )
    ctx.table(f, "plot_data", "效应图实际使用的数据")
    return result(
        ctx,
        "effect_plot",
        f"展示 {len(f)} 个效应，统计量为 {f.metric.iloc[0]}。",
        "使用已有真实结果，不重算p/q；方向映射仅按明确参数。",
        {"features": len(f)},
        [ctx.evidence("effects", "效应量图", {"features": len(f)})],
    )


def association_network(ctx):
    from .analysis import result

    p = ctx.params
    left = standard_result(ctx, result_frame(ctx, "left"))
    right = standard_result(ctx, result_frame(ctx, "right", right=True))

    def selected(f):
        if p.significance != "all":
            if not f[p.significance].notna().any():
                raise DataError("网络缺少有效显著性值；若只做描述性连接，须明确选择不筛选显著性。")
            f = f.loc[f[p.significance].le(p.alpha)]
        return (
            f.loc[f.effect.ne(0)]
            .assign(strength=lambda d: d.effect.abs())
            .nlargest(p.top_n, "strength")
        )

    left, right = selected(left), selected(right)
    middle = set(left.outcome) & set(right.predictor)
    left, right = (
        left.loc[left.outcome.isin(middle)],
        right.loc[right.predictor.isin(middle)],
    )
    if left.empty or right.empty:
        ctx.table(
            pd.DataFrame(
                columns=[
                    "source",
                    "target",
                    "source_label",
                    "target_label",
                    "source_stage",
                    "target_stage",
                    "effect",
                    "weight",
                    "p",
                    "q",
                    "metric",
                ]
            ),
            "network_edges",
            "筛选后的空关联网络",
        )
        ctx.table(
            pd.DataFrame(columns=["id", "label", "stage"]),
            "network_nodes",
            "空网络节点",
        )
        return empty_findings(
            ctx,
            "association_network",
            "筛选后没有通过中间层连接的完整关联路径。",
            {"edges": 0, "nodes": 0},
        )
    edges = []
    for stage, frame in enumerate([left, right]):
        for row in frame.itertuples():
            edges.append(
                {
                    "source": f"{stage}::{row.predictor}",
                    "target": f"{stage + 1}::{row.outcome}",
                    "source_label": row.predictor,
                    "target_label": row.outcome,
                    "source_stage": stage,
                    "target_stage": stage + 1,
                    "effect": row.effect,
                    "weight": abs(row.effect),
                    "p": row.p,
                    "q": row.q,
                    "metric": row.metric,
                }
            )
    edges = pd.DataFrame(edges)
    stages = [
        list(dict.fromkeys(left.predictor)),
        list(dict.fromkeys(pd.concat([left.outcome, right.predictor]))),
        list(dict.fromkeys(right.outcome)),
    ]
    if sum(map(len, stages)) > 80:
        raise DataError("网络超过80个节点，请缩小Top N或关联范围。")
    incoming = edges.groupby("target").weight.sum().to_dict()
    outgoing = edges.groupby("source").weight.sum().to_dict()
    weights = {
        f"{s}::{label}": max(incoming.get(f"{s}::{label}", 0), outgoing.get(f"{s}::{label}", 0))
        for s, labels in enumerate(stages)
        for label in labels
    }
    scale = max(
        sum(weights[f"{s}::{label}"] for label in labels) for s, labels in enumerate(stages)
    )
    positions = {}
    nodes = []
    for stage, labels in enumerate(stages):
        cursor = 0.0
        for label in labels:
            identity = f"{stage}::{label}"
            height = 0.8 * weights[identity] / scale
            positions[identity] = (cursor, height)
            cursor += height + 0.15 / max(1, len(labels))
            nodes.append(
                {
                    "id": identity,
                    "label": label,
                    "stage": stage,
                    "weight": weights[identity],
                }
            )
    fig, ax = plt.subplots(figsize=(15, max(6, max(map(len, stages)) * 0.43)))
    offsets_in = {k: v[0] for k, v in positions.items()}
    offsets_out = dict(offsets_in)
    for edge in edges.itertuples():
        x1 = edge.source_stage * 0.48 + 0.035
        x2 = edge.target_stage * 0.48
        y1, y2 = offsets_out[edge.source], offsets_in[edge.target]
        h = 0.8 * edge.weight / scale
        path = Path(
            [
                (x1, y1),
                (x1 + 0.16, y1),
                (x2 - 0.16, y2),
                (x2, y2),
                (x2, y2 + h),
                (x2 - 0.16, y2 + h),
                (x1 + 0.16, y1 + h),
                (x1, y1 + h),
                (x1, y1),
            ],
            [
                Path.MOVETO,
                Path.CURVE4,
                Path.CURVE4,
                Path.CURVE4,
                Path.LINETO,
                Path.CURVE4,
                Path.CURVE4,
                Path.CURVE4,
                Path.CLOSEPOLY,
            ],
        )
        ax.add_patch(
            PathPatch(
                path,
                facecolor=ctx.colors[0] if edge.effect > 0 else ctx.colors[5],
                alpha=0.35,
                lw=0,
            )
        )
        offsets_out[edge.source] += h
        offsets_in[edge.target] += h
    for node in nodes:
        y, h = positions[node["id"]]
        x = node["stage"] * 0.48
        ax.add_patch(
            Rectangle((x, y), 0.035, h, facecolor=ctx.colors[2], edgecolor="#617a6f", lw=0.5)
        )
        ax.text(
            x + 0.0175,
            y + h / 2,
            short(node["label"]),
            ha="center",
            va="center",
            fontsize=7,
        )
    for s, title in enumerate(p.network_stages):
        ax.text(s * 0.48 + 0.0175, 1.01, title, ha="center", weight="bold", fontsize=11)
    ax.set(xlim=(-0.04, 1.04), ylim=(-0.03, 1.05))
    ax.axis("off")
    ax.set_title(p.title or "三层显著关联网络", pad=35)
    fig.text(
        0.5,
        0.015,
        "边宽=|效应|；绿色正向、紫红负向；表示统计关联，不表示因果或代谢通量",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    option = {
        "tooltip": {"trigger": "item", "renderMode": "richText"},
        "series": [
            {
                "type": "sankey",
                "emphasis": {"focus": "adjacency"},
                "data": [
                    {
                        "name": n["id"],
                        "depth": n["stage"],
                        "displayLabel": n["label"],
                        "label": {"formatter": n["label"]},
                    }
                    for n in nodes
                ],
                "links": [
                    {
                        "source": e.source,
                        "target": e.target,
                        "value": e.weight,
                        "effect": e.effect,
                        "metric": e.metric,
                        "lineStyle": {
                            "color": ctx.colors[0] if e.effect > 0 else ctx.colors[5],
                            "opacity": 0.45,
                        },
                    }
                    for e in edges.itertuples()
                ],
                "label": {"formatter": "{b}"},
                "nodeAlign": "justify",
            }
        ],
    }
    ctx.chart(fig, "association_network", "三层关联桑基图", option)
    ctx.table(edges, "network_edges", "网络实际使用的边、符号及显著性")
    ctx.table(pd.DataFrame(nodes), "network_nodes", "分层节点及唯一标识")
    metrics = {"edges": len(edges), "nodes": len(nodes), "middle_nodes": len(stages[1])}
    summary = (
        f"构建 {len(nodes)} 个节点、{len(edges)} 条边的三层关联网络；每段最多展示Top {p.top_n}。"
    )
    if left.metric.iloc[0] != right.metric.iloc[0]:
        ctx.warnings.append("两段关联使用不同统计量，边宽不能作跨统计量的直接强弱比较。")
    return result(
        ctx,
        "association_network",
        summary,
        "按原q/p筛选、匹配中间层；重复关系不取均值，零效应不伪造epsilon边。",
        metrics,
        [ctx.evidence("network", summary, metrics)],
    )


def empty_findings(ctx, skill, summary, metrics):
    """Valid null findings are completed analyses, never fabricated graph edges."""
    from .analysis import result

    fig, ax = plt.subplots(figsize=(8, 3.5))
    ax.axis("off")
    ax.text(0.5, 0.55, summary, ha="center", va="center", wrap=True, fontsize=14)
    ax.text(
        0.5,
        0.35,
        "保留原定阈值和空结果，不补造关系。",
        ha="center",
        va="center",
        color="#6a786d",
    )
    ctx.chart(fig, skill, "分析完成 · 未发现满足条件的关系")
    ctx.warnings.append("未发现满足条件的关系不等于证明不存在关联；未放宽原定阈值。")
    return result(
        ctx,
        skill,
        summary,
        "按确认的显著性阈值和真实输入筛选；空结果如实记录。",
        metrics,
        [ctx.evidence("no_findings", summary, {"no_findings": True})],
    )
