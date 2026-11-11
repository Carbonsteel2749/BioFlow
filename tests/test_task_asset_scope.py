from test_provenance import client,record
from backend.bioflow_platform import app as gateway,assets
from backend.bioflow_platform.provenance import project_task_ids

def test_workflow_membership_filters_assets_and_rejects_other_project(client):
    c=client;store=gateway.store
    store.claim('workflow','flow-a',c.uid);store.link(c.pid,'workflow','flow-a')
    assets.write_snapshot('flow-a',{'id':'flow-a','name':'Flow A','status':'completed'},[])
    record(c,'linked',metadata={'task_id':'flow-a'});record(c,'unrelated')
    assert project_task_ids(store,c.pid,c.uid)=={'flow-a'}
    data=c.get(c.base+'/assets?task_id=flow-a').json()
    assert [x['id'] for x in data['items']]==['linked']
    assert c.put(c.base+'/asset-task-links?asset_id=unrelated',json={'task_ids':['flow-a']}).status_code==200
    other=c.post('/api/projects',json={'name':'Other'}).json()['id']
    assert not project_task_ids(store,other,c.uid)
    assert c.post('/api/projects/'+other+'/uploads/drafts',json={'name':'draft.csv','size_bytes':10,'purpose':'data','details':{'task_id':'flow-a'}}).status_code==404
    assert c.post(c.base+'/uploads/drafts',json={'name':'draft.csv','size_bytes':10,'purpose':'data','details':{'task_id':'flow-a'}}).status_code==201

def test_same_file_upload_keeps_each_task_membership(client):
    from test_uploads import manifest
    c=client;store=gateway.store;data=b'feature,value\na,1\n';base=c.base+'/uploads'
    ids=[]
    for tid in ('flow-a','flow-b'):
        store.claim('workflow',tid,c.uid);store.link(c.pid,'workflow',tid)
        body=manifest('results.csv',data);body.update(purpose='results',details={'task_id':tid})
        draft=c.post(base+'/drafts',json={'name':'results.csv','size_bytes':len(data),'purpose':'results','details':{'task_id':tid}})
        assert draft.status_code==201,draft.text
        body['draft_id']=draft.json()['id'];res=c.post(base,json=body);assert res.status_code==201,res.text
        path=base+'/'+res.json()['id']
        for i in range(len(body['hashes'])):assert c.put(path+'/chunks/'+str(i),content=data[i*8:(i+1)*8]).status_code==200
        complete=c.post(path+'/complete',json={});assert complete.status_code==200,complete.text
        ids.append(complete.json()['asset_id'])
    assert ids[0]!=ids[1]
    for index,tid in enumerate(('flow-a','flow-b')):
        assert [r['id'] for r in c.get(c.base+'/assets?kind=results&task_id='+tid).json()['items']]==[ids[index]]
