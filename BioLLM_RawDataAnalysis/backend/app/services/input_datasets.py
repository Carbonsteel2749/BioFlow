"""Immutable validated input datasets and task-local binding snapshots.

Registration only reads files already staged in input_root/typed-datasets/<id>.
The owned gateway performs staging; callers cannot supply an arbitrary root.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from ..workflows.input_contracts import InputContractError, SAFE_ID, validate_input_contract

MAX_CONTRACT_BYTES = 4 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024


def _serialized(value,limit,label):
    data=json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True).encode('utf-8')
    if len(data)>limit:raise InputContractError(label+'超过字节上限')
    return data


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def _file_sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def _safe_id(value):
    if not isinstance(value,str) or not SAFE_ID.fullmatch(value):raise InputContractError('输入数据集或节点标识无效')
    return value


def _regular(path,root):
    if not path.is_file() or path.resolve()!=path.absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise InputContractError('输入文件不存在、包含符号链接或越界')


def evidence_files(record):
    return [{'path':s['staged_file'],'size_bytes':s['size_bytes'],'sha256':s['sha256']}
            for s in (record.get('origin') or {}).get('sources',[])]


def validate_origin(origin,folder):
    if origin is None:return
    _serialized(origin,MAX_CONTRACT_BYTES,'来源证据')
    if not isinstance(origin,dict) or set(origin)!={'authority','sources','conversion'} or origin['authority']!='owned_import_gateway':
        raise InputContractError('来源证据字段无效；不能设置验证身份')
    rows=origin['sources']
    if not isinstance(rows,list) or not 1<=len(rows)<=10000:raise InputContractError('来源证据数量无效')
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'id','name','size_bytes','sha256','staged_file','source_task','node','producer'}:
            raise InputContractError('来源证据字段无效')
        if not isinstance(row['id'],str) or not re.fullmatch(r'(asset|artifact):[A-Za-z0-9][A-Za-z0-9._-]{0,127}',row['id']):raise InputContractError('来源标识无效')
        if not isinstance(row['name'],str) or not 1<=len(row['name'])<=1024:raise InputContractError('来源名称无效')
        for key in ('source_task','node','producer'):
            if row[key] is not None and (not isinstance(row[key],str) or len(row[key])>512):raise InputContractError('来源生产记录无效')
        if type(row['size_bytes']) is not int or row['size_bytes']<=0 or not isinstance(row['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',row['sha256']):raise InputContractError('来源大小或 SHA-256 无效')
        name=row['staged_file']
        if not isinstance(name,str) or Path(name).is_absolute() or '..' in Path(name).parts or '\\' in name or ':' in name:raise InputContractError('来源证据路径无效')
        path=folder/name;_regular(path,folder)
        if path.stat().st_size!=row['size_bytes'] or _file_sha(path)!=row['sha256']:raise InputContractError('来源证据 SHA-256 校验失败')
    conversion=origin['conversion']
    if conversion is not None:
        fields={'schema','rule','input_rows','included_rows','excluded_diagnostics','excluded_strata','unit','normalization','output_file'}
        if not isinstance(conversion,dict) or set(conversion)!=fields:raise InputContractError('转换证据字段无效')
        for key in ('input_rows','included_rows','excluded_diagnostics','excluded_strata'):
            if type(conversion[key]) is not int or conversion[key]<0:raise InputContractError('转换计数无效')
        for key in fields-{'input_rows','included_rows','excluded_diagnostics','excluded_strata'}:
            if not isinstance(conversion[key],str) or len(conversion[key])>2048:raise InputContractError('转换描述无效')
        if conversion['input_rows']!=conversion['included_rows']+conversion['excluded_diagnostics']+conversion['excluded_strata']:raise InputContractError('转换计数不一致')


class InputDatasetService:
    def __init__(self,settings):
        self.settings=settings
        self.root=settings.input_root.absolute()/'typed-datasets'

    def _folder(self,dataset_id):
        p=self.root/_safe_id(dataset_id)
        if not p.is_dir() or p.resolve()!=p.absolute():raise InputContractError('输入数据集不存在或包含符号链接')
        return p

    def register(self,dataset_id:str,spec:dict,origin:dict|None=None)->dict:
        folder=self._folder(dataset_id);target=folder/'contract.json'
        if target.exists():
            record=self.get(dataset_id)
            if record['spec']!=spec or record.get('origin')!=origin:raise InputContractError('已登记输入不可变；修改后请创建新数据集')
            return record
        contract=validate_input_contract(spec,folder)
        validate_origin(origin,folder)
        if origin and origin['conversion'] is not None and origin['conversion']['output_file'] not in {f['path'] for f in contract['files']}:
            raise InputContractError('转换输出不属于已验证输入文件')
        record={'dataset_id':dataset_id,'spec':spec,'contract':contract}
        if origin is not None:record['origin']=origin
        record['record_sha256']=_digest(record)
        serialized=_serialized(record,MAX_CONTRACT_BYTES,'输入契约')
        # Exclusive publication: concurrent registration cannot replace a dataset.
        try:
            with target.open('xb') as f:
                f.write(serialized);f.flush();os.fsync(f.fileno())
        except FileExistsError:
            existing=self.get(dataset_id)
            if existing!=record:raise InputContractError('已登记输入不可变；并发登记发生冲突')
        return record

    def metadata(self,dataset_id:str)->dict:
        """Bounded contract-only read; does not attest current data-file integrity."""
        folder=self._folder(dataset_id);path=folder/'contract.json';_regular(path,self.root)
        if path.stat().st_size>MAX_CONTRACT_BYTES:raise InputContractError('输入契约超过字节上限')
        try:
            with path.open('rb') as handle:data=handle.read(MAX_CONTRACT_BYTES+1)
            if len(data)>MAX_CONTRACT_BYTES:raise InputContractError('输入契约超过字节上限')
            record=json.loads(data.decode('utf-8'))
        except (ValueError,OSError,UnicodeError) as e:raise InputContractError('输入契约校验失败') from e
        actual={k:v for k,v in record.items() if k!='record_sha256'}
        if record.get('record_sha256')!=_digest(actual) or record.get('dataset_id')!=dataset_id:raise InputContractError('输入契约校验失败')
        contract=record.get('contract',{});basis={k:v for k,v in contract.items() if k!='manifest_sha256'}
        if contract.get('manifest_sha256')!=_digest(basis):raise InputContractError('输入 manifest 校验失败')
        return record

    def get(self,dataset_id:str)->dict:
        """Full data SHA verification, mandatory for snapshots and execution checks."""
        record=self.metadata(dataset_id);folder=self._folder(dataset_id)
        contract=record['contract']
        for entry in contract.get('files',[])+evidence_files(record):
            name=entry.get('path')
            if not isinstance(name,str) or Path(name).is_absolute() or '..' in Path(name).parts:raise InputContractError('输入文件校验路径不合法')
            file=folder/name;_regular(file,folder)
            if file.stat().st_size!=entry.get('size_bytes') or _file_sha(file)!=entry.get('sha256'):
                raise InputContractError('输入文件 SHA-256 校验失败；请重新导入，不会静默复用旧缓存')
        return record

    def snapshot(self,bindings:dict[str,str],task_id:str)->Path:
        return self._snapshot(bindings,task_id,verify_files=True)

    def snapshot_metadata_only(self,bindings:dict[str,str],task_id:str)->Path:
        """Freeze metadata for queue admission; NOT execution integrity evidence."""
        return self._snapshot(bindings,task_id,verify_files=False)

    def _snapshot(self,bindings,task_id,*,verify_files):
        _safe_id(task_id)
        if not isinstance(bindings,dict) or not 1<=len(bindings)<=100:raise InputContractError('输入节点绑定数量无效')
        records={}
        for node,dataset_id in bindings.items():
            _safe_id(node);record=(self.get if verify_files else self.metadata)(dataset_id)
            records[node]={'dataset_id':dataset_id,'spec':record['spec'],'contract':record['contract'],'manifest_sha256':record['contract']['manifest_sha256']}
            if 'origin' in record:records[node]['origin']=record['origin']
        body={'schema_version':'1.0','task_id':task_id,'bindings':records,
              'integrity_check':'full_sha256_at_snapshot' if verify_files else 'pending_execution_sha256'}
        body['snapshot_sha256']=_digest(body)
        serialized=_serialized(body,MAX_SNAPSHOT_BYTES,'输入快照')
        folder=self.settings.state_root.absolute()/'workflow_runs'/task_id/'inputs'
        if folder.exists() and folder.resolve()!=folder.absolute():raise InputContractError('任务输入快照目录不安全')
        folder.mkdir(parents=True,exist_ok=True)
        if folder.resolve()!=folder.absolute():raise InputContractError('任务输入快照目录不安全')
        target=folder/'bindings.json'
        try:
            with target.open('xb') as f:f.write(serialized);f.flush();os.fsync(f.fileno())
        except FileExistsError as e:raise InputContractError('任务输入快照已存在，不允许替换历史') from e
        return target

    def verify_snapshot(self,path:Path)->dict:
        return self._inspect_snapshot(path,verify_files=True)

    def inspect_snapshot_metadata(self,path:Path)->dict:
        """Validate frozen metadata only, for references/redaction, NOT execution."""
        return self._inspect_snapshot(path,verify_files=False)

    def _inspect_snapshot(self,path,*,verify_files):
        path=Path(path).absolute();_regular(path,self.settings.state_root/'workflow_runs')
        if path.stat().st_size>MAX_SNAPSHOT_BYTES:raise InputContractError('任务输入快照超过字节上限')
        try:
            with path.open('rb') as handle:data=handle.read(MAX_SNAPSHOT_BYTES+1)
            if len(data)>MAX_SNAPSHOT_BYTES:raise InputContractError('任务输入快照超过字节上限')
            body=json.loads(data.decode('utf-8'))
        except (ValueError,OSError) as e:raise InputContractError('任务输入快照校验失败') from e
        if body.get('snapshot_sha256')!=_digest({k:v for k,v in body.items() if k!='snapshot_sha256'}):raise InputContractError('任务输入快照校验失败')
        for node,binding in body.get('bindings',{}).items():
            _safe_id(node);record=(self.get if verify_files else self.metadata)(binding['dataset_id'])
            if binding.get('origin')!=record.get('origin') or binding.get('spec',record['spec'])!=record['spec'] or binding.get('contract')!=record['contract'] or binding.get('manifest_sha256')!=record['contract']['manifest_sha256']:
                raise InputContractError('任务输入快照与登记契约校验不一致')
        return body

    def snapshot_reference_paths(self,path:Path,task_id:str)->list[Path]:
        """Bounded, checksum-validated ownership/path references; no data scans."""
        expected=self.settings.state_root.absolute()/'workflow_runs'/_safe_id(task_id)/'inputs/bindings.json'
        if Path(path).absolute()!=expected:raise InputContractError('任务输入快照路径不匹配')
        body=self.inspect_snapshot_metadata(expected)
        if body.get('task_id')!=task_id:raise InputContractError('任务输入快照身份不匹配')
        paths=[]
        for binding in body['bindings'].values():
            folder=self._folder(binding['dataset_id'])
            for entry in binding['contract']['files']+evidence_files(binding):
                name=entry['path'];candidate=folder/name
                if Path(name).is_absolute() or '..' in Path(name).parts or candidate.resolve()!=candidate.absolute() or not candidate.resolve().is_relative_to(folder.resolve()):
                    raise InputContractError('输入引用路径校验失败')
                paths.append(candidate)
        return paths

    def snapshot_sample_identifiers(self,task_id:str)->list[str]:
        path=self.settings.state_root.absolute()/'workflow_runs'/_safe_id(task_id)/'inputs/bindings.json'
        if not path.exists():return []
        body=self.inspect_snapshot_metadata(path)
        if body.get('task_id')!=task_id:raise InputContractError('任务输入快照身份不匹配')
        identifiers=set()
        for binding in body['bindings'].values():
            contract=binding['contract']
            identifiers.update(row['sample_id'] for row in contract.get('samples',[]))
            identifiers.update(contract.get('sample_ids',[]))
        return sorted(identifiers)
