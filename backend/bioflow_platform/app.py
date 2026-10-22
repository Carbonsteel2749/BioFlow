"""BioFlow single-origin gateway. No legacy frontend is served here."""
import asyncio
import os
import secrets
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from .store import Store
from . import auth, access, record_status

ROOT = Path(__file__).resolve().parents[2]
SERVICES = {
    'analysis': os.getenv('BIOFLOW_ANALYSIS_URL', 'http://127.0.0.1:8000').rstrip('/'),
    'writing': os.getenv('BIOFLOW_WRITING_URL', 'http://127.0.0.1:8765').rstrip('/'),
    'literature': os.getenv('BIOFLOW_LITERATURE_URL', 'http://127.0.0.1:8080').rstrip('/'),
}
store = Store(os.getenv('BIOFLOW_DATABASE', str(ROOT/'runtime'/'bioflow.sqlite3')))
app = FastAPI(title='BioFlow Unified API')
app.mount('/static', StaticFiles(directory=ROOT/'frontend'), name='static')


@app.middleware('http')
async def same_origin(request: Request, call_next):
    origin = request.headers.get('origin')
    if request.method not in {'GET','HEAD','OPTIONS'} and origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
        raise_error = {'detail': '来源与当前工作台不一致'}
        return JSONResponse(raise_error, status_code=403)
    path=request.url.path
    public=path in {'/','/ai4s-interface.html','/api/auth/me','/api/auth/login','/api/auth/register'} or path.startswith('/static/')
    request.state.user=auth.session_user(store,request.cookies.get(auth.COOKIE))
    if not public:
        user=request.state.user
        if not user:
            return JSONResponse({'detail':'请登录后继续'},status_code=401)
        if request.method not in {'GET','HEAD','OPTIONS'} and not secrets.compare_digest(request.headers.get('x-bioflow-csrf',''),user['csrf']):
            return JSONResponse({'detail':'会话校验已过期，请刷新页面'},status_code=403)
    response=await call_next(request)
    if path.startswith('/api/') or path.startswith('/services/'):
        response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    return response


class Credentials(BaseModel):
    username: str = Field(min_length=3,max_length=32)
    password: str = Field(min_length=8,max_length=128)


@app.get('/api/auth/me')
def me(request: Request):
    return {'user':request.state.user}


def authenticated_response(user,status_code=200):
    token,csrf=auth.new_session(store,user['id'])
    response=JSONResponse({'user':{**user,'csrf':csrf}},status_code=status_code)
    response.set_cookie(auth.COOKIE,token,httponly=True,samesite='lax',secure=os.getenv('BIOFLOW_SECURE_COOKIE','0')=='1',path='/')
    response.headers['Cache-Control']='no-store'
    return response


@app.post('/api/auth/register',status_code=201)
def register(body: Credentials):
    if os.getenv('BIOFLOW_ALLOW_REGISTRATION','1')!='1':
        raise HTTPException(403,'当前工作空间由管理员创建账户')
    user=auth.create_user(store,body.username,body.password)
    return authenticated_response(user,status_code=201)


@app.post('/api/auth/login')
def login(body: Credentials,request: Request):
    return authenticated_response(auth.login(store,body.username,body.password,request.client.host if request.client else 'local'))


@app.post('/api/auth/logout')
def logout(request: Request):
    auth.logout(store,request.cookies.get(auth.COOKIE))
    response=JSONResponse({'ok':True})
    response.delete_cookie(auth.COOKIE,path='/')
    return response


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1,max_length=128)
    new_password: str = Field(min_length=8,max_length=128)


@app.post('/api/auth/password')
def change_password(body: PasswordChange,request: Request):
    user=request.state.user
    with store.connect() as db:
        row=db.execute('SELECT password_hash FROM users WHERE id=?',(user['id'],)).fetchone()
        if not row or not auth.verify_password(body.current_password,row[0]):
            raise HTTPException(400,'当前密码不正确')
        db.execute('UPDATE users SET password_hash=? WHERE id=?',(auth.hash_password(body.new_password),user['id']))
        db.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
    return authenticated_response({key:user[key] for key in ('id','username','role')})


@app.get('/')
@app.get('/ai4s-interface.html')
def index():
    return FileResponse(ROOT/'ai4s-interface.html', headers={'Cache-Control':'no-cache'})


