"""Account-owned papers with independent files and project-specific indexing."""
import json
import os
import shutil
import uuid
from pathlib import Path
from fastapi import HTTPException,Request
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field,ConfigDict
from . import assets


def remember(store,uid,name,path,sha,size,metadata,probe=True):
    with store.connect() as db:row=db.execute('SELECT id FROM personal_documents WHERE user_id=? AND sha256=?',(uid,sha)).fetchone()
    if row:return row['id']
    identifier=uuid.uuid4().hex;relative=Path(uid)/(identifier+Path(name).suffix.lower())
    target=assets.ROOT/'runtime/personal-library'/relative;target.parent.mkdir(parents=True,exist_ok=True)
    try:os.link(path,target)
    except OSError:shutil.copyfile(path,target)
    from .paper_metadata import extract
    bibliographic=extract(path,name) if probe else {'title':metadata.get('title') or Path(name).stem,'authors':'','year':'','doi':'','tags':''}
    with store.connect() as db:
        db.execute('INSERT OR IGNORE INTO personal_documents(id,user_id,sha256,name,path,size_bytes,metadata) VALUES(?,?,?,?,?,?,?)',
            (identifier,uid,sha,name,relative.as_posix(),size,json.dumps(bibliographic,ensure_ascii=False)))
        return db.execute('SELECT id FROM personal_documents WHERE user_id=? AND sha256=?',(uid,sha)).fetchone()['id']


class PaperDetails(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=1,max_length=500)
    authors:str=Field(default='',max_length=1000)
    year:str=Field(default='',pattern=r'^([12]\d{3})?$')
    doi:str=Field(default='',max_length=250)
    tags:str=Field(default='',max_length=500)


def install_library(app,router,get_store,check_project,files):
    def paper(identifier,user):
        with get_store().connect() as db:row=db.execute('SELECT * FROM personal_documents WHERE id=? AND user_id=?',(identifier,user['id'])).fetchone()
        if not row:raise HTTPException(404,'文献不存在')
        return dict(row)

    def path_for(row):
        base=(assets.ROOT/'runtime/personal-library'/row['user_id']).resolve()
        path=(assets.ROOT/'runtime/personal-library'/row['path']).resolve()
        if not path.is_relative_to(base) or assets.availability(path,row['sha256'])[0]!='ready':raise HTTPException(409,'文献文件缺失或校验失败')
        return path

    @app.get('/api/library/literature')
    def listing(request:Request):
        store=get_store();uid=request.state.user['id']
        # Import pre-existing papers lazily; old records and their file paths stay intact.
        with store.connect() as db:old=[dict(r) for r in db.execute("SELECT * FROM assets WHERE user_id=? AND kind='literature'",(uid,))]
        for row in old:
            m=json.loads(row['metadata'])
            if m.get('personal_document_id'):continue
            try:pid=remember(store,uid,row['name'],assets.uploaded_path(row,row['project_id'],uid),row['sha256'],row['size_bytes'],m,probe=False)
            except OSError:continue
            m['personal_document_id']=pid
            with store.connect() as db:db.execute('UPDATE assets SET metadata=? WHERE id=?',(json.dumps(m,ensure_ascii=False),row['id']))
        with store.connect() as db:
            papers=[dict(r) for r in db.execute('SELECT * FROM personal_documents WHERE user_id=? ORDER BY created_at DESC',(uid,))]
            linked=[dict(r) for r in db.execute("SELECT a.id,a.project_id,a.status,a.metadata,p.name AS project_name FROM assets a JOIN projects p ON p.id=a.project_id WHERE a.user_id=? AND a.kind='literature' AND a.status!='archived'",(uid,))]
        return {'items':[{'id':p['id'],'name':p['name'],'size_bytes':p['size_bytes'],'metadata':json.loads(p['metadata']),
            'projects':[{'id':a['project_id'],'name':a['project_name'],'asset_id':a['id'],'file_status':'已保存','index_status':json.loads(a['metadata']).get('index_status','pending'),'status':a['status']} for a in linked if json.loads(a['metadata']).get('personal_document_id')==p['id']]} for p in papers]}

    @app.patch('/api/library/literature/{identifier}')
    def edit(identifier:str,body:PaperDetails,request:Request):
        paper(identifier,request.state.user)
        with get_store().connect() as db:db.execute('UPDATE personal_documents SET metadata=? WHERE id=? AND user_id=?',(body.model_dump_json(),identifier,request.state.user['id']))
        return {'ok':True}

    @app.get('/api/library/literature/{identifier}/download')
    def download(identifier:str,request:Request):
        row=paper(identifier,request.state.user);return FileResponse(path_for(row),filename=row['name'])

    @router.post('/literature/{identifier}/link')
    async def link(pid:str,identifier:str,request:Request):
        check_project(pid,request);user=request.state.user;row=paper(identifier,user);source=path_for(row)
        with get_store().connect() as db:existing=[dict(r) for r in db.execute("SELECT * FROM assets WHERE project_id=? AND user_id=? AND kind='literature' AND status!='archived'",(pid,user['id']))]
        if any(json.loads(r['metadata']).get('personal_document_id')==identifier for r in existing):return {'ok':True,'already_linked':True}
        aid=uuid.uuid4().hex;target=assets.ROOT/'runtime/assets'/user['id']/pid/(aid+Path(row['name']).suffix.lower());target.parent.mkdir(parents=True,exist_ok=True)
        try:os.link(source,target)
        except OSError:shutil.copyfile(source,target)
        return await files.adopt(pid,user,aid,row['name'],target,row['sha256'],row['size_bytes'],purpose='literature',details={'personal_document_id':identifier,**json.loads(row['metadata'])})

    @router.post('/literature/{identifier}/unlink')
    async def unlink(pid:str,identifier:str,request:Request):
        check_project(pid,request);user=request.state.user;paper(identifier,user)
        with get_store().connect() as db:rows=[dict(r) for r in db.execute("SELECT * FROM assets WHERE project_id=? AND user_id=? AND kind='literature' AND status!='archived'",(pid,user['id']))]
        for row in rows:
            m=json.loads(row['metadata'])
            if m.get('personal_document_id')!=identifier:continue
            if m.get('document_id') and m.get('knowledge_base_id'):
                try:await files.call('literature','/api/knowledge-bases/'+m['knowledge_base_id']+'/documents/'+m['document_id'],'DELETE')
                except HTTPException as exc:
                    if exc.status_code!=404:raise HTTPException(503,'知识库暂不可连接，关联保留，请稍后重试') from exc
            with get_store().connect() as db:db.execute("UPDATE assets SET status='archived' WHERE id=?",(row['id'],))
            with get_store().connect() as db:
                for entry in db.execute('SELECT id,payload FROM catalogue_entries WHERE project_id=? AND user_id=?',(pid,user['id'])).fetchall():
                    payload=json.loads(entry['payload'])
                    if m.get('document_id') and payload.get('kind')=='literature' and payload.get('document_id')==m['document_id']:db.execute('DELETE FROM catalogue_entries WHERE project_id=? AND user_id=? AND id=?',(pid,user['id'],entry['id']))
                db.execute('UPDATE catalogue_sync SET updated_at=0 WHERE project_id=? AND user_id=?',(pid,user['id']))
        return {'ok':True,'personal_file_retained':True}
