import unittest

from backend.bioflow_platform.research_routing import compile_route


class ResearchRoutingTests(unittest.TestCase):
    def setUp(self):
        self.data = [
            {'id':'tax','role':'taxonomy','engine_info':{'summary':{'rank_counts':{'species':12}}}},
            {'id':'meta','role':'metadata','schema':{'columns':['Zn分组','age']}},
            {'id':'fun','role':'functional','engine_info':{'summary':{'stratified_rows':20,'total_pathways':0}}},
        ]
        self.bindings = {'metadata_id':'meta','group_column':'Zn分组','group_values':['低水平组','高水平组']}

    def route(self, goal, **kwargs):
        return compile_route(self.data,goal,self.bindings,**kwargs)

    def test_original_failed_request_compiles_without_asking_model_to_pick_tools(self):
        goal='根据样本信息给出的高低水平分组，对物种丰度和通路表进行初步差异分析、多样性分析。\n用户选定的登记工具（必须包含）：sankey'
        route=self.route(goal)
        self.assertEqual(route['id'],'microbiome_grouped_basic.v1')
        steps=route['plan']['steps']
        self.assertEqual([s['skill'] for s in steps],
                         ['differential','alpha_diversity','alpha_comparison','community_comparison'])
        self.assertTrue(all(s['parameters']['dataset_id']=='tax' for s in steps))
        self.assertEqual({p['code'] for p in route['pending_requests']},
                         {'community_pathways_missing','relationship_definition_missing'})
        self.assertFalse(any(s['skill'] in {'group_feature_summary','functional_differential','sankey'} for s in steps))
        self.assertFalse(any('协变量' in q or '哪一列' in q for q in route['plan']['questions']))

    def test_total_pathways_have_their_own_tool_and_actual_dataset(self):
        self.data[2]['engine_info']['summary']['total_pathways']=10
        route=self.route('按已有组别对物种丰度和通路做差异分析')
        self.assertEqual([s['skill'] for s in route['plan']['steps']],['differential','functional_differential'])
        functional=route['plan']['steps'][1]
        self.assertEqual(functional['parameters']['dataset_id'],'fun')
        self.assertEqual(functional['parameters']['metadata_id'],'meta')
        self.assertEqual(functional['parameters']['group_values'],['低水平组','高水平组'])

    def test_explicit_contribution_is_not_reported_as_functional_differential(self):
        route=self.route('做物种差异和通路贡献分析')
        self.assertEqual([s['skill'] for s in route['plan']['steps']],['differential','pathway_contribution'])
        self.assertEqual(route['plan']['steps'][1]['parameters']['dataset_id'],'fun')
        self.assertEqual(route['pending_requests'],[])

    def test_only_alpha_revision_does_not_keep_old_differential_and_beta(self):
        route=self.route('初始研究目标：差异和多样性。后续修改：只计算Alpha指标，不做组间比较',
                         latest='只计算Alpha指标，不做组间比较')
        self.assertEqual([s['skill'] for s in route['plan']['steps']],['alpha_diversity'])

    def test_missing_group_is_a_specific_real_question(self):
        self.bindings['group_column']=None
        route=self.route('比较两组物种差异')
        self.assertEqual(route['plan']['steps'][0]['skill'],'data_check')
        self.assertEqual(route['pending_requests'][0]['code'],'group_column_missing')
        self.assertIn('Zn分组',route['plan']['questions'][0])

    def test_advanced_method_and_unhandled_tool_go_to_model(self):
        for goal in ['校正年龄后比较物种差异','先筛选样本再做差异','做差异并训练随机森林',
                     '多样性分析\n用户选定的登记工具（必须包含）：bioflow_heatmap']:
            self.assertIsNone(self.route(goal),goal)

    def test_generic_overview_remains_with_existing_overview_router(self):
        self.assertIsNone(self.route('分析这三个文件，给一个方案'))

    def test_group_values_remain_real_and_no_preparation_is_invented(self):
        route=self.route('按样本已有高低组做差异分析')
        parameters=route['plan']['steps'][0]['parameters']
        self.assertEqual(parameters['group_values'],self.bindings['group_values'])
        self.assertNotIn('preparation',parameters)
        self.assertNotIn('independent',parameters)


if __name__=='__main__':
    unittest.main()
