"""Task-scoped uploads and bounded table previews for the chart workbench."""
import csv,hashlib,io,json,uuid
from pathlib import Path
from fastapi import HTTPException,Request,UploadFile,File,Form
from fastapi.responses import Response
from . import assets
from .provenance import project_task_ids
from .chart_examples import example_files

def check_source_task(store,pid,uid,tid,source):
    if source.get('simulated'):return
    if source.get('task_id')==tid:return
    with store.connect() as db:
        row=db.execute('SELECT metadata FROM assets WHERE id=? AND project_id=? AND user_id=?',(source['id'],pid,uid)).fetchone()
        links={r[0] for r in db.execute('SELECT task_id FROM asset_task_links WHERE project_id=? AND user_id=? AND asset_id=?',(pid,uid,source['id']))}
    if row and json.loads(row[0]).get('task_id')==tid:return
    if tid in links:return
    raise HTTPException(422,'来源表不属于当前任务，请切换任务或将数据上传到当前任务')

def install_workbench(router,get_store,check_project,resolve_source,folder,owned):
    @router.get('/chart-capabilities')
    def capabilities(pid:str,request:Request):
        check_project(pid,request)
        return {'version':2,'editor':'svg','task_scoped':True,'uploads':['csv','tsv','txt']}

    @router.get('/chart-examples/{chart}/{role}')
    def examples(pid:str,chart:str,role:str,request:Request):
        check_project(pid,request)
        if role not in {'matrix','groups'}:raise HTTPException(404,'示例不存在')
        try:text=example_files(chart)[role=='groups']
        except ValueError as e:raise HTTPException(404,str(e))
        return Response(text,media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="{chart}-{role}.csv"'})

    @router.post('/chart-inputs')
    async def upload(pid:str,request:Request,file:UploadFile=File(...),task_id:str=Form(''),role:str=Form('matrix')):
        check_project(pid,request);store=get_store();uid=request.state.user['id']
        if not task_id or task_id not in project_task_ids(store,pid,uid):raise HTTPException(422,'请先选择当前任务')
        if role not in {'matrix','groups'}:raise HTTPException(422,'文件用途无效')
        name=Path((file.filename or '').replace('\\','/')).name
        if Path(name).suffix.lower() not in {'.csv','.tsv','.txt'}:raise HTTPException(422,'请上传 CSV、TSV 或制表符 TXT 文件')
        data=await file.read(10*1024**2+1)
        if not data or len(data)>10*1024**2:raise HTTPException(422,'请输入不超过 10 MB 的表格')
        try:
            text=data.decode('utf-8-sig');reader=csv.reader(io.StringIO(text),delimiter='\t' if '\t' in text.splitlines()[0] else ',');headers=next(reader)
            if len(headers)<2 or len(headers)!=len(set(headers)) or any(not h.strip() for h in headers):raise ValueError('表头为空、重复或少于两列')
            for i,row in enumerate(reader):
                if row and len(row)!=len(headers):raise ValueError(f'第 {i+2} 行列数与表头不一致')
                if i>50000:raise ValueError('绘图输入最多 50000 行，请先筛选或聚合')
        except (UnicodeError,ValueError,StopIteration,IndexError) as e:raise HTTPException(422,'表格解析失败：'+str(e))
        aid=uuid.uuid4().hex;path=assets.ROOT/'runtime/assets'/uid/pid/(aid+Path(name).suffix.lower());path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        metadata={'task_id':task_id,'chart_role':role,'origin':'chart_upload'}
        with store.connect() as db:db.execute('INSERT INTO assets(id,project_id,user_id,name,kind,path,size_bytes,sha256,status,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)',(aid,pid,uid,name,'data',path.relative_to(assets.ROOT/'runtime/assets').as_posix(),len(data),hashlib.sha256(data).hexdigest(),'ready',json.dumps(metadata)))
        return {'id':aid,'name':name,'task_id':task_id,'role':role}

    @router.get('/chart-table')
    def table(pid:str,request:Request,source_id:str='',offset:int=0,limit:int=30,task_id:str='',figure_id:str='',role:str='matrix'):
        check_project(pid,request);uid=request.state.user['id']
        if role not in {'matrix','groups'}:raise HTTPException(422,'表格类型无效')
        if figure_id:
            figure=owned(pid,figure_id,request);record=json.loads(figure['source_json'])
            source=record if role=='matrix' else record.get('grouping',{})
            path=folder(pid,uid,figure_id)/('source.csv' if role=='matrix' else 'grouping.csv')
            if not path.is_file():raise HTTPException(404,'此版本没有对应表格')
            if task_id and record.get('task_id')!=task_id:raise HTTPException(422,'图表不属于当前任务')
        else:source,path=resolve_source(pid,uid,source_id)
        if task_id:
            if task_id not in project_task_ids(get_store(),pid,uid):raise HTTPException(404,'任务不属于当前项目')
            if not figure_id:check_source_task(get_store(),pid,uid,task_id,source)
        if path.suffix.lower() not in {'.csv','.tsv','.txt'}:raise HTTPException(422,'请选择文本表格')
        limit=max(1,min(limit,100));offset=max(0,min(offset,50000))
        with path.open(encoding='utf-8-sig',newline='') as f:
            first=f.readline(65536);f.seek(0);reader=csv.DictReader(f,delimiter='\t' if '\t' in first else ',');rows=[];more=False
            if not reader.fieldnames:raise HTTPException(422,'表格为空')
            for i,row in enumerate(reader):
                if i<offset:continue
                if len(rows)==limit:more=True;break
                rows.append(row)
        return {'columns':reader.fieldnames,'rows':rows,'offset':offset,'more':more,'source':source,'editable':path.stat().st_size<=10*1024**2}
