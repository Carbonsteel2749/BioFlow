import numpy as np
from .common import required, number, pvalue, series, palette


def draw(rows, fig, ax, params):
    required(rows, 'gene', 'log2fc', 'pvalue')

    fc = float(params.get('volcano_fc', params.get('volcano_fc_threshold', 1.0)))
    cutoff = float(params.get('volcano_p', params.get('volcano_p_threshold', 0.05)))
    top_n = int(params.get('volcano_label_top_n', 0))
    p_label = params.get('volcano_p_label', 'P value')

    if not np.isfinite(fc) or fc <= 0:
        raise ValueError('log2FC 阈值必须为正数')
    if not np.isfinite(cutoff) or not 0 < cutoff <= 1:
        raise ValueError('显著性阈值必须位于 0–1')
    if not 0 <= top_n <= 50:
        raise ValueError('基因标注数量必须位于 0–50')
    if p_label not in {'P value', 'FDR'}:
        raise ValueError('纵轴统计值必须为 P value 或 FDR')

    groups = {'up': ([], []), 'down': ([], []), 'other': ([], [])}
    significant = []

    for row in rows:
        x = number(row, 'log2fc')
        p = pvalue(row)
        y = -np.log10(p)

        key = (
            'up' if x >= fc and p < cutoff else
            'down' if x <= -fc and p < cutoff else
            'other'
        )

        groups[key][0].append(x)
        groups[key][1].append(y)

        if key != 'other':
            significant.append(
                (p, str(row['gene']).strip(), x, y)
            )

    styles = [
        ('up', '上调', '#b94d49'),
        ('down', '下调', '#417caa'),
        ('other', '未达到阈值', '#9ca69e'),
    ]

    for (key, label, _), color in zip(styles, palette(params, 3)):
        x, y = groups[key]
        series(
            ax, 'series:' + key, label,
            ax.scatter(x, y, c=color, s=params.get('plot_point_size',22), alpha=0.8)
        )

    if params.get('volcano_show_p_line', True):
        ax.axhline(
            -np.log10(cutoff),
            color='#777777', linestyle='--', linewidth=0.9
        )

    if params.get('volcano_show_fc_lines', True):
        for threshold in (-fc, fc):
            ax.axvline(
                threshold, color='#777777',
                linestyle='--', linewidth=0.9
            )

    for p, gene, x, y in sorted(
        significant, key=lambda item: item[0]
    )[:top_n]:
        if gene:
            ax.annotate(
                gene, (x, y),
                xytext=(4, 4), textcoords='offset points',
                fontsize=max(6, float(params.get('font_size', 10)) - 2)
            )

    ax.set(
        xlabel='log2 fold change',
        ylabel='−log10(' + p_label + ')',
        title='Volcano plot'
    )
