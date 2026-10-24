import numpy as np
from .common import required,number,pvalue,series
def draw(rows,fig,ax,params):
    required(rows,'gene','log2fc','pvalue');groups={'up':([],[]),'down':([],[]),'other':([],[])}
    for row in rows:
        x=number(row,'log2fc');p=pvalue(row);key='up' if x>=1 and p<.05 else 'down' if x<=-1 and p<.05 else 'other'
        groups[key][0].append(x);groups[key][1].append(-np.log10(p))
    for key,label,color in [('up','上调','#b94d49'),('down','下调','#417caa'),('other','未达到阈值','#9ca69e')]:
        x,y=groups[key];series(ax,'series:'+key,label,ax.scatter(x,y,c=color,s=22,alpha=.8))
    ax.axhline(-np.log10(.05),c='#aaa',ls='--');ax.set(xlabel='log2 fold change',ylabel='−log10(P value)',title='Volcano plot')
