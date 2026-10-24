from .common import required,number,series
def draw(rows,fig,ax,params):
    required(rows,'term','count')
    if len(rows)>100:raise ValueError('GO 图最多展示 100 个条目')
    counts=[number(r,'count') for r in rows]
    if min(counts)<0 or any(v!=int(v) for v in counts):raise ValueError('count 必须为非负整数')
    groups={}
    for i,row in enumerate(rows):groups.setdefault(row.get('ontology') or 'GO',[]).append(i)
    if len(groups)>30:raise ValueError('GO 图最多展示 30 个类别')
    for key,indices in groups.items():
        bars=ax.barh(indices,[counts[i] for i in indices]);series(ax,'ontology:'+key,key,bars)
    ax.set_yticks(range(len(rows)),[r['term'] for r in rows]);ax.set(title='GO enrichment',xlabel='Gene count',ylabel='GO term')
