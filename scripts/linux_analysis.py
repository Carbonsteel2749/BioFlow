"""Linux/WSL entry point for the existing analysis backend."""
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'BioLLM_RawDataAnalysis'),str(ROOT/'.runtime/linux'),str(ROOT)]
os.environ.setdefault('BIOLLM_EXECUTION_ISOLATION','process_group')

if __name__=='__main__':
    import uvicorn
    uvicorn.run('backend.app.main:create_app',factory=True,host='127.0.0.1',port=8000)
