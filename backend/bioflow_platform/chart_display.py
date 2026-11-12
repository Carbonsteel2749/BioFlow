"""Shared data preparation and SVG-editable display semantics."""
import csv,hashlib,io
import numpy as np

def prepare(rows,spec,directory):
    p=spec['parameters']
    if p.get('matrix_format')=='long' and spec['chart'] in {'pca','heatmap'}:
        feature=p.get('feature_column','taxonomy');value=p.get('value_column','abundance');samples={};totals={}
        for r in rows:
            if not r.get('sample') or not r.get(feature):raise ValueError('长表缺少样本或特征列，请检查列名对应')
            try:v=float(r[value])
            except (KeyError,ValueError):raise ValueError('长表丰度列无法读取：'+value)
            if not np.isfinite(v) or v<0:raise ValueError('丰度必须为有限非负数')
            name=r[feature];sample=r['sample'];record=samples.setdefault(sample,{'sample':sample})
            if 'group' in r:record['group']=r['group']
            record[name]=record.get(name,0)+v;totals[name]=totals.get(name,0)+v
        names=sorted(totals,key=totals.get,reverse=True)[:p.get('matrix_top_n',50)]
        rows=[{'sample':sample,**({'group':r['group']} if 'group' in r else {}),**{name:r.get(name,0) for name in names}} for sample,r in samples.items()]
    if p.get('matrix_orientation')=='samples_columns' and spec['chart'] in {'pca','heatmap'}:
        first=list(rows[0])[0];names=[r[first] for r in rows]
        if len(names)!=len(set(names)):raise ValueError('特征名称重复')
        rows=[{'sample':sample,**{name:r[sample] for name,r in zip(names,rows)}} for sample in rows[0] if sample!=first]
    if (directory/'grouping.csv').is_file():
        data=(directory/'grouping.csv').read_bytes()
        if hashlib.sha256(data).hexdigest()!=spec.get('grouping_sha256'):raise ValueError('分组快照校验失败')
        text=data.decode('utf-8-sig');groups=list(csv.DictReader(io.StringIO(text),delimiter='\t' if '\t' in text.splitlines()[0] else ','))
        sample=p.get('sample_column','sample');group=p.get('group_column','group');lookup={}
        for row in groups:
            if not row.get(sample) or not row.get(group):raise ValueError('分组信息缺少样本或分组列：'+sample+' / '+group)
            if row[sample] in lookup:raise ValueError('分组信息样本重复：'+row[sample])
            lookup[row[sample]]=row[group]
        missing=sorted({r.get('sample','') for r in rows}-lookup.keys())
        if missing:raise ValueError('以下样本没有匹配到分组：'+', '.join(missing[:12]))
        for row in rows:row['group']=lookup[row['sample']]
    return rows

def display(fig,ax,p):
    for a in fig.axes:
        a.title.set_fontsize(p.get('title_size',16));a.title.set_visible(p.get('show_title',True))
        a.xaxis.label.set_fontsize(p.get('axis_title_size',12));a.yaxis.label.set_fontsize(p.get('axis_title_size',12))
        a.tick_params(axis='x',labelsize=p.get('x_tick_size',10));a.tick_params(axis='y',labelsize=p.get('y_tick_size',10))
        a.grid(p.get('show_grid',False),alpha=.2) if p.get('show_grid',False) else a.grid(False)
        a.set_axisbelow(True)
        legend=a.get_legend()
        if legend:
            legend.set_visible(p.get('show_legend',True));legend.set_gid('bf-legend');legend.get_title().set_fontsize(p.get('legend_size',10))
            for i,t in enumerate(legend.get_texts()):t.set_fontsize(p.get('legend_size',10));t.set_gid('bf-legend-text-'+str(i))
            if p.get('legend_x') is not None and p.get('legend_y') is not None:legend.set_bbox_to_anchor((p['legend_x'],p['legend_y']),transform=ax.transAxes)
    ax.title.set_gid('bf-title');ax.xaxis.label.set_gid('bf-x-label');ax.yaxis.label.set_gid('bf-y-label')
    # Free-position titles are figure text, so their exact coordinates survive every export.
    managed=getattr(fig,'_suptitle',None)
    if managed is not None:
        ax.title.set_gid(None);managed.set_gid('bf-title');managed.set_fontsize(p.get('title_size',16));managed.set_visible(p.get('show_title',True))
    title=managed.get_text() if managed is not None else ax.get_title()
    if p.get('title_x',.5)!=.5 or p.get('title_y',1.02)!=1.02:
        ax.set_title('')
        if managed is not None:managed.set_visible(False);managed.set_gid(None)
        t=ax.text(p.get('title_x',.5),p.get('title_y',1.02),title,transform=ax.transAxes,ha='center',fontsize=p.get('title_size',16),visible=p.get('show_title',True));t.set_gid('bf-title')
    ax.set_gid('bf-main-axis');ax.patch.set_gid('bf-plot-area')
    # Explicit colors identify actual renderer series, not only their legend labels.
    for key,label,artist in getattr(ax,'_bioflow_series',[]):
        color=p.get('series_colors',{}).get(key)
        if color:
            if hasattr(artist,'_bioflow_band'):artist._bioflow_band.set_facecolor(color)
            if hasattr(artist,'patches'):
                for patch in artist.patches:patch.set_facecolor(color)
            elif hasattr(artist,'set_color'):artist.set_color(color)
            elif hasattr(artist,'set_facecolor'):artist.set_facecolor(color)
    legend=ax.get_legend()
    if legend:
        handles=getattr(legend,'legend_handles',getattr(legend,'legendHandles',[]))
        all_series=getattr(ax,'_bioflow_series',[]);by_id={s[0]:s for s in all_series};order=p.get('legend_order',[])+[s[0] for s in all_series if s[0] not in p.get('legend_order',[])]
        for (key,_,_),handle in zip([by_id[k] for k in order],handles):
            color=p.get('series_colors',{}).get(key)
            if color and hasattr(handle,'set_color'):handle.set_color(color)
            elif color and hasattr(handle,'set_facecolor'):handle.set_facecolor(color)
