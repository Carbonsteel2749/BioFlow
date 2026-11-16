import numpy as np
from .common import matrix

def draw(rows,fig,ax,params):
    from scipy.cluster.hierarchy import linkage,leaves_list
    x,keys=matrix(rows)
    if x.shape[0]>150 or x.shape[1]>150:raise ValueError('聚类热图最多展示 150×150，请先筛选矩阵')
    log=params.get('heatmap_log','none')
    if log!='none':
        if np.any(x<0):raise ValueError('对数转换要求非负数值')
        x=np.log1p(x) if log=='log1p' else np.log2(x+1)
    scale=params.get('heatmap_scale','none')
    if scale!='none':
        axis=1 if scale=='row' else 0;std=x.std(axis=axis,keepdims=True)
        x=(x-x.mean(axis=axis,keepdims=True))/np.where(std>0,std,1)
    method=params.get('heatmap_linkage','average');metric=params.get('heatmap_distance','euclidean')
    if method=='ward' and metric!='euclidean':raise ValueError('Ward 聚类只支持欧氏距离')
    r=leaves_list(linkage(x,method=method,metric=metric)) if params.get('heatmap_row_cluster',True) else np.arange(len(rows))
    c=leaves_list(linkage(x.T,method=method,metric=metric)) if params.get('heatmap_col_cluster',True) else np.arange(len(keys))
    image=ax.imshow(x[r][:,c],aspect='auto',cmap=params.get('heatmap_palette','viridis'));fig.colorbar(image,ax=ax,label='Z-score' if scale!='none' else 'Value')
    ax.set_xticks(range(len(c)),[keys[i] for i in c] if params.get('heatmap_show_columns',True) else ['']*len(c),rotation=60,ha='right')
    ax.set_yticks(range(len(r)),[rows[i]['sample'] for i in r] if params.get('heatmap_show_rows',True) else ['']*len(r));ax.set(title='Clustered heatmap',xlabel='Feature',ylabel='Sample')
