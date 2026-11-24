import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from test_unified_research import client, upload, plan, confirm
from backend.bioflow_platform import app as gateway, research_store as rs, research_queue


def program(identifier, code=''):
    return {'method':'保留四个输入样本，计算value列的算术均值。','inputs':[identifier],
            'requirements':[{'id':'r1','description':'输入四个有效数值，输出一行有限均值。'}],
            'outputs':[{'name':'means','kind':'table','title':'均值结果','columns':['mean'],'min_rows':1}],
            'requires_independent':False,'code':code}


def mean_code(identifier, broken=False):
    operation='mena' if broken else 'mean'
    return f'''values = pd.to_numeric(tables[{identifier!r}]['value'], errors='raise')
value = float(values.{operation}())
check('r1', len(values) == 4 and np.isfinite(value), '四个有效数值和有限均值')
emit_table(pd.DataFrame({{'mean':[value]}}), 'means')
finish('完成算术均值计算', metrics={{'mean':value}})
'''


@pytest.fixture
def model(monkeypatch):
    state={'calls':[],'fail_first':True,'incomplete':False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            contexts=[]
            for message in body['messages']:
                try: value=json.loads(message['content'])
                except (ValueError,TypeError):continue
                if isinstance(value,dict):contexts.append(value)
            context=contexts[0] if contexts else {}
            if 'frozen_spec' in context:
                state['calls'].append('code')
                if state['incomplete']:
                    code="check('r1', len(tables) > 0, '有输入'); finish('未输出约定的结果')"
                else:
                    code=mean_code(context['frozen_spec']['inputs'][0],state['fail_first'] and not context.get('feedback'))
                answer={'code':code}
            elif 'schema' in context and 'datasets' in context:
                state['calls'].append('plan')
                identifier=context['datasets'][0]['id']
                answer={'objective':context['objective'],'summary':'计算已上传数据的均值。','questions':[],
                        'steps':[{'id':'s1','skill':'custom_analysis','program':program(identifier,"value='unexecuted planning draft'")}]}
            elif 'plan' in context:
                state['calls'].append('design_review');answer={'approved':True,'feedback':''}
            elif 'checks' in context:
                state['calls'].append('result_review')
                answer={'approved':True,'requirements':[{'id':r['id'],'passed':True,'reason':'实际输出完整'} for r in context['requirements']]}
            else:
                answer={'title':'通用Python均值分析'}
            payload=json.dumps({'choices':[{'message':{'content':json.dumps(answer,ensure_ascii=False)},'finish_reason':'stop'}],
                                'usage':{}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(payload)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('BIOFLOW_RESEARCH_MODEL_URL',f'http://127.0.0.1:{server.server_port}/v1')
    monkeypatch.setenv('BIOFLOW_RESEARCH_MODEL_KEY','synthetic-test-key')
    try:yield state
    finally:server.shutdown();server.server_close();thread.join(timeout=3)


def data(client):
    return upload(client,'values.csv',b'sample,value\nS1,1\nS2,2\nS3,3\nS4,4\n',role='table')


def test_ai_plan_defers_code_until_confirmation_then_repairs_and_publishes(client,model):
    d=data(client)
    response=client.post(client.base+'/plans',json={'mode':'ai','objective':'用通用Python计算value列算术均值',
                                                  'dataset_ids':[d['id']]})
    assert response.status_code==201,response.text
    p=response.json();assert p['status']=='ready',p
    assert p['steps'][0]['program']['code']==''
    assert 'code' not in model['calls']
    run=confirm(client,p)
    assert 'code' not in model['calls']
    assert research_queue.run_one(gateway.store)
    done=client.get(client.base+'/runs/'+run['id']).json()
    assert done['status']=='completed',done
    result=done['step_results'][0]
    assert result['metrics']['mean']==2.5 and result['metrics']['attempts']==2
    artifacts={a['filename']:a for a in result['artifacts']}
    assert {'means.tsv','analysis.py','acceptance.json','environment.json'}<=set(artifacts)
    audit=client.get(client.base+'/artifacts/'+artifacts['acceptance.json']['id']).json()
    assert [a['status'] for a in audit['attempts']]==['failed','accepted']
    code=client.get(client.base+'/artifacts/'+artifacts['analysis.py']['id']).text
    assert '.mean()' in code and '.mena()' not in code
    assert model['calls'].count('code')==2


def test_custom_output_is_consumed_by_next_step_and_method_is_frozen(client,model):
    model['fail_first']=False;d=data(client)
    second={'method':'将上游均值乘以2。','inputs':[],
            'requirements':[{'id':'r1','description':'输出一个有限的两倍均值。'}],
            'outputs':[{'name':'doubled','kind':'table','title':'两倍均值','columns':['twice'],'min_rows':1}],
            'code':"value=float(pd.to_numeric(results['previous']['mean']).iloc[0])*2\ncheck('r1', np.isfinite(value), '有限数值')\nemit_table(pd.DataFrame({'twice':[value]}),'doubled')\nfinish('完成两倍均值计算',metrics={'twice':value})"}
    p=plan(client,[d['id']],[{'id':'s1','skill':'custom_analysis','program':program(d['id'],mean_code(d['id']))},
        {'id':'s2','skill':'custom_analysis','program':second,'depends_on':['s1'],
         'inputs':{'previous':{'step_id':'s1','output':'means'}}}])
    assert p['status']=='ready',p
    run=confirm(client,p);assert research_queue.run_one(gateway.store)
    done=client.get(client.base+'/runs/'+run['id']).json()
    assert done['status']=='completed',done
    assert done['step_results'][1]['metrics']['twice']==5


def test_missing_declared_outputs_are_not_marked_success(client,model):
    model['incomplete']=True;d=data(client)
    p=plan(client,[d['id']],[{'id':'s1','skill':'custom_analysis','program':program(d['id'])}])
    assert p['status']=='ready',p
    run=confirm(client,p);assert research_queue.run_one(gateway.store)
    done=client.get(client.base+'/runs/'+run['id']).json()
    assert done['status']=='failed' and not done['step_results'],done
    model['incomplete']=False;model['fail_first']=False
    assert client.post(client.base+'/runs/'+run['id']+'/resume').status_code==200
    assert research_queue.run_one(gateway.store)
    recovered=client.get(client.base+'/runs/'+run['id']).json()
    assert recovered['status']=='completed',recovered
    assert recovered['step_results'][0]['metrics']['mean']==2.5


def test_repair_cannot_change_confirmed_method():
    from backend.bioflow_platform import research_engine_bridge
    from yanji.adaptive import frozen_program,PlanChangeRequired
    from yanji.contracts import AnalysisProgram
    original=AnalysisProgram.model_validate(program('source'))
    with pytest.raises(PlanChangeRequired) as error:
        frozen_program(original,original.model_copy(update={'method':'改成其他统计方法'}))
    assert error.value.diagnostic['requires_plan_confirmation']
    for change in ({'sample_alignment':'canonical'},{'inputs':['different_source']},
                   {'requires_independent':True}):
        with pytest.raises(PlanChangeRequired):
            frozen_program(original,original.model_copy(update=change))
    assert frozen_program(original,original.model_copy(update={'code':'value=1'})).method==original.method


def test_plan_change_requires_new_confirmation_before_resume(client):
    d=data(client)
    p=plan(client,[d['id']],[{'id':'s1','skill':'custom_analysis','program':program(d['id'],mean_code(d['id']))}])
    run=confirm(client,p)
    stored=rs.get(gateway.store,client.pid,client.uid,run['id'],'run')
    rs.update(gateway.store,stored,'failed',needs_plan_confirmation=True)
    response=client.post(client.base+'/runs/'+run['id']+'/resume')
    assert response.status_code==409,response.text


def test_scientific_library_and_chart_run_inside_linux_boundary(client,model):
    d=data(client)
    spec={'method':'使用scipy.stats.zscore按总体标准差标准化value，保留样本编号并绘图。','inputs':[d['id']],
          'requirements':[{'id':'r1','description':'保留四个样本，标准化后均值为0、标准差为1。'}],
          'outputs':[{'name':'standardized','kind':'table','title':'标准化数值','columns':['z'],'min_rows':4},
                     {'name':'trend','kind':'chart','title':'标准化数值图'}],
          'code':f'''from scipy.stats import zscore
frame=tables[{d['id']!r}]
values=zscore(pd.to_numeric(frame['value']).to_numpy())
check('r1',len(values)==4 and np.isclose(values.mean(),0) and np.isclose(values.std(),1),'四个样本的标准化检查')
emit_table(pd.DataFrame({{'z':values}},index=frame.index),'standardized')
fig,ax=plt.subplots()
ax.plot(list(frame.index),values)
emit_chart(fig,'trend')
finish('完成标准化和绘图',metrics={{'n':len(values)}})
'''}
    p=plan(client,[d['id']],[{'id':'s1','skill':'custom_analysis','program':spec}])
    assert p['status']=='ready',p
    run=confirm(client,p);assert research_queue.run_one(gateway.store)
    done=client.get(client.base+'/runs/'+run['id']).json()
    assert done['status']=='completed',done
    artifacts=done['step_results'][0]['artifacts']
    assert {'table','png','svg','code','audit'}<={a['artifact_type'] for a in artifacts}
    assert done['step_results'][0]['metrics']['n']==4
