import numpy as np
from .common import required,number,pvalue,series,palette
def draw(rows,fig,ax,params):
    required(rows,'gene','log2fc','pvalue');groups={'up':([],[]),'down':([],[]),'other':([],[])}
    fc=params.get('volcano_fc',1);cutoff=params.get('volcano_p',.05)
    for row in rows:
        x=number(row,'log2fc');p=pvalue(row);key='up' if x>=fc and p<cutoff else 'down' if x<=-fc and p<cutoff else 'other'
        groups[key][0].append(x);groups[key][1].append(-np.log10(p))
    for (key,label),color in zip([('up','上调'),('down','下调'),('other','未达到阈值')],palette(params,3)):
        x,y=groups[key];series(ax,'series:'+key,label,ax.scatter(x,y,color=color,s=params.get('plot_point_size',22),alpha=.8))
    ax.axhline(-np.log10(cutoff),c='#aaa',ls='--');ax.set(xlabel='log2 fold change',ylabel='−log10(P value)',title='Volcano plot')
