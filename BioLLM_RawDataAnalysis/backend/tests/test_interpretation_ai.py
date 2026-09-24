import json
import httpx
import pytest


def result():
    return {'version':'1.2','task_id':'t','display_sections':[{'category':'taxonomy','sample_id':'private-patient','source_ids':['source1'],'reasoning':{'finding':'前3项累计占比80%。','interpretation':'当前估计中占过半。','limitation':'不是疾病证据。','recommendation':'核对分类率。'}}], 'sources':[{'id':'source1','path':'private-patient/result.tsv','sha256':'a'*64}]}


@pytest.mark.parametrize('response, expected', [({'selected_ids':['E1']},'ai_selected'),({'selected_ids':['FAKE']},'fallback'),({'selected_ids':['E1'],'text':'患者患病'},'fallback')])
def test_model_can_select_only_existing_evidence(response,expected):
    from backend.app.services.interpretation_ai import InterpretationAI
    seen=[]
    def transport(request):
        payload=json.loads(request.content);seen.append(payload)
        return httpx.Response(200,json={'message':{'content':json.dumps(response)}})
    service=InterpretationAI('http://127.0.0.1:11434','qwen3:14b',2,client=httpx.Client(transport=httpx.MockTransport(transport)))
    output=service.refine(result())
    assert output['status']==expected
    assert output['highlights'][0]['finding']=='前3项累计占比80%。'
    assert 'private-patient' not in json.dumps(seen)
    assert '患者患病' not in json.dumps(output,ensure_ascii=False)


def test_timeout_keeps_deterministic_findings():
    from backend.app.services.interpretation_ai import InterpretationAI
    def timeout(request): raise httpx.ReadTimeout('timeout')
    service=InterpretationAI('http://127.0.0.1:11434','qwen3:14b',2,client=httpx.Client(transport=httpx.MockTransport(timeout)))
    output=service.refine(result())
    assert output['status']=='fallback' and output['highlights']


def test_sample_identifier_cannot_rewrite_evidence_ids_or_json_fields():
    from backend.app.services.interpretation_ai import build_prompt
    data=result();data['display_sections'][0]['sample_id']='E1'
    prompt=build_prompt(data)
    evidence=json.loads(prompt['user'])['evidence'][0]
    assert evidence['id']=='E1'
    assert evidence['sample_alias']=='SAMPLE_1'
    assert 'sample_id' not in evidence
