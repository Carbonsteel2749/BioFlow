import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'article_writing'))
from article_writing.skills import availability
from bioflow_skills import run_skill


@pytest.fixture
def guides(tmp_path,monkeypatch):
    db=tmp_path/'database/center.sqlite';db.parent.mkdir()
    sid='a'*32;rid='b'*32
    folder=tmp_path/'skills'/sid/'revisions'/rid/'bundle'/'test-guide';folder.mkdir(parents=True)
    content=b'---\nname: test-guide\ndescription: Guide\n---\nUse the traceable evidence matrix.\n'
    (folder/'SKILL.md').write_bytes(content)
    with sqlite3.connect(db) as con:
        con.executescript('CREATE TABLE skills(id TEXT,body TEXT);CREATE TABLE revisions(id TEXT,body TEXT);')
        con.execute('INSERT INTO skills VALUES(?,?)',(sid,json.dumps({'id':sid,'skill_name':'test-guide','description':'Guide','enabled':True,'published_revision_id':rid,'applicable_modules':['writing']})))
        con.execute('INSERT INTO revisions VALUES(?,?)',(rid,json.dumps({'id':rid,'entry_path':'test-guide/SKILL.md','files':[{'path':'test-guide/SKILL.md','sha256':hashlib.sha256(content).hexdigest()}]})))
    monkeypatch.setenv('BIOFLOW_SKILL_CENTER_DATABASE',str(db))
    return 'center:'+sid,folder


def test_shared_guide_reaches_real_pipeline_and_is_pinned(guides):
    from article_writing.orchestrator.pipeline import WritingPipeline
    selector,_=guides
    pipe=WritingPipeline(fixtures_dir=ROOT/'article_writing/fixtures',selected_skills=[selector],react_enabled=False)
    assert selector in pipe.selected_skills
    assert 'traceable evidence matrix' in pipe.llm_instructions
    assert pipe.shared_skill_provenance[0]['revision_id']=='b'*32


def test_modified_snapshot_is_refused(guides):
    selector,folder=guides
    (folder/'SKILL.md').write_text('changed')
    with pytest.raises(ValueError,match='校验失败'):
        availability.selected_guidance([selector])


def test_unpublished_or_unknown_guides_are_not_silently_ignored(guides):
    with pytest.raises(ValueError,match='不存在'):
        availability.selected_guidance(['center:'+'c'*32])


def test_author_supplied_approval_is_preserved_exactly():
    from article_writing import section_library
    from article_writing.contracts import SectionId
    from article_writing.skills.library_sections import write_library_section
    text='本研究获测试委员会批准，编号 TEST-2026-ABC；此文本仅用于验收。'
    d=write_library_section(section_library.get('ethics_statement'),SectionId.ethics_statement,
                            author_information={'ethics_statement':text})
    assert d.markdown.strip()==text
    assert d.metadata['library']['status']=='author_provided'


def test_keywords_and_abbreviations_come_from_supplied_text():
    result=run_skill('keywords-writer',{'brief':{'title':'Metagenomic microbiome diversity profiles'}})
    assert result.succeeded and 'microbiome' in result.data['markdown']
    result=run_skill('abbreviations-writer',{'written':{'methods':'Principal component analysis (PCA) was used.'}})
    assert 'PCA: Principal component analysis' in result.data['markdown']
    assert 'DNA' not in result.data['markdown']


def test_outputs_are_not_automatically_claimed_as_supplementary():
    result=run_skill('supplementary-writer',{'materials':{'tables':[{'file':'tables/results.tsv'}]}})
    assert 'AUTHOR_INPUT_NEEDED' in result.data['markdown']
    assert 'results.tsv' not in result.data['markdown']


def test_literature_writer_accepts_valid_reference_claim():
    class Model:
        def generate(self,prompt,system=None):return 'The cited study reported incomplete metadata [@StudyA].'
    result=run_skill('review-topics-writer',{'cards':[{'key':'StudyA','claims':[{'text':'The cited study reported incomplete metadata.'}]}],'model':Model()})
    assert result.succeeded
    assert result.data['citations']==['StudyA']
    assert 'AUTHOR_INPUT_NEEDED' not in result.data['markdown']


