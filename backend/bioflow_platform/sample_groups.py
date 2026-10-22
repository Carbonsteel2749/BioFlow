"""Project-scoped grouping editor with upstream groups and durable batch revisions."""
import asyncio
import csv
import io
import json
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class GroupSave(BaseModel):
    version: str = Field(max_length=80)
    rows: list[dict[str, str]] = Field(max_length=10000)


class GroupPreview(BaseModel):
    text: str = Field(max_length=5*1024*1024)
    delimiter: str = ','
    mapping: dict[str, str] = Field(default_factory=lambda: {'sample_id':'sample_id','group':'group','batch':'batch'})


def normalize(rows, samples):
    errors, seen, result = [], set(), []
    for row in rows:
        sid = row.get('sample_id', '').strip()
        if sid not in samples: errors.append(f'未知样本：{sid}')
        if sid in seen: errors.append(f'重复样本：{sid}')
        seen.add(sid)
        item = {'sample_id':sid}
        for key, label in [('group','分组'), ('batch','批次')]:
            value = row.get(key, '').strip()
            if len(value)>128 or any(ord(c)<32 or ord(c)==127 for c in value): errors.append(f'{sid} 的{label}格式无效')
            if key=='group' and not value: errors.append(f'{sid} 缺少分组')
            item[key]=value
        result.append(item)
    missing = set(samples)-seen
    if missing: errors.append('缺少样本：'+ '、'.join(sorted(missing)[:20]))
    if not samples: errors.append('数据集没有已登记样本')
    return sorted(result,key=lambda r:r['sample_id']), errors


def install_groups(router, get_store, check_project, call):
    locks={}

    def access(pid,did,request):
        check_project(pid,request)
        store=get_store()
        if not store.owns('dataset',did,request.state.user['id']): raise HTTPException(404,'数据集不存在')
        with store.connect() as db:
            linked=db.execute("SELECT 1 FROM links WHERE project_id=? AND kind='dataset' AND external_id=?",(pid,did)).fetchone()
            db.execute('CREATE TABLE IF NOT EXISTS sample_group_batches(dataset_id TEXT, revision INTEGER, rows_json TEXT NOT NULL, PRIMARY KEY(dataset_id,revision))')
        if not linked: raise HTTPException(404,'数据集未关联到当前项目')
        return store

    async def read(store,did):
        upstream=await call('analysis',f'/api/datasets/{did}/sample-metadata')
        with store.connect() as db:
            saved=db.execute('SELECT * FROM sample_group_batches WHERE dataset_id=? ORDER BY revision DESC LIMIT 1',(did,)).fetchone()
        batches={r['sample_id']:r.get('batch','') for r in json.loads(saved['rows_json'])} if saved else {}
        return {'version':f"{upstream['version']}:{saved['revision'] if saved else 0}", 'backend_version':upstream['version'],
                'rows':[{**r,'batch':batches.get(r['sample_id'],'')} for r in upstream['rows']]}

    @router.get('/datasets/{did}/sample-groups')
    async def get_groups(pid:str,did:str,request:Request):
        return await read(access(pid,did,request),did)

    @router.post('/datasets/{did}/sample-groups/preview')
    async def preview_groups(pid:str,did:str,body:GroupPreview,request:Request):
        store=access(pid,did,request)
        if body.delimiter not in {',','\t'}: raise HTTPException(422,'请选择 CSV 或 TSV')
        if len(body.text.encode('utf-8'))>5*1024*1024: raise HTTPException(413,'分组表超过 5 MiB')
        reader=csv.DictReader(io.StringIO(body.text.removeprefix('\ufeff')),delimiter=body.delimiter,strict=True)
        columns=reader.fieldnames or []
        if len(columns)!=len(set(columns)): raise HTTPException(422,'表头包含重复列名')
        if not body.mapping.get('sample_id') or not body.mapping.get('group'): raise HTTPException(422,'请选择样本与分组列')
        chosen=[v for v in body.mapping.values() if v]
        if len(chosen)!=len(set(chosen)) or any(v not in columns for v in chosen): raise HTTPException(422,'列映射无效或重复')
        rows=[]
        try:
            for row in reader:
                if len(rows)>=10000 or None in row: raise HTTPException(422,'表格超过 10000 行或包含多余字段')
                rows.append({k:row.get(body.mapping.get(k,''),'') or '' for k in ('sample_id','group','batch')})
        except csv.Error as exc: raise HTTPException(422,'表格格式错误') from exc
        current=await read(store,did)
        normalized,errors=normalize(rows,{r['sample_id'] for r in current['rows']})
        return {'rows':normalized,'errors':errors,'valid':not errors}

    @router.put('/datasets/{did}/sample-groups')
    async def save_groups(pid:str,did:str,body:GroupSave,request:Request):
        store=access(pid,did,request)
        async with locks.setdefault(did,asyncio.Lock()):
            current=await read(store,did)
            if body.version!=current['version']: return JSONResponse({'detail':'内容已更新，请选择要保留的内容','current':current},409)
            rows,errors=normalize(body.rows,{r['sample_id'] for r in current['rows']})
            if errors: raise HTTPException(422,'；'.join(errors[:20]))
            try:
                await call('analysis',f'/api/datasets/{did}/sample-metadata','PUT',{'expected_version':current['backend_version'],'rows':[{k:r[k] for k in ('sample_id','group')} for r in rows]})
            except HTTPException as exc:
                if exc.status_code==409: return JSONResponse({'detail':'内容已更新，请选择要保留的内容','current':await read(store,did)},409)
                raise
            revision=int(current['version'].split(':')[1])+1
            with store.connect() as db: db.execute('INSERT INTO sample_group_batches VALUES(?,?,?)',(did,revision,json.dumps(rows,ensure_ascii=False)))
            return await read(store,did)
