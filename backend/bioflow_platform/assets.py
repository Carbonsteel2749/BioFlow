"""Project files and existing analysis outputs share one asset catalogue."""
import asyncio
import csv
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import time
import uuid

import httpx
from fastapi import HTTPException

ROOT=Path(__file__).resolve().parents[2]
LITERATURE={'.pdf','.docx','.doc','.md','.rst','.ris','.bib','.enw'}
IMAGE={'.png','.jpg','.jpeg','.svg','.webp','.tif','.tiff','.pdf'}
HASH_CACHE={}
KB_LOCKS={}
SYNCED={}


def analysis_root():
    value=Path(os.getenv('BIOFLOW_ANALYSIS_ROOT',str(ROOT/'BioLLM_RawDataAnalysis')))
    return (value if value.is_absolute() else ROOT/value).resolve()


def classify(name,sample=b''):
    suffix=Path(name).suffix.lower()
    if suffix in LITERATURE:return 'literature'
    if suffix=='.txt':
        text=sample[:4096].decode('utf-8',errors='replace')
        line=text.splitlines()[0] if text.splitlines() else ''
        return 'data' if '\t' in line or (',' in line and len(line.split(','))>=3) or line.startswith('@') else 'literature'
    return 'data'


def local_output(path):
    marker='/runtime/outputs/'
    value=str(path).replace('\\','/')
    base=(analysis_root()/'runtime/outputs').resolve()
    if marker in value:value=value.split(marker,1)[1]
    elif Path(value).is_absolute() or re.match(r'^[A-Za-z]:',value):return None
    candidate=(base/value).resolve()
    return candidate if candidate.is_relative_to(base) else None


def relative_reference(value,marker):
    value=str(value or '').replace('\\','/')
    if marker in value:return value.split(marker,1)[1]
    if not Path(value).is_absolute() and not re.match(r'^[A-Za-z]:',value) and '..' not in Path(value).parts:return value
    return None


def uploaded_path(row,pid,uid):
    metadata=json.loads(row['metadata']) if isinstance(row.get('metadata'),str) else row.get('metadata',{})
    folder=analysis_root()/'runtime/incoming/uploads'/metadata['analysis_upload_id'] if metadata.get('storage')=='analysis' else ROOT/'runtime/assets'/uid/pid
    value=str(row['path']).replace('\\','/')
    marker='/runtime/incoming/uploads/' if metadata.get('storage')=='analysis' else '/runtime/assets/'
    relative=relative_reference(value,marker)
    base=analysis_root()/'runtime/incoming/uploads' if metadata.get('storage')=='analysis' else ROOT/'runtime/assets'
    candidate=(base/relative).resolve() if relative is not None else (folder/Path(value).name).resolve()
    if not candidate.is_relative_to(folder.resolve()):raise HTTPException(409,'资料路径不属于当前项目')
    return candidate


def availability(path,expected_hash=None):
    if path is None or not path.is_file():return 'missing',0
    size=path.stat().st_size
    if not size:return 'empty',0
    if expected_hash:
        key=(str(path),size,path.stat().st_mtime_ns,expected_hash)
        if key not in HASH_CACHE:
            digest=hashlib.sha256()
            with path.open('rb') as f:
                for block in iter(lambda:f.read(1024*1024),b''):digest.update(block)
            HASH_CACHE[key]=digest.hexdigest()==expected_hash
        if not HASH_CACHE[key]:return 'integrity_error',size
    return 'ready',size


def snapshot_path(tid):
    return ROOT/'runtime/artifact-catalog'/(hashlib.sha256(tid.encode()).hexdigest()+'.json')


def read_snapshot(tid):
    try:return json.loads(snapshot_path(tid).read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}


def write_snapshot(tid,task,artifacts):
    task=dict(task)
    for key,marker in [('result_archive','/runtime/outputs/'),('manifest_path','/runtime/incoming/')]:
        if task.get(key):task[key]=relative_reference(task[key],marker) or task[key]
    artifacts=[dict(a) for a in artifacts]
    for a in artifacts:
        old=str(a.get('path',''))
        filename=a.get('file_name') or Path(old.replace(chr(92),'/')).name or a['artifact_id']
        relative=relative_reference(old,'/runtime/outputs/') if old else None
        if relative and '.unresolved/' in relative:relative=None
        a['path']=relative or f"{tid}/.unresolved/{a['artifact_id']}/{filename}"
        a['storage_root']='analysis_outputs'
    path=snapshot_path(tid);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps({'task':task,'artifacts':artifacts},ensure_ascii=False),encoding='utf-8')
    temporary.replace(path)


