"""One isolated computation; BioFlow owns datasets, plans and run lifecycle."""
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

ENGINE=Path(__file__).with_name('research_engine')
sys.path.insert(0,str(ENGINE/'src'))

def configure():
    from yanji.skills import registry
    from yanji.contracts import SkillParams
    registry.SPECS=[s for s in registry.SPECS if s.id not in {'custom_analysis','report'}]
    registry.SPECS.append(registry.Spec('existing_effect_plot','已有结果效应图',
        '读取已上传结果中的效应量及可选置信区间绘图，不重新计算检验。','research',outputs=('plot_data',)))
    from .charts.registry import CHARTS,VERSION
    for chart in CHARTS:
        registry.SPECS.append(registry.Spec('bioflow_'+chart['id'],'BioFlow '+chart['name'],chart['description'],
            'research',version=VERSION,outputs=('plot_data',)))
    registry.CATALOG={s.id:s for s in registry.SPECS}
    return registry,SkillParams

def seed(spec):
    from yanji import db
    from yanji.config import runtime_root
    db.initialize();pid=spec['project_id'];runtime=runtime_root()
    with db.session() as con:
        if not con.get(db.Record,pid):con.add(db.Record(id=pid,kind='project',data={'name':'BioFlow研究任务','material_ids':[],'active_datasets':{},'study':spec.get('study',{})}))
        for item in spec['datasets']:
            identifier=item['id'];source=Path(item['directory']);target=runtime/'projects'/pid/'datasets'/identifier
            target.mkdir(parents=True,exist_ok=True)
            for name,sha in item['hashes'].items():
                path=source/name
                if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('输入数据版本校验失败')
                destination=target/name
                if not destination.exists():
                    try:os.link(path,destination)
                    except OSError:shutil.copyfile(path,destination)
            info=dict(item['engine_info']);info.update(source_sha256=item['source_sha256'],filename=item['filename'],revision=item['revision'],source_id=item['source_asset_id']);info['storage']=str(target.relative_to(runtime))
            info['content_sha256']=item['hashes']['matrix.json.gz']
            if not con.get(db.Record,identifier):con.add(db.Record(id=identifier,kind='dataset',project_id=pid,data=info))
        project=con.get(db.Record,pid)
        project.data={**project.data,'material_ids':[d['id'] for d in spec['datasets']],
                      'active_datasets':{d['id']:d['id'] for d in spec['datasets']},'study':spec.get('study',{})}
    return {'datasets':{d['id']:d['id'] for d in spec['datasets']},'study':spec.get('study',{})}

def result_plot(ctx):
    import pandas as pd
    import numpy as np
    import matplotlib.pyplot as plt
    from yanji import db,data
    from yanji.skills.analysis import result
    d=db.get(ctx.params.dataset_id,'dataset');frame=data.load_frame(d).copy()
    effect=ctx.params.result_effect
    if effect not in frame:raise ValueError('请在参数 result_effect 中指定真实效应量列名')
    selected=frame.head(ctx.params.top_n);values=pd.to_numeric(selected[effect],errors='raise').to_numpy(float)
    if not np.isfinite(values).all():raise ValueError('效应量必须是有限数值')
    label=ctx.params.result_predictor
    labels=selected[label].astype(str).tolist() if label in selected else selected.index.astype(str).tolist()
    lo,hi=ctx.params.result_ci_low,ctx.params.result_ci_high
    fig,ax=plt.subplots(figsize=(9,max(4,len(selected)*.32)))
    if lo in selected and hi in selected:
        lower=pd.to_numeric(selected[lo],errors='raise').to_numpy(float);upper=pd.to_numeric(selected[hi],errors='raise').to_numpy(float)
        if not np.isfinite(lower).all() or not np.isfinite(upper).all() or (lower>values).any() or (upper<values).any():raise ValueError('置信区间必须包含效应量，且数值有限')
        ax.errorbar(values,range(len(values)),xerr=[values-lower,upper-values],fmt='o',color=ctx.colors[0],capsize=3)
    else:
        ax.scatter(values,range(len(values)),color=ctx.colors[0]);ctx.warnings.append('输入未提供完整置信区间，图中只展示已有的效应量。')
    ax.set_yticks(range(len(values)),labels);ax.set_xlabel(ctx.params.effect_metric or effect);ax.set_title(ctx.params.title or '已有统计结果');ax.invert_yaxis()
    ctx.table(selected,'plot_data','已有结果作图数据');ctx.chart(fig,'existing_result','已有结果效应图')
    return result(ctx,'existing_effect_plot','已根据上传结果绘图，未重新计算统计检验。','读取已提供效应量及可选置信区间。',{'rows':len(selected)})

