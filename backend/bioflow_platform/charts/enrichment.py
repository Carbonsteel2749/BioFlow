import numpy as np
from .common import required,number,pvalue
def draw(rows,fig,ax,params):
    required(rows,'term','ratio','pvalue','count')
    if len(rows)>100:raise ValueError('气泡图最多展示 100 个条目')
    counts=[number(r,'count') for r in rows];ratios=[number(r,'ratio') for r in rows]
    if min(counts)<=0 or min(ratios)<0 or max(ratios)>1:raise ValueError('count 必须大于零，ratio 必须位于 0–1')
    dots=ax.scatter(ratios,range(len(rows)),s=np.array(counts)/max(counts)*300+20,c=[-np.log10(pvalue(r)) for r in rows],cmap='viridis')
    fig.colorbar(dots,ax=ax,label='−log10(P value)');ax.set_yticks(range(len(rows)),[r['term'] for r in rows]);ax.set(title='Enrichment',xlabel='Gene ratio',ylabel='Term')
