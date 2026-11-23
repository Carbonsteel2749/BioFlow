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
