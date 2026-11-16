"""Reuse installed Qwen weights and CUDA libraries in an independent loopback service."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:11436'


def ensure():
    config={'BIOFLOW_PUBLIC_QA_PROVIDER':'openai','BIOFLOW_PUBLIC_QA_URL':URL+'/v1/chat/completions'}
    try:
        with urllib.request.urlopen(URL+'/health',timeout=2) as r:
            if r.status==200:return config
    except OSError:pass
    directory=Path(os.getenv('BIOFLOW_LLAMA_ROOT','/snap/ollama/141/lib/ollama'))
    model=Path(os.getenv('BIOFLOW_PUBLIC_QWEN_GGUF','/var/snap/ollama/common/models/blobs/sha256-a8cc1361f3145dc01f6d77c6c82c9116b9ffe3c97b34716fe20418455876c40e'))
    cuda=directory/'cuda_v12';binary=directory/'llama-server'
    if not binary.is_file() or not model.is_file() or not (cuda/'libggml-cuda.so').is_file():return {}
    env=os.environ.copy();env['LD_LIBRARY_PATH']=str(cuda)+':'+str(directory)+':'+env.get('LD_LIBRARY_PATH','');env['GGML_BACKEND_PATH']=str(cuda/'libggml-cuda.so')
    log_path=ROOT/'runtime/logs/public-literature-inference.log';log_path.parent.mkdir(parents=True,exist_ok=True)
    args=[str(binary),'--model',str(model),'--alias','qwen3:14b','--host','127.0.0.1','--port','11436',
        '--gpu-layers','all','--ctx-size','4096','--parallel','1','--batch-size','128','--ubatch-size','128','--threads','4',
        '--jinja','--chat-template-kwargs',json.dumps({'enable_thinking':False}),'--reasoning-budget','0','--no-webui','--offline']
    with log_path.open('ab') as log:child=subprocess.Popen(args,cwd=ROOT,env=env,stdout=log,stderr=log,start_new_session=True)
    (ROOT/'runtime/public-literature-inference.pid').write_text(str(child.pid))
    for _ in range(100):
        if child.poll() is not None:raise RuntimeError('文献推理进程启动失败，请查看 public-literature-inference.log')
        try:
            with urllib.request.urlopen(URL+'/health',timeout=1) as r:
                if r.status==200:return config
        except OSError:time.sleep(.5)
    raise RuntimeError('文献推理仍未就绪，请查看日志')


if __name__=='__main__':print(json.dumps(ensure()))
