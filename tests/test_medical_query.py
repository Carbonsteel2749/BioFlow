import asyncio
import json
import pytest
import httpx
from backend.bioflow_platform.literature_query import prepare,tokens,medical_terms,normalize,CACHE
from backend.bioflow_platform.shared_literature import rank,paper_features


@pytest.fixture(autouse=True)
def fresh_query_cache():
    CACHE.clear()


def test_screenshot_query_uses_exact_medical_names_without_mt(monkeypatch):
    async def forbidden(*args,**kwargs):raise AssertionError('Known medical terms must bypass general MT')
    monkeypatch.setattr(httpx.AsyncClient,'post',forbidden)
    q='在遗传性自闭症小鼠模型中，木糖醇是否会影响肠道菌群、神经递质和突触结构？'
    plan=asyncio.run(prepare(q))
    assert {'xylitol','neurotransmitter','synaptic','structure','mouse','genetic'}<=set(plan['english'].split())
    assert not {'caramel','neurotransmittural','animal'}&set(plan['english'].split())
    assert plan['english'].startswith('xylitol ')
    assert not plan['unresolved_medical_terms']
    assert next(t for t in plan['medical_terms'] if t['source']=='木糖醇')['canonical']=='xylitol'


@pytest.mark.parametrize('chinese,english',[
    ('木糖醇','xylitol'),('神经递质','neurotransmitters'),('突触结构','synaptic structures'),
    ('树突棘形态','dendritic spine morphology'),('罗伊氏乳杆菌','L. reuteri'),
    ('γ-氨基丁酸','GABA'),('氧化三甲胺','TMAO'),('脆弱拟杆菌','Bacteroides fragilis'),
    ('丁酸钠','sodium butyrate'),('5-羟色氨酸','5-HTP'),('小鼠','mice'),('大鼠','rats')])
def test_domain_terms_match_english_synonyms(chinese,english):
    assert tokens(chinese)==tokens(english)


def test_distinct_animals_compounds_and_enantiomers_stay_distinct():
    assert tokens('小鼠')!=tokens('大鼠')!=tokens('动物')
    assert tokens('木糖醇')!=tokens('赤藓糖醇')!=tokens('甘露醇')
    assert tokens('L-乳酸')!=tokens('D-乳酸')
    assert tokens('5-羟色氨酸')!=tokens('五羟色胺')
    assert normalize('dexylitol')=='dexylitol'
    assert not any(t['canonical']=='chd8' for t in medical_terms('CHD80'))


def test_unknown_drug_name_keeps_characters_and_is_not_guessed(monkeypatch):
    async def forbidden(*args,**kwargs):raise AssertionError('Unconfirmed drugs must not go to MT')
    monkeypatch.setattr(httpx.AsyncClient,'post',forbidden)
    plan=asyncio.run(prepare('可的松与自闭症'))
    assert plan['unresolved_medical_terms']==['可的松']
    assert '可的松' in plan['issues'][0]


def test_general_qualifiers_cannot_change_protected_compound(monkeypatch):
    async def translated(self,url,**kwargs):
        assert '木糖醇' not in kwargs['json']['text']
        return httpx.Response(200,json={'translation':'glycosylation'},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx.AsyncClient,'post',translated)
    plan=asyncio.run(prepare('木糖醇糖基化与自闭症'))
    assert 'xylitol' in plan['english'] and 'glycosylation' in plan['english']


def test_specific_compound_precedes_general_model_review():
    def row(identifier,title,body):return {'id':identifier,'metadata':json.dumps({'title':title,'abstract':body}),'content':body,'vector':'[]'}
    rows=[row('general','Genetic autism mouse models neurotransmitter synaptic structure','Autism gut microbiota genetic model mouse neuroscience. '*20),
          row('target','Xylitol ameliorates ASD-like behavior in Chd8 mice','Gut microbiota and neuronal morphology.')]
    plan=asyncio.run(prepare('遗传性自闭症小鼠模型 木糖醇 神经递质 突触结构'))
    assert rank(rows,plan['english'],terms=plan['medical_terms'])[0]['id']=='target'


def test_paper_feature_cache_does_not_return_removed_rows():
    paper_features.cache_clear()
    row={'id':'one','metadata':json.dumps({'title':'Xylitol autism','abstract':'Gut microbiota.'}),'content':'Gut microbiota.','vector':'[]'}
    rank([row],'xylitol');rank([row],'autism')
    assert paper_features.cache_info().hits>=1
    assert rank([],'xylitol')==[]


def test_unconfirmed_medical_term_stops_qa_before_model_call(tmp_path,monkeypatch):
    from test_shared_literature import fixture
    _,client=fixture(tmp_path,monkeypatch)
    async def forbidden(*args,**kwargs):raise AssertionError('Unconfirmed medical names must not reach QA')
    monkeypatch.setattr(httpx.AsyncClient,'post',forbidden)
    response=client.post('/api/public-literature/qa',json={'query':'可的松与自闭症'})
    assert response.status_code==200 and response.json()['sources']==[]
    assert '可的松' in response.json()['answer'] and '标准英文' in response.json()['answer']