def native_chart(ctx,step):
    import pandas as pd
    from yanji import db,data
    from yanji.skills.analysis import result
    from .chart_runner import render
    from .chart_contracts import DisplayParameters
    chart=step['skill'].removeprefix('bioflow_');params=step.get('parameters',{})
    if ctx.inputs:frame=next(iter(ctx.inputs.values())).copy()
    else:
        d=db.get(ctx.params.dataset_id,'dataset')
        if d['data_type'] in {'taxonomy','functional'}:frame=ctx.matrix(d['data_type'])
        else:frame=data.load_frame(d).copy()
    if chart in {'pca','heatmap'}:frame.index.name='sample'
    if chart=='stacked' and 'taxon' not in frame.columns:
        frame.index.name='sample';frame=frame.reset_index().melt(id_vars='sample',var_name='taxon',value_name='abundance').set_index('sample')
    ctx.table(frame,'plot_data','BioFlow图形输入')
    target=ctx.directory/'native_chart';target.mkdir(exist_ok=True);source=target/'source.csv';frame.to_csv(source)
    options=DisplayParameters.model_validate(params.get('plot_options',{})).model_dump()
    columns=params.get('plot_columns',{})
    if not isinstance(columns,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in columns.items()):raise ValueError('plot_columns 必须是列名对应关系')
    request={'chart':chart,'source':{'sha256':hashlib.sha256(source.read_bytes()).hexdigest()},'columns':columns,'parameters':options}
    (target/'request.json').write_text(json.dumps(request,ensure_ascii=False));render(target)
    info=json.loads((target/'result.json').read_text())
    for ext in ('png','svg','pdf'):ctx.artifact(target/('figure.'+ext),ext,'BioFlow '+chart)
    return result(ctx,step['skill'],'图形已由 BioFlow 原有渲染器生成。','读取已登记数据或上游结果，通过固定图型程序作图。',{'rows':len(frame),'renderer_sha256':info['renderer_sha256']})

