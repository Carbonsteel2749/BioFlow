"""Lazy personal-paper storage and task-scoped logical file folders."""
import asyncio,csv,hashlib,io,json,mimetypes,uuid
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import File,HTTPException,Request,UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field
from typing import Literal
from . import assets
from .provenance import project_task_ids

Kind=Literal['data','results','charts','drafts']
class FolderBody(BaseModel):
 task_id:str
 kind:Kind
 name:str=Field(min_length=1,max_length=100)
 parent_id:str=''
class MoveBody(BaseModel):
 task_id:str
 kind:Kind
 folder_id:str=''
 asset_ids:list[str]=Field(min_length=1,max_length=2000)
class ReferenceBody(BaseModel):
 url:str=Field(min_length=1,max_length=2000)
 title:str=Field(default='',max_length=500)

def automatic_folders(store,pid,uid,tid,items):
 """Stable process identities; manual assignments always take priority."""
 labels={'WF_QC':'质量控制','WF_FASTQC':'质量检查（FastQC）','WF_FASTP':'质量过滤','WF_HOST':'去宿主','WF_TAXONOMY':'物种分类','WF_FUNCTIONAL_ANNOTATION':'功能注释','WF_REPORT':'汇总报告'}
 plots={'pca':'PCA','stacked':'堆叠柱状图','heatmap':'聚类热图','shannon':'Shannon曲线'}
 with store.connect() as db:
  for row in items:
   if row['kind'] not in {'data','results','charts','drafts'}:continue
   path=str(row.get('relative_path','')).split('/');process='';name=''
   if row.get('storage_root')=='analysis_outputs' and 'nodes' in path:
    i=path.index('nodes');process=path[i+1] if len(path)>i+1 else ''
    base=process.rsplit('_',1)[0];name=labels.get(base,base.removeprefix('WF_').replace('_',' '))
   elif row.get('storage_root')=='analysis_inputs':process='analysis-inputs';name='分析输入'
   elif row.get('metadata',{}).get('origin')=='plot_preparation':process='plot-inputs';name='作图输入'
   elif row.get('figure_id'):
    chart=row.get('processing',{}).get('chart','other');process='plot:'+chart;name='绘图 · '+plots.get(chart,chart)
   elif row['id'].startswith('archive:'):process='deliverables';name='交付包'
   if not process:continue
   fid=uuid.uuid5(uuid.NAMESPACE_URL,':'.join((pid,uid,tid,row['kind'],process))).hex
   db.execute('INSERT OR IGNORE INTO library_folders VALUES(?,?,?,?,?,?,?)',(fid,pid,uid,tid,row['kind'],name,''));db.execute('UPDATE library_folders SET name=? WHERE id=?',(name,fid));row['_automatic_folder']=fid