def project(pid,request):
    item = store.get(pid)
    if not item or item['archived'] or item['owner_id']!=request.state.user['id']:
        raise HTTPException(404, '项目不存在')
    return item


async def upstream(service, path, method='GET', payload=None, headers=None):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8 if method=='GET' else 600, connect=3), trust_env=False) as client:
            response = await client.request(method, SERVICES[service]+path, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(503, f'{service} 服务尚未连接：{SERVICES[service]}') from exc
    if response.is_error:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:1000]
        raise HTTPException(response.status_code, detail)
    return response.json()


class ProjectBody(BaseModel):
    name: str = Field(min_length=1,max_length=120)
    description: str = Field(default='',max_length=2000)


from .workspace import install as install_workspace
install_workspace(app,lambda:store,project,upstream,SERVICES)


@app.get('/api/projects')
def projects(request: Request):
    return store.projects(request.state.user['id'])


@app.post('/api/projects', status_code=201)
def create_project(body: ProjectBody,request: Request):
    if not body.name.strip():
        raise HTTPException(422,'项目名称不能为空')
    return store.create(body.name.strip(),body.description,request.state.user['id'])


@app.get('/api/projects/{pid}/history')
def history(pid: str,request: Request):
    project(pid,request)
    return store.history(pid)


class Binding(BaseModel):
    id: str = Field(min_length=1,max_length=200,pattern=r'^[A-Za-z0-9._-]+$')


@app.put('/api/projects/{pid}/knowledge-base')
async def bind_kb(pid: str, body: Binding, request: Request):
    project(pid,request)
    access.allowed(store,'kb',body.id,request.state.user)
    await upstream('literature',f'/api/knowledge-bases/{body.id}/health',headers={k:v for k,v in request.headers.items() if k.lower() in {'cookie','authorization'}})
    store.bind_kb(pid,body.id)
    store.claim('kb',body.id,request.state.user['id'])
    return project(pid,request)


@app.post('/api/projects/{pid}/links/{kind}')
async def link(pid: str,kind: str,body: Binding,request: Request):
    project(pid,request)
    paths={'task':('analysis','/api/tasks/'), 'session':('writing','/api/sessions/'), 'workflow':('analysis','/api/workflows/runs/')}
    if kind not in paths:
        raise HTTPException(422,'关联类型无效')
    if not store.owns(kind,body.id,request.state.user['id']):
        raise HTTPException(404,'该记录不属于当前账户')
    service,path=paths[kind]
    await upstream(service,path+body.id)
    store.link(pid,kind,body.id)
    return {'id':body.id}


@app.get('/api/projects/{pid}/links/{kind}')
def links(pid: str,kind: str,request: Request):
    project(pid,request)
    return store.ids(pid,kind)


@app.get('/api/status')
async def status():
    async def probe(name,base):
        try:
            async with httpx.AsyncClient(timeout=2,trust_env=False) as client:
                r=await client.get(base+('/health' if name=='literature' else '/api/health'))
            return {'name':name,'url':base,'ok':r.is_success,'status':r.status_code}
        except httpx.HTTPError:
            return {'name':name,'url':base,'ok':False,'status':0}
    return await asyncio.gather(*(probe(k,v) for k,v in SERVICES.items()))


@app.get('/api/skills')
def skills():
    from bioflow_skills import list_catalog
    return [entry.to_dict() for entry in list_catalog()]


@app.get('/api/models')
async def models():
    entries=[]
    errors=[]
    writing_config,literature_config=await asyncio.gather(upstream('writing','/api/llm-config'),upstream('literature','/api/config'),return_exceptions=True)
    try:
        if isinstance(writing_config,Exception):raise writing_config
        result=writing_config
        config=result.get('config',{})
        if config.get('model'):
            entries.append({'id':'writing-model','name':config['model'],'category':'language',
                'provider':config.get('provider',''),'functions':['writing','analysis'],
                'description':'用于论文起草、章节修订与结果解释',
                'available':result.get('model_available'),'configured':True})
        for name in result.get('available_models',[]):
            if not any(e['name']==name for e in entries):
                entries.append({'id':'local-'+name,'name':name,'category':'unassigned','provider':'ollama',
                    'functions':['general'],'description':'本地模型服务已安装；可由开发者配置用途',
                    'available':True,'configured':False})
    except HTTPException:
        errors.append('写作模型配置暂不可读取')
    try:
        if isinstance(literature_config,Exception):raise literature_config
        result=literature_config
        for key,category,label in [('chat','language','文献问答'),('embedding','embedding','文献向量检索')]:
            config=result.get(key,{})
            if config.get('model'):
                entries.append({'id':'literature-'+key,'name':config['model'],'category':category,
                    'provider':config.get('provider',''),'functions':['literature'],
                    'description':label,'available':None,'configured':True})
    except HTTPException:
        errors.append('文献模型配置暂不可读取')
    return {'items':entries,'notes':errors}


