from pathlib import Path
import shutil
import subprocess
import pytest


def test_sha256_http_fallback_matches_node_crypto():
    node=shutil.which('node')
    if not node:pytest.skip('Node is only needed to verify the browser SHA implementation')
    script="""
const fs=require('fs'),vm=require('vm'),crypto=require('crypto');
const ctx={self:{},TextEncoder,Uint8Array,Uint32Array,DataView};vm.createContext(ctx);
vm.runInContext(fs.readFileSync('frontend/upload-hash-worker.js','utf8'),ctx);
for(const n of [0,1,3,55,56,63,64,65,1000,1048576]){
 const b=crypto.randomBytes(n);ctx.input=new Uint8Array(b);
 const actual=vm.runInContext('sha256Fallback(input)',ctx);
 if(actual!==crypto.createHash('sha256').update(b).digest('hex'))throw Error('SHA mismatch '+n);
}
"""
    subprocess.run([node,'-e',script],cwd=Path(__file__).resolve().parents[1],check=True,capture_output=True,text=True,timeout=20)
