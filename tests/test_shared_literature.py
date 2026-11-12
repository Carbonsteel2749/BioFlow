import asyncio
import json
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.bioflow_platform import assets
from backend.bioflow_platform.store import Store
from backend.bioflow_platform.shared_literature import initialize,install,save_paper,rank,evidence
from backend.bioflow_platform.literature_query import prepare,tokens


def fixture(tmp_path,monkeypatch):
    monkeypatch.setenv('BIOFLOW_EVIDENCE_SEMANTIC_INDEX','0')
    store=Store(tmp_path/'test.sqlite3');initialize(store);monkeypatch.setattr(assets,'ROOT',tmp_path)
    app=FastAPI()
    @app.middleware('http')
    async def user(request,call_next):
        request.state.user={'id':request.headers.get('user','one'),'role':request.headers.get('role','user')}
        return await call_next(request)
    install(app,lambda:store)
    return store,TestClient(app)


def test_shared_read_private_collection_and_admin_removal(tmp_path,monkeypatch):
    store,c=fixture(tmp_path,monkeypatch);pid=save_paper(store,{'title':'Autism and gut microbiota','doi':'10.1/test','authors':'A','year':'2026'},'Gut microbiota in autism.','abstract')
    for uid in ['one','two']:
        assert c.get('/api/public-literature/search',headers={'user':uid}).json()['total']==1
        assert c.get(f'/api/public-literature/papers/{pid}/content',headers={'user':uid}).json()['text']=='Gut microbiota in autism.'
        assert c.post(f'/api/public-literature/papers/{pid}/collect',headers={'user':uid}).status_code==200
    assert c.post(f'/api/public-literature/papers/{pid}/collect').json()['already_saved']
    with store.connect() as db:
        rows=db.execute('SELECT * FROM personal_documents').fetchall();assert len(rows)==2
        assert all((tmp_path/'runtime/personal-library'/r['path']).is_file() for r in rows)
    assert c.delete(f'/api/public-literature/papers/{pid}').status_code==403
    assert c.delete(f'/api/public-literature/papers/{pid}',headers={'role':'developer'}).status_code==200
    assert c.get('/api/public-literature/search').json()['total']==0
    assert c.get(f'/api/public-literature/papers/{pid}/content').status_code==404
    with store.connect() as db:assert db.execute('SELECT count(*) FROM personal_documents').fetchone()[0]==2


def test_submission_moderation_privacy_and_replay(tmp_path,monkeypatch):
    store,c=fixture(tmp_path,monkeypatch)
    data={'title':'Gut microbiota and autism','doi':'10.1/new','abstract':'A study of autism and gut microbiota.'}
    sid=c.post('/api/public-literature/submissions',json=data).json()['id']
    assert not c.get('/api/public-literature/submissions',headers={'user':'two'}).json()['items']
    assert c.get('/api/public-literature/search').json()['total']==0
    assert c.post(f'/api/public-literature/submissions/{sid}/review',json={'decision':'approve'}).status_code==403
    assert c.post(f'/api/public-literature/submissions/{sid}/review',json={'decision':'approve'},headers={'role':'developer'}).status_code==200
    assert c.post(f'/api/public-literature/submissions/{sid}/review',json={'decision':'reject'},headers={'role':'developer'}).status_code==409
    assert c.get('/api/public-literature/search').json()['total']==1
    assert c.post('/api/public-literature/submissions',json={**data,'personal_id':'other-users-file'}).status_code==404


def test_paper_pagination_is_independent_of_evidence(tmp_path,monkeypatch):
    store,c=fixture(tmp_path,monkeypatch)
    for i in range(27):save_paper(store,{'title':f'Autism gut microbiota {i}','doi':f'10.1/{i}'},'Gut microbiota in autism.','abstract')
    a=c.get('/api/public-literature/search?limit=20').json();b=c.get('/api/public-literature/search?limit=20&offset=20').json()
    assert (a['total'],len(a['items']),len(b['items']))==(27,20,7)
    assert not set(p['id'] for p in a['items']).intersection(p['id'] for p in b['items'])
    rows=rank(__import__('backend.bioflow_platform.shared_literature',fromlist=['public_rows']).public_rows(store),'autism gut microbiota')
    assert len(evidence(rows,'autism',limit=3))==3
    assert evidence([{'quality':'metadata','content':'fake evidence'}],'autism')==[]


def test_chinese_dictionary_and_english_normalize_to_same_concepts(monkeypatch):
    import httpx
    async def unavailable(*args,**kwargs):raise httpx.ConnectError('offline')
    monkeypatch.setattr(httpx.AsyncClient,'post',unavailable)
    for chinese,english in [('自闭症与肠道菌群','autism and gut microbiota'),('益生菌干预自闭症','probiotic intervention for autism'),('自闭症儿童的短链脂肪酸','short chain fatty acids in children with autism')]:
        a=asyncio.run(prepare(chinese));b=asyncio.run(prepare(english))
        assert set(tokens(a['english']))==set(tokens(b['english']))


def test_xml_inline_markup_is_not_lost():
    from deploy.repair_public_literature import parse_xml
    xml='<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID><Article><ArticleTitle>Autism <i>gut</i> microbiota</ArticleTitle><Abstract><AbstractText>Before <b>important</b> after.</AbstractText></Abstract></Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/a</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>'
    p=parse_xml(xml)['10.1/a'];assert p['title']=='Autism gut microbiota';assert p['abstract']=='Before important after.'