class SkillBody(BaseModel):
    payload: dict = Field(default_factory=dict)
    version: str | None = None


@app.post('/api/projects/{pid}/skills/{name}')
def run(pid: str,name: str,body: SkillBody,request: Request):
    project(pid,request)
    if name=='file_profile' and request.state.user['role']!='developer':
        raise HTTPException(403,'服务器文件检查由开发者管理')
    from bioflow_skills import run_skill
    result=run_skill(name,body.payload,version=body.version).to_dict()
    store.event(pid,'执行 Skill',result)
    return result


@app.api_route('/services/{service}/{path:path}',methods=['GET','POST','PUT','PATCH','DELETE'])
async def proxy(service: str,path: str,request: Request):
    if service not in SERVICES or not (path.startswith('api/') or path.startswith('v1/') or path=='health'):
        raise HTTPException(404,'接口不存在')
    # Avoid path normalization escaping the fixed service origin.
    if any(part in {'.','..'} for part in path.split('/')) or '\\' in path:
        raise HTTPException(400,'无效路径')
    await access.authorize(store,service,path,request,{'call':upstream,'urls':SERVICES})
    if service=='analysis' and request.method=='GET' and path.startswith('api/tasks/') and path.endswith('/results'):
        tid=path.split('/')[2]
        with store.connect() as db:
            linked=db.execute("SELECT p.id FROM projects p JOIN links l ON l.project_id=p.id WHERE p.owner_id=? AND p.archived=0 AND l.kind='task' AND l.external_id=? LIMIT 1",(request.state.user['id'],tid)).fetchone()
        if linked:return RedirectResponse(f'/api/projects/{linked[0]}/tasks/{tid}/archive',status_code=307)
    url=SERVICES[service]+'/'+quote(path,safe='/@:-._~')
    if request.url.query:
        url+='?'+request.url.query
    headers={k:v for k,v in request.headers.items() if k.lower() in {
        'content-type','accept','cookie','authorization','x-csrf-token','x-upload-token','range'}}
    if 'cookie' in headers:
        headers['cookie']='; '.join(part.strip() for part in headers['cookie'].split(';') if part.strip().split('=',1)[0]!=auth.COOKIE)
    client=httpx.AsyncClient(timeout=httpx.Timeout(600,connect=3),trust_env=False)
    try:
        outgoing=client.build_request(request.method,url,headers=headers,content=request.stream())
        response=await client.send(outgoing,stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(503,f'{service} 服务尚未连接：{SERVICES[service]}') from exc
    async def close():
        await response.aclose()
        await client.aclose()
    if 'application/json' in response.headers.get('content-type',''):
        try:
            data=await response.aread()
            import json
            parsed=json.loads(data)
            if response.is_success:
                parsed=access.process_json(store,service,path,request.method,parsed,request.state.user)
                if service=='analysis' and path=='api/tasks' and isinstance(parsed,list):
                    parsed=[record_status.annotate(t) for t in parsed]
            result=JSONResponse(parsed,status_code=response.status_code)
            for cookie in response.headers.get_list('set-cookie'):
                result.headers.append('set-cookie',cookie)
            await close()
            return result
        except (ValueError,UnicodeError):
            await close()
            raise HTTPException(502,'上游返回了无效的接口数据')
    async def stream():
        if response.is_stream_consumed:
            yield response.content
        else:
            async for chunk in response.aiter_raw():
                yield chunk
    result=StreamingResponse(stream(), status_code=response.status_code,
        headers={k:v for k,v in response.headers.items() if k.lower() in {
            'content-type','content-disposition','content-range','accept-ranges','content-encoding'}},
        background=BackgroundTask(close))
    for cookie in response.headers.get_list('set-cookie'):
        result.headers.append('set-cookie',cookie)
    return result
