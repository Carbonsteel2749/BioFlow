import matplotlib.pyplot as plt
import numpy as np
from .common import required,number,series

def draw(rows,fig,ax,params):
    required(rows,'depth','shannon','lower','upper');groups={}
    for row in rows:
        x=number(row,'depth');y=number(row,'shannon');low=number(row,'lower');high=number(row,'upper')
        if x<=0 or low<0 or not low<=y<=high:raise ValueError('depth 必须大于零，且 0 ≤ lower ≤ shannon ≤ upper')
        groups.setdefault(row.get('group') or 'All samples',[]).append((x,y,low,high))
    if len(groups)>30:raise ValueError('Shannon 图最多展示 30 组')
    colors=plt.get_cmap(params.get('series_palette','Dark2'))(np.linspace(0,1,len(groups)))
    for (name,points),color in zip(groups.items(),colors):
        points=sorted(points)
        if len(points)<2 or len(set(p[0] for p in points))!=len(points):raise ValueError('每组需要至少两个不重复的 depth')
        x,y,low,high=np.array(points).T
        line,=ax.plot(x,y,color=color,lw=params.get('shannon_line_width',2),marker='o' if params.get('shannon_show_points',False) else None,markersize=3)
        series(ax,'group:'+name,name,line)
        if params.get('shannon_show_band',True):ax.fill_between(x,low,high,color=color,alpha=params.get('shannon_band_alpha',.18),linewidth=0)
    if params.get('shannon_log_depth',False):ax.set_xscale('log')
    ax.set(title='Shannon rarefaction',xlabel='Sequencing depth (reads)',ylabel='Shannon diversity (ln)');ax.grid(axis='y',alpha=.2)
    ax._bioflow_mapping={'x':'depth','y':'shannon','band':['lower','upper'],'interval':'provided bounds, not recomputed'}
