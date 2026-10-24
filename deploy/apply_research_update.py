"""Validate and back up a code-only update before replacing project files."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from datetime import datetime

def apply(package,target):
    package=Path(package).resolve();target=Path(target).resolve()
    if not (target/'Start-BioFlow.sh').is_file() or not (target/'runtime/bioflow.sqlite3').is_file():raise ValueError('目标应为已部署的 BioFlow 根目录')
    manifest=json.loads((package/'UPDATE-MANIFEST.json').read_text(encoding='utf-8'));sources=[]
    for item in manifest['files']:
        relative=Path(item['path']);source=(package/'payload'/relative).resolve();destination=(target/relative).resolve()
        if relative.is_absolute() or '..' in relative.parts or not source.is_relative_to(package/'payload') or not destination.is_relative_to(target):raise ValueError('更新路径无效')
        if relative.parts[0] in {'runtime','.runtime','.venv'} or 'runtime' in relative.parts:raise ValueError('更新包不得覆盖项目运行数据')
        if hashlib.sha256(source.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('更新文件校验失败：'+str(relative))
        sources.append((source,destination,relative))
    backup=target/'runtime/backups'/('research-code-update-'+datetime.now().strftime('%Y%m%d-%H%M%S'));backup.mkdir(parents=True,exist_ok=False)
    with sqlite3.connect(target/'runtime/bioflow.sqlite3') as source,sqlite3.connect(backup/'bioflow.sqlite3') as dest:source.backup(dest)
    for _,destination,relative in sources:
        if destination.is_file():
            old=backup/'files'/relative;old.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(destination,old)
    for source,destination,_ in sources:
        destination.parent.mkdir(parents=True,exist_ok=True);temporary=destination.with_name(destination.name+'.research-update')
        shutil.copyfile(source,temporary);os.replace(temporary,destination)
    (backup/'updated-files.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print('代码更新完成，项目数据和账户保留。备份：'+str(backup))
    return backup

if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('用法：python apply_research_update.py /home/xh/BioFlow')
    apply(Path(__file__).resolve().parent,sys.argv[1])
