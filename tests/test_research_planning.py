import json
from types import SimpleNamespace

from backend.bioflow_platform.research_planning import data_profile, eligible_tools, planning_context


def example_data():
    return [
        {'id': 'tax', 'filename': 'taxonomy.tsv', 'role': 'taxonomy', 'samples': ['S1', 'S2'],
         'schema': {'rows': 3, 'columns': ['S1', 'S2']},
         'engine_info': {'unit': 'fraction', 'summary': {'rank_counts': {'species': 3}}}},
        {'id': 'meta', 'filename': 'metadata.xlsx', 'role': 'metadata', 'samples': ['S1', 'S2', 'S3'],
         'schema': {'rows': 3, 'columns': ['group', 'age']}, 'engine_info': {'summary': {}}},
        {'id': 'fun', 'filename': 'pathways.tsv', 'role': 'functional', 'samples': ['S1_combined', 'S2_combined'],
         'schema': {'rows': 8, 'columns': ['S1_combined', 'S2_combined']},
         'engine_info': {'summary': {'stratified_rows': 8, 'total_pathways': 0}}},
    ]


def test_profile_limits_tools_without_changing_sample_identifiers():
    profile = data_profile(example_data())
    assert profile['files'][2]['stratified_only']
    assert profile['alignments'][0]['exact_matches'] == 2
    assert profile['alignments'][1]['exact_matches'] == 0
    tools = [{'id': 'functional_overview', 'requires': 'functional_total'},
             {'id': 'pathway_contribution', 'requires': 'functional_stratified'}]
    available, unavailable = eligible_tools(tools, profile)
    assert [t['id'] for t in available] == ['pathway_contribution']
    assert unavailable[0]['id'] == 'functional_overview'


def test_retry_receives_failed_draft_and_adapter_restores_original_model():
    calls = []
    draft = {'steps': [{'id': 's1', 'skill': 'composition', 'inputs': {}}]}

    def model(messages, **kwargs):
        calls.append(messages)
        return draft

    agent = SimpleNamespace(model_json=model)
    payload = {'datasets': [], 'skills': [{'id': 'composition', 'requires': 'taxonomy'},
                                        {'id': 'functional_overview', 'requires': 'functional_total'}],
               'schema': {'$defs': {'Step': {'properties': {'program': {}}}}}}
    messages = [{'role': 'system', 'content': 'old unsupported prompt'},
                {'role': 'user', 'content': json.dumps(payload)}]
    # The method guidance module is provided by the scientific environment in production.
    import sys
    from unittest.mock import patch
    fake_guidance = SimpleNamespace(GUIDANCE='Step.inputs must read declared results.')
    with patch.dict(sys.modules, {'yanji': SimpleNamespace(), 'yanji.skills': SimpleNamespace(),
                                  'yanji.skills.research_guidance': fake_guidance}):
        with planning_context(agent, example_data()) as trace:
            agent.model_json(messages)
            agent.model_json(messages + [{'role': 'user', 'content': 'missing result input at s2'}])
    assert agent.model_json is model
    repaired_input = json.loads(calls[1][1]['content'])
    assert [t['id'] for t in repaired_input['skills']] == ['composition']
    assert repaired_input['data_profile']['files'][2]['stratified_only']
    assert json.loads(calls[1][2]['content']) == draft
    assert 'missing result input at s2' in calls[1][3]['content']
    assert 'synthesis_step' in calls[1][0]['content']
    assert trace.attempts[0]['error'] == 'missing result input at s2'


def test_file_overview_uses_real_roles_and_does_not_override_specific_intent():
    from backend.bioflow_platform.research_planning import overview_plan
    initial=overview_plan(example_data(),'初始研究目标：\n我想对这三个文件进行分析，请帮我设计方案\n后续修订要求：\n')
    assert [s['skill'] for s in initial['steps']]==['composition','metadata_overview','pathway_contribution']
    assert initial['steps'][0]['parameters']['dataset_id']=='tax'
    assert initial['steps'][1]['parameters']['metadata_id']=='meta'
    assert initial['steps'][2]['parameters']['dataset_id']=='fun'
    assert initial['research_design'] is None
    assert overview_plan(example_data(),'按年龄和性别调整后分析菌种与 Zinc 的关联') is None
    assert overview_plan(example_data(),'仅进行 Alpha 多样性分析') is None
    assert overview_plan(example_data(),'我想分析这三个文件，比较 Zn 分组') is None
    assert overview_plan(example_data(),'分析文件。用户选定的登记工具（必须包含）：alpha_diversity') is None


def test_generation_failure_is_separate_from_scientific_clarification():
    from backend.bioflow_platform.research_planning import generation_failure, overview_plan
    assert generation_failure({'status':'needs_input','questions':['方案生成未通过检查：综合步骤没有输入']})
    assert not generation_failure({'status':'needs_input','questions':['请确认样本是否独立']})
    assert overview_plan(example_data(),'初始研究目标：我想分析这三个文件，请设计方案。后续修订要求：继续')
    assert overview_plan(example_data(),'初始研究目标：我想分析这三个文件，请设计方案。后续修订要求：仅做 Alpha 多样性') is None


def test_preflight_internal_error_enters_repair_and_real_question_is_retained():
    class Issue(ValueError):
        code='other'
        data={'steps':['s1']}
    original_model=lambda messages: {}
    original_validate=lambda plan,pid,datasets=None: plan
    state={'internal':True}
    def original_checks(*args):
        return [Issue('派生列必须有新名称')] if state['internal'] else [Issue('请确认样本是否独立')]
    agent=SimpleNamespace(model_json=original_model,validate_plan=original_validate,checks=original_checks)
    plan=SimpleNamespace(questions=[],steps=[])
    import pytest
    with planning_context(agent,example_data(),{'study':{}}):
        with pytest.raises(ValueError,match='派生列必须有新名称'):
            agent.validate_plan(plan,'p',[])
        state['internal']=False
        agent.validate_plan(plan,'p',[])
        assert plan.questions==['请确认样本是否独立']
    assert agent.checks is original_checks
    assert agent.validate_plan is original_validate


