"""Recover published files from the exact task's Nextflow cache; never use clean reads as raw."""
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'.runtime/python')]
from backend.bioflow_platform import assets
from backend.bioflow_platform.store import Store


def digest(path):
    sha=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):sha.update(block)
    return sha.hexdigest()


def recover(source,target,backup):
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if target.stat().st_size:return False
        saved=backup/(hashlib.sha256(str(target).encode()).hexdigest()+'.empty')
        target.replace(saved)
    os.link(source,target)
    return True


def linux_path(path):
    path=path.resolve().as_posix()
    return '/mnt/'+path[0].lower()+path[2:] if re.match(r'^[A-Za-z]:',path) else path


def main():
    root=assets.analysis_root();backup=root/'runtime/backups'/('link-repair-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(parents=True)
    store=Store(ROOT/'runtime/bioflow.sqlite3');report={'artifacts':[],'reads':[],'manifests':[],'unresolved':[]}
    tids={tid for p in store.projects() for tid in store.ids(p['id'],'task') if store.owns('task',tid,p['owner_id'])}
    for tid in tids:
        snapshot=assets.read_snapshot(tid);task=snapshot.get('task',{});work=root/'runtime/work'/tid
        index={}
        for p in work.rglob('*'):
            if p.is_file() and p.stat().st_size:index.setdefault(p.name,[]).append(p)
        for a in snapshot.get('artifacts',[]):
            name=a.get('file_name') or Path(a['path']).name;target=assets.local_output(a['path'])
            if target is None:continue
            if target.is_file() and target.stat().st_size>0:continue
            match=next((p for p in index.get(name,[]) if p.stat().st_size==a.get('size_bytes') and digest(p)==a.get('sha256')),None)
            if match:
                recover(match,target,backup);report['artifacts'].append({'id':a['artifact_id'],'task_id':tid,'path':str(target.relative_to(root)),'sha256':a['sha256']})
            else:report['unresolved'].append(a['artifact_id'])
        manifest=root/'runtime/incoming/manifests'/Path(task.get('manifest_path','')).name
        trace=root/'runtime/outputs'/tid/'trace.tsv'
        if not manifest.is_file() or not trace.is_file():continue
        stages={}
        with trace.open(encoding='utf-8-sig') as f:
            for row in csv.DictReader(f,delimiter='\t'):
                match=re.search(r'FASTQC_RAW \(([^)]+)\)',row.get('name',''))
                if match and row.get('status') in {'COMPLETED','CACHED'}:
                    prefix,short=row['hash'].split('/')
                    folders=[p for p in (work/prefix).glob(short+'*') if p.is_dir()]
                    if len(folders)==1:stages[match[1]]=folders[0]
        with manifest.open(encoding='utf-8-sig') as f:rows=list(csv.DictReader(f))
        portable=[];complete=True
        for row in rows:
            item={'sample_id':row['sample_id']}
            for mate in ('read1','read2'):
                value=row[mate];rel=assets.relative_reference(value,'/runtime/incoming/')
                target=(root/'runtime/incoming'/rel).resolve() if rel else (manifest.parent/value).resolve()
                if not target.is_relative_to((root/'runtime/incoming').resolve()):complete=False;continue
                source=stages.get(row['sample_id'],Path('/no-source'))/target.name
                if not target.is_file() or target.stat().st_size==0:
                    if not source.is_file() or not source.stat().st_size:complete=False;continue
                    with gzip.open(source,'rt') as f:lines=[f.readline().strip() for _ in range(4)]
                    if not lines[0].startswith('@') or not lines[2].startswith('+') or len(lines[1])!=len(lines[3]):raise ValueError('Invalid original FASTQ cache')
                    recover(source,target,backup)
                item[mate]=linux_path(target)
                report['reads'].append({'task_id':tid,'sample_id':row['sample_id'],'mate':mate,'path':str(target.relative_to(root)),'size_bytes':target.stat().st_size,'evidence':'original FASTQC_RAW trace'})
            portable.append(item)
        if complete:
            repaired=manifest.with_name(manifest.stem+'.linked.csv')
            with repaired.open('w',encoding='utf-8',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=['sample_id','read1','read2']);writer.writeheader();writer.writerows(portable)
            report['manifests'].append({'task_id':tid,'name':task.get('name') or tid,'path':linux_path(repaired)})
        assets.write_snapshot(tid,task,snapshot.get('artifacts',[]))
    (ROOT/'runtime/link-repair.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:len(v) for k,v in report.items()}))


if __name__=='__main__':main()
