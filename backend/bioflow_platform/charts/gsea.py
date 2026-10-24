from .common import required,number
def draw(rows,fig,ax,params):
    required(rows,'position','es');x=[number(r,'position') for r in rows];y=[number(r,'es') for r in rows]
    if len(x)<2 or any(b<=a for a,b in zip(x,x[1:])):raise ValueError('GSEA position 必须严格递增，至少两条记录')
    ax.plot(x,y,color='#2b8056',lw=2);ax.axhline(0,color='#999',lw=.8)
    hits=[number(r,'position') for r in rows if r.get('hit') in {'1','true','True'}]
    if hits:ax.plot(hits,[min(y)-max(max(y)-min(y),.1)*.1]*len(hits),'|',color='#222')
    ax.set(title='GSEA · imported running enrichment score',xlabel='Ranked position',ylabel='Running enrichment score')
