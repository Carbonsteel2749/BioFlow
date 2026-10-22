#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from plot_utils import TableError, abundance_matrix, read_standard_table, save_figure, skip, styled_plots, write_provenance
from figure_methods import build_figure_plan
from figure_panels import functional_panels
from taxonomy_plots import sample_groups

REQUIRED={"sample_id","function_source","function_namespace","function_id","function_name","function_type","abundance","abundance_unit","relative_abundance","normalization_method","evidence_level","analysis_scope","database_profile","database_release"}

def quantification_context(frame, label):
    context = {}
    for column in ('abundance_unit','normalization_method','database_profile','database_release'):
        values = sorted(frame[column].astype(str).str.strip().unique())
        if len(values) != 1 or not values[0] or values[0].lower() in {'nan','none'}:
            raise TableError(f'{label} incompatible or missing {column}; do not combine differently quantified/database-versioned results')
        context[column] = values
    return context

def render(frame, label, top_n, outdir, *, extended=True, display_scale='relative', groups=None, advice=None, charts=None):
    if frame.empty: return {"heatmap":skip("input table has no community-total rows"),"composition":skip("input table has no community-total rows")}
    if set(frame.function_source)!={"HUMAnN"} or set(frame.analysis_scope)!={"community_total"}: raise TableError(f"{label} must be HUMAnN community_total evidence")
    if frame.function_id.str.contains(r"\|",regex=True).any(): raise TableError(f"{label} contains stratified feature IDs; species/MAG attribution is prohibited")
    context = quantification_context(frame, label)
    samples=sorted(frame.sample_id.unique())
    special_mask=frame.function_id.str.match(r'^(UNMAPPED|READS_UNMAPPED|UNINTEGRATED|UNGROUPED|UniRef\d+_unknown)(:|$)')
    special=frame[special_mask]
    special_entries={sample: dict(zip(group.function_id,group.abundance)) for sample,group in special.groupby('sample_id')}
    frame=frame[~special_mask & (frame.abundance > 0)].copy()
    no_signal=sorted(set(samples)-set(frame.sample_id))
    evidence={**context,'sample_ids':samples,'samples_without_named_signal':no_signal,'special_entries':special_entries,'special_entry_note':'Diagnostic categories are not named functions; values are reported scores, not read percentages. Zero named-signal samples have undefined composition (N/A).'}
    evidence['sample_groups'] = groups.to_dict() if groups is not None else {}
    if frame.empty:
        return {**evidence,'heatmap':skip('no positive named functions; diagnostic categories excluded'),'composition':skip('no positive named functions; diagnostic categories excluded'),'selected_feature_ids':[]}
    coverage=label == 'pathway_coverage'
    if coverage and (frame.abundance > 1).any(): raise TableError('pathway coverage must lie within [0, 1]')
    # Missing features contribute zero to the cohort mean used for ranking.
    full=frame.pivot(index='sample_id',columns='function_id',values='abundance').reindex(samples).fillna(0)
    plan = build_figure_plan('functional', {'sample_count':int((full.sum(axis=1)>0).sum()),
        'feature_count':int((full > 0).any(axis=0).sum()), 'group_counts':{}})
    if advice and advice['source'] == 'ollama':
        for recipe in plan['selected_recipes']:
            if recipe not in advice['selected_recipes']:
                plan['skipped_recipes'][recipe] = 'Not selected by validated local model advice'
        plan['selected_recipes'] = [recipe for recipe in plan['selected_recipes'] if recipe in advice['selected_recipes']]
        plan['source'] = 'ollama'
        plan['ai_status'] = advice['ai_status']
    if charts is not None:
        plan['selected_recipes']=[recipe for recipe in plan['selected_recipes'] if recipe in charts]
    extras = functional_panels(frame,label,top_n,outdir,plan,samples,groups) if extended else {}
    if charts is not None and not {'heatmap','composition'}.intersection(charts):
        return {**evidence,**extras,'figure_plan':plan}
    selected=full.mean().nlargest(top_n).index
    display=frame[frame.function_id.isin(selected)].copy() if coverage else frame.copy(); display["display_feature"]=np.where(display.function_id.isin(selected),display.function_id,"Other annotated (outside Top-N)")
    matrix=display.pivot_table(index="sample_id",columns="display_feature",values="abundance",aggfunc="sum",fill_value=0.0).reindex(samples).fillna(0)
    relative = matrix.div(matrix.sum(axis=1).replace(0,np.nan),axis=0)
    heat_values = matrix if coverage else (relative if display_scale == 'relative' else np.log1p(matrix))
    if not coverage:
        heat_values.loc[no_signal] = np.nan
    heat=outdir/f"functional_{label}_top{top_n}_heatmap.png"
    figure,axis=plt.subplots(figsize=(max(6,matrix.shape[1]*.55),max(5,matrix.shape[0]*.45+3)),layout='constrained')
    image=axis.imshow(heat_values,aspect="auto",cmap=plt.get_cmap('magma').with_extremes(bad='#cbd5e1'),**({'vmin':0,'vmax':1} if coverage or display_scale == 'relative' else {}))
    axis.set(xticks=range(matrix.shape[1]),xticklabels=matrix.columns,yticks=range(matrix.shape[0]),yticklabels=matrix.index,title=f"{label}: Top-{top_n} " + ('coverage' if coverage else 'named-feature composition' if display_scale == 'relative' else 'reported scores (log1p)'))
    axis.tick_params(axis="x",labelrotation=65,labelsize=8)
    figure.colorbar(image,ax=axis,label="HUMAnN pathway\ncoverage (0-1)" if coverage else "Within-named-functions\nfraction" if display_scale == 'relative' else "log1p(HUMAnN\nreported abundance)")
    save_figure(figure,heat)
    heat_values.rename_axis('sample_id').reset_index().to_csv(heat.with_suffix('.data.tsv'),sep='\t',index=False,na_rep='NA')
    heat.with_suffix('.caption.md').write_text('HUMAnN reads-layer community-total functional potential, not expression or activity. '+
        ('Coverage is a 0–1 score, not additive abundance.' if coverage else 'Relative display is normalized by the sum of all named features, including Other. Raw reported scores, when selected, are log1p-transformed and are not standardized cross-sample absolute measurements.')+
        ' Diagnostic categories are excluded from named-feature composition. N/A is not a measured zero. No hypothesis tests were performed.\n',encoding='utf-8')
    if coverage:
        return {**evidence,**extras,'figure_plan':plan,'heatmap':{'status':'generated','output':heat.name},'composition':skip('Coverage is not additive abundance; no composition plot'), 'selected_feature_ids':list(selected),'display_transform':'identity (0-1 coverage)'}
    if charts is not None and 'composition' not in charts:
        return {**evidence,**extras,'figure_plan':plan,'heatmap':{'status':'generated','output':heat.name},'selected_feature_ids':list(selected),'heatmap_scale':display_scale}
    composition=matrix.div(matrix.sum(axis=1).replace(0,np.nan),axis=0).fillna(0); comp=outdir/f"functional_{label}_top{top_n}_composition.png"; figure,axis=plt.subplots(figsize=(max(7,len(matrix)*.85),5)); composition.plot(kind="bar",stacked=True,ax=axis,colormap="tab20"); axis.set(ylabel="Composition within displayed Top-N + Other",xlabel="Sample (N/A: no named signal)",title=f"{label}: Top-{top_n} composition"); axis.set_xticklabels([f'{sample} (N/A)' if sample in no_signal else sample for sample in matrix.index]); axis.legend(loc="upper left",bbox_to_anchor=(1,1),fontsize=7); save_figure(figure,comp)
    return {**evidence,**extras,'figure_plan':plan,"heatmap":{"status":"generated","output":heat.name},"composition":{"status":"generated","output":comp.name},"selected_feature_ids":list(selected),'composition_denominator':'sum of named annotated features only; diagnostic categories excluded','heatmap_scale':display_scale}

