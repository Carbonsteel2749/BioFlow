import copy

import pandas as pd
import pytest

from backend.bioflow_platform.research_bindings import resolve_context, repair_plan


def inputs(extra_columns=None):
    frame = pd.DataFrame({'Zn分组': ['低水平组', '高水平组', '无']})
    for column in extra_columns or []:
        frame[column] = ['A', 'B', 'C']
    data = [{'id': 'meta', 'role': 'metadata', 'schema': {'columns': list(frame.columns)}},
            {'id': 'tax', 'role': 'taxonomy'}]
    return data, {'meta': frame}


def test_existing_high_low_groups_are_bound_from_actual_data():
    data, frames = inputs()
    result = resolve_context(data, '按样本信息给出的高低水平分组，比较物种丰度', frames=frames)
    assert result['metadata_id'] == 'meta'
    assert result['study']['group_column'] == 'Zn分组'
    assert result['group_values'] == ['低水平组', '高水平组']
    assert not result['study'].get('independent')
    assert result['reuse_existing_group']


@pytest.mark.parametrize('text,expected', [
    ('确认是独立样本', True), ('样本相互独立', True),
    ('是否是独立样本？', False), ('未确认独立样本', False),
    ('不是独立样本', False), ('不能确认是独立样本', False),
    ('没有确认独立样本', False), ('不确定是独立样本', False), ('可能是独立样本', False),
])
def test_independence_requires_user_assertion(text, expected):
    data, frames = inputs()
    assert bool(resolve_context(data, text, frames=frames)['study'].get('independent')) == expected


def test_binding_survives_confirmation_and_respects_new_selection():
    data, frames = inputs(['年龄组别'])
    first = resolve_context(data, '按Zn分组比较高低组', frames=frames)
    assert first['group_column'] == 'Zn分组'
    following = resolve_context(data, '按Zn分组比较高低组。确认是独立样本', first['study'],
                                frames, first, '确认是独立样本')
    assert following['group_values'] == first['group_values']
    assert following['study']['independent']
    changed = resolve_context(data, '按Zn分组比较。改用年龄组别', following['study'], frames,
                              following, '改用年龄组别')
    assert changed['group_column'] == '年龄组别'
    assert changed['group_values'] == []
    ambiguous = resolve_context(data, '按样本分组比较', frames=frames)
    assert ambiguous['group_column'] is None
    confirmed = resolve_context(data, '比较两组', frames=frames, latest='是的',
                                known={'pending_questions':['请确认样本是否独立']})
    assert confirmed['study']['independent']
    ambiguous_answer = resolve_context(data, '比较两组', frames=frames, latest='是的',
        known={'pending_questions':['请确认样本是否独立', '请选择要比较的分组']})
    assert not ambiguous_answer['study'].get('independent')


def test_repairs_bad_model_recipe_without_changing_data_or_genuine_questions():
    data, frames = inputs()
    before = frames['meta'].copy(deep=True)
    binding = resolve_context(data, '按样本信息给出的高低水平分组', frames=frames)
    raw = {'summary': '初步分析', 'questions': ['是否需要进一步的协变量调整？',
           '是否需要更详细的多样性分析？', '请确认样本是否独立？'], 'steps': [
        {'id': 's1', 'skill': 'prepare_cohort', 'parameters': {'preparation': [
            {'op': 'quantile_group', 'column': 'Zn分组', 'output': ''}]}},
        {'id': 's2', 'skill': 'differential', 'parameters': {'group_values':['无']},
         'inputs':{'data':{'step_id':'s1','output':'prepared_metadata'}},
         'depends_on': ['s1'], 'cohort_from': 's1'}]}
    original = copy.deepcopy(raw)
    fixed, repairs = repair_plan(raw, binding, [{'id': 'differential', 'requires': 'groups'}], data, '初步分析')
    assert [s['id'] for s in fixed['steps']] == ['s2']
    step = fixed['steps'][0]
    assert step['parameters']['metadata_id'] == 'meta'
    assert step['parameters']['dataset_id'] == 'tax'
    assert step['parameters']['group_by'] == 'Zn分组'
    assert step['parameters']['group_values'] == ['低水平组', '高水平组']
    assert step['depends_on'] == [] and step['cohort_from'] is None
    assert step['inputs'] == {}
    assert fixed['questions'] == ['请确认样本是否独立？']
    assert repairs
    assert raw == original
    pd.testing.assert_frame_equal(frames['meta'], before)


def test_explicit_derivation_and_requested_covariates_are_not_removed():
    data, frames = inputs()
    binding = resolve_context(data, '按Zn分组重新分位数分组，并校正协变量', frames=frames)
    raw = {'steps': [{'id': 's1', 'skill': 'prepare_cohort', 'parameters': {'preparation': [
        {'op': 'quantile_group', 'column': 'Zn分组', 'output': 'new_group'}]}}],
        'questions': ['是否需要进一步的协变量调整？']}
    fixed, _ = repair_plan(raw, binding, [], data, '校正协变量')
    assert fixed['steps'][0]['parameters']['preparation']
    assert fixed['questions'] == raw['questions']


def test_sankey_waits_for_relationship_definition_and_preserves_edge_table_path():
    from backend.bioflow_platform.research_bindings import sankey_pending, SANKEY_QUESTION
    data, frames = inputs()
    assert sankey_pending(data, '用户选定的登记工具（必须包含）：sankey')
    assert not sankey_pending(data + [{'id':'edges','role':'results'}], '使用sankey')
    binding = resolve_context(data, '按高低组比较', frames=frames)
    raw = {'steps':[{'id':'empty','skill':'prepare_cohort','parameters':{}}], 'questions':[]}
    fixed, _ = repair_plan(raw, binding, [], data, '按高低组比较，使用sankey')
    assert fixed['steps'] == []
    assert fixed['questions'] == [SANKEY_QUESTION]


def test_explicit_result_reference_supplies_dependency_without_inventing_output():
    raw={'steps':[{'id':'producer','skill':'differential','parameters':{}},
                  {'id':'plot','skill':'effect_plot','parameters':{},
                   'inputs':{'data':{'step_id':'producer','output':'real_declared_table'}}}]}
    fixed,_=repair_plan(raw,{},[],[],'绘制结果')
    assert fixed['steps'][1]['depends_on']==['producer']
    assert fixed['steps'][1]['inputs']==raw['steps'][1]['inputs']
    cohort={'steps':[{'id':'s1','skill':'prepare_cohort','parameters':{'preparation':[
        {'op':'filter','column':'group','values':['A','B']}]}},
        {'id':'s2','skill':'differential','parameters':{},
         'inputs':{'data':{'step_id':'s1','output':'prepared_metadata'}}}]}
    fixed,_=repair_plan(cohort,{},[],[],'仅分析A和B')
    assert fixed['steps'][1]['inputs']=={}
    assert fixed['steps'][1]['cohort_from']=='s1'
    assert fixed['steps'][1]['depends_on']==['s1']
