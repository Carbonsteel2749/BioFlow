"""Project-owned chart previews and immutable saved versions."""
import asyncio
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import uuid
from fastapi import BackgroundTasks,HTTPException,Request
from fastapi.responses import FileResponse,Response
from . import assets
from .chart_contracts import FigureBody
from .charts.registry import CHARTS,get,VERSION

SLOTS=asyncio.Semaphore(2)

def run_renderer(directory):
    root=assets.ROOT;env=os.environ.copy()
    if os.name=='nt':
        def linux(path):
            path=Path(path).resolve();return '/mnt/'+path.drive[0].lower()+path.as_posix()[2:]
        command=['wsl','-d','Ubuntu','--cd',linux(root),'--','env','PYTHONPATH='+linux(root/'.runtime/linux')+':'+linux(root),
                 'timeout','120s','python3','-m','backend.bioflow_platform.chart_runner',linux(directory)]
    else:
        env['PYTHONPATH']=str(root);command=[sys.executable,'-m','backend.bioflow_platform.chart_runner',str(directory)]
    with (directory/'render.log').open('wb') as log:
        process=subprocess.Popen(command,cwd=root,env=env,stdout=log,stderr=log,start_new_session=os.name!='nt',creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:code=process.wait(timeout=130)
        except subprocess.TimeoutExpired:
            if os.name!='nt':os.killpg(process.pid,signal.SIGKILL)
            else:process.kill()
            process.wait();raise ValueError('绘图超过两分钟，请减少输入规模后重试')
    if code or not (directory/'result.json').is_file():
        message=(directory/'error.txt').read_text(encoding='utf-8') if (directory/'error.txt').is_file() else '绘图环境未就绪或程序已退出，请检查绘图日志'
        raise ValueError(message[:500])
    return json.loads((directory/'result.json').read_text(encoding='utf-8'))

def install_figures(router,get_store,check_project):
    def resolve_source(pid,uid,identifier):
        if identifier.startswith('artifact:'):
            aid=identifier.split(':',1)[1];tids=set(get_store().ids(pid,'task'))&get_store().owned_ids('task',uid)
            for tid in tids:
                record=next((a for a in assets.read_snapshot(tid).get('artifacts',[]) if a['artifact_id']==aid and a['status']=='active'),None)
                if not record:continue
                path=assets.local_output(record.get('path',''))
                if path is None or not path.is_file():path=Path(assets.resolve_catalogue_paths(tid,[dict(record)])[0]['path'])
                if not path.resolve().is_relative_to((assets.analysis_root()/'runtime/outputs'/tid).resolve()):raise HTTPException(409,'来源路径无效')
                return {'id':identifier,'name':record.get('file_name',path.name),'sha256':record['sha256']},path
            raise HTTPException(404,'来源成果不属于当前项目')
        with get_store().connect() as db:row=db.execute("SELECT * FROM assets WHERE id=? AND project_id=? AND user_id=? AND kind IN ('data','results') AND status!='archived'",(identifier,pid,uid)).fetchone()
        if not row:raise HTTPException(404,'请选择当前项目的数据或分析结果表')
        return {'id':row['id'],'name':row['name'],'sha256':row['sha256']},assets.uploaded_path(dict(row),pid,uid)
    def folder(pid,uid,fid):
        base=(assets.ROOT/'runtime/figures'/uid/pid).resolve();target=(base/fid).resolve()
        if not target.is_relative_to(base):raise HTTPException(404,'图表不存在')
        return target
    def owned(pid,fid,request):
        check_project(pid,request)
        with get_store().connect() as db:r=db.execute('SELECT * FROM project_figures WHERE id=? AND project_id=? AND user_id=?',(fid,pid,request.state.user['id'])).fetchone()
        if not r:raise HTTPException(404,'图表不存在')
        return dict(r)
    def public(row):
        try:
            columns=json.loads((folder(row['project_id'],row['user_id'],row['id'])/'request.json').read_text(encoding='utf-8')).get('columns',{})
        except (OSError,ValueError):columns={}
        return {**{k:row[k] for k in ('id','chart','status','parent_id','error','saved','created_at')},'columns':columns,'parameters':json.loads(row['parameters']),'source':json.loads(row['source_json']),'result':json.loads(row['result'])}
    async def execute(pid,uid,fid):
        async with SLOTS:
            from .ability_management import enabled
            with get_store().connect() as db:row=db.execute('SELECT chart FROM project_figures WHERE id=?',(fid,)).fetchone()
            if not enabled(get_store(),'skills','chart:'+row['chart']):
                with get_store().connect() as db:db.execute("UPDATE project_figures SET status='failed',error='该绘图能力已停用' WHERE id=?",(fid,))
                return
            with get_store().connect() as db:db.execute("UPDATE project_figures SET status='running' WHERE id=?",(fid,))
            try:
                result=await asyncio.to_thread(run_renderer,folder(pid,uid,fid))
                with get_store().connect() as db:db.execute("UPDATE project_figures SET status='completed',result=? WHERE id=?",(json.dumps(result,ensure_ascii=False),fid))
            except Exception as error:
                with get_store().connect() as db:db.execute("UPDATE project_figures SET status='failed',error=? WHERE id=?",(str(error)[:500],fid))

    @router.get('/figure-templates')
    def templates(pid:str,request:Request):
        check_project(pid,request);return {'items':CHARTS,'version':VERSION}

    @router.get('/figure-templates/{chart}/example')
    def example(pid:str,chart:str,request:Request):
        check_project(pid,request);entry=get(chart)
        if not entry:raise HTTPException(404,'图型不存在')
        return Response(entry['example'],media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="{chart}-example.csv"'})

    @router.get('/figure-sources/{aid}')
    def source_info(pid:str,aid:str,request:Request):
        check_project(pid,request)
        source,path=resolve_source(pid,request.state.user['id'],aid)
        if path.suffix not in {'.csv','.tsv','.txt'}:raise HTTPException(422,'请选择 CSV 或 TSV 结果表')
        with path.open(encoding='utf-8-sig') as stream:text=stream.read(65536)
        if not text.strip():raise HTTPException(422,'表格内容为空')
        reader=csv.DictReader(io.StringIO(text),delimiter='\t' if '\t' in text.splitlines()[0] else ',')
        return {'columns':reader.fieldnames,'preview':[r for _,r in zip(range(5),reader)]}

    @router.get('/figure-sources')
    def sources(pid:str,request:Request):
        check_project(pid,request);uid=request.state.user['id']
        with get_store().connect() as db:rows=[{'id':r['id'],'name':r['name'],'source':'项目上传','kind':r['kind']} for r in db.execute("SELECT id,name,kind FROM assets WHERE project_id=? AND user_id=? AND kind IN ('data','results') AND status!='archived' ORDER BY rowid DESC",(pid,uid)) if Path(r['name']).suffix.lower() in {'.csv','.tsv','.txt'}]
        tids=set(get_store().ids(pid,'task'))&get_store().owned_ids('task',uid)
        for tid in tids:
            snapshot=assets.read_snapshot(tid)
            for a in snapshot.get('artifacts',[]):
                name=a.get('file_name') or Path(a.get('path','')).name
                if a['status']=='active' and Path(name).suffix.lower() in {'.csv','.tsv','.txt'} and not a.get('artifact_type','').startswith('reads.'):
                    rows.append({'id':'artifact:'+a['artifact_id'],'name':name,'source':snapshot.get('task',{}).get('name',tid),'sample_id':a.get('sample_id'),'kind':'results'})
        return {'items':rows}

    @router.get('/figures')
    def listing(pid:str,request:Request):
        check_project(pid,request)
        with get_store().connect() as db:rows=db.execute('SELECT * FROM project_figures WHERE project_id=? AND user_id=? ORDER BY rowid DESC LIMIT 100',(pid,request.state.user['id'])).fetchall()
        return {'items':[public(dict(r)) for r in rows]}

    @router.post('/figures',status_code=202)
    async def create(pid:str,body:FigureBody,request:Request,background:BackgroundTasks):
        check_project(pid,request);uid=request.state.user['id']
        from .ability_management import enabled
        if not enabled(get_store(),'skills','chart:'+body.chart):raise HTTPException(409,'该绘图能力已停用')
        if not get(body.chart):raise HTTPException(422,'图型未登记')
        with get_store().connect() as db:pending=db.execute("SELECT count(*) FROM project_figures WHERE user_id=? AND status IN ('queued','running')",(uid,)).fetchone()[0]
        if pending>=4:raise HTTPException(429,'最多同时保留四个待处理预览，请等待完成')
        if body.parent_id:
            parent=owned(pid,body.parent_id,request)
            if parent['status']!='completed' or parent['chart']!=body.chart:raise HTTPException(422,'请选择同图型的已完成版本')
            source=json.loads(parent['source_json']);data=(folder(pid,uid,body.parent_id)/'source.csv').read_bytes()
            if hashlib.sha256(data).hexdigest()!=source['sha256']:raise HTTPException(409,'原版本输入快照校验失败')
            old=json.loads((folder(pid,uid,body.parent_id)/'request.json').read_text(encoding='utf-8'));columns=old.get('columns',{})
        else:
            source,path=await asyncio.to_thread(resolve_source,pid,uid,body.source_id)
            if not path.is_file():raise HTTPException(409,'来源文件缺失，请先恢复项目文件')
            if path.suffix.lower() not in {'.csv','.tsv','.txt'} or path.stat().st_size>10*1024**2:raise HTTPException(422,'请提供不超过 10 MB 的 CSV/TSV 表格')
            data=await asyncio.to_thread(path.read_bytes)
            if hashlib.sha256(data).hexdigest()!=source['sha256']:raise HTTPException(409,'来源文件校验失败')
            columns=body.columns
        fid=uuid.uuid4().hex;directory=folder(pid,uid,fid);directory.mkdir(parents=True,exist_ok=False);(directory/'source.csv').write_bytes(data)
        spec={'chart':body.chart,'source':source,'parameters':body.parameters.model_dump(),'columns':columns,'parent_id':body.parent_id,'version':VERSION}
        (directory/'request.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
        with get_store().connect() as db:db.execute('INSERT INTO project_figures(id,project_id,user_id,chart,status,parameters,source_json,parent_id) VALUES(?,?,?,?,?,?,?,?)',
            (fid,pid,uid,body.chart,'queued',body.parameters.model_dump_json(),json.dumps(source,ensure_ascii=False),body.parent_id or None))
        background.add_task(execute,pid,uid,fid)
        return {'id':fid,'status':'queued'}

    @router.get('/figures/{fid}')
    def detail(pid:str,fid:str,request:Request):return public(owned(pid,fid,request))

    @router.post('/figures/{fid}/save')
    def save(pid:str,fid:str,request:Request):
        row=owned(pid,fid,request)
        if row['status']!='completed':raise HTTPException(409,'预览完成后才能保存')
        with get_store().connect() as db:db.execute('UPDATE project_figures SET saved=1 WHERE id=?',(fid,))
        get_store().event(pid,'保存图表版本',{'id':fid,'parent_id':row['parent_id']});return {'ok':True}

    @router.get('/figures/{fid}/file/{extension}')
    def download(pid:str,fid:str,extension:str,request:Request,download:bool=False):
        row=owned(pid,fid,request);result=json.loads(row['result'])
        if extension not in {'png','svg','pdf'} or row['status']!='completed':raise HTTPException(404,'图表文件不存在')
        path=folder(pid,request.state.user['id'],fid)/('figure.'+extension)
        if assets.availability(path,result.get('outputs',{}).get(extension,{}).get('sha256'))[0]!='ready':raise HTTPException(409,'图表文件校验失败')
        return FileResponse(path,media_type={'png':'image/png','svg':'image/svg+xml','pdf':'application/pdf'}[extension],filename='figure.'+extension if download else None,
            headers={'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; sandbox",'X-Content-Type-Options':'nosniff'})
    return create
