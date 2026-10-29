"""Render a fixed chart program in the existing numeric Python environment."""
import csv
import hashlib
import importlib
import io
import json
from pathlib import Path
import sys

def render(directory):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from .charts.registry import get,VERSION
    directory=Path(directory);spec=json.loads((directory/'request.json').read_text(encoding='utf-8'));source=directory/'source.csv'
    content=source.read_bytes()
    if hashlib.sha256(content).hexdigest()!=spec['source']['sha256']:raise ValueError('绘图快照校验失败')
    text=content.decode('utf-8-sig');reader=csv.DictReader(io.StringIO(text),delimiter='\t' if '\t' in text.splitlines()[0] else ',')
    if not reader.fieldnames or any(not k.strip() for k in reader.fieldnames) or len(reader.fieldnames)!=len({k.strip() for k in reader.fieldnames}):raise ValueError('列名为空或重复')
    mapping=spec.get('columns',{});rows=[]
    for row in reader:
        if None in row or any(value is None for value in row.values()):raise ValueError('表格行列数量不一致')
        item={k.strip():str(v).strip() for k,v in row.items()}
        for canonical,original in mapping.items():
            if original not in row:raise ValueError('映射列不存在：'+original)
            item[canonical]=str(row[original]).strip()
            if original.strip()!=canonical and original.strip() not in mapping:item.pop(original.strip(),None)
        rows.append(item)
        if len(rows)>50000:raise ValueError('绘图预览最多支持 50000 行，请先筛选结果')
    if not rows:raise ValueError('输入表没有数据')
    entry=get(spec['chart'])
    if not entry:raise ValueError('图型未登记')
    plt.rcParams.update({'svg.fonttype':'none','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    for font in ('/mnt/c/Windows/Fonts/msyh.ttc','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc','/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc','/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'):
        if Path(font).is_file():
            font_manager.fontManager.addfont(font);plt.rcParams['font.family']=font_manager.FontProperties(fname=font).get_name();break
    params=spec['parameters']
    plt.rcParams['font.size']=params.get('font_size',10)
    fig,ax=plt.subplots(figsize=(params.get('width',9),params.get('height',6)),layout='constrained')
    params=spec['parameters'];module=importlib.import_module('.charts.'+entry['id'],package='backend.bioflow_platform')
    try:
        module.draw(rows,fig,ax,params)
        series=getattr(ax,'_bioflow_series',[]);ids=[r[0] for r in series]
        if not getattr(ax,'_bioflow_custom_legend',False) and (any(k not in ids for k in params.get('legend_labels',{})) or any(k not in ids for k in params.get('legend_order',[]))):raise ValueError('图例标识与原图不一致，请重新打开编辑器')
        defaults={'title':getattr(ax,'_bioflow_title',ax.get_title()),'x_label':ax.get_xlabel(),'y_label':ax.get_ylabel()}
        for key,setter in [('title',ax.set_title),('x_label',ax.set_xlabel),('y_label',ax.set_ylabel)]:
            if params.get(key) is not None and not getattr(ax,'_bioflow_managed_display',False):setter(params[key])
        if series and not getattr(ax,'_bioflow_custom_legend',False):
            lookup={r[0]:r for r in series};order=params.get('legend_order',[])+[k for k in ids if k not in params.get('legend_order',[])]
            arranged=[lookup[k] for k in order];labels=[params.get('legend_labels',{}).get(k,label) for k,label,_ in arranged]
            position=params.get('legend_position','best');kwargs={'loc':position}
            if position=='outside_right':kwargs={'loc':'upper left','bbox_to_anchor':(1.02,1)}
            elif position=='outside_bottom':kwargs={'loc':'upper center','bbox_to_anchor':(.5,-.18),'ncol':min(4,len(arranged))}
            ax.legend([r[2] for r in arranged],labels,**kwargs)
        for artist in fig.findobj():
            if hasattr(artist,'set_parse_math'):artist.set_parse_math(False)
            if hasattr(artist,'set_fontsize'):artist.set_fontsize(params.get('font_size',10))
        outputs={}
        for extension in ('png','svg','pdf'):
            path=directory/('figure.'+extension);fig.savefig(path,dpi=180,bbox_inches='tight');outputs[extension]={'name':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'size_bytes':path.stat().st_size}
        result={'outputs':outputs,'legend':[{'id':k,'label':label} for k,label,_ in series],'defaults':defaults,'template_version':VERSION,
            'renderer_sha256':hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),'source_sha256':spec['source']['sha256'],'rows':len(rows),
            'legend_config':getattr(ax,'_bioflow_legend_config',{}),'data_mapping':getattr(ax,'_bioflow_mapping',{}),'canvas':{'width':fig.get_figwidth(),'height':fig.get_figheight(),'font_size':params.get('font_size',10)}}
        (directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:plt.close(fig)

if __name__=='__main__':
    try:render(sys.argv[1])
    except Exception as error:
        (Path(sys.argv[1])/'error.txt').write_text(str(error),encoding='utf-8');raise
