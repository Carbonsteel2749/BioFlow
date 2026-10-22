"""Read-only registered reference checks and project-owned analysis submission."""
import hashlib
import asyncio
import json
import os
import re
from pathlib import Path
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from .assets import analysis_root

REFERENCE_KEYS={'kraken_db':('taxonomy_reads','kraken2'),'humann_nucleotide_db':('function_reads','humann_nucleotide'),
                'humann_protein_db':('function_reads','humann_protein'),'metaphlan_db':('function_reads','metaphlan')}
LABELS={'taxonomy_reads':'物种分析','function_reads':'功能分析','mag_annotation':'MAG 分析'}


class AnalysisSetup(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=120)
    manifest_path:str=Field(min_length=1,max_length=4096)
    dataset_id:str=Field(min_length=1,max_length=128)
    parameters:dict=Field(default_factory=dict)


class AnalysisStart(AnalysisSetup):
    review_token:str=Field(min_length=64,max_length=64)


def load_json(path):
    try:
        if path.stat().st_size>8*1024*1024:return {}
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def inspect_entry(group,key,entry):
    item={k:entry.get(k) for k in ('database_name','path','release','tool_compatibility_version','purpose','manifest_sha256')}
    item.update(id=f'{group}/{key}',group=group,key=key,category=LABELS.get(group,group),errors=[])
    path=Path(entry.get('path') or '')
    if not path.is_absolute() or not path.is_dir():item['errors'].append('数据库目录不存在或未挂载')
    else:
        required=entry.get('required_sentinel_files',[])
        if not isinstance(required,list) or not required:item['errors'].append('登记表缺少必需文件清单')
        else:
            for name in required:
                candidate=path/str(name)
                if Path(str(name)).is_absolute() or '..' in Path(str(name)).parts or not candidate.is_file() or candidate.stat().st_size==0:
                    item['errors'].append(f'必需文件缺失或为空：{name}')
        basis=entry.get('manifest_basis')
        if basis=='SHA256 of the sorted top-level file-name and byte-size inventory':
            try:
                entries=list(path.iterdir())
                if len(entries)>100000:raise ValueError('目录文件数超过检查上限')
                inventory=''.join(f'{p.name}\t{p.stat().st_size}\n' for p in sorted(entries,key=lambda p:p.name) if p.is_file())
                if hashlib.sha256(inventory.encode()).hexdigest()!=str(entry.get('manifest_sha256','')).lower():item['errors'].append('目录清单校验值与登记版本不一致')
            except (OSError,ValueError) as exc:item['errors'].append(f'目录清单检查失败：{exc}')
        elif basis:item['errors'].append('登记表的完整性校验方式暂不支持')
        else:item['errors'].append('登记表缺少可验证的目录清单校验方式')
    item['ready']=not item['errors']
    return item


