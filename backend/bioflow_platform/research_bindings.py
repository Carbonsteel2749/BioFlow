"""Bind explicit research intent to selected, immutable data before planning."""
import copy
import re

SANKEY_QUESTION = '桑基图要展示哪些层级之间的关系，连线宽度表示什么？当前所选文件没有现成的关系边表；请说明希望展示物种对通路的贡献，还是统计分析得到的关联。'


def sankey_pending(datasets, objective):
    if not re.search(r'sankey|桑基', objective, re.I):
        return False
    for row in datasets:
        role = row.get('role', row.get('engine_info', {}).get('data_type'))
        if role in {'table', 'results'}:
            return False
        columns = row.get('schema', {}).get('columns', [])
        if role == 'metadata' and sum(str(c) in objective for c in columns) >= 3:
            return False
    return True


def internal_issue(message):
    return bool(re.search(r'请为\w+绑定上游结果表', str(message))) or any(term in str(message) for term in (
        '派生列必须有新名称', '依赖必须引用前面的步骤', '步骤 ID 重复',
        '综合步骤没有读取', '准备队列必须绑定', '表格分析必须使用该准备队列',
    ))


def resolve_context(datasets, objective, study=None, frames=None, known=None, latest=None):
    text = objective.split('当前方案，供本次修改参考：')[0]
    study = dict(study or {})
    known=known or {};latest=latest or text
    candidates = []
    metadata = [d for d in datasets if d.get('role', d.get('engine_info', {}).get('data_type')) == 'metadata']
    for row in metadata:
        columns = row.get('schema', {}).get('columns', row.get('engine_info', {}).get('columns', []))
        for column in columns:
            if column in text or re.search(r'分组|组别|group', column, re.I):
                candidates.append((row, column, column in text))
    recent=[c for c in candidates if c[1] in latest]
    explicit = recent if len(recent)==1 else [c for c in candidates if c[2]]
    if not explicit and study.get('group_column'):
        explicit=[c for c in candidates if c[1]==study['group_column']]
    grouping = bool(re.search(r'分组|高低|组别|group', text, re.I))
    chosen = explicit if len(explicit) == 1 else candidates if grouping and len(candidates) == 1 else []
    result = {'study': study, 'metadata_id': metadata[0]['id'] if len(metadata) == 1 else None,
              'group_column': None, 'group_values': [], 'repairs': [], 'defaults': [],
              'reuse_existing_group': False}
    if len(chosen) == 1:
        row, column, named = chosen[0]
        result.update(metadata_id=row['id'], group_column=column)
        study['group_column'] = column
        derive = bool(re.search(r'分位|阈值|重新分组|重新划分|派生|重编码|quartile|quantile|threshold', latest, re.I))
        result['reuse_existing_group'] = (grouping or bool(study.get('group_column'))) and not derive
        frame = frames.get(row['id']) if frames is not None else None
        if frame is None and row.get('directory'):
            import pandas as pd
            from pathlib import Path
            frame = pd.read_json(Path(row['directory']) / 'matrix.json.gz', orient='split', dtype=False, convert_axes=False)
        if frame is not None and column in frame:
            values = [str(v).strip() for v in frame[column].dropna().unique() if str(v).strip()]
            explicit_values=sorted([v for v in values if v in latest],key=lambda v:latest.index(v))
            if len(explicit_values)>=2:
                result['group_values']=explicit_values
            elif not re.search(r'分组|组别|比较|高低|group',latest,re.I) and known.get('group_column')==column and set(known.get('group_values',[]))<=set(values):
                result['group_values']=known.get('group_values',[])
            if not result['group_values'] and re.search(r'高低|高.{0,6}低|high.{0,12}low', latest if re.search(r'分组|组别|比较|高低|group',latest,re.I) else text, re.I):
                low = [v for v in values if re.search(r'低|low', v, re.I)]
                high = [v for v in values if re.search(r'高|high', v, re.I)]
                if len(low) == len(high) == 1 and low[0] != high[0]:
                    result['group_values'] = [low[0], high[0]]
        result['repairs'].append('已绑定样本表中的现有分组列「' + column + '」。')
    # Unique identifiers do not prove independence. Only explicit user assertions do.
    for sentence in re.split(r'[。！？?!\n]', text):
        if '独立' in sentence and re.search(r'不确定|不清楚|不知道|没(?:有)?确认|可能是|应该是|也许|大概', sentence):
            study['independent'] = False
            continue
        if re.search(r'是否|吗|能否|请确认', sentence):
            continue
        if re.search(r'不是独立样本|非独立样本|不独立|(?:不|未|尚未|不能|无法)确认.{0,8}独立', sentence):
            study['independent'] = False
        elif re.search(r'(?:确认|均为|都是|是)独立样本|独立样本(?:已确认|确认)|样本相互独立|每(?:人|个研究对象)仅?(?:采样|记录)?一次', sentence):
            study['independent'] = True
    pending = known.get('pending_questions', [])
    if len(pending) == 1 and '独立' in str(pending[0]):
        answer = latest.strip(' 。！!')
        if answer in {'是', '是的', '确认', '已确认'}:
            study['independent'] = True
        elif answer in {'否', '不是', '不确认'}:
            study['independent'] = False
    return result


def optional_question(question, objective):
    if not re.search(r'是否需要|要不要|是否要', question):
        return False
    if re.search(r'更详细|更多|进一步|扩展', question) and '多样性' in question:
        return True
    return bool('协变量' in question and not re.search(r'协变量|校正|混杂|控制', objective.split('当前方案，供本次修改参考：')[0]))


