"""Owned, immutable imports. Only opaque source identifiers cross the UI boundary."""
import hashlib
import errno
import json
import os
import re
import sqlite3
import stat
import shutil
import time
import uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from . import assets

# A single executor bounds both hashing/copying and private full-file validation.
EXECUTOR=ThreadPoolExecutor(max_workers=1,thread_name_prefix='typed-input')
EXTENSIONS=('.fastq.gz','.fq.gz','.fasta.gz','.fna.gz','.fa.gz','.fastq','.fq','.fasta','.fna','.fa','.tsv','.csv')
CATALOG={'upload_kinds':['reads','contigs','mags','matrix'],
         'reuse':'owned_registered_artifacts_from_successful_tasks',
         'limitations':['Unindexed FASTQ/contig/MAG outputs are not discoverable; upload them as project assets.',
                        'Processing declarations remain user_declared_unverified, including reused files.'],
         'source_reference':'asset:<asset_id> or artifact:<artifact_id>'}
CATALOG.update({'formats':{'reads':['.fastq','.fq','.fastq.gz','.fq.gz'],'contigs':['.fasta','.fa','.fna','.fasta.gz','.fa.gz','.fna.gz'],'mags':['.fasta','.fa','.fna','.fasta.gz','.fa.gz','.fna.gz'],'matrix':['.csv','.tsv']},
    'feature_types':['species','gene_families','KO','EC','pathway_abundance','pathway_coverage'],
    'units':['fraction','percent','counts','CPM','RPK','RPKM','TPM','HUMAnN_reported','coverage'],
    'read_states':['raw','clean','host_removed'],
    'validator_caps':{'source_bytes':250*1024**3,'expanded_sequence_bytes':250*1024**3,'matrix_rows':2000000,'paired_samples':1024,'mags':10000,'fasta_records':1000000,'sequence_line_bytes':16*1024**2},
    'storage_note':'导入复制来源文件；转换保留原始证据。执行另有输入副本、工作文件及完整归档；估计不是配额或保证上限。'})


class ImportDiagnostic(ValueError):
    """Only controlled user-recoverable messages cross the worker boundary."""


def available_space(root,required,reserve):
    try:free=shutil.disk_usage(root).free
    except OSError as exc:raise ImportDiagnostic('无法测量导入存储空间，请稍后重试') from exc
    if free<required+reserve:raise ImportDiagnostic('导入存储空间不足，请增加可用空间后重新导入；原文件保留')


@contextmanager
def import_lock(root,blocking=True):
    """OS-released lease bounds import work across gateway processes/restarts."""
    root=Path(root).absolute();root.mkdir(parents=True,exist_ok=True)
    if root.resolve()!=root:raise ValueError('unsafe input root')
    lock=root/'.typed-import.lock'
    if lock.is_symlink():raise ValueError('unsafe lock')
    with lock.open('a+b') as handle:
        if handle.seek(0,2)==0:handle.write(b'0');handle.flush()
        handle.seek(0)
        while True:
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError as exc:
                contention=(exc.errno in {errno.EACCES,errno.EAGAIN,errno.EWOULDBLOCK}
                            and (os.name!='nt' or getattr(exc,'winerror',None) in (None,33)))
                if not contention:raise
                if not blocking:raise BlockingIOError('input import active') from exc
                time.sleep(.25)
        try:yield
        finally:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


class ImportRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    idempotency_key:str=Field(min_length=1,max_length=128,pattern=r'^[A-Za-z0-9._-]+$')
    spec:dict


def slots(spec):
    """Strict shape validation; engine remains authoritative for file contents."""
    kind=spec.get('kind');fields={'reads':{'processing_status','samples'},'contigs':{'cohort_id','fasta'},
                                'mags':{'cohort_id','mags'},'matrix':{'table','feature_type','unit','normalization'}}
    if not isinstance(kind,str) or kind not in fields or set(spec)-fields[kind]-{'kind','processing_note','confirmed'}:
        raise HTTPException(422,'输入类型或字段无效')
    if 'confirmed' in spec and type(spec['confirmed']) is not bool:raise HTTPException(422,'确认字段须为布尔值')
    if kind=='reads':
        stage=spec.get('processing_status','raw')
        if stage not in ('raw','clean','host_removed'):raise HTTPException(422,'处理状态无效')
        if stage!='raw' and (spec.get('confirmed') is not True or not isinstance(spec.get('processing_note'),str) or not spec['processing_note'].strip()):
            raise HTTPException(422,'外部处理数据必须填写处理说明并确认')
        rows=spec.get('samples')
        if not isinstance(rows,list) or not 1<=len(rows)<=1024:raise HTTPException(422,'样本清单无效')
        for row in rows:
            if not isinstance(row,dict) or set(row)!={'sample_id','read1','read2'} or not isinstance(row['sample_id'],str):raise HTTPException(422,'样本字段无效')
            yield row,'read1';yield row,'read2'
    elif kind=='mags':
        rows=spec.get('mags')
        if not isinstance(rows,list) or not 1<=len(rows)<=10000:raise HTTPException(422,'MAG 清单无效')
        for row in rows:
            if not isinstance(row,dict) or set(row)!={'mag_id','fasta'}:raise HTTPException(422,'MAG 字段无效')
            yield row,'fasta'
    else:yield spec,'fasta' if kind=='contigs' else 'table'


