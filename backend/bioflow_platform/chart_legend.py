"""Legend presentation is independent from the underlying series and data."""
from matplotlib.patches import Patch

def configure(fig,ax,params):
    series=list(getattr(ax,'_bioflow_series',[]))
    for entry in params.get('extra_legends',[]):series.append((entry['id'],entry['label'],Patch(facecolor=entry['color'],edgecolor='none')))
    ticks=getattr(ax,'_bioflow_legend_config',{}).get('ticks',[])
    series=[(key,f'{ticks[int(key.split(":")[-1])]:.4g}' if key.startswith('bubble-size:') and int(key.split(':')[-1])<len(ticks) else label,artist) for key,label,artist in series]
    ids=[s[0] for s in series]
    if len(ids)!=len(set(ids)):raise ValueError('图例标识重复')
    if not getattr(ax,'_bioflow_custom_legend',False) and any(k not in ids for k in [*params.get('legend_labels',{}),*params.get('legend_order',[]),*params.get('legend_hidden',[])]):raise ValueError('图例标识与原图不一致，请重新打开编辑器')
    ax._bioflow_series=series
    custom=getattr(ax,'_bioflow_custom_legend',False);target=next((a for a in fig.axes if a.get_legend()),ax) if custom else ax
    old=target.get_legend();title=old.get_title().get_text() if old else ''
    by_id={s[0]:s for s in series};order=list(dict.fromkeys(params.get('legend_order',[])+ids));hidden=set(params.get('legend_hidden',[]));arranged=[by_id[k] for k in order if k in by_id and k not in hidden]
    if old:old.remove()
    if not arranged:return series
    for key,label,artist in arranged:
        color=params.get('series_colors',{}).get(key)
        if color:
            if hasattr(artist,'set_markerfacecolor'):artist.set_markerfacecolor(color)
            elif hasattr(artist,'set_facecolor'):artist.set_facecolor(color)
            elif hasattr(artist,'patches'):
                for patch in artist.patches:patch.set_facecolor(color)
            elif hasattr(artist,'set_color'):artist.set_color(color)
    kwargs={'loc':'center left','title':title,'frameon':False} if custom else {'loc':params.get('legend_position','best')}
    if not custom:
        if kwargs['loc']=='outside_right':kwargs={'loc':'upper left','bbox_to_anchor':(1.02,1)}
        elif kwargs['loc']=='outside_bottom':kwargs={'loc':'upper center','bbox_to_anchor':(.5,-.18),'ncol':min(4,len(arranged))}
    target.legend([s[2] for s in arranged],[params.get('legend_labels',{}).get(s[0],s[1]) for s in arranged],**kwargs)
    return series
