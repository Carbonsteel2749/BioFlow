"""Prepare bounded plotting inputs from owned task outputs; no analysis is rerun."""
import asyncio,csv,hashlib,io,json,math,re,uuid
from collections import defaultdict
from pathlib import Path
from fastapi import BackgroundTasks,HTTPException,Request
from pydantic import BaseModel,Field
from typing import Literal
from . import assets
from .provenance import project_task_ids
from .report_outputs import digest
SLOTS=asyncio.Semaphore(1)
KINDS={'family':'菌科（Kraken计数）','species':'物种（Bracken）','pathway':'通路（MetaCyc）','ko':'功能组（KO）','uniref':'基因家族（UniRef90）'}
TYPES={'family':'taxonomy.kraken_report','species':'taxonomy.species_abundance','pathway':'functional.pathway_abundance','ko':'functional.ko_abundance','uniref':'functional.gene_families'}
MERGED={'species':'species_abundance.tsv','pathway':'pathway_abundance.tsv','uniref':'gene_families.tsv'}
SPECIAL=re.compile(r'^(UNMAPPED|READS_UNMAPPED|UNINTEGRATED|UNGROUPED|UniRef\d+_unknown)(:|$)')

class PrepareBody(BaseModel):
    task_id:str=Field(min_length=1,max_length=100)
    chart:Literal['pca','heatmap','stacked']
    kind:Literal['family','species','pathway','ko','uniref']
    top_n:int=Field(default=50,ge=0,le=2000)


def input_records(tid,kind):
    records=[r for r in assets.read_snapshot(tid).get('artifacts',[]) if r.get('status')=='active' and r.get('artifact_type')==TYPES[kind]]
    merged=next((r for r in records if r.get('file_name')==MERGED.get(kind) and '/tables/by_sample/' not in str(r.get('path','')) and '/tables/' in str(r.get('path',''))),None)
    if merged:return [merged]
    # Original normalized per-sample outputs; report copies never get appended.
    selected={}
    for r in records:
        if not r.get('sample_id') or '/tables/' in str(r.get('path','')):continue
        if r['sample_id'] in selected:raise ValueError('同一样本存在多个有效分析来源，请先核对版本：'+r['sample_id'])
        selected[r['sample_id']]=r
    return [selected[k] for k in sorted(selected)]


def resolve_inputs(tid,kind):
    base=(assets.analysis_root()/'runtime/outputs'/tid).resolve();result=[]
    for r in input_records(tid,kind):
        p=assets.local_output(r.get('path',''))
        if p is None or not p.is_relative_to(base) or not p.is_file():raise ValueError('来源文件未挂载：'+r.get('file_name',''))
        result.append((r,p))
    if not result:raise ValueError('当前任务没有可用于'+KINDS[kind]+'的结果')
    return result


def observations(inputs,kind):
    for record,path in inputs:
        if kind=='family':
            sample=record['sample_id']
            with path.open(encoding='utf-8-sig') as f:
                for line in f:
                    parts=line.rstrip('\r\n').split('\t')
                    if len(parts)>=6 and parts[3]=='F':yield sample,parts[5].strip(),float(parts[1])
        else:
            with path.open(encoding='utf-8-sig',newline='') as f:
                reader=csv.DictReader(f,delimiter='\t')
                for row in reader:
                    sample=row.get('sample_id')
                    feature=row.get('taxonomy') if kind=='species' else row.get('function_id')
                    if not sample or not feature:raise ValueError('结果缺少 sample_id 或特征字段')
                    if kind!='species' and (SPECIAL.match(feature) or '|' in feature):continue
                    if kind!='species' and row.get('analysis_scope')!='community_total':raise ValueError('功能来源必须为 community_total 标准化表')
                    key='read_count_or_estimated_reads' if kind=='species' else 'abundance'
                    yield sample,feature,float(row[key])


