"""One isolated numeric redraw. Executed as a subprocess, never generated code."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

from .figure_contracts import FigureRequest


def main(directory):
    directory = Path(directory).resolve(strict=True)
    spec = json.loads((directory/'request.json').read_text(encoding='utf-8'))
    request = FigureRequest.model_validate({k:v for k,v in spec.items() if k in FigureRequest.model_fields})
    source = directory/'source.tsv'
    if hashlib.sha256(source.read_bytes()).hexdigest() != spec['snapshot_sha256']:
        raise ValueError('绘图输入快照校验失败')
    visualization = Path(__file__).resolve().parents[3]/'workflow/bin/visualization'
    sys.path.insert(0,str(visualization))
    import plot_utils
    from figure_methods import build_figure_plan
    from taxonomy_plots import diversity, heatmap, pcoa
    from figure_panels import taxonomy_panels
    import pandas as pd
    import numpy as np
    import matplotlib.pyplot as plt
    outdir = directory/'rendered'
    outdir.mkdir(exist_ok=False)
    plot_utils.RENDER_OPTIONS.update(request.parameters.model_dump())
    if request.groups:
        with (directory/'groups.tsv').open('w',encoding='utf-8',newline='') as handle:
            writer = csv.writer(handle,delimiter='\t',lineterminator='\n')
            writer.writerow(['sample_id','group']); writer.writerows(request.groups.items())
    params = request.parameters
    groups = pd.Series(request.groups).reindex(request.sample_ids) if request.groups else None
    analysis = 'taxonomy' if request.data_type == 'taxonomy' else 'functional'
    profile = {**spec['profile'],'group_counts':{str(key):int(value) for key,value in pd.Series(request.groups).value_counts().items()} if request.groups else {}}
    plan = build_figure_plan(analysis,profile,ai_advice=params.ai_advice)
    provenance = {'analysis':analysis+'_plots','analysis_version':'4.0','job_id':spec['job_id'],
        'sample_ids':request.sample_ids,'sample_groups':request.groups,'parameters':params.model_dump(),
        'source_artifacts':spec['source_sha256'],'snapshot_sha256':spec['snapshot_sha256'],
        'figure_plan':plan,'statistical_tests':'not_performed','status':'completed','plots':{}}
    renderer_files=[Path(__file__),*[visualization/name for name in
        ('plot_utils.py','taxonomy_plots.py','functional_plots.py','figure_panels.py','figure_methods.py')]]
    provenance['renderer_sha256']={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in renderer_files}

    @plot_utils.styled_plots
    def draw():
        if analysis == 'taxonomy':
            from taxonomy_plots import REQUIRED
            frame = plot_utils.read_standard_table(source,REQUIRED,['sample_id','taxonomy_system','taxid'])
            if set(frame.abundance_unit) != {'fraction_of_reads'} or set(frame.taxonomy_rank) != {'species'}:
                raise plot_utils.TableError('物种图需要 species 层级和 fraction_of_reads 单位')
            frame['feature_key'] = frame.taxonomy_system+':'+frame.taxid
            if frame.groupby('feature_key').taxonomy.nunique().max() > 1:
                raise plot_utils.TableError('同一物种 ID 对应冲突名称')
            full = frame.pivot(index='sample_id',columns='feature_key',values='abundance').reindex(request.sample_ids).fillna(0)
            names = frame.drop_duplicates('feature_key').set_index('feature_key').taxonomy
            means = full.mean()
            selected = means[means>=params.low_abundance_threshold].sort_values(ascending=False,kind='stable').head(params.top_n).index
            display = full.loc[:,selected].copy()
            display.columns = [f'{names[key]} [{key}]' for key in display.columns]
            omitted = full.loc[:,~full.columns.isin(selected)].sum(axis=1)
            if (omitted>0).any():
                display['Other (display only)'] = omitted
            if display.shape[1] == 0:
                display['No classified species'] = 0.
            target = outdir/('taxonomy_'+request.chart+'.png')
            if request.chart == 'alpha_diversity':
                provenance['diversity'] = diversity(full,target,groups,params.alpha_style)
            elif request.chart == 'pcoa':
                provenance['ordination'] = pcoa(full.loc[full.sum(axis=1)>0],target,groups)
            elif request.chart == 'heatmap':
                heatmap(display,'Species composition · display matrix',target)
            elif request.chart == 'stacked':
                figure,axis=plt.subplots(figsize=(max(8,len(display)*.7+4),5.5),layout='constrained')
                display.plot(kind='bar',stacked=True,ax=axis,colormap='tab20',edgecolor='white',width=.65)
                axis.set(xlabel='Sample',ylabel='Bracken-reported relative abundance (fraction)',title=f'Species composition · Top-{params.top_n} plus Other')
                axis.legend(loc='upper left',bbox_to_anchor=(1.01,1),fontsize=7)
                plot_utils.save_figure(figure,target)
            else:
                chosen = {**plan,'selected_recipes':[request.chart],'skipped_recipes':{}}
                result = taxonomy_panels(full,names,outdir,chosen,params.top_n,groups)[request.chart]
                target = outdir/result['output']
            if not target.with_suffix('.data.tsv').exists():
                display.rename_axis('sample_id').reset_index().to_csv(target.with_suffix('.data.tsv'),sep='\t',index=False,na_rep='NA')
            provenance.update(input=str(source),abundance_unit='fraction_of_reads',
                abundance_denominator='source Bracken fraction; diversity uses the full species matrix normalized within positive samples',
                database_profile=frame.database_profile.iloc[0],database_release=frame.database_release.iloc[0],
                missingness_policy='absent species rows are zero detections; zero-total samples are not quantifiable')
        else:
            from functional_plots import REQUIRED, render
            frame=plot_utils.read_standard_table(source,REQUIRED,['sample_id','function_id'])
            context = {'gene_families':('gene_family','UniRef90'),'ko':('ko','KO'),'ec':('ec','EC'),
                'pathway_abundance':('pathway_abundance','MetaCyc'),'pathway_coverage':('pathway_coverage','MetaCyc')}[request.data_type]
            if set(frame.function_type)!={context[0]} or set(frame.function_namespace)!={context[1]}:
                raise plot_utils.TableError('功能类型或命名空间与所选结果不一致')
            results=render(frame,request.data_type,params.top_n,outdir,extended=True,
                display_scale=params.display_scale,groups=groups,charts={request.chart})
            key='clustered_heatmap' if request.chart=='functional_clustered_heatmap' else request.chart
            result=results[key]
            if result.get('status')!='generated':
                raise plot_utils.TableError(result.get('reason','该图型不适用于当前结果'))
            target=outdir/result['output']
            provenance.update(inputs={request.data_type:str(source)},display_scale=params.display_scale,
                units=results.get('abundance_unit'),database_context=results,
                attribution_boundary='Community-total HUMAnN functional potential; not expression, activity or species/MAG attribution.')
        provenance['plots']={request.chart:{'status':'generated','output':target.name}}
        plot_utils.write_provenance(outdir/(analysis+'_plots.provenance.json'),provenance)
        files = sorted(path.name for path in outdir.iterdir() if path.name.startswith(target.stem+'.'))
        if request.chart=='pcoa':
            files.extend(['taxonomy_bray_curtis.distances.tsv','taxonomy_bray_curtis.eigenvalues.tsv'])
        files.append(analysis+'_plots.provenance.json')
        manifest={'files':files,'primary':target.name,'figure_plan':plan,'parameters':params.model_dump()}
        (directory/'result.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    draw()


if __name__=='__main__':
    main(sys.argv[1])
