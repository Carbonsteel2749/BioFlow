"""Persistent owned upload queues, content-bound resume and explicit read pairs."""
import asyncio
import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid
from typing import Annotated

import httpx
from fastapi import HTTPException,Request
from pydantic import BaseModel,Field,ConfigDict
from . import assets

LOCKS={}
RECEIVERS=asyncio.Semaphore(2)
HEX=Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]


class UploadBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=240)
    size_bytes:int=Field(ge=1,le=250*1024**3,strict=True)
    chunk_bytes:int=Field(ge=1,le=64*1024**2,strict=True)
    hashes:list[HEX]=Field(min_length=1,max_length=16384)
    fingerprint:HEX
    draft_id:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')


class DraftBody(BaseModel):
    name:str=Field(min_length=1,max_length=240)
    size_bytes:int=Field(ge=1,le=250*1024**3,strict=True)


class Pair(BaseModel):
    sample_id:str=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
    read1:str=Field(pattern=r'^[a-f0-9]{32}$')
    read2:str=Field(pattern=r'^[a-f0-9]{32}$')


class PairBody(BaseModel):
    name:str=Field(min_length=1,max_length=120)
    pairs:list[Pair]=Field(min_length=1,max_length=1000)


def fastq(name):return bool(re.search(r'\.(fastq|fq)(\.gz)?$',name,re.I))


def public(row):
    received=json.loads(row['received'])
    return {**{k:row[k] for k in ('id','project_id','name','size_bytes','chunk_bytes','fingerprint','status','asset_id','error','created_at')},
        'received_chunks':received,'received_bytes':sum(min(row['chunk_bytes'],row['size_bytes']-i*row['chunk_bytes']) for i in received)}


