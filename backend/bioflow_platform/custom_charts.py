"""Isolated Python/R plotting with owned, bounded jobs."""
import asyncio,json,os,shutil,sys,uuid
from pathlib import Path
from fastapi import Request,UploadFile,File,HTTPException
from fastapi.responses import FileResponse
ROOT=Path(__file__).resolve().parents[2]
R='/home/xh/.local/share/mamba/envs/biollm-humann/bin/Rscript'

def install(app,get_store,check):
 @app.get('/api/custom-charts/capabilities')
 def capabilities(request:Request):
  if request.state.user.get('role')!='developer':raise HTTPException(403,'代码绘图当前仅对开发者开放')
  return {'languages':['python','r'],'timeout_seconds':60,'outputs':['png']}
 def folder(pid,uid,jid):
  if len(jid)!=32 or any(c not in '0123456789abcdef' for c in jid):raise HTTPException(404,'图表不存在')
  return ROOT/'runtime/custom-charts'/uid/pid/jid
 @app.post('/api/projects/{pid}/custom-charts')
 async def create(pid:str,request:Request,code:UploadFile=File(...),data:UploadFile=File(...)):
  check(pid,request)
  if request.state.user.get('role')!='developer':raise HTTPException(403,'代码绘图当前仅对开发者开放')
  ext=Path(code.filename or '').suffix.lower();de=Path(data.filename or '').suffix.lower()
  if ext not in {'.py','.r'} or de not in {'.csv','.tsv'}:raise HTTPException(422,'请上传 Python/R 代码和 CSV/TSV 数据')
  script=await code.read(512*1024+1);table=await data.read(20*1024**2+1)
  if len(script)>512*1024 or len(table)>20*1024**2:raise HTTPException(413,'代码限512KB，数据限20MB')
  jid=uuid.uuid4().hex;job=folder(pid,request.state.user['id'],jid);job.mkdir(parents=True);(job/'output').mkdir()
  (job/('plot'+ext)).write_bytes(script);(job/('input'+de)).write_bytes(table)
  command=['bwrap','--unshare-all','--die-with-parent','--new-session','--ro-bind','/usr','/usr','--ro-bind','/lib','/lib','--ro-bind','/lib64','/lib64','--ro-bind','/bin','/bin','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/home','--dir','/home/xh']
  paths=['/etc/alternatives','/etc/ld.so.cache','/etc/fonts','/home/xh/BioLLM_RawDataAnalysis/.venv','/home/xh/.local/lib/python3.10/site-packages','/home/xh/.local/share/mamba/envs/biollm-humann']
  for path in paths:
   if Path(path).exists():command+=['--ro-bind',path,path]
  command+=['--bind',str(job),'/work','--chdir','/work','--clearenv','--setenv','PATH','/usr/bin:/bin','--setenv','HOME','/tmp','--setenv','MPLBACKEND','Agg','--setenv','MPLCONFIGDIR','/tmp/mpl','--setenv','OPENBLAS_NUM_THREADS','1','--setenv','OMP_NUM_THREADS','1','--setenv','BIOFLOW_INPUT','/work/input'+de,'--setenv','BIOFLOW_OUTPUT','/work/output']
  if ext=='.py':command+=['--setenv','PYTHONPATH','/home/xh/.local/lib/python3.10/site-packages',sys.executable,'/work/plot.py']
  else:command+=['--setenv','R_HOME','/home/xh/.local/share/mamba/envs/biollm-humann/lib/R','--setenv','R_LIBS_SITE','/home/xh/.local/share/mamba/envs/biollm-humann/lib/R/library',R,'--vanilla','/work/plot.r']
  log=job/'run.log'
  async def execute():
   with log.open('wb') as out:
    process=await asyncio.create_subprocess_exec('/usr/bin/prlimit','--cpu=45','--fsize=26214400','--as=3221225472','--nproc=4096','--','/usr/bin/timeout','--kill-after=3','60',*command,stdout=out,stderr=out)
    await process.wait()
   outputs=[]
   from PIL import Image
   for f in sorted((job/'output').glob('*.png'))[:12]:
    if f.stat().st_size>25*1024**2:continue
    try:
     with Image.open(f) as im:
      if im.width*im.height>20000000:continue
      im.verify()
     outputs.append({'name':f.name,'url':f'/api/projects/{pid}/custom-charts/{jid}/files/{f.name}'})
    except Exception:continue
   text=log.read_bytes()[-16000:].decode(errors='replace')
   result={'id':jid,'status':'completed' if process.returncode==0 and outputs else 'failed','outputs':outputs,'error':text if process.returncode or not outputs else '', 'message':'请将 PNG 图片保存到 BIOFLOW_OUTPUT 目录' if not outputs else ''}
   (job/'result.json').write_text(json.dumps(result,ensure_ascii=False));return result
  return await execute()
 @app.get('/api/projects/{pid}/custom-charts/{jid}/files/{name}')
 def output(pid:str,jid:str,name:str,request:Request):
  check(pid,request);job=folder(pid,request.state.user['id'],jid)
  try:result=json.loads((job/'result.json').read_text())
  except OSError:raise HTTPException(404,'图表不存在')
  if name not in {r['name'] for r in result['outputs']} or Path(name).name!=name:raise HTTPException(404,'图表不存在')
  return FileResponse(job/'output'/name,media_type='image/png')

