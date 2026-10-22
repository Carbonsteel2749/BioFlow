"""Gateway resource ownership checks. The compute APIs stay on a private network."""
import re
from fastapi import HTTPException

LISTS={
    ('analysis','api/tasks'):('task','id'),
    ('analysis','api/workflows/runs'):('workflow','task_id'),
    ('analysis','api/datasets'):('dataset','dataset_id'),
    ('writing','api/sessions'):('session','session_id'),
    ('literature','api/knowledge-bases'):('kb','id'),
}


def allowed(store,kind,eid,user):
    if not store.owns(kind,eid,user['id']):
        raise HTTPException(404,'记录不存在或不属于当前账户')


async def authorize(store,service,path,request,fetch):
    user=request.state.user
    method=request.method
    parts=path.split('/')
    if (service,path) in LISTS:
        if method=='POST':
            body=await request.json()
            if service=='analysis':
                if user['role']!='developer':
                    manifests={m.get('manifest_path') for m in store.resource_metadata('dataset',user['id'])}
                    if body.get('manifest_path') not in manifests:
                        raise HTTPException(403,'请选择当前账户的数据集')
            elif service=='writing':
                if body.get('analysis_task_id'):
                    allowed(store,'task',body['analysis_task_id'],user)
                    if body.get('analysis_url','').rstrip('/')!=fetch['urls']['analysis']:
                        raise HTTPException(403,'分析服务地址不匹配')
                elif user['role']!='developer':
                    raise HTTPException(403,'请从当前项目分析任务生成稿件')
                if body.get('use_live_literature'):
                    allowed(store,'kb',body.get('knowledge_base_id',''),user)
                    if body.get('literature_url','').rstrip('/')!=fetch['urls']['literature']:
                        raise HTTPException(403,'文献服务地址不匹配')
                if user['role']!='developer' and any(body.get(k) for k in ('analysis_run_dir','analysis_bundle','brief_path','literature_db')):
                    raise HTTPException(403,'当前账户使用项目中的分析任务与文献')
        return
    if service=='analysis' and len(parts)>=3 and parts[:2]==['api','tasks']:
        allowed(store,'task',parts[2],user);return
    if service=='analysis' and len(parts)>=4 and parts[:3]==['api','workflows','runs']:
        allowed(store,'workflow',parts[3],user);return
    if service=='analysis' and len(parts)>=3 and parts[:2]==['api','datasets']:
        allowed(store,'dataset',parts[2],user);return
    if service=='writing' and len(parts)>=3 and parts[:2]==['api','sessions']:
        allowed(store,'session',parts[2],user);return
    if service=='literature' and len(parts)>=3 and parts[:2]==['api','knowledge-bases']:
        allowed(store,'kb',parts[2],user);return
    if service=='analysis' and len(parts)>=3 and parts[:2]==['api','artifacts']:
        result=await fetch['call']('analysis','/api/artifacts/'+parts[2])
        allowed(store,'task',result.get('task_id',''),user);return
    if service=='analysis' and path=='api/uploads/files' and method=='POST':
        return
    if service=='analysis' and path=='api/uploads/manifests' and method=='POST':
        body=await request.json()
        for pair in body.get('files',[]):
            for key in ('read1_upload_id','read2_upload_id'):
                allowed(store,'upload',pair.get(key,''),user)
        return
    if service=='analysis' and path.startswith('api/uploads/sessions'):
        if len(parts)>3 and parts[3]!='config':
            allowed(store,'upload_session',parts[3],user)
        return
    if method=='GET' and ((service=='analysis' and path in {'api/health','api/workflows/templates','api/workflows/registry','api/capabilities','api/methods/parameters'}) or (service=='writing' and path in {'api/health','api/llm-config'}) or (service=='literature' and path=='health')):
        return
    if method=='GET' and service=='analysis' and path.startswith('api/workflows/templates/'):
        return
    if user['role']=='developer':
        return
    raise HTTPException(403,'该管理操作仅对开发者开放')


def process_json(store,service,path,method,data,user):
    definition=LISTS.get((service,path))
    if definition:
        kind,key=definition
        if method=='GET':
            owned=store.owned_ids(kind,user['id'])
            def filtered(rows):
                return [r for r in rows if str(r.get(key) or r.get('id') or '') in owned]
            if isinstance(data,list):
                return filtered(data)
            if isinstance(data,dict):
                for field in ('items','sessions','tasks','runs'):
                    if isinstance(data.get(field),list):
                        data[field]=filtered(data[field])
                        if 'total' in data:data['total']=len(data[field])
                        break
        elif method=='POST' and isinstance(data,dict):
            record=data.get('item',data)
            eid=record.get(key) or record.get('id')
            if eid:store.claim(kind,str(eid),user['id'])
    if service=='analysis' and method=='POST' and isinstance(data,dict):
        if path=='api/uploads/files' and data.get('id'):
            store.claim('upload',data['id'],user['id'])
        if path=='api/uploads/manifests' and data.get('dataset_id'):
            store.claim('dataset',data['dataset_id'],user['id'],data)
        if path=='api/uploads/sessions' and data.get('id'):
            store.claim('upload_session',data['id'],user['id'])
        if path.startswith('api/uploads/sessions/') and path.endswith('/complete') and data.get('id'):
            store.claim('upload',data['id'],user['id'])
    return data