def references(capabilities,verify=True):
    root=analysis_root()
    resolved=load_json(root/'runtime/config/database.resolved.json')
    registry=load_json(root/'config/database-registry.local.json')
    profile=capabilities.get('database_profile')
    active=registry.get('profiles',{}).get(profile,{})
    if not active and resolved.get('database_profile')==profile:active=resolved.get('databases',{})
    rows=[]
    for group in LABELS:
        for key,entry in active.get(group,{}).items():
            if isinstance(entry,dict):
                try:item=inspect_entry(group,key,entry) if verify else {k:entry.get(k) for k in ('database_name','path','release','tool_compatibility_version','purpose','manifest_sha256')}
                except OSError as exc:
                    item={'id':f'{group}/{key}','group':group,'key':key,'category':LABELS[group],**{k:entry.get(k) for k in ('database_name','path','release','tool_compatibility_version','purpose','manifest_sha256')},'errors':[f'文件无法读取：{exc}'],'ready':False}
                if not verify:item.update(id=f'{group}/{key}',group=group,key=key,category=LABELS[group],errors=[],ready=None)
                if resolved and verify:
                    frozen=resolved.get('databases',{}).get(group,{}).get(key,{})
                    same_path=bool(entry.get('path') and frozen.get('path')) and Path(entry['path']).resolve()==Path(frozen['path']).resolve()
                    if resolved.get('database_profile')!=profile or not same_path or any(entry.get(k)!=frozen.get(k) for k in ('release','manifest_sha256')):
                        item['errors'].append('登记记录与分析服务的已解析数据库清单不一致')
                        item['ready']=False
                rows.append(item)
    host=os.getenv('BIOFLOW_HOST_INDEX') or os.getenv('BIOLLM_HOST_INDEX')
    if not host:
        try:
            script=(root/'scripts/run_backend.sh').read_text(encoding='utf-8')
            match=re.search(r'BIOLLM_HOST_INDEX:-([^}\n]+)',script)
            host=match.group(1) if match else None
        except OSError:pass
    errors=[];host_inventory=[]
    if not host:errors.append('尚未找到服务器登记的宿主索引')
    elif verify:
        parts=('1','2','3','4','rev.1','rev.2')
        try:complete=any(all(Path(f'{host}.{part}.{extension}').is_file() and Path(f'{host}.{part}.{extension}').stat().st_size>0 for part in parts) for extension in ('bt2','bt2l'))
        except OSError:complete=False
        if not complete:errors.append('宿主索引的六个分片缺失或为空')
        else:
            for extension in ('bt2','bt2l'):
                for part in parts:
                    file=Path(f'{host}.{part}.{extension}')
                    try:
                        stat=file.stat();host_inventory.append(f'{file.name}:{stat.st_size}:{stat.st_mtime_ns}')
                    except OSError:pass
    return {'profile':profile,'items':rows,'host':{'path':host,'name':Path(host).name if host else '未配置','ready':not errors if verify else None,'errors':errors,'fingerprint':hashlib.sha256('\n'.join(host_inventory).encode()).hexdigest()},
            'selection_scope':'当前分析服务启用的登记配置；其他配置需先由服务器启用。'}


