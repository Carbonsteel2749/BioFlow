"""Compile well-defined microbiome requests; leave other methods to the planner."""
import re

from .research_bindings import SANKEY_QUESTION, sankey_pending


SUPPORTED = {'data_check', 'metadata_overview', 'composition', 'group_feature_summary',
             'alpha_diversity', 'alpha_comparison', 'beta_diversity', 'community_comparison',
             'differential', 'functional_differential', 'pathway_contribution', 'sankey', 'bioflow_sankey'}


def compile_route(datasets, objective, bindings, latest=None):
    """Return a typed route only when all requested methods belong to this route.

    A missing scientific input is a pending request, never a different analysis.
    No sample aliases, group definitions or independence assertions are invented.
    """
    text = objective.split('当前方案，供本次修改参考：')[0]
    if latest and re.search(r'只|仅|改为|不做|不要', latest) and re.search(r'分析|多样性|差异|Alpha|Beta', latest, re.I):
        text = latest
    if re.search(r'协变量|校正|回归|偏相关|配对|重复测量|分位|阈值|派生|筛选|合并|连接|预测|随机森林|生存|富集|自定义|CLR|log\d?|Pearson|相关分析|关联分析', text, re.I):
        return None
    match = re.search(r'\n用户选定的登记工具（必须包含）：([^\n]+)', objective)
    selected = {s.strip() for s in match.group(1).split(',')} if match else set()
    if selected - SUPPORTED:
        return None
    difference = bool(re.search(r'差异|组间比较', text) or selected & {'differential', 'functional_differential'})
    if re.search(r'(?:不做|不进行|不要|无需).{0,6}(?:差异|组间比较|组间检验)', text) and not selected & {'differential','functional_differential'}:
        difference = False
    diversity = bool(re.search(r'多样性|Alpha|Beta|Shannon|Simpson|Pielou', text, re.I)
                     or selected & {'alpha_diversity', 'alpha_comparison', 'beta_diversity', 'community_comparison'})
    if not (difference or diversity):
        return None
    tax = [d for d in datasets if d.get('role') == 'taxonomy']
    meta = [d for d in datasets if d.get('role') == 'metadata']
    functional = [d for d in datasets if d.get('role') == 'functional']
    if len(tax) != 1 or len(meta) > 1 or len(functional) > 1:
        return None
    if re.search(r'(?:只|仅).{0,5}(?:通路|功能)', text):
        return None

    steps, questions, pending, decisions = [], [], [], []
    defaults = []
    group = bindings.get('group_column')
    metadata_id = bindings.get('metadata_id')
    grouping = bool(group or re.search(r'分组|组别|高低|两组|组间', text))
    ranks = tax[0].get('engine_info', {}).get('summary', {}).get('rank_counts', {})
    rank = next((r for r in ('species', 'genus', 'family', 'phylum') if ranks.get(r)), 'species')
    for label, candidate in (('属水平', 'genus'), ('菌科', 'family'), ('科水平', 'family'), ('门水平', 'phylum'), ('物种水平', 'species')):
        if label in text:
            if ranks and not ranks.get(candidate):
                return None
            rank = candidate
    parameters = {'dataset_id': tax[0]['id'], 'rank': rank}

    def add(skill, params, reason):
        steps.append({'id':'s'+str(len(steps)+1), 'skill':skill, 'parameters':params,
                      'depends_on':[], 'inputs':{}, 'reason':reason})
        decisions.append({'tool':skill, 'dataset_id':params.get('dataset_id'),
                          'metadata_id':params.get('metadata_id'), 'reason':reason})

    def grouped(params):
        return {**params, 'metadata_id':metadata_id, 'group_by':group,
                'group_values':bindings.get('group_values', [])}

    needs_group = difference or grouping or bool(selected & {'alpha_comparison', 'community_comparison', 'group_feature_summary'})
    if needs_group and not metadata_id:
        questions.append('组间比较缺少对应的样本信息表，请提供样本编号和真实组别。')
        pending.append({'request':'group_comparison', 'code':'metadata_missing'})
    elif needs_group and not group:
        candidates = [c for c in meta[0].get('schema', {}).get('columns', []) if re.search(r'分组|组别|group', c, re.I)] if meta else []
        questions.append('请明确使用样本信息中的哪一列进行分组。'+('候选列：'+'、'.join(candidates)+'。' if candidates else ''))
        pending.append({'request':'group_comparison', 'code':'group_column_missing', 'candidates':candidates})
    bound_group = bool(metadata_id and group)

    if selected & {'composition', 'group_feature_summary'} or '组成' in text:
        skill = 'group_feature_summary' if bound_group else 'composition'
        add(skill, grouped(parameters) if bound_group else dict(parameters), '按已选分类层级描述菌群组成。')
    if difference and bound_group:
        add('differential', grouped(parameters), '使用物种丰度表与已绑定样本分组进行组间差异检验。')
    alpha = diversity and (not re.search(r'Beta|β', text, re.I) or bool(re.search(r'Alpha|α|Shannon|Simpson|Pielou', text, re.I)))
    beta = bool(re.search(r'Beta|β', text, re.I) or selected & {'beta_diversity', 'community_comparison'})
    if diversity and not re.search(r'Alpha|α|Beta|β|Shannon|Simpson|Pielou', text, re.I) and not selected & {'alpha_diversity', 'alpha_comparison'}:
        beta = True
        defaults.append('未细分多样性类型时，基础方案包含 Alpha 指标和 Bray–Curtis 样本关系。')
    if alpha:
        add('alpha_diversity', dict(parameters), '计算各样本的 Alpha 多样性指标。')
        if bound_group and (grouping or 'alpha_comparison' in selected) and not re.search(r'不.*(?:比较|检验)|仅计算|只计算', text):
            add('alpha_comparison', grouped(parameters), '按已有组别比较 Alpha 多样性。')
    if beta:
        add('community_comparison' if bound_group else 'beta_diversity', grouped(parameters) if bound_group else dict(parameters),
            '以 Bray–Curtis 距离描述样本关系'+('并进行组间群落比较。' if bound_group else '。'))

    wants_functional = bool(re.search(r'通路|功能', text) or selected & {'functional_differential', 'pathway_contribution'})
    contribution = bool('pathway_contribution' in selected or re.search(r'通路贡献|物种贡献', text))
    if re.search(r'(?:不做|不分析|不要|暂不|先不).{0,8}(?:通路|功能)', text) and not selected & {'functional_differential','pathway_contribution'}:
        wants_functional = False
    if wants_functional and difference and not contribution:
        totals = next((d for d in functional if d.get('engine_info', {}).get('summary', {}).get('total_pathways')), None)
        if totals and bound_group:
            add('functional_differential', grouped({'dataset_id':totals['id']}), '对真实群落总通路丰度进行组间差异检验。')
        elif not totals:
            questions.append('通路组间差异分析缺少群落总通路丰度表。当前物种分层贡献不能替代总丰度，请补充未分层的通路总丰度表。')
            pending.append({'request':'functional_differential', 'code':'community_pathways_missing'})
    if contribution:
        stratified = next((d for d in functional if d.get('engine_info', {}).get('summary', {}).get('stratified_rows')), None)
        if stratified:
            add('pathway_contribution', {'dataset_id':stratified['id']}, '展示实际物种分层贡献，不解释为统计关联。')
        else:
            questions.append('通路贡献分析缺少物种分层通路表，请提供对应数据。')
            pending.append({'request':'pathway_contribution', 'code':'stratified_pathways_missing'})
    if re.search(r'sankey|桑基', text, re.I) or selected & {'sankey', 'bioflow_sankey'}:
        if sankey_pending(datasets, objective):
            questions.append(SANKEY_QUESTION)
            pending.append({'request':'sankey', 'code':'relationship_definition_missing'})
        else:
            # Edge-table field selection is a distinct supported routing problem.
            return None
    if 'metadata_overview' in selected and metadata_id:
        add('metadata_overview', {'metadata_id':metadata_id}, '检查样本表的字段分布与缺失值。')
    if not steps:
        add('data_check', {}, '核对已读取的数据，等待必要的研究信息。')
    defaults.append('保留工具的基础默认参数，不额外增加协变量调整、偏相关或新的分组派生。')
    return {'id':'microbiome_grouped_basic.v1', 'title':'分组差异与多样性分析' if difference and diversity else '组间差异分析' if difference else '多样性分析',
            'decisions':decisions, 'pending_requests':pending, 'defaults':defaults,
            'plan':{'objective':objective, 'summary':'根据实际文件类型和已明确的研究目标编排基础分析。'+('使用已有分组列「'+group+'」。' if group else '')+
                    ('未具备输入的请求保留为待补充项，不以其他分析替代。' if pending else ''),
                    'steps':steps, 'questions':questions, 'clarifications':[], 'research_design':None}}
