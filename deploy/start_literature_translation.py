"""Start the existing server's offline medical translation model, without a crawler."""
import os
from pathlib import Path
import subprocess
import urllib.request


def ensure():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8090/health',timeout=2) as r:
            if r.status==200:return
    except OSError:pass
    root=Path(__file__).resolve().parents[1];directory=Path(os.getenv('BIOFLOW_MT_SCRIPTS','/home/xh/BioFLow/Article_repository/scripts'))
    python=Path(os.getenv('BIOFLOW_MT_PYTHON','/home/xh/rag_env/bin/python'))
    if not (directory/'mt_service.py').is_file() or not python.is_file():return
    env=os.environ.copy();env.update({'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','MT_THREADS':'2','OMP_NUM_THREADS':'2'})
    logs=root/'runtime/logs';logs.mkdir(parents=True,exist_ok=True)
    with (logs/'literature-translation.log').open('ab') as log:
        child=subprocess.Popen([str(python),'-m','uvicorn','mt_service:app','--host','0.0.0.0','--port','8090','--log-level','warning'],cwd=directory,env=env,stdout=log,stderr=log,start_new_session=True)
    (root/'runtime/literature-translation.pid').write_text(str(child.pid))


if __name__=='__main__':ensure()
