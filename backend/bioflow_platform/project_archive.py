"""Authenticated exports of the current project's actual, hash-checked results."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import threading
import uuid
from . import assets

LOCKS={}


def result_archive(pid,uid,tid,snapshot):
    original=assets.local_output(snapshot.get('task',{}).get('result_archive',''))
    task_root=(assets.analysis_root()/'runtime/outputs'/tid).resolve()
    if original is not None and original.is_relative_to(task_root) and original.is_file() and original.stat().st_size>0:return original
    records=[a for a in snapshot.get('artifacts',[]) if a.get('status')=='active' and not a.get('artifact_type','').startswith('reads.')]
    key=hashlib.sha256(json.dumps([(a['artifact_id'],a.get('sha256'),a.get('local_status')) for a in records],sort_keys=True).encode()).hexdigest()[:16]
    folder=assets.ROOT/'runtime/project-exports'/uid/pid;folder.mkdir(parents=True,exist_ok=True)
    target=folder/f'{tid}-{key}-available-results.tar.gz'
    with LOCKS.setdefault(str(target),threading.Lock()):
        if target.is_file():return target
        temporary=target.with_suffix('.'+uuid.uuid4().hex+'.tmp');included=[];missing=[]
        try:
            with tarfile.open(temporary,'w:gz',compresslevel=1) as tar:
                for a in records:
                    path=assets.local_output(a.get('path',''))
                    if path is None or not path.is_relative_to(task_root) or assets.availability(path,a.get('sha256'))[0]!='ready':missing.append(a['artifact_id']);continue
                    relative=f"results/{a['artifact_id']}/{path.name}"
                    tar.add(path,arcname=relative,recursive=False)
                    included.append({'path':relative,'sha256':a.get('sha256'),'artifact_id':a['artifact_id']})
                data=json.dumps({'task_id':tid,'scope':'available analysis results; original FASTQ downloaded separately','artifacts':included,'unavailable_artifact_ids':missing},ensure_ascii=False,indent=2).encode()
                info=tarfile.TarInfo('manifest.json');info.size=len(data);tar.addfile(info,io.BytesIO(data))
            if not included:raise ValueError('暂无可用成果，需连接原服务器同步')
            temporary.replace(target)
        finally:temporary.unlink(missing_ok=True)
    return target