def prepare_table(tid,kind,chart,top_n):
    if chart=='stacked' and not 1<=top_n<=19:raise ValueError('堆叠图保留分类数为 1–19，其余合并为 Other')
    if chart=='heatmap' and not 2<=top_n<=150:raise ValueError('热图请选择 2–150 个特征')
    inputs=resolve_inputs(tid,kind);totals=defaultdict(float);samples=set();count=0
    for sample,feature,value in observations(inputs,kind):
        if not math.isfinite(value) or value<0:raise ValueError('结果包含无效或负数丰度')
        totals[feature]+=value;samples.add(sample);count+=1
    ordered=sorted((f for f,v in totals.items() if v>0),key=lambda f:(-totals[f],f))
    selected=ordered[:top_n] if top_n else ordered
    if len(samples)<2 or len(selected)<(1 if chart=='stacked' else 2):raise ValueError('至少需要两个样本、两个非零特征')
    if chart!='stacked' and len(selected)>2000:raise ValueError('特征超过 2000，请设置保留特征数')
    if len(samples)>150 and chart=='heatmap':raise ValueError('热图最多 150 个样本')
    values={s:defaultdict(float) for s in samples};keep=set(selected);has_other=False
    for sample,feature,value in observations(inputs,kind):
        if feature in keep:values[sample][feature]+=value
        elif chart=='stacked':values[sample]['Other taxa']+=value;has_other=has_other or value>0
    if chart=='stacked' and has_other:
        if 'Other taxa' in keep:raise ValueError('特征名与汇总标签 Other taxa 冲突')
        selected.append('Other taxa')
    out=io.StringIO(newline='');w=csv.writer(out,delimiter='\t');sample_ids=sorted(samples)
    if chart=='stacked':
        w.writerow(['sample','taxon','abundance'])
        for sample in sample_ids:
            for feature in selected:w.writerow([sample,feature,format(values[sample][feature],'.12g')])
        mapping={};params={'stacked_level':'sample','stacked_top_n':len(selected),'stacked_normalize':True,'series_palette':'Set2'}
    else:
        w.writerow(['sample',*selected])
        for sample in sample_ids:w.writerow([sample,*[format(values[sample][f],'.12g') for f in selected]])
        mapping={};params={'matrix_format':'wide','matrix_orientation':'samples_rows','pca_transform':'clr','pca_pseudocount':.5 if kind in {'family','species'} else .000001,'pca_show_labels':True,'series_palette':'Set2'}
        if chart=='heatmap':params.update(heatmap_log='log1p',heatmap_scale='column',heatmap_palette='RdBu_r')
    data=out.getvalue().encode()
    if len(data)>10*1024**2:raise ValueError('整理后的数据仍超过 10 MB，请减少保留特征数')
    provenance=[{'id':'artifact:'+r['artifact_id'],'relative_path':assets.relative_reference(str(p),'/runtime/outputs/'),'sha256':digest(p)} for r,p in inputs]
    return data,{'sample_count':len(samples),'feature_count':len(selected),'source_rows':count,'sources':provenance,'columns':mapping,'recommended_parameters':params,'kind':kind,'chart':chart,'top_n':top_n,'note':'菌科来自 Kraken clade counts；不是科级 Bracken 重估或三工具对照' if kind=='family' else '标准化结果的全深度作图输入；不包含分深度重复实验','excluded_special':kind not in {'family','species'}}


def describe_source(record):
    name=record.get('file_name') or record.get('name','');path=str(record.get('path') or record.get('relative_path',''));meta=record.get('metadata',{})
    if isinstance(meta,str):meta=json.loads(meta)
    if meta.get('origin')=='plot_preparation':return {'label':'作图就绪 · '+name,'recommended':True,'recommended_parameters':meta.get('recommended_parameters',{}),'columns':meta.get('columns',{}),'note':meta.get('note','')}
    cohort='/tables/' in path and '/by_sample/' not in path
    labels={'species_abundance.tsv':'物种丰度汇总','gene_families.tsv':'UniRef90 基因家族汇总','pathway_abundance.tsv':'MetaCyc 通路丰度汇总','read_counts.tsv':'质控计数汇总'}
    if cohort and name in labels:return {'label':labels[name]+' · '+name,'recommended':True}
    return {'label':name+(' · '+record['sample_id'] if record.get('sample_id') else ' · 报告副本' if '/tables/' in path else ''),'recommended':not str(record.get('id','')).startswith('artifact:') and meta.get('chart_role')!='groups'}