def safe_path(path,root):
    path=Path(path).absolute();root=Path(root).absolute()
    if not path.is_relative_to(root) or path.resolve()!=path or any(p.is_symlink() for p in (path,*path.parents)):
        raise HTTPException(409,'来源文件路径不安全')
    if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):raise HTTPException(409,'来源文件不可读取')
    return path


class ImportGateway:
    def __init__(self,get_store,input_root,remote):
        self.get_store=get_store;self.input_root=Path(input_root).absolute();self.remote=remote
        self.max_bytes=250*1024**3
        self.reserve_bytes=max(0,int(os.environ.get('BIOFLOW_IMPORT_MINIMUM_FREE_BYTES',str(10*1024**3))))
        with self.get_store().connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS input_dataset_imports(
              id TEXT PRIMARY KEY,project_id TEXT NOT NULL,user_id TEXT NOT NULL,
              idempotency_key TEXT NOT NULL,request_json TEXT NOT NULL,status TEXT NOT NULL,
              error TEXT NOT NULL DEFAULT '',record_json TEXT,provenance_json TEXT,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(project_id,user_id,idempotency_key))''')

    def recover(self):
        try:
            with import_lock(self.input_root,blocking=False),self.get_store().connect() as db:
                db.execute("UPDATE input_dataset_imports SET status='failed',error='服务重启中断导入；文件保留，请使用新幂等键重试',updated_at=CURRENT_TIMESTAMP WHERE status IN ('queued','validating')")
        except BlockingIOError:pass  # Another live worker still owns these jobs.

    def owner(self,pid,uid):
        project=self.get_store().get(pid)
        if not project or project['owner_id']!=uid or project.get('archived'):raise HTTPException(404,'项目不存在')

    def source(self,pid,uid,reference):
        if not isinstance(reference,str) or not re.fullmatch(r'(asset|artifact):[A-Za-z0-9][A-Za-z0-9._-]{0,127}',reference):
            raise HTTPException(422,'请选择项目来源文件标识')
        store=self.get_store();source_kind,identifier=reference.split(':',1)
        if source_kind=='asset':
            with store.connect() as db:row=db.execute('SELECT * FROM assets WHERE id=? AND project_id=? AND user_id=?',(identifier,pid,uid)).fetchone()
            if not row:raise HTTPException(404,'来源文件不存在')
            row=dict(row);metadata=json.loads(row['metadata'])
            # Inspect the unresolved storage path too: uploaded_path resolves symlinks.
            storage=assets.analysis_root()/'runtime/incoming/uploads' if metadata.get('storage')=='analysis' else assets.ROOT/'runtime/assets'
            raw=assets.relative_reference(row['path'],'/runtime/incoming/uploads/' if metadata.get('storage')=='analysis' else '/runtime/assets/')
            if raw is None:raise HTTPException(409,'来源路径无效')
            safe_path(storage/raw,storage)
            path=assets.uploaded_path(row,pid,uid)
            result={'id':reference,'name':row['name'],'size_bytes':row['size_bytes'],'sha256':row['sha256'],'source_task':None,'node':None,'producer':None,'path':path,'root':storage}
        else:
            result=None
            for tid in store.ids(pid,'task'):
                if not store.owns('task',tid,uid):continue
                snapshot=assets.read_snapshot(tid);task=snapshot.get('task',{})
                if task.get('status') not in ('completed','succeeded'):continue
                for row in snapshot.get('artifacts',[]):
                    if row.get('artifact_id')!=identifier or row.get('status')!='active' or not row.get('producer'):continue
                    relative=assets.relative_reference(row.get('path'),'/runtime/outputs/')
                    if not relative:continue
                    root=assets.analysis_root()/'runtime/outputs';path=safe_path(root/relative,root)
                    # Artifact must be physically confined to its owned producer task.
                    safe_path(path,root/tid)
                    result={'id':reference,'name':row.get('file_name') or path.name,'size_bytes':row['size_bytes'],
                            'sha256':row['sha256'],'source_task':tid,'node':row.get('node_id'),'producer':row['producer'],'path':path,'root':root}
            if result is None:raise HTTPException(404,'可复用结果不存在')
        name=result['name'].lower();suffix=next((x for x in EXTENSIONS if name.endswith(x)),None)
        if not suffix or not re.fullmatch('[a-fA-F0-9]{64}',result['sha256'] or ''):raise HTTPException(422,'文件类型或哈希无效')
        result['extension']=suffix
        path=safe_path(result['path'],result['root'])
        if not 0<result['size_bytes']<=self.max_bytes or path.stat().st_size!=result['size_bytes']:raise HTTPException(409,'文件大小异常或超过上限')
        return result

    def status(self,pid,uid,jid):
        self.owner(pid,uid)
        with self.get_store().connect() as db:row=db.execute('SELECT * FROM input_dataset_imports WHERE id=? AND project_id=? AND user_id=?',(jid,pid,uid)).fetchone()
        if not row:raise HTTPException(404,'导入记录不存在')
        return dict(row)

    @staticmethod
    def public(row):
        return {k:row[k] for k in ('id','status','error','created_at','updated_at')}|{'dataset_id':row['id']}

    def create(self,pid,uid,body):
        self.owner(pid,uid);spec=body.spec
        serialized=json.dumps(spec,sort_keys=True,ensure_ascii=False)
        with self.get_store().connect() as db:
            old=db.execute('SELECT * FROM input_dataset_imports WHERE project_id=? AND user_id=? AND idempotency_key=?',(pid,uid,body.idempotency_key)).fetchone()
        if old:
            if old['request_json']!=serialized:raise HTTPException(409,'幂等键已用于不同导入')
            return self.public(dict(old))
        sources={}
        for row,key in slots(spec):
            ref=row.get(key);source=self.source(pid,uid,ref);sources[ref]=source
        jid=uuid.uuid4().hex
        try:
            with self.get_store().connect() as db:
                if db.execute("SELECT count(*) FROM input_dataset_imports WHERE status IN ('queued','validating')").fetchone()[0]>=32:raise HTTPException(429,'导入队列已满')
                db.execute('INSERT INTO input_dataset_imports(id,project_id,user_id,idempotency_key,request_json,status) VALUES(?,?,?,?,?,?)',(jid,pid,uid,body.idempotency_key,serialized,'queued'))
        except sqlite3.IntegrityError:return self.create(pid,uid,body)
        EXECUTOR.submit(self.run,pid,uid,jid)
        return self.public(self.status(pid,uid,jid))

    def run(self,pid,uid,jid):
        try:
            with import_lock(self.input_root):self._run(pid,uid,jid)
        except Exception:
            self._failed(jid)

    def _failed(self,jid,message=None):
        message=message or '导入校验失败：检查文件完整性、类型声明、配对关系与来源权限；已保留暂存文件'
        with self.get_store().connect() as db:db.execute("UPDATE input_dataset_imports SET status='failed',error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(message,jid))

    def _run(self,pid,uid,jid):
        try:
            job=self.status(pid,uid,jid)
            with self.get_store().connect() as db:
                if db.execute("UPDATE input_dataset_imports SET status='validating',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued'",(jid,)).rowcount!=1:return
            spec=json.loads(job['request_json']);folder=self.input_root/'typed-datasets'/jid
            sources={row[key]:self.source(pid,uid,row[key]) for row,key in slots(spec)}
            available_space(self.input_root,sum(s['size_bytes'] for s in sources.values()),self.reserve_bytes)
            folder.mkdir(parents=True,exist_ok=False)
            if folder.resolve()!=folder:raise ValueError('unsafe staging root')
            (folder/'files').mkdir();copied={};provenance=[]
            for row,key in slots(spec):
                ref=row[key]
                if ref not in copied:
                    source=sources[ref];path=safe_path(source['path'],source['root'])
                    before=path.stat();relative='files/'+uuid.uuid4().hex+source['extension'];target=folder/relative
                    digest=hashlib.sha256();size=0
                    flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0)
                    with os.fdopen(os.open(path,flags),'rb') as handle,target.open('xb') as out:
                        opened=os.fstat(handle.fileno())
                        if (opened.st_ino,opened.st_size)!=(before.st_ino,before.st_size):raise ValueError('changed source')
                        while block:=handle.read(4*1024*1024):
                            size+=len(block)
                            if size>self.max_bytes:raise ValueError('source too large')
                            available_space(self.input_root,len(block),self.reserve_bytes)
                            digest.update(block);out.write(block)
                        out.flush();os.fsync(out.fileno())
                    after=path.stat()
                    if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns) or size!=source['size_bytes'] or digest.hexdigest()!=source['sha256']:raise ValueError('changed source')
                    safe_path(path,source['root']);copied[ref]=relative
                    provenance.append({k:v for k,v in source.items() if k not in ('path','root','extension')}|{'staged_file':relative})
                row[key]=copied[ref]
            with self.get_store().connect() as db:db.execute('UPDATE input_dataset_imports SET provenance_json=? WHERE id=?',(json.dumps(provenance),jid))
            origin={'authority':'owned_import_gateway','sources':provenance,'conversion':None}
            record=self.remote('POST','/api/input-datasets/register',{'dataset_id':jid,'spec':spec,'origin':origin})
            self.owner(pid,uid)
            store=self.get_store();store.claim('input_dataset',jid,uid);store.link(pid,'input_dataset',jid)
            with store.connect() as db:db.execute("UPDATE input_dataset_imports SET status='ready',record_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='validating'",(json.dumps(record),jid))
        except ImportDiagnostic as exc:
            self._failed(jid,str(exc))
        except Exception:
            self._failed(jid)

    def candidates(self,pid,uid):
        self.owner(pid,uid);store=self.get_store()
        with store.connect() as db:refs=['asset:'+r[0] for r in db.execute('SELECT id FROM assets WHERE project_id=? AND user_id=?',(pid,uid))]
        for tid in store.ids(pid,'task'):
            if store.owns('task',tid,uid):refs.extend('artifact:'+a['artifact_id'] for a in assets.read_snapshot(tid).get('artifacts',[]) if a.get('artifact_id'))
        rows=[]
        for ref in refs:
            try:
                source=self.source(pid,uid,ref)
                rows.append({k:v for k,v in source.items() if k not in ('path','root','extension')})
            except (HTTPException,OSError,KeyError,ValueError):continue
        return {'items':rows,'catalog':CATALOG,'storage':{'reserve_bytes':self.reserve_bytes,'all_candidate_source_bytes':sum(row['size_bytes'] for row in rows),'estimate_scope':'sum selected unique source IDs for import copies; conversion and execution require additional copies'}}


def install(router,get_store,check_project,urls,gateway=None):
    def remote(method,path,payload=None):
        with httpx.Client(timeout=httpx.Timeout(86400,connect=5),trust_env=False) as client:
            response=client.request(method,urls['analysis']+path,json=payload)
        if response.is_error:
            if response.status_code in (409,422):
                try:detail=response.json().get('detail')
                except ValueError:detail=None
                if isinstance(detail,dict) and detail.get('code') in ('input_contract_invalid','input_immutable') and isinstance(detail.get('message'),str):
                    raise ImportDiagnostic(detail['message'][:512])
            raise HTTPException(response.status_code,'输入数据校验失败')
        return response.json()
    gateway=gateway or ImportGateway(get_store,assets.analysis_root()/'runtime/incoming',remote)
    def who(pid,request):
        check_project(pid,request);uid=request.state.user['id'];gateway.owner(pid,uid);return uid
    @router.post('/input-datasets',status_code=202)
    def create(pid:str,request:Request,body:ImportRequest):return gateway.create(pid,who(pid,request),body)
    @router.get('/input-datasets')
    def listing(pid:str,request:Request):
        uid=who(pid,request)
        with get_store().connect() as db:rows=db.execute('SELECT * FROM input_dataset_imports WHERE project_id=? AND user_id=? ORDER BY created_at DESC',(pid,uid)).fetchall()
        return {'items':[gateway.public(dict(r)) for r in rows],'catalog':CATALOG}
    @router.get('/input-datasets/candidates')
    def candidates(pid:str,request:Request):return gateway.candidates(pid,who(pid,request))
    @router.get('/input-datasets/imports/{jid}')
    def status(pid:str,jid:str,request:Request):return gateway.public(gateway.status(pid,who(pid,request),jid))
    @router.get('/input-datasets/{jid}')
    def detail(pid:str,jid:str,request:Request):
        row=gateway.status(pid,who(pid,request),jid)
        if row['status']!='ready':raise HTTPException(409,'输入尚未通过校验')
        # Interactive reads show immutable import-time metadata, never scan large files.
        record=json.loads(row['record_json'])
        return {**record,'file_integrity':{'state':'not_rechecked','verified_at':'registration',
            'import_completed_at':row['updated_at'],'full_verification_required_before_execution':True}}
    return gateway
