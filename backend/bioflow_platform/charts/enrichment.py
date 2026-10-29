"""Enrichment bubbles with optional direction facets and a reserved legend column."""
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.colors import LinearSegmentedColormap,Normalize
from matplotlib.ticker import MaxNLocator
from .common import required,number,pvalue,series


def draw(rows,fig,ax,params):
    rows=[dict(r) for r in rows]
    for row in rows:
        if not str(row.get('ratio','')).strip() and str(row.get('gene_ratio','')).strip():row['ratio']=row['gene_ratio']
    required(rows,'term','ratio','pvalue','count')
    if len(rows)>100:raise ValueError('气泡图最多展示 100 个条目')
    counts=np.array([number(r,'count') for r in rows]);ratios=np.array([number(r,'ratio') for r in rows])
    if min(counts)<=0 or min(ratios)<0 or max(ratios)>1:raise ValueError('count 必须大于零，ratio 必须位于 0–1')
    x_key=params.get('enrichment_x','ratio');size_key=params.get('enrichment_size','count')
    if x_key not in {'ratio','count'} or size_key not in {'ratio','count'}:raise ValueError('不支持的数据映射')
    values={'ratio':ratios,'count':counts};labels={'ratio':'GeneRatio','count':'Count'}
    size_values=values[size_key];maximum=float(max(size_values))
    if maximum<=0:raise ValueError('气泡大小字段必须包含正数')
    low=params.get('enrichment_bubble_min',20);high=params.get('enrichment_bubble_max',320)
    if low>=high:raise ValueError('气泡基础面积必须小于最大面积')
    def area(value):return low+np.asarray(value)/maximum*(high-low)
    ticks=params.get('enrichment_size_ticks')
    if ticks is None:
        if size_key=='ratio':
            ticks=MaxNLocator(nbins=6).tick_values(min(size_values),maximum)
            ticks=[float(t) for t in ticks if t>0 and t>=min(size_values)-1e-9 and t<=maximum+1e-9]
            if not ticks:ticks=[maximum]
        else:ticks=[float(v) for v in np.unique(np.quantile(size_values,[0,.5,1]))]
    if any(t<=0 or t>maximum+1e-9 for t in ticks):raise ValueError(f'大小图例刻度应大于 0 且不超过当前 {labels[size_key]} 最大值 {maximum:g}')
    palette=params.get('enrichment_palette','paper')
    if palette not in {'paper','viridis','RdYlBu_r','YlGnBu','magma','cividis'}:raise ValueError('不支持的色系')
    cmap=LinearSegmentedColormap.from_list('paper_red_purple',['#ff0033','#ef007d','#7700ff']) if palette=='paper' else palette
    color_mode=params.get('enrichment_color','pvalue')
    pvalues=np.array([pvalue(r) for r in rows]);colors=pvalues if color_mode=='pvalue' else -np.log10(pvalues)
    color_title='pvalue' if color_mode=='pvalue' else '−log10(P value)'
    norm=Normalize(float(min(colors)),float(max(colors)))
    facet=params.get('enrichment_facet','none')
    if facet=='direction':
        required(rows,'direction');groups=list(dict.fromkeys(str(r['direction']).strip() for r in rows))
        if set(groups)=={'activated','suppressed'}:groups=['activated','suppressed']
        if len(groups)>4:raise ValueError('direction 分面最多支持 4 组')
    else:groups=[None]
    terms=list(dict.fromkeys(str(r['term']) for r in rows));positions={term:i for i,term in enumerate(terms)}
    outer=fig.add_gridspec(1,2,width_ratios=[1,.21],wspace=.08)
    panels=outer[0,0].subgridspec(2 if facet=='direction' else 1,len(groups),height_ratios=[.055,1] if facet=='direction' else [1],wspace=.035,hspace=0)
    data_axes=[];dots=None
    x_values=values[x_key];span=float(max(x_values)-min(x_values));margin=max(span*.1,float(max(x_values))*.05,.02)
    for i,group in enumerate(groups):
        row_index=1 if facet=='direction' else 0
        if i==0:ax.set_subplotspec(panels[row_index,i]);panel=ax
        else:panel=fig.add_subplot(panels[row_index,i],sharey=ax)
        data_axes.append(panel)
        if facet=='direction':
            header=fig.add_subplot(panels[0,i]);header.set_facecolor('#e4e4e4');header.text(.5,.5,group,ha='center',va='center',transform=header.transAxes);header.set(xticks=[],yticks=[])
            for spine in header.spines.values():spine.set_visible(True);spine.set_color('#aaaaaa')
        indices=[j for j,r in enumerate(rows) if group is None or str(r['direction']).strip()==group]
        dots=panel.scatter(x_values[indices],[positions[str(rows[j]['term'])] for j in indices],s=area(size_values[indices]),c=colors[indices],cmap=cmap,norm=norm,edgecolors='white',linewidths=.45,zorder=3)
        panel.set_yticks(range(len(terms)),terms);panel.set_ylim(len(terms)-.4,-.6)
        panel.set_xlim(float(min(x_values))-margin,float(max(x_values))+margin)
        panel.set_xlabel(params.get('x_label') if params.get('x_label') is not None else labels[x_key])
        panel.set_ylabel((params.get('y_label') or '') if i==0 else '')
        panel.grid(True,color='#ededed',linewidth=.7);panel.set_axisbelow(True)
        for spine in panel.spines.values():spine.set_visible(True);spine.set_color('#b6b6b6')
        if i:panel.tick_params(axis='y',left=False,labelleft=False)
    sidebar=outer[0,1].subgridspec(2,1,height_ratios=[.66,.34],hspace=.1)
    legend_ax=fig.add_subplot(sidebar[0]);legend_ax.set_axis_off()
    handles=[];tick_labels=[]
    for i,value in enumerate(ticks):
        handle=Line2D([],[],marker='o',linestyle='',markerfacecolor='#262626',markeredgecolor='none',markersize=float(np.sqrt(area(value))))
        label=f'{value:.4g}';handles.append(handle);tick_labels.append(label);series(ax,'bubble-size:'+str(i),label,handle)
    size_title=params.get('enrichment_size_title') or labels[size_key]
    legend_ax.legend(handles,tick_labels,title=size_title,loc='center left',frameon=False,labelspacing=1.15,handletextpad=1,borderaxespad=0)
    color_holder=fig.add_subplot(sidebar[1]);color_holder.set_axis_off();cax=color_holder.inset_axes([.08,.15,.24,.7])
    colorbar=fig.colorbar(dots,cax=cax);cax.set_title(color_title,pad=8,loc='left');colorbar.ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
    comparison=params.get('enrichment_comparison','').strip();title=params.get('title')
    heading='\n'.join(v for v in (title,comparison) if v) or 'Functional enrichment'
    fig.suptitle(heading)
    ax._bioflow_title=heading;ax._bioflow_managed_display=True;ax._bioflow_custom_legend=True
    ax._bioflow_mapping={'x':x_key,'y':'term','size':size_key,'color':'pvalue' if color_mode=='pvalue' else '-log10(pvalue)','facet':facet}
    ax._bioflow_legend_config={'title':size_title,'ticks':list(ticks),'layout':'right_stacked','palette':palette,'color_title':color_title}