def install_preparation(router,get_store,check_project,files):
    with get_store().connect() as db:db.execute("CREATE TABLE IF NOT EXISTS plot_preparations(id TEXT PRIMARY KEY,project_id TEXT,user_id TEXT,task_id TEXT,status TEXT,body TEXT,result TEXT DEFAULT '{}',error TEXT DEFAULT '')")
    def scope(pid,tid,request):
        check_project(pid,request)
        if tid not in project_task_ids(get_store(),pid,request.state.user['id']):raise HTTPException(404,'任务不属于当前项目')
    async def sync(pid,tid,request):
        scope(pid,tid,request)
        if files:
            from .catalogue import sync_task_snapshot
            await sync_task_snapshot(files,get_store(),pid,request.state.user['id'],tid)
    @router.get('/plot-inputs')
    async def available(pid:str,task_id:str,request:Request):
        await sync(pid,task_id,request)
        return {'items':[{'kind':k,'name':v,'available':bool(input_records(task_id,k))} for k,v in KINDS.items()],'shannon_note':'本任务缺少分深度重复抽样结果。完整 Shannon 曲线需要 depth/shannon/lower/upper 表；内置示例仅演示格式。'}
    async def execute(jid,pid,uid,body):
        async with SLOTS:
            store=get_store()
            with store.connect() as db:db.execute("UPDATE plot_preparations SET status='running' WHERE id=?",(jid,))
            try:
                data,meta=await asyncio.to_thread(prepare_table,body.task_id,body.kind,body.chart,body.top_n)
                aid=uuid.uuid4().hex;name=f"{body.kind}_{body.chart}_top{body.top_n or 'all'}.tsv";folder=assets.ROOT/'runtime/assets'/uid/pid;folder.mkdir(parents=True,exist_ok=True);path=folder/(aid+'.tsv');path.write_bytes(data)
                meta.update(origin='plot_preparation',task_id=body.task_id,parent_refs=[s['id'] for s in meta['sources']])
                with store.connect() as db:
                    db.execute('INSERT INTO assets(id,project_id,user_id,name,kind,path,size_bytes,sha256,status,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)',(aid,pid,uid,name,'results',path.relative_to(assets.ROOT/'runtime/assets').as_posix(),len(data),hashlib.sha256(data).hexdigest(),'ready',json.dumps(meta,ensure_ascii=False)))
                    db.execute("UPDATE plot_preparations SET status='completed',result=? WHERE id=?",(json.dumps({'id':aid,'name':name,**meta},ensure_ascii=False),jid))
                store.event(pid,'整理作图输入',{'id':aid,'task_id':body.task_id,'kind':body.kind,'chart':body.chart})
            except Exception as e:
                with store.connect() as db:db.execute("UPDATE plot_preparations SET status='failed',error=? WHERE id=?",(str(e)[:500],jid))
    @router.post('/plot-inputs',status_code=202)
    async def start(pid:str,body:PrepareBody,request:Request,background:BackgroundTasks):
        await sync(pid,body.task_id,request);uid=request.state.user['id'];store=get_store()
        with store.connect() as db:
            if db.execute("SELECT count(*) FROM plot_preparations WHERE user_id=? AND status IN ('queued','running')",(uid,)).fetchone()[0]>=2:raise HTTPException(429,'已有两个整理任务，请等待完成')
            jid=uuid.uuid4().hex;db.execute("INSERT INTO plot_preparations(id,project_id,user_id,task_id,status,body) VALUES(?,?,?,?,'queued',?)",(jid,pid,uid,body.task_id,body.model_dump_json()))
        background.add_task(execute,jid,pid,uid,body);return {'id':jid,'status':'queued'}
    @router.get('/plot-inputs/{jid}')
    def status(pid:str,jid:str,request:Request):
        check_project(pid,request)
        with get_store().connect() as db:r=db.execute('SELECT * FROM plot_preparations WHERE id=? AND project_id=? AND user_id=?',(jid,pid,request.state.user['id'])).fetchone()
        if not r:raise HTTPException(404,'整理任务不存在')
        return {'id':r['id'],'status':r['status'],'result':json.loads(r['result']),'error':r['error']}
