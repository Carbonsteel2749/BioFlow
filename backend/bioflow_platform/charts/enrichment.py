import numpy as np
from matplotlib.lines import Line2D

from .common import number, pvalue, required, series


def _normalize_rows(rows):
    """Accept both the platform ratio field and the supplied gene_ratio field."""
    normalized = []
    for row in rows:
        item = dict(row)
        if not str(item.get('ratio', '')).strip() and str(item.get('gene_ratio', '')).strip():
            item['ratio'] = item['gene_ratio']
        normalized.append(item)
    return normalized


def draw(rows, fig, ax, params):
    rows = _normalize_rows(rows)
    required(rows, 'term', 'ratio', 'pvalue', 'count')
    if not rows:
        raise ValueError('气泡图至少需要一条记录')
    if len(rows) > 100:
        raise ValueError('气泡图最多展示 100 个条目')

    counts = np.array([number(row, 'count') for row in rows], dtype=float)
    ratios = np.array([number(row, 'ratio') for row in rows], dtype=float)
    if np.any(counts <= 0):
        raise ValueError('count 必须大于零')
    if np.any((ratios < 0) | (ratios > 1)):
        raise ValueError('ratio 或 gene_ratio 必须位于 0–1')

    x_key = params.get('enrichment_x', 'ratio')
    size_key = params.get('enrichment_size', 'count')
    if x_key not in {'ratio', 'count'} or size_key not in {'ratio', 'count'}:
        raise ValueError('不支持的数据映射')

    values = {'ratio': ratios, 'count': counts}
    labels = {'ratio': 'Gene ratio', 'count': 'Count'}
    size_values = values[size_key]
    size_min = float(np.min(size_values))
    size_max = float(np.max(size_values))
    if np.isclose(size_min, size_max):
        bubble_areas = np.full(len(rows), 150.0)
    else:
        bubble_areas = 55 + (size_values - size_min) / (size_max - size_min) * 245

    color_values = np.array([-np.log10(pvalue(row)) for row in rows], dtype=float)
    y_positions = np.arange(len(rows))
    dots = ax.scatter(values[x_key], y_positions, s=bubble_areas, c=color_values,
                      cmap='RdYlBu_r', alpha=0.9, edgecolors='white', linewidths=0.7)
    colorbar = fig.colorbar(dots, ax=ax, pad=0.02)
    colorbar.set_label('−log10(P value)')

    ax.set_yticks(y_positions, [str(row['term']) for row in rows])
    ax.invert_yaxis()
    ax.set(title=params.get('title') or 'Functional enrichment', xlabel=labels[x_key], ylabel='')
    ax.grid(axis='x', linestyle='--', linewidth=0.7, alpha=0.35)
    ax.set_axisbelow(True)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.tick_params(axis='y', labelsize=9)

    legend_values = np.unique(np.quantile(size_values, [0, 0.5, 1]))
    for index, value in enumerate(legend_values):
        if np.isclose(size_min, size_max):
            area = 150.0
        else:
            area = 55 + (float(value) - size_min) / (size_max - size_min) * 245
        handle = Line2D([], [], marker='o', linestyle='', markerfacecolor='#6a8d7a',
                        markeredgecolor='white', markersize=float(np.sqrt(area)))
        series(ax, 'bubble-size:' + str(index), f'{labels[size_key]} = {value:g}', handle)

    ax.margins(y=0.08)
    ax._bioflow_mapping = {
        'x': x_key, 'y': 'term', 'size': size_key, 'color': '-log10(pvalue)'
    }