def execute(spec):
    registry,Params=configure()
    from yanji.contracts import AnalysisPlan,ChatRequest,ImportOptions
    from yanji import data
    from yanji.skills.context import Context,clean_json
    from yanji.skills.analysis import execute as run_tool
    from dataclasses import asdict
    custom={'id':'existing_effect_plot','name':'已有结果效应图','version':'1.0.0','description':'读取已上传结果中的效应量及可选置信区间绘图，不重新计算检验。','requires':'research','outputs':['plot_data']}
    if spec['mode']=='catalog':
        from .chart_contracts import DisplayParameters
        return {'tools':[{**asdict(s),'backend':'bioflow-chart' if s.id.startswith('bioflow_') else 'python','contract_version':'bioflow.research.v1',
                **({'plot_options_schema':DisplayParameters.model_json_schema()} if s.id.startswith('bioflow_') else {})} for s in registry.SPECS],
                'parameters_schema':Params.model_json_schema(),'plan_schema':AnalysisPlan.model_json_schema()}
    if spec['mode']=='inspect':
        options=dict(spec['options']);sample_rule=options.pop('sample_id_rule','none');options['kind']='table' if options.pop('role')=='results' else spec['options']['role'];options.pop('description',None)
        frame,info=data.inspect(Path(spec['source']).read_bytes(),spec['filename'],ImportOptions.model_validate(options))
        if sample_rule=='strip_combined':
            if info['data_type']!='functional' or not all(str(c).endswith('_combined') for c in frame.columns):raise ValueError('当前文件不满足统一移除 _combined 后缀的条件')
            mapping={str(c):str(c)[:-9] for c in frame.columns}
            if len(set(mapping.values()))!=len(mapping):raise ValueError('样本编号转换会产生重复')
            frame=frame.rename(columns=mapping);info['samples']=list(frame.columns)
            info['transformations'].append({'operation':'sample_id_mapping','rule':'strip_combined','mapping':mapping})
            info['warnings'].append('已移除样本编号的 _combined 后缀，并记录原始编号映射。')
        directory=Path(spec['destination']);data.write_frame(directory,frame)
        path=directory/'matrix.json.gz';sha=hashlib.sha256(path.read_bytes()).hexdigest()
        preview=frame.head(10).reset_index().astype(str)
        return {'engine_info':info,'hashes':{'matrix.json.gz':sha},'preview':{'columns':list(preview.columns),'rows':preview.values.tolist()},
                'schema':{'index':str(frame.index.name or 'ID'),'columns':[str(c) for c in frame.columns],'rows':len(frame)},'samples':info.get('samples',[])}
    if spec['mode'] in {'recognize','table_preview','edit_data','correct_settings'}:
        from .research_experience import data_operation
        return data_operation(spec)
    if spec['mode']=='report':
        from .research_experience import build_report
        return build_report(spec['run'])
    snapshot=seed(spec);pid=spec['project_id']
    if spec['mode'] in {'plan','validate'}:
        from yanji import agent
        agent.CATALOG=registry.CATALOG
        if spec['mode']=='plan':
            # Unavailable scientific inputs become concrete questions, never fabricated totals.
            try:
                plan=agent.create_plan(pid,ChatRequest(message=spec['objective'],dataset_ids=list(snapshot['datasets']),
                    study=spec.get('study',{}),include_report=False,mode='agent'))
                from .research_experience import model_object,short_title
                try:
                    named=model_object('用中文概括研究任务标题，最多18个字。返回JSON {"title":"..."}，不输出研究指令全文。',{'objective':spec['objective'],'steps':[s['skill'] for s in plan.get('steps',[])]},120)
                    plan['title']=str(named.get('title','')).strip()[:28]
                except Exception:plan['title']=short_title(plan)
                return {'plan':{k:plan.get(k) for k in ['title','objective','summary','steps','questions','clarifications']},'ready':not bool(plan.get('questions'))}
            except (ValueError,agent.ModelError) as exc:
                return {'plan':{'objective':spec['objective'],'summary':'请补充下列信息后重新生成方案。','steps':[],'questions':[str(exc)[:1500]]},'ready':False}
        proposed=json.loads(json.dumps(spec['plan']));extras={}
        from .chart_contracts import DisplayParameters
        for step in proposed['steps']:
            if step['skill'].startswith('bioflow_'):
                extras[step['id']]={k:step.get('parameters',{}).pop(k) for k in ('plot_options','plot_columns') if k in step.get('parameters',{})}
                DisplayParameters.model_validate(extras[step['id']].get('plot_options',{}))
        body=AnalysisPlan.model_validate(proposed)
        for step in body.steps:
            if step.skill=='existing_effect_plot' or step.skill.startswith('bioflow_'):
                if step.skill not in registry.CATALOG:raise ValueError('图型未登记')
                Params.model_validate(step.parameters.model_dump())
                if not step.inputs and step.parameters.dataset_id not in snapshot['datasets']:raise ValueError('绘图必须选择当前输入的数据集或上游结果')
            elif step.skill not in registry.CATALOG:raise ValueError('工具未接入统一执行器：'+step.skill)
        from yanji.result_inputs import validate_bindings
        validate_bindings(body)
        seen=set()
        for step in body.steps:
            if step.id in seen or any(d not in seen for d in step.depends_on):raise ValueError('步骤标识重复或依赖顺序错误')
            seen.add(step.id)
        ordinary=[s for s in body.steps if s.skill!='existing_effect_plot' and not s.skill.startswith('bioflow_')]
        if ordinary:
            check=body.model_copy(update={'steps':ordinary});agent.validate_plan(check,pid)
            blockers=agent.checks(check,pid,study=spec.get('study',{}))
        else:blockers=[]
        output=body.model_dump()
        for step in output['steps']:step['parameters'].update(extras.get(step['id'],{}))
        return {'plan':output,'ready':not bool(blockers),'questions':[str(b) for b in blockers]}
    if spec['mode']=='execute':
        step=spec['step'];parameters={k:v for k,v in step.get('parameters',{}).items() if k not in {'plot_options','plot_columns'}}
        ctx=Context(pid,spec['run_id'],step['id'],snapshot,parameters,spec.get('completed',[]),step.get('inputs',{}))
        if step['skill'].startswith('bioflow_') and step['skill'] in registry.CATALOG:output=native_chart(ctx,step)
        elif step['skill']=='existing_effect_plot':output=result_plot(ctx)
        elif step['skill'] in registry.CATALOG:output=run_tool(ctx,step['skill'])
        else:raise ValueError('工具未接入统一执行器')
        return clean_json(output)
    raise ValueError('未知计算请求')

if __name__=='__main__':
    request,response=map(Path,sys.argv[1:3])
    try:
        output=execute(json.loads(request.read_text()));code=0
    except Exception as exc:output={'error':str(exc)[:2000]};code=1
    response.write_text(json.dumps(output,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    raise SystemExit(code)