def install_uploads(router,get_store,check_project,files,call,urls):
    def get(pid,uid,user):
        with get_store().connect() as db:row=db.execute('SELECT * FROM project_uploads WHERE id=? AND project_id=? AND user_id=?',(uid,pid,user['id'])).fetchone()
        if not row:raise HTTPException(404,'上传记录不存在')
        return dict(row)

    def update(uid,**values):
        with get_store().connect() as db:
            db.execute('UPDATE project_uploads SET '+','.join(k+'=?' for k in values)+',updated_at=CURRENT_TIMESTAMP WHERE id=?',(*values.values(),uid))

    def directory(row):
        base=assets.ROOT/'runtime/upload-parts'
        legacy=base/row['user_id']/row['project_id']/row['id']
        folder=(legacy if legacy.is_dir() else base/row['id']).resolve()
        if not folder.is_relative_to(base.resolve()):raise HTTPException(400,'上传路径无效')
        try:folder.mkdir(parents=True,exist_ok=True)
        except OSError as exc:raise HTTPException(507,'无法创建上传暂存目录，请检查可用空间和目录权限') from exc
        return folder

    def reserve(size,exclude=''):
        base=assets.ROOT/'runtime';base.mkdir(parents=True,exist_ok=True)
        with get_store().connect() as db:rows=db.execute("SELECT * FROM project_uploads WHERE backend_id IS NULL AND status IN ('open','finalizing') AND id!=?",(exclude,)).fetchall()
        reserved=sum(max(0,2*r['size_bytes']-public(r)['received_bytes']) for r in rows)
        if shutil.disk_usage(base).free-reserved-512*1024**2<2*size:
            raise HTTPException(507,'磁盘空间不足，请先释放空间再继续上传')

    async def backend(row,method='GET',tail=''):
        return await call('analysis','/api/uploads/sessions/'+row['backend_id']+tail,method,{} if method=='POST' else None,
            headers={'X-Upload-Token':row['backend_token']})

    @router.get('/uploads/config')
    async def config(pid:str,request:Request):
        check_project(pid,request)
        try:config=await call('analysis','/api/uploads/sessions/config')
        except HTTPException:config={'chunk_bytes':16*1024**2,'max_file_bytes':250*1024**3,'parallel_files':2}
        return config

    @router.get('/uploads')
    def listing(pid:str,request:Request):
        check_project(pid,request);user=request.state.user
        with get_store().connect() as db:
            rows=db.execute('SELECT * FROM project_uploads WHERE project_id=? AND user_id=? ORDER BY rowid DESC LIMIT 500',(pid,user['id'])).fetchall()
            uploaded=db.execute("SELECT id,name,metadata FROM assets WHERE project_id=? AND user_id=? AND kind='data'",(pid,user['id'])).fetchall()
            pairs=[dict(r) for r in db.execute('SELECT * FROM upload_pairs WHERE project_id=? AND user_id=? ORDER BY rowid',(pid,user['id']))]
        candidates=[{'id':r['id'],'name':r['name']} for r in uploaded if json.loads(r['metadata']).get('analysis_upload_id')]
        return {'items':[public(r) for r in rows],'files':candidates,'pairs':pairs}

    @router.post('/uploads',status_code=201)
    async def create(pid:str,body:UploadBody,request:Request):
        check_project(pid,request);user=request.state.user
        if '/' in body.name or '\\' in body.name or any(ord(c)<32 for c in body.name):raise HTTPException(422,'文件名无效')
        if (body.size_bytes+body.chunk_bytes-1)//body.chunk_bytes!=len(body.hashes):raise HTTPException(422,'文件分片数量不匹配')
        expected=hashlib.sha256(f"{body.size_bytes}:{body.chunk_bytes}:{':'.join(body.hashes)}".encode()).hexdigest()
        if expected!=body.fingerprint:raise HTTPException(422,'文件内容校验信息不匹配')
        async with LOCKS.setdefault('create',asyncio.Lock()),LOCKS.setdefault(body.draft_id or 'new-session',asyncio.Lock()):
            draft=get(pid,body.draft_id,user) if body.draft_id else None
            if draft and (draft['name']!=body.name or draft['size_bytes']!=body.size_bytes):raise HTTPException(422,'请选择这条上传记录对应的原文件')
            if draft and draft['status']=='cancelled':raise HTTPException(409,'上传已取消')
            with get_store().connect() as db:
                rows=db.execute("SELECT * FROM project_uploads WHERE project_id=? AND user_id=? AND fingerprint=? AND name=? AND status!='cancelled' ORDER BY rowid DESC",(pid,user['id'],body.fingerprint,body.name)).fetchall()
                if rows:
                    if draft and draft['id']!=rows[0]['id']:update(draft['id'],status='cancelled',error='已合并相同文件的上传记录')
                    return public(rows[0])
                count=db.execute("SELECT count(*) FROM project_uploads WHERE user_id=? AND status NOT IN ('completed','cancelled')",(user['id'],)).fetchone()[0]
            if count>=32 and not draft:raise HTTPException(409,'未完成的上传达到 32 条，请先完成或取消部分记录')
            uid=draft['id'] if draft else uuid.uuid4().hex;backend_id=None;token=None
            if fastq(body.name):
                config=await call('analysis','/api/uploads/sessions/config')
                if config['chunk_bytes']!=body.chunk_bytes:raise HTTPException(409,'服务器分片设置已变化，请重新添加文件')
                result=await call('analysis','/api/uploads/sessions','POST',{'original_name':body.name,'size_bytes':body.size_bytes,'chunk_sha256':body.hashes,'fingerprint':body.fingerprint})
                backend_id=result['id'];token=result['token'];get_store().claim('upload_session',backend_id,user['id'])
            else:reserve(body.size_bytes)
            if draft:update(uid,chunk_bytes=body.chunk_bytes,hashes=json.dumps(body.hashes),fingerprint=body.fingerprint,backend_id=backend_id,backend_token=token,status='open')
            else:
                with get_store().connect() as db:db.execute('INSERT INTO project_uploads(id,project_id,user_id,name,size_bytes,chunk_bytes,hashes,fingerprint,backend_id,backend_token) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (uid,pid,user['id'],body.name,body.size_bytes,body.chunk_bytes,json.dumps(body.hashes),body.fingerprint,backend_id,token))
        return public(get(pid,uid,user))

    @router.post('/uploads/drafts',status_code=201)
    def draft(pid:str,body:DraftBody,request:Request):
        check_project(pid,request);user=request.state.user
        if '/' in body.name or '\\' in body.name or any(ord(c)<32 for c in body.name):raise HTTPException(422,'文件名无效')
        uid=uuid.uuid4().hex
        with get_store().connect() as db:
            db.execute('BEGIN IMMEDIATE')
            count=db.execute("SELECT count(*) FROM project_uploads WHERE user_id=? AND status NOT IN ('completed','cancelled')",(user['id'],)).fetchone()[0]
            if count>=32:raise HTTPException(409,'请先完成或取消部分上传，最多保留 32 条未完成记录')
            db.execute("INSERT INTO project_uploads(id,project_id,user_id,name,size_bytes,chunk_bytes,hashes,fingerprint,status) VALUES(?,?,?,?,?,16777216,'[]','','preparing')",(uid,pid,user['id'],body.name,body.size_bytes))
        return public(get(pid,uid,user))

    @router.get('/uploads/{uid}')
    async def detail(pid:str,uid:str,request:Request):
        check_project(pid,request);row=get(pid,uid,request.state.user)
        async with LOCKS.setdefault(uid,asyncio.Lock()):
            row=get(pid,uid,request.state.user)
            if row['backend_id'] and row['status']!='completed':
                state=await backend(row)
                update(uid,received=json.dumps(state['received_chunks']))
            return public(get(pid,uid,request.state.user))

    @router.put('/uploads/{uid}/chunks/{index}')
    async def chunk(pid:str,uid:str,index:int,request:Request):
        check_project(pid,request);row=get(pid,uid,request.state.user)
        async with RECEIVERS,LOCKS.setdefault(uid,asyncio.Lock()):
            row=get(pid,uid,request.state.user);hashes=json.loads(row['hashes'])
            if not 0<=index<len(hashes):raise HTTPException(422,'分片编号无效')
            if row['status']!='open':raise HTTPException(409,'请先恢复这条上传')
            limit=min(row['chunk_bytes'],row['size_bytes']-index*row['chunk_bytes'])
            if row['backend_id']:
                async def stream():
                    size=0
                    async for block in request.stream():
                        size+=len(block)
                        if size>limit:raise HTTPException(413,'分片过大')
                        yield block
                try:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(300,connect=3),trust_env=False) as client:
                        response=await client.put(urls['analysis']+'/api/uploads/sessions/'+row['backend_id']+'/chunks/'+str(index),content=stream(),headers={'X-Upload-Token':row['backend_token'],'Content-Type':'application/octet-stream'})
                    if response.is_error:raise HTTPException(response.status_code,response.json().get('detail','分片校验失败'))
                except httpx.HTTPError as exc:raise HTTPException(503,'传输中断，已完成的分片仍然保留') from exc
            else:
                data=bytearray()
                async for block in request.stream():
                    if len(data)+len(block)>limit:raise HTTPException(413,'分片过大')
                    data.extend(block)
                if len(data)!=limit or hashlib.sha256(data).hexdigest()!=hashes[index]:raise HTTPException(422,'分片大小或内容校验不匹配，请重新选择原文件')
                destination=directory(row)/(str(index)+'.part');temporary=destination.with_suffix('.'+uuid.uuid4().hex[:8]+'.tmp')
                try:
                    await asyncio.to_thread(temporary.write_bytes,data);temporary.replace(destination)
                except OSError as exc:raise HTTPException(507,'无法保存分片，请检查可用空间后重试；已完成的分片保留') from exc
                finally:
                    try:temporary.unlink(missing_ok=True)
                    except OSError:pass
            received=sorted(set(json.loads(row['received']))|{index});update(uid,received=json.dumps(received),error='')
            return public(get(pid,uid,request.state.user))

    async def change(pid,uid,request,status):
        check_project(pid,request);get(pid,uid,request.state.user)
        async with LOCKS.setdefault(uid,asyncio.Lock()):
            row=get(pid,uid,request.state.user)
            if row['status']=='completed':raise HTTPException(409,'文件已完成，上传队列操作不会删除项目资料')
            if row['backend_id']:
                try:await backend(row,'POST','/resume' if status=='open' else '/park')
                except HTTPException as exc:
                    if not (status=='cancelled' and exc.status_code==404):raise
            elif status=='open' and row['fingerprint']:reserve(max(0,row['size_bytes']-public(row)['received_bytes']//2),uid)
            if status=='open' and not row['fingerprint']:status='preparing'
            update(uid,status=status,error='');return public(get(pid,uid,request.state.user))

    @router.post('/uploads/{uid}/pause')
    async def pause(pid:str,uid:str,request:Request):return await change(pid,uid,request,'paused')

    @router.post('/uploads/{uid}/resume')
    async def resume(pid:str,uid:str,request:Request):return await change(pid,uid,request,'open')

    @router.post('/uploads/{uid}/cancel')
    async def cancel(pid:str,uid:str,request:Request):return await change(pid,uid,request,'cancelled')

    @router.post('/uploads/{uid}/complete')
    async def complete(pid:str,uid:str,request:Request):
        check_project(pid,request);get(pid,uid,request.state.user)
        async with LOCKS.setdefault(uid,asyncio.Lock()):
            row=get(pid,uid,request.state.user)
            if row['status']=='completed':return public(row)
            if row['status'] not in {'open','finalizing'}:raise HTTPException(409,'请先恢复这条上传')
            hashes=json.loads(row['hashes'])
            if len(json.loads(row['received']))!=len(hashes):raise HTTPException(409,'仍有分片未上传完成')
            update(uid,status='finalizing')
            try:
                headers={k:v for k,v in request.headers.items() if k in {'cookie','x-csrf-token'}}
                if row['backend_id']:
                    result=await backend(row,'POST','/complete')
                    suffix=next(s for s in ('.fastq.gz','.fq.gz','.fastq','.fq') if row['name'].lower().endswith(s))
                    path=assets.analysis_root()/'runtime/incoming/uploads'/result['id']/('reads'+suffix)
                    sha=result['checksum'].removeprefix('sha256:')
                    asset=await files.adopt(pid,request.state.user,uid,row['name'],path,sha,row['size_bytes'],headers=headers,analysis_upload=result['id'])
                else:
                    folder=assets.ROOT/'runtime/assets'/row['user_id']/pid;folder.mkdir(parents=True,exist_ok=True)
                    path=folder/(uid+Path(row['name']).suffix.lower())
                    def assemble():
                        temporary=path.with_suffix(path.suffix+'.assembling');digest=hashlib.sha256();sample=b''
                        try:
                            with temporary.open('wb') as output:
                                for i,expected in enumerate(hashes):
                                    part=directory(row)/(str(i)+'.part')
                                    data=part.read_bytes()
                                    if len(data)!=min(row['chunk_bytes'],row['size_bytes']-i*row['chunk_bytes']) or hashlib.sha256(data).hexdigest()!=expected:
                                        raise HTTPException(409,'暂存分片校验失败，请重新选择原文件')
                                    if not sample:sample=data[:4096]
                                    output.write(data);digest.update(data)
                            temporary.replace(path);return digest.hexdigest(),sample
                        finally:temporary.unlink(missing_ok=True)
                    sha,sample=await asyncio.to_thread(assemble)
                    asset=await files.adopt(pid,request.state.user,uid,row['name'],path,sha,row['size_bytes'],sample,headers)
                update(uid,status='completed',asset_id=asset['id'],error='')
                if not row['backend_id']:
                    for i in range(len(hashes)):(directory(row)/(str(i)+'.part')).unlink(missing_ok=True)
                return public(get(pid,uid,request.state.user))
            except Exception as exc:
                update(uid,status='open',error=str(getattr(exc,'detail','文件校验或入库失败，可重试')));raise

    @router.post('/uploads/pairs',status_code=201)
    async def pair(pid:str,body:PairBody,request:Request):
        check_project(pid,request);user=request.state.user;seen=set();sample_ids=set();mapped=[]
        for pair in body.pairs:
            if pair.sample_id in sample_ids:raise HTTPException(422,'样本编号重复：'+pair.sample_id)
            sample_ids.add(pair.sample_id);entry={'sample_id':pair.sample_id}
            for key,aid in [('read1_upload_id',pair.read1),('read2_upload_id',pair.read2)]:
                if aid in seen:raise HTTPException(422,'同一个文件不能重复用于多个配对位置')
                seen.add(aid)
                with get_store().connect() as db:record=db.execute('SELECT * FROM assets WHERE id=? AND project_id=? AND user_id=?',(aid,pid,user['id'])).fetchone()
                if not record:raise HTTPException(404,'配对文件不属于当前项目')
                upload_id=json.loads(record['metadata']).get('analysis_upload_id')
                if not upload_id or not get_store().owns('upload',upload_id,user['id']):raise HTTPException(422,'请选择已完成校验的 FASTQ 文件')
                entry[key]=upload_id
            mapped.append(entry)
        async with LOCKS.setdefault('pairs:'+pid,asyncio.Lock()):
            with get_store().connect() as db:
                existing=[dict(r) for r in db.execute('SELECT * FROM upload_pairs WHERE project_id=? AND user_id=?',(pid,user['id']))]
            if any(aid in {r['read1'],r['read2']} for aid in seen for r in existing):raise HTTPException(409,'所选文件已建立数据集，请从数据集记录查看')
            result=await call('analysis','/api/uploads/manifests','POST',{'dataset_name':body.name,'files':mapped})
            get_store().claim('dataset',result['dataset_id'],user['id'],result);get_store().link(pid,'dataset',result['dataset_id'])
            with get_store().connect() as db:
                for pair in body.pairs:db.execute('INSERT INTO upload_pairs(project_id,user_id,sample_id,read1,read2,dataset_id) VALUES(?,?,?,?,?,?)',
                    (pid,user['id'],pair.sample_id,pair.read1,pair.read2,result['dataset_id']))
            return result