def test_corrupt_optional_report_does_not_destroy_verified_table_facts(tmp_path):
    from test_writing_core_skills import package
    from bioflow_skills.skills.writing.fact_ledger import build_ledger
    root=package(tmp_path)
    folder=root/'qc'/'s1';folder.mkdir(parents=True,exist_ok=True)
    (folder/'s1.fastp.json').write_text('')
    ledger=build_ledger(root)
    assert any(f['id']=='COHORT.sample_count' for f in ledger['facts'])
    assert not any(f['id']=='READQ.q30_after.s1' for f in ledger['facts'])
    assert any('Unreadable fastp' in n for n in ledger['notes'])


def test_native_call_gets_model_from_server_not_json(monkeypatch):
    from fastapi.testclient import TestClient
    from frontend import server
    class Model:
        def generate(self,prompt,system=None):return 'The benchmark took 12 seconds.'
    monkeypatch.setattr(server,'load_llm_config',lambda:{'provider':'fake','model':'test','url':'http://local','timeout':10})
    monkeypatch.setattr(server,'build_llm_client',lambda **kwargs:Model())
    response=TestClient(server.app).post('/api/runtime-skills/benchmarking-writer',json={
        'payload':{'model':'an unusable JSON string','section':'benchmarking','ledger':{'facts':[
            {'id':'BENCH.runtime','text':'The benchmark took 12 seconds.'}]}}})
    assert response.status_code==200
    assert response.json()['data']['markdown']=='The benchmark took 12 seconds.'


def test_local_qwen_request_disables_hidden_reasoning_and_rejects_truncation(monkeypatch):
    import io
    from article_writing.llm import openai_compat
    captured={}
    def response(request,timeout):
        captured.update(json.loads(request.data))
        return io.BytesIO(json.dumps({'choices':[{'message':{'content':'Verified draft.'},'finish_reason':'stop'}]}).encode())
    monkeypatch.setattr(openai_compat.urllib.request,'urlopen',response)
    client=openai_compat.OpenAICompatClient('qwen3:14b','http://127.0.0.1:11440/v1')
    assert client.generate('Draft.')=='Verified draft.'
    assert captured['chat_template_kwargs']=={'enable_thinking':False}
    assert captured['max_tokens']==4096


def test_agent_handoff_is_consumed_and_requires_source_material(monkeypatch):
    from fastapi.testclient import TestClient
    from frontend import server
    class Model:
        def generate(self,prompt,system=None):return 'The supplied record describes 12 samples.'
    monkeypatch.setattr(server,'load_llm_config',lambda:{'provider':'fake','model':'test','url':'http://local','timeout':10})
    monkeypatch.setattr(server,'build_llm_client',lambda **kwargs:Model())
    client=TestClient(server.app)
    bad=client.post('/api/runtime-skills/nature-data',json={'payload':{'task_context':'Write an availability statement.'}})
    assert bad.status_code==422
    good=client.post('/api/runtime-skills/nature-data',json={'payload':{'task_context':'Write an availability statement.',
       'source_materials':'The supplied record describes 12 samples.'}})
    assert good.status_code==200 and good.json()['status']=='success'
    assert good.json()['data']['execution_mode']=='model_assisted'


@pytest.mark.parametrize('path',['api/section-library','api/writing-skills'])
def test_regular_account_can_read_writing_catalogues(path):
    import asyncio
    import importlib.util
    from types import SimpleNamespace
    spec=importlib.util.spec_from_file_location('tested_skill_access',ROOT/'backend/bioflow_platform/access.py')
    access=importlib.util.module_from_spec(spec);spec.loader.exec_module(access)
    request=SimpleNamespace(method='GET',state=SimpleNamespace(user={'role':'user','id':'ordinary-test-user'}))
    asyncio.run(access.authorize(object(),'writing',path,request,{}))


def test_regular_account_still_cannot_change_model_configuration():
    import asyncio
    import importlib.util
    from types import SimpleNamespace
    from fastapi import HTTPException
    spec=importlib.util.spec_from_file_location('tested_skill_access',ROOT/'backend/bioflow_platform/access.py')
    access=importlib.util.module_from_spec(spec);spec.loader.exec_module(access)
    request=SimpleNamespace(method='PUT',state=SimpleNamespace(user={'role':'user','id':'ordinary-test-user'}))
    with pytest.raises(HTTPException) as error:
        asyncio.run(access.authorize(object(),'writing','api/llm-config',request,{}))
    assert error.value.status_code==403