def test_fixed_tool_review_receives_bound_context_and_tool_contracts():
    calls=[]
    def model(messages):
        calls.append(messages)
        return {'approved':True}
    agent=SimpleNamespace(model_json=model,catalog=lambda:[{'id':'differential','requires':'groups'}])
    with planning_context(agent,example_data(),{'study':{'group_column':'group'}}):
        assert agent.model_json([{'role':'system','content':'custom program review'},
          {'role':'user','content':json.dumps({'objective':'比较组间差异','plan':{'steps':[]},'inputs':{}})}])['approved']
    review=json.loads(calls[0][1]['content'])
    assert review['resolved_context']['study']['group_column']=='group'
    assert review['registered_tools'][0]['id']=='differential'
    assert '固定登记工具' in calls[0][0]['content']


def test_optional_adjustment_is_not_added_to_basic_plan():
    calls=[]
    agent=SimpleNamespace(model_json=lambda messages: calls.append(messages) or {'steps':[],'questions':[]})
    payload={'datasets':[], 'objective':'先进行物种差异和多样性分析',
             'skills':[{'id':'differential','requires':'groups'}, {'id':'adjusted_regression','requires':'research'},
                       {'id':'partial_correlation','requires':'research'}], 'schema':{}}
    import sys
    from unittest.mock import patch
    fake=SimpleNamespace(GUIDANCE='')
    with patch.dict(sys.modules,{'yanji':SimpleNamespace(),'yanji.skills':SimpleNamespace(),
                                'yanji.skills.research_guidance':fake}):
        bindings={}
        with planning_context(agent,example_data(),bindings):
            agent.model_json([{'role':'user','content':json.dumps(payload)}])
        sent=json.loads(calls[-1][1]['content'])
        assert [s['id'] for s in sent['skills']]==['differential']
        assert not sent['default_settings']['covariate_adjustment']
        assert bindings['defaults']
        payload['objective']='控制协变量后做调整回归'
        with planning_context(agent,example_data(),{}):
            agent.model_json([{'role':'user','content':json.dumps(payload)}])
        assert 'adjusted_regression' in [s['id'] for s in json.loads(calls[-1][1]['content'])['skills']]


def test_taxonomy_tool_cannot_silently_read_functional_file():
    agent=SimpleNamespace(model_json=lambda messages:{},
        validate_plan=lambda *args:None,checks=lambda *args:[],
        CATALOG={'differential':SimpleNamespace(requires='groups')})
    plan=SimpleNamespace(questions=[],steps=[SimpleNamespace(id='s1',skill='differential',
                                                         parameters=SimpleNamespace(dataset_id='fun'))])
    import pytest
    with planning_context(agent,example_data(),{'study':{}}):
        with pytest.raises(ValueError,match='工具需要 taxonomy 数据'):
            agent.validate_plan(plan,'p',[{'id':'fun','data_type':'functional'}])


def test_custom_review_receives_used_parameters_only():
    calls=[]
    agent=SimpleNamespace(model_json=lambda messages:calls.append(messages) or {'approved':True},
                          catalog=lambda:[{'id':'custom_analysis','requires':'custom'}])
    plan={'steps':[{'id':'s1','skill':'custom_analysis','parameters':{
        'correlation_method':'spearman','standard_error':'HC3','adjustment':'BH','preparation':[]},
        'program':{'method':'z=(x-mean)/std(ddof=0)','outputs':[{'name':'z'}]}}]}
    with planning_context(agent,example_data(),{}):
        agent.model_json([{'role':'user','content':json.dumps({'objective':'标准化','plan':plan,'inputs':{}})}])
    reviewed=json.loads(calls[0][1]['content'])['plan']['steps'][0]
    assert reviewed['parameters']=={'preparation':[]}
    assert reviewed['program']==plan['steps'][0]['program']
    assert 'correlation_method' in plan['steps'][0]['parameters']


def test_explicit_python_selection_has_a_program_specific_schema():
    calls=[]
    agent=SimpleNamespace(model_json=lambda messages:calls.append(messages) or {'steps':[],'questions':[]})
    payload={'datasets':[], 'objective':'只用Python\n用户选定的登记工具（必须包含）：custom_analysis',
        'skills':[{'id':'custom_analysis'},{'id':'differential'},{'id':'prepare_cohort'}],
        'schema':{'$defs':{'SkillParams':{'properties':{'preparation':{},'correlation_method':{}}},
                          'Step':{'properties':{'program':{'type':'object'}}}}}}
    import sys
    from unittest.mock import patch
    with patch.dict(sys.modules,{'yanji':SimpleNamespace(),'yanji.skills':SimpleNamespace(),
        'yanji.skills.research_guidance':SimpleNamespace(GUIDANCE=''),
        'yanji.adaptive':SimpleNamespace(available=lambda:True)}):
        with planning_context(agent,example_data(),{}):
            agent.model_json([{'role':'user','content':json.dumps(payload)}])
    sent=json.loads(calls[0][1]['content'])
    assert [t['id'] for t in sent['skills']]==['custom_analysis','prepare_cohort']
    assert set(sent['schema']['$defs']['SkillParams']['properties'])=={'preparation'}
    assert 'program' in sent['schema']['$defs']['Step']['properties']
