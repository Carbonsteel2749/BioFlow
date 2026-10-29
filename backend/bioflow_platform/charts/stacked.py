from collections import OrderedDict
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
from .common import required,number,series

def draw(rows,fig,ax,params):
    required(rows,'sample','taxon','abundance')
    samples=OrderedDict();taxa=OrderedDict();seen=set();sample_groups={}
    for row in rows:
        sample=row['sample'];taxon=row['taxon'];value=number(row,'abundance');group=row.get('group') or 'All samples'
        if value<0:raise ValueError('abundance 不能为负数')
        if (sample,taxon) in seen:raise ValueError('同一样本的 taxon 出现重复，请先合并或检查数据')
        if sample in sample_groups and sample_groups[sample]!=group:raise ValueError('同一样本的 group 不一致')
        seen.add((sample,taxon));sample_groups[sample]=group;samples.setdefault(sample,{})[taxon]=value;taxa[taxon]=None
    if len(samples)>200 or len(taxa)>500:raise ValueError('堆叠图最多支持 200 个样本、500 个分类')
    labels=list(samples);taxon_names=list(taxa);matrix=np.array([[samples[s].get(t,0) for t in taxon_names] for s in labels]);totals=matrix.sum(axis=1)
    if np.any(totals<=0):raise ValueError('每个样本的 abundance 总和必须大于零')
    normalized=params.get('stacked_normalize',True)
    if normalized:matrix=matrix/totals[:,None]
    level=params.get('stacked_level','group')
    if level=='group':
        groups=list(dict.fromkeys(sample_groups.values()));matrix=np.stack([matrix[[sample_groups[s]==g for s in labels]].mean(axis=0) for g in groups]);labels=groups
    elif level!='sample':raise ValueError('不支持的汇总方式')
    order=np.argsort(-matrix.mean(axis=0),kind='stable');top=params.get('stacked_top_n',10);indices=order[:top];values=matrix[:,indices];names=[taxon_names[i] for i in indices]
    if len(order)>top:
        values=np.column_stack([values,matrix[:,order[top:]].sum(axis=1)]);names.append('Other taxa (remaining)')
    colors=plt.get_cmap(params.get('series_palette','tab20'))(np.linspace(0,1,len(names)))
    bottom=np.zeros(len(labels));x=np.arange(len(labels))
    for i,name in enumerate(names):
        bars=ax.bar(x,values[:,i],bottom=bottom,width=.8,color=colors[i],linewidth=0);bottom+=values[:,i];series(ax,'taxon:'+name,name,bars)
    ax.set_xticks(x,labels,rotation=60,ha='right');ax.set(title='Microbial composition',xlabel='Group' if level=='group' else 'Sample',ylabel='Relative abundance' if normalized else 'Mean abundance' if level=='group' else 'Abundance')
    if normalized:ax.yaxis.set_major_formatter(PercentFormatter(1));ax.set_ylim(0,1)
    ax._bioflow_mapping={'x':level,'stack':'taxon','y':'abundance','normalize':normalized,'aggregation':'equal-weight sample mean' if level=='group' else 'none'}
