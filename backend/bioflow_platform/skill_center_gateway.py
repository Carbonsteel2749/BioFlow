"""Native account integration for the shared Skill and algorithm center."""
import json
from pathlib import Path
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.background import BackgroundTask

PREFIX = "/skill-center"


def gateway(authorize, access, frontend, upstream="http://127.0.0.1:18120", transport=None):
    app = FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    frontend = Path(frontend).resolve()

    @app.api_route(PREFIX,methods=["GET"])
    @app.api_route(PREFIX+"/{path:path}",methods=["GET","HEAD","POST","PUT","PATCH","DELETE"])
    async def forward(request:Request,path:str=""):
        user = authorize(request)
        if not isinstance(user,dict):return user
        if "\\" in path or any(p in {".",".."} for p in path.split("/")):
            raise HTTPException(400,"无效路径")
        if path=="entry.json":
            manifest=json.loads((frontend/".vite/manifest.json").read_text())
            entry=manifest.get("src/main.tsx",manifest["index.html"])
            return JSONResponse({"module":PREFIX+"/"+entry["file"],"css":[PREFIX+"/"+x for x in entry.get("css",[])]},headers={"Cache-Control":"no-store"})
        if path.startswith("assets/"):
            if request.method not in {"GET","HEAD"}:raise HTTPException(405,"方法不允许")
            file=(frontend/path).resolve()
            if not file.is_relative_to(frontend) or not file.is_file():raise HTTPException(404,"资源不存在")
            return FileResponse(file,headers={"Cache-Control":"no-cache","X-Content-Type-Options":"nosniff"})
        if not path.startswith("api/v1/"):
            if path and path.split("/")[0] not in {"skills","sources","algorithms","plots","runs"}:raise HTTPException(404,"接口不存在")
            return RedirectResponse("/?"+urlencode({"skill_center":"/"+(path or "skills")+("?"+request.url.query if request.url.query else "")}),status_code=303)
        if request.method not in {"GET","HEAD"}:
            if request.headers.get("origin","").rstrip("/") != str(request.base_url).rstrip("/"):
                raise HTTPException(403,"来源与当前工作台不一致")
            if not secrets.compare_digest(request.headers.get("x-bioflow-csrf",""),user["csrf"]):
                raise HTTPException(403,"会话校验已过期，请刷新页面")
        if path=="api/v1/auth/me":
            return JSONResponse({"role":user["role"],"username":user["username"],"authentication":"bioflow"},headers={"Cache-Control":"no-store"})
        if path.startswith("api/v1/auth/"):
            raise HTTPException(403,"请使用总系统的账户菜单登录或退出")
        payload={}
        is_json="application/json" in request.headers.get("content-type","")
        if is_json and request.method not in {"GET","HEAD"}:
            if int(request.headers.get("content-length","0"))>1024*1024:raise HTTPException(413,"请求过大")
            try:payload=await request.json()
            except ValueError:raise HTTPException(422,"JSON 格式无效")
            if not isinstance(payload,dict):raise HTTPException(422,"请求需要是对象")
        payload=access.check(path,request.method,payload,request.query_params,user)
        if request.method in {"GET","HEAD"}:
            local=access.local_read(path,request.query_params,user)
            if local is not None:return JSONResponse(local,headers={"Cache-Control":"no-store"})
        token=access.bridge_token(user,request.cookies.get("bioflow_session",""))
        headers={k:v for k,v in request.headers.items() if k.lower() in {"content-type","accept","range","if-none-match","if-modified-since"}}
        headers["cookie"]="sc_session="+token
        if request.method not in {"GET","HEAD"}:headers["origin"]=upstream
        raw=request.scope["raw_path"].split(b"?",1)[0]
        target=upstream+raw[len(PREFIX.encode()):].decode("ascii")
        if request.url.query:target+="?"+request.url.query
        client=httpx.AsyncClient(transport=transport,trust_env=False,timeout=httpx.Timeout(600,connect=3),follow_redirects=False)
        try:
            outgoing=client.build_request(request.method,target,headers=headers,content=json.dumps(payload).encode() if is_json and request.method not in {"GET","HEAD"} else request.stream())
            response=await client.send(outgoing,stream=True)
        except httpx.HTTPError:
            await client.aclose();raise HTTPException(503,"Skill 中心暂时不可用")
        async def close():
            await response.aclose();await client.aclose()
        if response.is_redirect:
            await close();raise HTTPException(502,"上游返回了非预期跳转")
        if "application/json" in response.headers.get("content-type",""):
            try:
                data=json.loads(await response.aread())
                if response.is_success:data=access.after(path,request.method,data,user)
                return JSONResponse(data,status_code=response.status_code,headers={"Cache-Control":"no-store"})
            finally:await close()
        result=StreamingResponse(response.aiter_raw(),status_code=response.status_code,background=BackgroundTask(close))
        for name in ["content-type","content-disposition","content-encoding","etag","last-modified","accept-ranges","content-range"]:
            if name in response.headers:result.headers[name]=response.headers[name]
        result.headers["Cache-Control"]="no-store"
        result.headers["X-Content-Type-Options"]="nosniff"
        return result
    return app


class IntegratedApp:
    def __init__(self,total_app,authorize,access,frontend,upstream="http://127.0.0.1:18120"):
        self.total_app=total_app
        self.center=gateway(authorize,access,frontend,upstream)
    async def __call__(self,scope,receive,send):
        path=scope.get("path","")
        if scope["type"]=="http" and (path==PREFIX or path.startswith(PREFIX+"/")):
            await self.center(scope,receive,send)
        else:await self.total_app(scope,receive,send)