def resolve_catalogue_paths(tid,artifacts):
    base=(analysis_root()/'runtime/outputs').resolve()
    folder=(base/tid).resolve()
    if not folder.is_relative_to(base):raise HTTPException(422,'任务路径无效')
    index={}
    if folder.is_dir():
        for path in folder.rglob('*'):
            if path.is_file():index.setdefault(path.name,[]).append(path)
    for artifact in artifacts:
        name=Path(artifact.get('file_name') or artifact.get('path','')).name
        candidates=index.get(name,[])
        sized=[p for p in candidates if p.stat().st_size==artifact.get('size_bytes')]
        preferred=sized or candidates
        matched=preferred[0] if len(preferred)==1 else next((p for p in preferred if availability(p,artifact.get('sha256'))[0]=='ready'),None)
        path=matched or (candidates[0] if candidates else folder/'.missing'/artifact['artifact_id']/name)
        artifact['path']=str(path)
    return artifacts


def existing_outputs(task_ids,include_inputs=True):
    rows=[]
    for tid in task_ids:
        snapshot=read_snapshot(tid);task=snapshot.get('task')
        if not task:continue
        for a in snapshot.get('artifacts',[]):
            if a.get('status')!='active':continue
            if str(a['artifact_type']).startswith('reads.'):continue
            status,size=a.get('local_status') or ('empty' if a.get('size_bytes')==0 else 'not_checked'),a.get('local_size',a.get('size_bytes',0))
            name=a.get('file_name') or Path(a['path']).name
            kind='charts' if a['artifact_type'].startswith('figure.') and (Path(name).suffix.lower() in IMAGE or a.get('media_type','').startswith('image/')) else 'results'
            rows.append({'id':'artifact:'+a['artifact_id'],'artifact_id':a['artifact_id'],'name':name,
                'kind':kind,'task_id':tid,'source':task.get('name') or tid,'status':status,'size_bytes':size,
                'expected_bytes':a['size_bytes'],'media_type':a['media_type'],
                'relative_path':relative_reference(a['path'],'/runtime/outputs/'),'storage_root':'analysis_outputs',
                'url':'/api/projects/{pid}/artifacts/'+a['artifact_id']+'/preview?download=1' if status!='empty' else None})
        if task.get('result_archive'):
            status,size='not_checked',0
            rows.append({'id':'archive:'+tid,'name':Path(task['result_archive']).name,'kind':'results',
                'task_id':tid,'source':task.get('name') or tid,'status':status,'size_bytes':size,
                'media_type':'application/gzip','summary':'分析成果包；原始 FASTQ 可在测序数据中单独下载','url':'/api/projects/{pid}/tasks/'+tid+'/archive'})
        if not include_inputs or not task.get('manifest_path'):continue
        manifest=analysis_root()/'runtime/incoming/manifests'/Path(task['manifest_path']).name
        if manifest.is_file():
            with manifest.open(encoding='utf-8-sig') as handle:
                for entry in csv.DictReader(handle):
                    for role in ('read1','read2'):
                        value=entry.get(role,'')
                        if not value:continue
                        from .project_files import input_path
                        raw=input_path(value,manifest)
                        try:
                            size=raw.stat().st_size;status='ready' if size else 'empty'
                        except OSError:status,size='missing',0
                        rows.append({'id':tid+':'+entry['sample_id']+':'+role,'name':Path(value).name,'kind':'data',
                            'task_id':tid,'sample_id':entry['sample_id'],'source':task.get('name') or tid,'status':status,'size_bytes':size,'media_type':'application/gzip','url':None,
                            'relative_path':raw.relative_to(analysis_root()/'runtime/incoming').as_posix(),'storage_root':'analysis_inputs'})
    return rows

