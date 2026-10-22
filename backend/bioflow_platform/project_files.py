"""Read retained input manifests relative to the movable project directory."""
import csv
from pathlib import Path
from fastapi import HTTPException
from .assets import analysis_root,read_snapshot


def input_path(value,manifest=None):
    base=(analysis_root()/'runtime/incoming').resolve();text=str(value or '').replace('\\','/')
    if '/runtime/incoming/' in text:path=base/text.split('/runtime/incoming/',1)[1]
    elif Path(text).is_absolute() or (len(text)>1 and text[1]==':'):raise HTTPException(409,'数据路径未位于 BioFlow 项目内')
    else:path=(manifest.parent if manifest else base)/text
    path=path.resolve()
    if not path.is_relative_to(base):raise HTTPException(409,'数据路径超出项目目录')
    return path


def dataset_files(store,pid,uid,did):
    if not store.owns('dataset',did,uid) or did not in store.ids(pid,'dataset'):raise HTTPException(404,'数据集不属于当前项目')
    metadata=next((m for m in store.resource_metadata('dataset',uid) if m.get('dataset_id')==did),{})
    raw=metadata.get('relative_manifest') or metadata.get('manifest_path')
    if not raw:raise HTTPException(409,'数据集尚未绑定项目内样本清单')
    manifest=input_path(raw)
    if not manifest.is_file():raise HTTPException(409,'项目内样本清单不存在')
    samples=[]
    with manifest.open(encoding='utf-8-sig',newline='') as handle:
        for row in csv.DictReader(handle):
            sample={'sample_id':row['sample_id']}
            for mate in ('read1','read2'):
                path=input_path(row[mate],manifest);size=path.stat().st_size if path.is_file() else 0
                sample[mate]={'name':path.name,'size_bytes':size,'availability':'available' if size else 'missing',
                              'relative_path':path.relative_to(analysis_root()/'runtime/incoming').as_posix()}
            samples.append(sample)
    ready=bool(samples) and all(s[k]['size_bytes']>0 for s in samples for k in ('read1','read2'))
    title=metadata.get('name') or manifest.stem
    for tid in store.ids(pid,'task'):
        task=read_snapshot(tid).get('task',{})
        if Path(task.get('manifest_path','')).stem.replace('.linked','')==manifest.stem.replace('.linked',''):
            title=task.get('name') or title;break
    return {'id':did,'name':title,'samples':samples,'sample_count':len(samples),'status':'available' if ready else 'unavailable',
            'reason':None if ready else '项目内部分原始文件缺失或为空','relative_manifest':manifest.relative_to(analysis_root()/'runtime/incoming').as_posix()}
