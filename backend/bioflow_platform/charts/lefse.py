import math

from .common import required, number, series


PALETTE = [
    "#246750", "#7195ac", "#c59d65", "#b85c5c",
    "#8f78a8", "#83bda5", "#9c755f", "#5b8e9e",
]


def draw(rows, fig, ax, params):
    required(rows, "taxon", "lda_score", "group")

    records = []
    for row in rows:
        taxon = str(row.get("taxon", "")).strip()
        group = str(row.get("group", "")).strip()

        if not taxon:
            raise ValueError("分类单元名称不能为空")
        if not group:
            raise ValueError("富集组别不能为空")

        score = number(row, "lda_score")
        if not math.isfinite(score):
            raise ValueError("LDA 分数必须是有限数值")

        records.append((taxon, score, group))

    if len(records) > 100:
        raise ValueError("LEfSe 图最多显示 100 个分类单元，请先筛选结果")

    records.sort(
        key=lambda item: (
            abs(item[1]), item[0].casefold(), item[2].casefold()
        )
    )

    groups = list(dict.fromkeys(item[2] for item in records))

    # Keep labels readable when there are many taxa.
    fig.set_size_inches(
        fig.get_figwidth(),
        min(18, max(fig.get_figheight(), 2.5 + 0.22 * len(records)))
    )

    for group_index, group in enumerate(groups):
        indices = [
            i for i, item in enumerate(records) if item[2] == group
        ]
        bars = ax.barh(
            indices,
            [records[i][1] for i in indices],
            height=0.72,
            color=PALETTE[group_index % len(PALETTE)],
            label=group,
        )
        series(ax, "group:" + group, group, bars)

    ax.set_yticks(range(len(records)))
    ax.set_yticklabels([item[0] for item in records])
    ax.axvline(0, color="#777777", linestyle="--", linewidth=0.8)
    ax.grid(axis="x", linestyle=":", alpha=0.35)
    ax.set_axisbelow(True)
    ax.set_xlabel("LDA score")
    ax.set_ylabel("Taxon")
    ax.set_title("LEfSe LDA scores")