def install(app,router,get_store,check_project,files):
 with get_store().connect() as db:
  db.execute('CREATE TABLE IF NOT EXISTS library_folders(id TEXT PRIMARY KEY,project_id TEXT,user_id TEXT,task_id TEXT,kind TEXT,name TEXT,parent_id TEXT)')
  db.execute('CREATE TABLE IF NOT EXISTS library_members(project_id TEXT,user_id TEXT,task_id TEXT,asset_id TEXT,folder_id TEXT,PRIMARY KEY(project_id,user_id,task_id,asset_id))')
 def scope(pid,tid,request):
  check_project(pid,request)
  if tid not in project_task_ids(get_store(),pid,request.state.user['id']):raise HTTPException(404,'任务不属于当前项目')
 def folder(pid,uid,tid,kind,fid):
  if not fid:return None
  with get_store().connect() as db:r=db.execute('SELECT * FROM library_folders WHERE id=? AND project_id=? AND user_id=? AND task_id=? AND kind=?',(fid,pid,uid,tid,kind)).fetchone()
  if not r:raise HTTPException(404,'当前分类中没有该文件夹')
  return dict(r)
 async def rows(pid,tid,request):
  scope(pid,tid,request)
  if hasattr(files,'call'):
   from .catalogue import sync_task_snapshot,put,task_rows
   try:
    await sync_task_snapshot(files,get_store(),pid,request.state.user['id'],tid)
    await asyncio.to_thread(put,get_store(),pid,request.state.user['id'],'tasks',task_rows(get_store(),pid,request.state.user['id']))
   except (HTTPException,TimeoutError):pass
  return [r for r in await files.catalogue(pid,request.state.user) if tid in r.get('task_ids',[])]
 @app.post('/api/library/literature/add-url',status_code=201)
 async def reference(body:ReferenceBody,request:Request):
  url=body.url.strip();parsed=urlsplit(url)
  if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:raise HTTPException(422,'请输入有效的 HTTP/HTTPS 文献链接')
  uid=request.state.user['id'];blob=json.dumps({'url':url},ensure_ascii=False).encode();sha=hashlib.sha256(blob).hexdigest();identifier=uuid.uuid4().hex
  with get_store().connect() as db:
   old=db.execute('SELECT id FROM personal_documents WHERE user_id=? AND sha256=?',(uid,sha)).fetchone()
   if old:return {'id':old['id'],'already_saved':True,'index_status':'not_indexed'}
   relative=Path(uid)/(identifier+'.url.json');p=assets.ROOT/'runtime/personal-library'/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(blob)
   metadata={'title':body.title.strip() or url,'authors':'','year':'','doi':'','tags':url,'url':url,'record_type':'url','index_status':'not_indexed'}
   db.execute('INSERT INTO personal_documents(id,user_id,sha256,name,path,size_bytes,metadata) VALUES(?,?,?,?,?,?,?)',(identifier,uid,sha,(body.title.strip() or parsed.hostname)+'.url.json',relative.as_posix(),len(blob),json.dumps(metadata,ensure_ascii=False)))
  return {'id':identifier,'index_status':'not_indexed'}
 @app.post('/api/library/literature/add-file',status_code=201)
 async def paper_file(request:Request,file:UploadFile=File(...)):
  uid=request.state.user['id'];name=Path((file.filename or '').replace('\\','/')).name;suffix=Path(name).suffix.lower()
  if suffix not in {'.pdf','.md','.docx','.txt','.bib','.ris','.enw'}:raise HTTPException(422,'支持 PDF、Markdown、DOCX、TXT、BibTeX、RIS、ENW')
  target=assets.ROOT/'runtime/personal-library'/uid/(uuid.uuid4().hex+suffix);target.parent.mkdir(parents=True,exist_ok=True);size=0;digest=hashlib.sha256()
  try:
   with target.open('xb') as out:
    while block:=await file.read(1024*1024):
     size+=len(block)
     if size>50*1024**2:raise HTTPException(413,'个人文献单文件上限为50 MB')
     digest.update(block);out.write(block)
   if not size:raise HTTPException(422,'文件内容为空')
   from .personal_library import remember
   identifier=await asyncio.to_thread(remember,get_store(),uid,name,target,digest.hexdigest(),size,{'title':Path(name).stem},False)
   with get_store().connect() as db:
    row=db.execute('SELECT metadata FROM personal_documents WHERE id=?',(identifier,)).fetchone();m=json.loads(row['metadata']);m.update(record_type='file',index_status=m.get('index_status','not_indexed'))
    db.execute('UPDATE personal_documents SET metadata=? WHERE id=?',(json.dumps(m,ensure_ascii=False),identifier))
   return {'id':identifier,'name':name,'index_status':m['index_status']}
  finally:target.unlink(missing_ok=True);await file.close()
 @router.post('/library-folders',status_code=201)
 def create_folder(pid:str,body:FolderBody,request:Request):
  scope(pid,body.task_id,request);uid=request.state.user['id'];folder(pid,uid,body.task_id,body.kind,body.parent_id);name=body.name.strip()
  if not name or name in {'.','..'} or '/' in name or '\\' in name:raise HTTPException(422,'文件夹名称不能包含路径分隔符')
  with get_store().connect() as db:
   old=db.execute('SELECT id FROM library_folders WHERE project_id=? AND user_id=? AND task_id=? AND kind=? AND parent_id=? AND name=?',(pid,uid,body.task_id,body.kind,body.parent_id,name)).fetchone()
   if old:return {'id':old['id']}
   fid=uuid.uuid4().hex;db.execute('INSERT INTO library_folders VALUES(?,?,?,?,?,?,?)',(fid,pid,uid,body.task_id,body.kind,name,body.parent_id))
  return {'id':fid}
 @router.patch('/library-folders/{fid}')
 def rename_folder(pid:str,fid:str,body:FolderBody,request:Request):
  scope(pid,body.task_id,request);folder(pid,request.state.user['id'],body.task_id,body.kind,fid)
  if uuid.UUID(fid).version==5:raise HTTPException(409,'系统文件夹名称由分析进程管理')
  name=body.name.strip()
  if not name or name in {'.','..'} or '/' in name or '\\' in name:raise HTTPException(422,'文件夹名称无效')
  with get_store().connect() as db:db.execute('UPDATE library_folders SET name=? WHERE id=?',(name,fid))
  return {'ok':True}
 @router.delete('/library-folders/{fid}')
 async def delete_folder(pid:str,fid:str,task_id:str,kind:Kind,request:Request):
  items=await rows(pid,task_id,request);uid=request.state.user['id'];folder(pid,uid,task_id,kind,fid);automatic_folders(get_store(),pid,uid,task_id,items)
  if uuid.UUID(fid).version==5:raise HTTPException(409,'系统文件夹随分析进程保留')
  with get_store().connect() as db:
   members={r['asset_id']:r['folder_id'] for r in db.execute('SELECT asset_id,folder_id FROM library_members WHERE project_id=? AND user_id=? AND task_id=?',(pid,uid,task_id))}
   if db.execute('SELECT 1 FROM library_folders WHERE parent_id=?',(fid,)).fetchone() or any(members.get(r['id'],r.get('metadata',{}).get('folder_id') or r.get('_automatic_folder',''))==fid for r in items):raise HTTPException(409,'请先移出文件并删除子文件夹，再删除空文件夹')
   db.execute('DELETE FROM library_folders WHERE id=?',(fid,))
  return {'ok':True}
 @router.post('/library-folders/move')
 async def move(pid:str,body:MoveBody,request:Request):
  items={r['id']:r for r in await rows(pid,body.task_id,request)};uid=request.state.user['id'];folder(pid,uid,body.task_id,body.kind,body.folder_id)
  if any(a not in items or items[a]['kind']!=body.kind for a in body.asset_ids):raise HTTPException(404,'所选文件不属于当前任务分类')
  with get_store().connect() as db:db.executemany('INSERT OR REPLACE INTO library_members VALUES(?,?,?,?,?)',[(pid,uid,body.task_id,a,body.folder_id) for a in body.asset_ids])
  return {'ok':True}
 @router.post('/library-browser/refresh')
 def refresh(pid:str,request:Request):
  check_project(pid,request)
  with get_store().connect() as db:db.execute('UPDATE catalogue_sync SET updated_at=0 WHERE project_id=? AND user_id=?',(pid,request.state.user['id']))
  return {'ok':True}
 @router.get('/library-browser')
 async def browse(pid:str,task_id:str,request:Request,kind:Kind='data',folder_id:str='',q:str='',offset:int=0,hide_unavailable:bool=False):
  all_rows=await rows(pid,task_id,request);uid=request.state.user['id'];automatic_folders(get_store(),pid,uid,task_id,all_rows);folder(pid,uid,task_id,kind,folder_id)
  with get_store().connect() as db:
   folders=[dict(r) for r in db.execute('SELECT * FROM library_folders WHERE project_id=? AND user_id=? AND task_id=? AND kind=? ORDER BY name',(pid,uid,task_id,kind))]
   membership={r['asset_id']:r['folder_id'] for r in db.execute('SELECT asset_id,folder_id FROM library_members WHERE project_id=? AND user_id=? AND task_id=?',(pid,uid,task_id))}
  fids={f['id'] for f in folders};items=[r for r in all_rows if r['kind']==kind]
  for r in items:r['folder_id']=membership.get(r['id'],r.get('metadata',{}).get('folder_id') or r.get('_automatic_folder',''));r['folder_id']=r['folder_id'] if r['folder_id'] in fids else ''
  for f in folders:f['count']=sum(r['folder_id']==f['id'] for r in items);f['managed']=uuid.UUID(f['id']).version==5
  selected=[r for r in items if (q.lower() in (r['name']+' '+str(r.get('relative_path',''))).lower() if q else r['folder_id']==folder_id) and (not hide_unavailable or r['status'] not in {'missing','empty','integrity_error'})]
  offset=max(0,offset);counts={k:sum(r['kind']==k for r in all_rows) for k in ('data','results','charts','drafts')}
  return {'items':selected[offset:offset+50],'total':len(selected),'counts':counts,'folders':folders,'offset':offset,'category_total':len(items)}
 async def owned_path(pid,tid,aid,request):
  items=await rows(pid,tid,request);r=next((r for r in items if r['id']==aid),None)
  if not r:raise HTTPException(404,'文件不属于当前任务')
  uid=request.state.user['id']
  if r.get('figure_id'):
   p=assets.ROOT/'runtime/figures'/uid/pid/r['figure_id']/'figure.svg'
   # Renderer file names are recorded rather than assumed below.
   result=p.parent/'result.json'
   if result.is_file():
    meta=json.loads(result.read_text());name=meta.get('outputs',{}).get('svg',{}).get('name','figure.svg');p=p.parent/Path(name).name
  elif r.get('storage_root')=='analysis_outputs':p=assets.local_output(r.get('relative_path',''))
  elif r.get('storage_root')=='analysis_inputs':
   from .project_files import input_path
   p=input_path(r['relative_path'])
  else:
   with get_store().connect() as db:a=db.execute('SELECT * FROM assets WHERE id=? AND project_id=? AND user_id=?',(aid,pid,uid)).fetchone()
   if not a:return r,None
   p=assets.uploaded_path(dict(a),pid,uid)
  return r,p
 @router.get('/library-preview')
 async def preview(pid:str,task_id:str,asset_id:str,request:Request,content:bool=False):
  r,p=await owned_path(pid,task_id,asset_id,request)
  if p is None or not p.is_file():return {'mode':'unavailable','reason':'文件未挂载或此类型暂不支持预览，请使用原入口查看。'}
  size=p.stat().st_size;suffix=p.suffix.lower();mode='image' if suffix in {'.png','.jpg','.jpeg','.webp','.svg'} else 'pdf' if suffix=='.pdf' else 'table' if suffix in {'.csv','.tsv'} else 'text' if suffix in {'.txt','.md','.json','.log','.report'} else ''
  limit=(20 if mode=='pdf' else 10 if mode=='image' else 5 if mode=='table' else 1)*1024**2
  if not mode:return {'mode':'unavailable','reason':'此格式不支持在线预览，可下载后查看。'}
  if size>limit:return {'mode':'unavailable','reason':f'文件大小 {size/1024**2:.1f} MB，超过该格式预览上限 {limit//1024**2} MB，请下载或先筛选。'}
  if content:
   if mode not in {'image','pdf'}:raise HTTPException(422,'此文件请使用文本预览')
   return FileResponse(p,media_type=mimetypes.guess_type(p.name)[0],headers={'X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'"})
  if mode in {'image','pdf'}:return {'mode':mode,'url':f'/api/projects/{pid}/library-preview?task_id={task_id}&asset_id={asset_id}&content=true'}
  text=await asyncio.to_thread(lambda:p.read_bytes().decode('utf-8-sig',errors='replace'))
  if mode=='table':
   reader=csv.reader(io.StringIO(text),delimiter='\t' if suffix=='.tsv' else ',');data=[row for _,row in zip(range(101),reader)]
   return {'mode':mode,'columns':data[0][:50] if data else [],'rows':[row[:50] for row in data[1:]],'note':'预览前100行、50列；完整数据请下载。'}
  return {'mode':mode,'text':text[:50000],'note':'最多显示50000个字符。'}
