"""Read-only validation of operator-registered database manifests and file trees."""
import hashlib
import json
from pathlib import Path


INVENTORY_BASIS='SHA256 of the sorted top-level file-name and byte-size inventory'


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def tree_evidence(path, allowed):
    root=Path(path).resolve();records=[]
    for p in sorted(root.rglob('*')):
        if p.is_symlink():raise ValueError('输入目录不能包含符号链接')
        if p.is_file():
            if not any(p.resolve().is_relative_to(Path(x).resolve()) for x in allowed):raise ValueError('输入文件越出登记目录')
            records.append({'path':p.relative_to(root).as_posix(),'size':p.stat().st_size,'sha256':file_hash(p)})
        if len(records)>10000:raise ValueError('输入目录超过 10000 个文件，请按节点拆分')
    if not records:raise ValueError('输入目录为空')
    digest=hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest()
    return {'path':str(root),'files':records,'sha256':digest}


def database_check(record):
    errors=[];path=Path(record.get('path','/nonexistent'))
    if not path.is_dir():return ['DATABASE_PATH_MISSING']
    for field in ['release','manifest_sha256','required_sentinel_files']:
        if not record.get(field):errors.append('DATABASE_METADATA_MISSING:'+field)
    for name in record.get('required_sentinel_files',[]):
        p=(path/name).resolve()
        if not p.is_relative_to(path.resolve()) or not p.is_file() or p.stat().st_size==0:errors.append('DATABASE_SENTINEL_MISSING:'+name)
    if record.get('manifest_basis')==INVENTORY_BASIS:
        inventory=''.join(f'{p.name}\t{p.stat().st_size}\n' for p in sorted(path.iterdir(),key=lambda p:p.name) if p.is_file())
        if hashlib.sha256(inventory.encode()).hexdigest()!=record.get('manifest_sha256'):errors.append('DATABASE_INVENTORY_CHANGED')
    elif record.get('content_files'):
        for name,expected in record['content_files'].items():
            p=(path/name).resolve()
            if not p.is_relative_to(path.resolve()) or not p.is_file() or file_hash(p)!=expected:errors.append('DATABASE_CONTENT_CHANGED:'+name)
    else:errors.append('DATABASE_CHECKSUM_BASIS_REQUIRED')
    return errors


def manifest_check(path, environment, keys):
    expected=environment.get('database_manifests',{}).get(str(Path(path).resolve()))
    if not expected or file_hash(path)!=expected:return ['DATABASE_MANIFEST_UNREGISTERED_OR_CHANGED']
    value=json.loads(Path(path).read_text());errors=[]
    for key in keys:
        cursor=value.get('databases',{})
        for name in key.split('.'):cursor=cursor.get(name,{}) if isinstance(cursor,dict) else {}
        registered=environment.get('databases',{}).get(key,{})
        for field in ['path','release','manifest_sha256']:
            if not cursor.get(field) or cursor.get(field)!=registered.get(field):errors.append('DATABASE_BINDING_MISMATCH:'+key+':'+field)
    if 'taxonomy_reads.kraken2' in keys:
        a=value['databases']['taxonomy_reads']['kraken2'];b=value['databases']['taxonomy_reads']['bracken']
        if any(a.get(f)!=b.get(f) for f in ['path','release','taxonomy_system','manifest_sha256']):errors.append('KRAKEN_BRACKEN_PROFILE_MISMATCH')
    return errors