def install_analysis(router,get_store,check_project,call,files=None):
    async def options(verify=True):
        schema,caps=await asyncio.gather(call('analysis','/api/methods/parameters'),call('analysis','/api/capabilities'))
        return {**schema,'capabilities':caps,'references':await run_in_threadpool(references,caps,verify)}

    @router.get('/analysis/options')
    async def get_options(pid:str,request:Request):
        check_project(pid,request)
        return await options(False)

    @router.get('/analysis/datasets')
    async def get_datasets(pid:str,request:Request):
        check_project(pid,request);store=get_store();items=[]
        if files:
            rows=await files.catalogue(pid,request.state.user)
            return {'items':[{'id':r['dataset_id'],'name':r['name'],'sample_count':r.get('sample_count'),'status':r['status'],'reason':r.get('reason')} for r in rows if r['kind']=='datasets']}
        for did in store.ids(pid,'dataset'):
            if not store.owns('dataset',did,request.state.user['id']):continue
            try:
                data=await call('analysis',f'/api/datasets/{did}')
                items.append({k:data.get(k) for k in ('id','name','sample_count','status','reason')})
            except HTTPException as exc:
                if exc.status_code!=404:raise
        return {'items':items}

    async def check(pid,body,request):
        check_project(pid,request);store=get_store()
        if not store.owns('dataset',body.dataset_id,request.state.user['id']) or body.dataset_id not in store.ids(pid,'dataset'):
            raise HTTPException(404,'请选择当前项目的数据集')
        reused=await call('analysis',f'/api/datasets/{body.dataset_id}/reuse','POST',{})
        if reused['manifest_path']!=body.manifest_path:raise HTTPException(422,'数据集与样本清单不匹配，请重新选择数据集')
        config=await options();refs=config['references'];errors=[]
        if not config['capabilities'].get('reads_analysis'):errors.append('分析服务尚未启用完整的 reads 数据库配置')
        if body.parameters.get('enable_mags') and not config['capabilities'].get('mag_analysis'):errors.append('当前分析服务的 MAG 数据库配置不可用')
        params=dict(body.parameters)
        schema=config['parameters_schema']
        allowed={k for k in schema if k.startswith('fastp_') or k.startswith('host_')}|{'threads','read_length','enable_mags','mag_threads','mag_memory_gb','enable_reassembly','assembler','bin_completeness','bin_contamination',*REFERENCE_KEYS}
        if set(params)-allowed:raise HTTPException(422,'参数中含有此页面未支持的字段')
        host=refs['host']
        if not host['ready']:errors.extend(host['errors'])
        if params.get('host_index') and params['host_index']!=host['path']:raise HTTPException(422,'所选宿主索引未在当前服务登记')
        if host['path']:params['host_index']=host['path']
        for name,(group,key) in REFERENCE_KEYS.items():
            selected=next((r for r in refs['items'] if r['group']==group and r['key']==key),None)
            if not selected:continue
            if params.get(name) and params[name]!=selected['path']:raise HTTPException(422,f'{name} 未在当前服务登记')
            params[name]=selected['path']
        for row in refs['items']:
            if row['group']=='mag_annotation' and not params.get('enable_mags'):continue
            errors.extend(f"{row['database_name']}：{e}" for e in row['errors'])
        for group,keys in {'taxonomy_reads':['kraken2','bracken'],'function_reads':['humann_nucleotide','humann_protein','humann_utility','metaphlan','ko_mapping','ec_mapping'],**({'mag_annotation':['classification','function']} if params.get('enable_mags') else {})}.items():
            for key in keys:
                if not any(r['id']==f'{group}/{key}' for r in refs['items']):
                    label={'kraken2':'Kraken2','bracken':'Bracken','humann_nucleotide':'HUMAnN 核酸库','humann_protein':'HUMAnN 蛋白库','humann_utility':'HUMAnN 配套工具','metaphlan':'MetaPhlAn','ko_mapping':'KO 映射表','ec_mapping':'EC 映射表','classification':'MAG 分类库','function':'MAG 功能库'}[key]
                    errors.append(f'当前配置未登记：{label}')
        kraken=next((r for r in refs['items'] if r['id']=='taxonomy_reads/kraken2'),None)
        if kraken:
            length=params.get('read_length',schema.get('read_length',{}).get('default',150))
            if not (Path(kraken['path'])/f'database{length}mers.kmer_distrib').is_file():errors.append(f'缺少与读长 {length} bp 匹配的 Bracken 分布文件')
        review=None
        if not errors:
            try:
                review=await call('analysis','/api/tasks/review','POST',{**body.model_dump(exclude={'review_token'}),'parameters':params})
                for key in ['host_index',*REFERENCE_KEYS]:
                    if review.get('parameters',{}).get(key)!=params.get(key):errors.append(f'{key} 与分析服务实际配置不一致')
            except HTTPException as exc:
                if exc.status_code>=500:raise
                errors.append(str(exc.detail))
        payload={**body.model_dump(exclude={'review_token'}),'parameters':params}
        identity={'request':payload,'profile':refs['profile'],'host':{k:refs['host'][k] for k in ('path','fingerprint')},
                  'databases':[{k:r.get(k) for k in ('id','path','release','tool_compatibility_version','manifest_sha256')} for r in refs['items'] if r['group']!='mag_annotation' or params.get('enable_mags')],
                  'parameters':review.get('parameters') if review else None,'groups':review.get('groups') if review else None}
        token=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        return {'ready':not errors,'errors':errors,'references':refs,'review':review,'request':payload,'review_token':token}

    @router.post('/analysis/check')
    async def check_setup(pid:str,body:AnalysisSetup,request:Request):
        return await check(pid,body,request)

    @router.post('/analysis/start',status_code=201)
    async def start_analysis(pid:str,body:AnalysisStart,request:Request):
        result=await check(pid,body,request)
        if not result['ready']:raise HTTPException(422,{'message':'分析设置检查未通过','errors':result['errors']})
        if body.review_token!=result['review_token']:raise HTTPException(409,'分析参数、分组或数据库版本已变化，请重新检查并确认')
        task=await call('analysis','/api/tasks','POST',result['request'])
        store=get_store();store.claim('task',task['id'],request.state.user['id']);store.link(pid,'task',task['id'])
        return task