class AssetService:
    def __init__(self,get_store,call,urls):
        self.get_store=get_store;self.call=call;self.urls=urls

    async def ensure_kb(self,pid,user,headers=None):
        lock=KB_LOCKS.setdefault(pid,asyncio.Lock())
        async with lock:
            store=self.get_store();project=store.get(pid)
            if project['knowledge_base_id']:return project['knowledge_base_id']
            kb=await self.call('literature','/api/knowledge-bases','POST',{'name':project['name']},headers=headers)
            kb=kb.get('item',kb)
            store.claim('kb',kb['id'],user['id']);store.bind_kb(pid,kb['id'])
            return kb['id']

    async def send_file(self,service,path,stored_path,name,headers=None):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600,connect=3),trust_env=False) as client:
                with stored_path.open('rb') as f:
                    response=await client.post(self.urls[service]+path,files={'file':(name,f,mimetypes.guess_type(name)[0] or 'application/octet-stream')},headers=headers)
            if response.is_error:raise HTTPException(response.status_code,response.json())
            return response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(503,f'{service} 服务未连接，文件已保存在项目资料中') from exc

    async def upload(self,pid,user,file,headers=None):
        store=self.get_store();name=(file.filename or 'upload').replace('\\','/').split('/')[-1]
        if not name or len(name)>240:raise HTTPException(422,'文件名无效')
        identifier=uuid.uuid4().hex
        folder=ROOT/'runtime/assets'/user['id']/pid
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/(identifier+Path(name).suffix.lower())
        digest=hashlib.sha256();size=0;sample=b''
        try:
            with path.open('xb') as out:
                while block:=await file.read(1024*1024):
                    size+=len(block)
                    if size>250*1024**3:raise HTTPException(413,'文件超过单文件上传上限')
                    if not sample:sample=block[:4096]
                    digest.update(block);out.write(block)
            if size==0:raise HTTPException(422,'文件内容为空')
        except Exception:
            path.unlink(missing_ok=True);raise
        finally:
            await file.close()
        return await self.adopt(pid,user,identifier,name,path,digest.hexdigest(),size,sample,headers)

    async def adopt(self,pid,user,identifier,name,path,sha256,size,sample=b'',headers=None,analysis_upload=None):
        store=self.get_store()
        with store.connect() as db:
            existing=db.execute('SELECT * FROM assets WHERE id=? AND project_id=? AND user_id=?',(identifier,pid,user['id'])).fetchone()
        if existing:
            return {**{k:existing[k] for k in ('id','name','kind','size_bytes','status')},'metadata':json.loads(existing['metadata'])}
        kind=classify(name,sample)
        initial_status='stored' if kind=='literature' else 'ready'
        with store.connect() as db:
            db.execute('INSERT INTO assets(id,project_id,user_id,name,kind,path,size_bytes,sha256,status) VALUES(?,?,?,?,?,?,?,?,?)',
                (identifier,pid,user['id'],name,kind,str(path),size,sha256,initial_status))
        metadata={};status='ready'
        try:
            if kind=='literature':
                kb=await self.ensure_kb(pid,user,headers)
                document=await self.send_file('literature','/api/knowledge-bases/'+kb+'/documents',path,name,headers)
                uploaded=document.get('uploaded',document.get('document',document))
                metadata={'knowledge_base_id':kb,'document_id':uploaded.get('id'),'index_status':uploaded.get('status','processing')}
                status='ready' if uploaded.get('status') in {'ready','indexed','completed'} else 'indexing'
            elif analysis_upload:
                metadata={'analysis_upload_id':analysis_upload,'storage':'analysis'}
                store.claim('upload',analysis_upload,user['id'])
            elif re.search(r'\.(fastq|fq)(\.gz)?$',name,re.I):
                result=await self.send_file('analysis','/api/uploads/files',path,name)
                metadata={'analysis_upload_id':result['id']}
                store.claim('upload',result['id'],user['id'])
        except HTTPException as exc:
            metadata['upstream_error']=exc.detail;status='stored'
        with store.connect() as db:
            relative=str(path.relative_to(analysis_root()/'runtime/incoming/uploads' if metadata.get('storage')=='analysis' else ROOT/'runtime/assets')).replace(chr(92),'/')
            db.execute('UPDATE assets SET status=?,metadata=?,path=? WHERE id=?',(status,json.dumps(metadata,ensure_ascii=False),relative,identifier))
        store.event(pid,'添加资料',{'name':name,'kind':kind,'status':status})
        return {'id':identifier,'name':name,'kind':kind,'size_bytes':size,'status':status,'metadata':metadata}

    async def catalogue(self,pid,user):
        from .catalogue import catalogue
        return await catalogue(self,pid,user)

    async def manifest(self,pid,user):
        store=self.get_store()
        with store.connect() as db:
            confirmed=db.execute('SELECT dataset_id FROM upload_pairs WHERE project_id=? AND user_id=? ORDER BY rowid DESC LIMIT 1',(pid,user['id'])).fetchone()
        if confirmed and store.owns('dataset',confirmed['dataset_id'],user['id']):
            return await self.call('analysis','/api/datasets/'+confirmed['dataset_id']+'/reuse','POST',{})
        with store.connect() as db:
            rows=db.execute("SELECT name,metadata FROM assets WHERE project_id=? AND user_id=? AND kind='data'",(pid,user['id'])).fetchall()
        pairs={}
        for row in rows:
            match=re.match(r'(.+?)[_.-](?:R)?([12])(?:[_.-]001)?\.(?:fastq|fq)(?:\.gz)?$',row['name'],re.I)
            upload_id=json.loads(row['metadata']).get('analysis_upload_id')
            if match and upload_id:pairs.setdefault(match[1],{})[match[2]]=upload_id
        if not pairs or any(set(p)!= {'1','2'} for p in pairs.values()):
            raise HTTPException(422,'需要完整的双端 FASTQ 文件，文件名包含 _R1/_R2 或 _1/_2')
        response=await self.call('analysis','/api/uploads/manifests','POST',{'dataset_name':store.get(pid)['name'],
            'files':[{'sample_id':sample,'read1_upload_id':p['1'],'read2_upload_id':p['2']} for sample,p in pairs.items()]})
        store.claim('dataset',response['dataset_id'],user['id'],response)
        store.link(pid,'dataset',response['dataset_id'])
        return response