def test_ordinary_user_qa_uses_public_evidence_and_refuses_title_only(tmp_path,monkeypatch):
    store,c=fixture(tmp_path,monkeypatch)
    save_paper(store,{'title':'Autism gut microbiota title only','doi':'10.1/title'},'',quality='metadata')
    r=c.post('/api/public-literature/qa',json={'query':'autism gut microbiota'})
    assert r.status_code==200 and r.json()['sources']==[]
    pid=save_paper(store,{'title':'Autism gut microbiota abstract','doi':'10.1/evidence'},'A controlled study in mice with autism-like behavior.','abstract')
    import httpx
    calls=[]
    async def answer(self,url,**kwargs):
        calls.append(kwargs['json'])
        return httpx.Response(200,json={'message':{'content':'动物研究结果 [1]。'}},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx.AsyncClient,'post',answer)
    r=c.post('/api/public-literature/qa',json={'query':'autism gut microbiota'})
    assert r.status_code==200 and r.json()['sources'][0]['id']==pid
    assert calls[0]['model']=='qwen3:14b' and 'mice' in calls[0]['messages'][1]['content']
    assert 'title only' not in calls[0]['messages'][1]['content']


def test_online_reference_collection_needs_no_project(tmp_path,monkeypatch):
    store,c=fixture(tmp_path,monkeypatch)
    p={'title':'An online paper','doi':'https://doi.org/10.1/Online','authors':'A','year':'2026'}
    a=c.post('/api/library/references',json=p);b=c.post('/api/library/references',json=p)
    assert a.status_code==200 and a.json()['id']==b.json()['id'] and b.json()['already_saved']
    with store.connect() as db:
        row=db.execute('SELECT * FROM personal_documents').fetchone()
        assert json.loads(row['metadata'])['doi']=='10.1/online'
        assert db.execute('SELECT count(*) FROM projects').fetchone()[0]==0


def test_openai_stream_keeps_sources_and_completion_event(tmp_path,monkeypatch):
    store,c=fixture(tmp_path,monkeypatch)
    pid=save_paper(store,{'title':'Autism gut microbiota','doi':'10.1/source'},'A public source.','abstract')
    monkeypatch.setenv('BIOFLOW_PUBLIC_QA_PROVIDER','openai')
    monkeypatch.setenv('BIOFLOW_PUBLIC_QA_URL','http://model.invalid/v1/chat/completions')
    import httpx
    class Stream:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def raise_for_status(self):pass
        async def aiter_lines(self):
            yield 'data: '+json.dumps({'choices':[{'delta':{'content':'Evidence [1]'}}]})
            yield ''
            yield 'data: '+json.dumps({'choices':[{'delta':{},'finish_reason':'stop'}]})
            yield 'data: [DONE]'
    def stream(self,*args,**kwargs):
        assert kwargs['json']['chat_template_kwargs']['enable_thinking'] is False
        return Stream()
    monkeypatch.setattr(httpx.AsyncClient,'stream',stream)
    r=c.post('/api/public-literature/qa',json={'query':'autism','stream':True})
    events=[json.loads(line) for line in r.text.splitlines()]
    assert events[0]['type']=='sources' and events[0]['sources'][0]['id']==pid
    assert events[1]=={'type':'token','text':'Evidence [1]'}
    assert events[-1]=={'type':'done','truncated':False}


def test_qa_respects_model_disable_setting(tmp_path,monkeypatch):
    store,c=fixture(tmp_path,monkeypatch)
    with store.connect() as db:db.execute("INSERT INTO ability_settings(kind,id,enabled) VALUES('models','literature-chat',0)")
    assert c.post('/api/public-literature/qa',json={'query':'autism'}).status_code==409


def test_pagination_reuses_query_vector_without_caching_paper_permissions(tmp_path,monkeypatch):
    from backend.bioflow_platform import shared_literature as lib
    store,c=fixture(tmp_path,monkeypatch);lib.QUERY_VECTORS.clear();calls=[]
    async def embedding(texts):calls.append(texts);return [[1.0,0.0]]
    monkeypatch.setattr(lib,'embed',embedding)
    pid=save_paper(store,{'title':'Autism gut microbiota','doi':'10.1/cache'},'Evidence.','abstract',vector=[1.0,0.0])
    assert c.get('/api/public-literature/search?q=autism').json()['total']==1
    assert c.get('/api/public-literature/search?q=autism&offset=1').json()['items']==[]
    assert len(calls)==1
    assert c.delete(f'/api/public-literature/papers/{pid}',headers={'role':'developer'}).status_code==200
    assert c.get('/api/public-literature/search?q=autism').json()['total']==0


def test_translation_cannot_replace_known_medical_concepts(monkeypatch):
    import httpx
    async def translate(self,url,**kwargs):
        assert kwargs['json']['text']=='糖基化'
        return httpx.Response(200,json={'translation':'glycosylation'},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx.AsyncClient,'post',translate)
    result=asyncio.run(prepare('自闭症糖基化'))
    assert set(tokens(result['english']))=={'autism','glycosylation'}
