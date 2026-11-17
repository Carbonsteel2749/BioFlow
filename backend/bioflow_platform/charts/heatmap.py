import numpy as np
from matplotlib.patches import Patch, Rectangle
from .common import matrix, series


def draw(rows, fig, ax, params):
    import matplotlib.pyplot as plt
    from scipy.cluster.hierarchy import linkage, leaves_list

    x, keys = matrix(rows)

    if x.shape[0] > 150 or x.shape[1] > 150:
        raise ValueError('聚类热图最多展示 150×150，请先筛选矩阵')

    cluster_rows = params.get('heatmap_cluster_rows', True)
    cluster_columns = params.get('heatmap_cluster_columns', True)

    r = (
        leaves_list(linkage(x, method='average', metric='euclidean'))
        if cluster_rows else np.arange(x.shape[0])
    )
    c = (
        leaves_list(linkage(x.T, method='average', metric='euclidean'))
        if cluster_columns else np.arange(x.shape[1])
    )

    ordered = x[r][:, c]
    palette = params.get('heatmap_palette', 'RdBu_r')
    center_zero = params.get('heatmap_center_zero', True)

    if center_zero:
        limit = float(params.get('heatmap_color_limit', 2.5))
        if not np.isfinite(limit) or limit <= 0:
            raise ValueError('热图色阶上限必须为正数')

        image = ax.imshow(
            ordered, aspect='auto', cmap=palette,
            vmin=-limit, vmax=limit
        )
        colorbar_label = 'Z-score'
    else:
        image = ax.imshow(ordered, aspect='auto', cmap=palette)
        colorbar_label = 'Input value'

    fig.colorbar(image, ax=ax, label=colorbar_label)

    if params.get('heatmap_show_group', True):
        ordered_groups = [
            str(rows[i].get('group', '')).strip() for i in r
        ]
        group_names = list(dict.fromkeys(
            g for g in ordered_groups if g
        ))

        if group_names:
            cmap = plt.get_cmap('tab10')
            group_colors = {
                name: cmap(i % 10)
                for i, name in enumerate(group_names)
            }

            for position, group in enumerate(ordered_groups):
                if group:
                    ax.add_patch(Rectangle(
                        (-0.88, position - 0.5), 0.22, 1,
                        facecolor=group_colors[group],
                        edgecolor='none',
                        clip_on=False
                    ))

            ax.set_xlim(-1.05, len(c) - 0.5)

            for group in group_names:
                series(
                    ax, 'group:' + group, group,
                    Patch(
                        facecolor=group_colors[group],
                        edgecolor='none'
                    )
                )

    ax.set_xticks(
        range(len(c)),
        [keys[i] for i in c],
        rotation=60, ha='right'
    )
    ax.set_yticks(
        range(len(r)),
        [rows[i]['sample'] for i in r]
    )
    ax.set(
        title='Clustered heatmap',
        xlabel='Feature',
        ylabel='Sample'
    )