def repair_plan(body, bindings, tools, datasets, objective):
    """Repair identifiers and redundant recipes, never statistical data or evidence."""
    body = copy.deepcopy(body)
    metadata_id = bindings.get('metadata_id')
    column = bindings.get('group_column')
    requirements = {t['id']: t.get('requires') for t in tools}
    taxonomy = [d['id'] for d in datasets if d.get('role', d.get('engine_info', {}).get('data_type')) == 'taxonomy']
    repairs = []
    removed = set()
    if sankey_pending(datasets, objective):
        body['questions'] = list(dict.fromkeys((body.get('questions') or []) + [SANKEY_QUESTION]))
    for step in body.get('steps', []):
        params = step.setdefault('parameters', {})
        requirement = requirements.get(step.get('skill'))
        if metadata_id and (requirement in {'metadata', 'groups', 'taxa_numeric', 'functional_groups', 'functional_numeric'} or step.get('skill') == 'prepare_cohort'):
            if not params.get('metadata_id'):
                params['metadata_id'] = metadata_id
        if len(taxonomy) == 1 and requirement in {'taxonomy', 'groups', 'taxa_numeric'} and not params.get('dataset_id'):
            params['dataset_id'] = taxonomy[0]
        operations = params.get('preparation') or []
        kept = []
        for operation in operations:
            if metadata_id and not operation.get('dataset_id'):
                operation['dataset_id'] = metadata_id
            if bindings.get('reuse_existing_group') and operation.get('column') == column and operation.get('op') in {'quantile_group', 'threshold_group', 'recode'}:
                repairs.append('复用已有分组列，移除对「' + column + '」的重复派生。')
                continue
            kept.append(operation)
        params['preparation'] = kept
        if column and requirement in {'groups', 'functional_groups', 'taxa_numeric', 'functional_numeric'}:
            params['group_by'] = column
            if bindings.get('group_values'):
                params['group_values'] = bindings['group_values']
        if step.get('skill') == 'prepare_cohort' and not kept and (operations or bindings.get('reuse_existing_group')):
            removed.add(step['id'])
        if step.get('skill') == 'sankey' and not step.get('inputs') and not all(params.get(k) for k in ('dataset_id', 'source', 'target', 'weight')):
            # Choosing a relationship and its weight changes the scientific question.
            # A plotting tool cannot invent the calculation that supplies its edges.
            if not any(d.get('role') in {'table', 'results'} for d in datasets):
                body.setdefault('questions', [])
                if SANKEY_QUESTION not in body['questions']:
                    body['questions'].append(SANKEY_QUESTION)
    body['steps'] = [s for s in body.get('steps', []) if s.get('id') not in removed]
    preparations = {s['id'] for s in body['steps'] if s.get('skill') == 'prepare_cohort'}
    seen = set()
    for step in body['steps']:
        step['depends_on'] = [p for p in step.get('depends_on', []) if p not in removed]
        step['inputs'] = {key:value for key,value in step.get('inputs', {}).items()
                          if value.get('step_id') not in removed}
        if step.get('cohort_from') in removed:
            step['cohort_from'] = None
        program = step.get('program')
        if step.get('skill') == 'custom_analysis' and isinstance(program, dict) and not seen:
            source_ids = {d['id'] for d in datasets}
            declared = program.get('inputs') or []
            if len(datasets)==1 and len(set(declared))<=1:
                program['inputs'] = [datasets[0]['id']]
            if program.get('inputs') and set(program['inputs'])<=source_ids and not step['depends_on']:
                if step['inputs']:
                    step['inputs'] = {}
                    repairs.append('首个程序步骤已绑定原始数据，移除将原始文件误写为上游结果的引用。')
        for key, value in list(step['inputs'].items()):
            parent = value.get('step_id')
            if (parent in preparations and parent in seen and value.get('output') == 'prepared_metadata'
                    and step.get('cohort_from') in {None, parent}):
                step['cohort_from'] = parent
                del step['inputs'][key]
                repairs.append('已将准备队列引用绑定到统一队列入口。')
        consumed = [value.get('step_id') for value in step.get('inputs', {}).values()]
        if step.get('cohort_from'):
            consumed.append(step['cohort_from'])
        for parent in consumed:
            if parent in seen and parent not in step['depends_on']:
                step['depends_on'].append(parent)
                repairs.append('已按明确的结果引用补齐步骤依赖。')
        seen.add(step['id'])
    design = body.get('research_design')
    if design and removed:
        for aim in design.get('aims', []):
            aim['step_ids'] = [sid for sid in aim.get('step_ids', []) if sid not in removed]
        design['aims'] = [a for a in design.get('aims', []) if a.get('step_ids')]
        if not design['aims']:
            body['research_design'] = None
        elif design.get('synthesis_step') in removed:
            design['synthesis_step'] = None
    questions = body.get('questions') or []
    optional = [q for q in questions if optional_question(str(q), objective)]
    body['questions'] = [q for q in questions if q not in optional]
    cards = body.get('clarifications') or []
    body['clarifications'] = [c for c in cards if not optional_question(str(c.get('prompt', c.get('question', ''))), objective)]
    if optional or len(body['clarifications']) != len(cards):
        assumption = '先采用本方案列出的基础分析，不额外加入未要求的协变量调整或扩展检验。'
        body['summary'] = (str(body.get('summary', '')) + '\n' + assumption)[:3000]
        repairs.append(assumption)
    return body, list(dict.fromkeys(repairs))
