import numpy as np
from matplotlib.lines import Line2D
from .common import required,number,pvalue,series

def draw(rows,fig,ax,params):
    required(rows,'term','ratio','pvalue','count')
    if len(rows)>100:raise ValueError('气泡图最多展示 100 个条目')
    counts=[number(r,'count') for r in rows];ratios=[number(r,'ratio') for r in rows]
    if min(counts)<=0 or min(ratios)<0 or max(ratios)>1:raise ValueError('count 必须大于零，ratio 必须位于 0–1')
    x_key=params.get('enrichment_x','ratio');size_key=params.get('enrichment_size','count')
    if x_key not in {'ratio','count'} or size_key not in {'ratio','count'}:raise ValueError('不支持的数据映射')
    values={'ratio':np.array(ratios),'count':np.array(counts)};labels={'ratio':'GeneRatio','count':'Count'}
    size_values=values[size_key];maximum=max(size_values)
    if maximum<=0:raise ValueError('气泡大小字段必须包含正数')
    def area(value):return np.asarray(value)/maximum*300+20
    ax.scatter(values[x_key],range(len(rows)),s=area(size_values),c=[-np.log10(pvalue(r)) for r in rows],cmap='viridis')
    dots=ax.collections[0];fig.colorbar(dots,ax=ax,label='−log10(P value)')
    ax.set_yticks(range(len(rows)),[r['term'] for r in rows]);ax.set(title='Enrichment',xlabel=labels[x_key],ylabel='Term')
    ticks=np.unique(np.quantile(size_values,[0,.5,1]))
    for i,value in enumerate(ticks):
        handle=Line2D([],[],marker='o',linestyle='',markerfacecolor='#6a8d7a',markeredgecolor='#6a8d7a',markersize=float(np.sqrt(area(value))))
        series(ax,'bubble-size:'+str(i),f'{labels[size_key]} = {value:g}',handle)
    ax._bioflow_mapping={'x':x_key,'y':'term','size':size_key,'color':'-log10(pvalue)'}
