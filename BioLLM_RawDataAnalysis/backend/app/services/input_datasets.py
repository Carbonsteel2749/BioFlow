"""Immutable validated input datasets and task-local binding snapshots.

Registration only reads files already staged in input_root/typed-datasets/<id>.
The owned gateway performs staging; callers cannot supply an arbitrary root.
"""
from __future__ import annotations

import hashlib
import json
import os
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


class InputDatasetService:
    def __init__(self,settings):
        self.settings=settings
        self.root=settings.input_root.absolute()/'typed-datasets'

    def _folder(self,dataset_id):
        p=self.root/_safe_id(dataset_id)
        if not p.is_dir() or p.resolve()!=p.absolute():raise InputContractError('输入数据集不存在或包含符号链接')
        return p

    def register(self,dataset_id:str,spec:dict)->dict:
        folder=self._folder(dataset_id);target=folder/'contract.json'
        if target.exists():
            record=self.get(dataset_id)
            if record['spec']!=spec:raise InputContractError('已登记输入不可变；修改后请创建新数据集')
            return record
        contract=validate_input_contract(spec,folder)
        record={'dataset_id':dataset_id,'spec':spec,'contract':contract}
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

    def get(self,dataset_id:str)->dict:
        folder=self._folder(dataset_id);path=folder/'contract.json';_regular(path,self.root)
        if path.stat().st_size>MAX_CONTRACT_BYTES:raise InputContractError('输入契约超过字节上限')
        try:record=json.loads(path.read_text(encoding='utf-8'))
        except (ValueError,OSError,UnicodeError) as e:raise InputContractError('输入契约校验失败') from e
        actual={k:v for k,v in record.items() if k!='record_sha256'}
        if record.get('record_sha256')!=_digest(actual) or record.get('dataset_id')!=dataset_id:raise InputContractError('输入契约校验失败')
        contract=record.get('contract',{});basis={k:v for k,v in contract.items() if k!='manifest_sha256'}
        if contract.get('manifest_sha256')!=_digest(basis):raise InputContractError('输入 manifest 校验失败')
        for entry in contract.get('files',[]):
            name=entry.get('path')
            if not isinstance(name,str) or Path(name).is_absolute() or '..' in Path(name).parts:raise InputContractError('输入文件校验路径不合法')
            file=folder/name;_regular(file,folder)
            if file.stat().st_size!=entry.get('size_bytes') or _file_sha(file)!=entry.get('sha256'):
                raise InputContractError('输入文件 SHA-256 校验失败；请重新导入，不会静默复用旧缓存')
        return record

    def snapshot(self,bindings:dict[str,str],task_id:str)->Path:
        _safe_id(task_id)
        if not isinstance(bindings,dict) or not 1<=len(bindings)<=100:raise InputContractError('输入节点绑定数量无效')
        records={}
        for node,dataset_id in bindings.items():
            _safe_id(node);record=self.get(dataset_id)
            records[node]={'dataset_id':dataset_id,'contract':record['contract'],'manifest_sha256':record['contract']['manifest_sha256']}
        body={'schema_version':'1.0','task_id':task_id,'bindings':records}
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
        path=Path(path).absolute();_regular(path,self.settings.state_root/'workflow_runs')
        if path.stat().st_size>MAX_SNAPSHOT_BYTES:raise InputContractError('任务输入快照超过字节上限')
        try:body=json.loads(path.read_text(encoding='utf-8'))
        except (ValueError,OSError) as e:raise InputContractError('任务输入快照校验失败') from e
        if body.get('snapshot_sha256')!=_digest({k:v for k,v in body.items() if k!='snapshot_sha256'}):raise InputContractError('任务输入快照校验失败')
        for node,binding in body.get('bindings',{}).items():
            _safe_id(node);record=self.get(binding['dataset_id'])
            if binding.get('contract')!=record['contract'] or binding.get('manifest_sha256')!=record['contract']['manifest_sha256']:
                raise InputContractError('任务输入快照与登记契约校验不一致')
        return body
