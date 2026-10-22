"""Convert retained input manifests to relative references, with reversible backups."""
import csv
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.bioflow_platform import assets
from backend.bioflow_platform.project_files import input_path
from backend.bioflow_platform.store import Store


def prepare():
    root=assets.analysis_root();store=Store(ROOT/'runtime/bioflow.sqlite3');manifests={}
    for project in store.projects():
        for tid in store.ids(project['id'],'task'):
            if not store.owns('task',tid,project['owner_id']):continue
            task=assets.read_snapshot(tid).get('task',{})
            if task.get('manifest_path'):manifests[input_path(task['manifest_path'])]=tid
        for m in store.resource_metadata('dataset',project['owner_id']):
            if m.get('manifest_path'):
                try:manifests.setdefault(input_path(m.get('relative_manifest') or m['manifest_path']),m.get('recovered_from'))
                except Exception:pass
    report={'format':1,'manifests':[]};hashes={};backup=ROOT/'runtime/backups'/('portable-'+time.strftime('%Y%m%d-%H%M%S'))
    for path,tid in manifests.items():
        if not path.is_file():continue
        original=path.read_bytes();rows=list(csv.DictReader(io.StringIO(original.decode('utf-8-sig'))));portable=[];samples=[]
        for row in rows:
            item={'sample_id':row['sample_id']};info={'sample_id':row['sample_id']}
            for mate in ('read1','read2'):
                file=input_path(row[mate],path)
                if not file.is_file() or file.stat().st_size==0:raise ValueError(f'原始文件缺失：{file}')
                opener=gzip.open if file.name.lower().endswith('.gz') else open
                with opener(file,'rt') as handle:lines=[handle.readline().strip() for _ in range(4)]
                if not lines[0].startswith('@') or not lines[2].startswith('+') or len(lines[1])!=len(lines[3]):raise ValueError(f'FASTQ 初检失败：{file}')
                item[mate]=os.path.relpath(file,path.parent).replace('\\','/')
                key=str(file)
                if key not in hashes:
                    digest=hashlib.sha256()
                    with file.open('rb') as stream:
                        for block in iter(lambda:stream.read(8*1024*1024),b''):digest.update(block)
                    hashes[key]=digest.hexdigest()
                info[mate]={'path':file.relative_to(root/'runtime/incoming').as_posix(),'size_bytes':file.stat().st_size,'sha256':hashes[key]}
            portable.append(item);samples.append(info)
        output=io.StringIO(newline='');writer=csv.DictWriter(output,fieldnames=['sample_id','read1','read2'],lineterminator='\n');writer.writeheader();writer.writerows(portable);content=output.getvalue().encode()
        if content!=original:
            saved=backup/path.relative_to(root);saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(original);path.write_bytes(content)
        relative=path.relative_to(root/'runtime/incoming').as_posix()
        report['manifests'].append({'task_id':tid,'path':relative,'sha256':hashlib.sha256(content).hexdigest(),'samples':samples})
    (root/'runtime/project-portable.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with store.connect() as db:
        for row in db.execute("SELECT kind,external_id,user_id,metadata FROM resources WHERE kind='dataset'").fetchall():
            m=json.loads(row['metadata'])
            if not m.get('manifest_path'):continue
            file=input_path(m.get('relative_manifest') or m['manifest_path']);m['relative_manifest']=file.relative_to(root/'runtime/incoming').as_posix();m['manifest_path']=m['relative_manifest']
            db.execute('UPDATE resources SET metadata=? WHERE kind=? AND external_id=?',(json.dumps(m,ensure_ascii=False),row['kind'],row['external_id']))
        db.execute('UPDATE catalogue_sync SET updated_at=0')
    print(json.dumps({'portable_manifests':len(report['manifests']),'raw_read_pairs':sum(len(m['samples']) for m in report['manifests'])}))


if __name__=='__main__':prepare()