@styled_plots
def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--gene-families",type=Path,required=True); parser.add_argument("--ko",type=Path,required=True); parser.add_argument("--ec",type=Path,required=True); parser.add_argument("--pathway-abundance",type=Path,required=True); parser.add_argument("--pathway-coverage",type=Path); parser.add_argument("--outdir",type=Path,required=True); parser.add_argument("--top-n",type=int,default=20)
    parser.add_argument('--display-scale',choices=['relative','reported'],default='relative')
    parser.add_argument('--extended-plots',choices=['true','false'],default='true')
    parser.add_argument('--sample-metadata',type=Path)
    parser.add_argument('--metadata-sha256')
    parser.add_argument('--group-column',default='group')
    parser.add_argument('--ai-advice',action='store_true')
    args=parser.parse_args(); args.outdir.mkdir(parents=True,exist_ok=True)
    inputs={"gene_families":(args.gene_families,"gene_family","UniRef90"),"ko":(args.ko,"ko","KO"),"ec":(args.ec,"ec","EC"),"pathway_abundance":(args.pathway_abundance,"pathway_abundance","MetaCyc")}
    if args.pathway_coverage: inputs["pathway_coverage"]=(args.pathway_coverage,"pathway_coverage","MetaCyc")
    provenance={"analysis":"functional_plots","analysis_version":"2.0","inputs":{label:str(path) for label,(path,_,_) in inputs.items()},"sample_scope":"all rows in each supplied normalized table","top_n":args.top_n,"top_n_rule":"select positive named features by mean community-total HUMAnN abundance; exclude diagnostic categories; coverage is never summed or renormalized","attribution_boundary":"All plots describe community_total reads-layer HUMAnN estimates of functional potential, not expression or activity. They do not attribute functions to a species or MAG.","missingness_policy":"Within the same validated table, an absent feature row is treated as zero reported abundance; invalid quantitative cells fail validation. Samples without positive named signal have undefined composition (N/A). No missing sample or empty table is imputed as a measured zero.","units":{},"database_context":{},"plots":{}}
    provenance['display_scale'] = args.display_scale
    try:
        if not 1 <= args.top_n <= 100:
            raise TableError('top-n must be 1..100')
        import hashlib
        if args.metadata_sha256 and not args.sample_metadata:
            raise TableError('metadata SHA-256 requires a sample metadata file')
        if args.sample_metadata:
            if not args.sample_metadata.is_file():
                raise TableError('sample metadata file does not exist')
            digest=hashlib.sha256(args.sample_metadata.read_bytes()).hexdigest()
            if args.metadata_sha256 and digest != args.metadata_sha256.lower():
                raise TableError('sample metadata SHA-256 does not match the immutable snapshot')
            provenance['metadata_sha256']=digest
        all_samples=set()
        frames={}
        feature_count=0
        for label,(path,kind,namespace) in inputs.items():
            frame=read_standard_table(path,REQUIRED,["sample_id","function_id"])
            if not frame.empty and (set(frame.function_type)!={kind} or set(frame.function_namespace)!={namespace}): raise TableError(f"{label} function_type or function_namespace mismatch")
            if not frame.empty:
                context = quantification_context(frame, label)
                provenance['units'][label] = {key:context[key] for key in ('abundance_unit','normalization_method')}
                provenance['database_context'][label] = {key:context[key] for key in ('database_profile','database_release')}
            frames[label]=frame
            named=frame[~frame.function_id.str.match(r'^(UNMAPPED|READS_UNMAPPED|UNINTEGRATED|UNGROUPED|UniRef\d+_unknown)(:|$)') & (frame.abundance > 0)]
            all_samples.update(named.sample_id.unique())
            feature_count=max(feature_count,named.function_id.nunique())
        plan=build_figure_plan('functional',{'sample_count':len(all_samples),'feature_count':feature_count,'group_counts':{}},ai_advice=args.ai_advice and args.extended_plots=='true')
        provenance['figure_plan']=plan
        for label,frame in frames.items():
            groups=sample_groups(args.sample_metadata,sorted(frame.sample_id.unique()),args.group_column)
            provenance['plots'][label]=render(frame,label,args.top_n,args.outdir,extended=args.extended_plots=='true',display_scale=args.display_scale,groups=groups,advice=plan)
    except TableError as exc:
        provenance["status"]="failed"; provenance["reason"]=str(exc); write_provenance(args.outdir/"functional_plots.provenance.json",provenance); return 65
    provenance["status"]="completed"; write_provenance(args.outdir/"functional_plots.provenance.json",provenance); return 0
if __name__=="__main__": raise SystemExit(main())
