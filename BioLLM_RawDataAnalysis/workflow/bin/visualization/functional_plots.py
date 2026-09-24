#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from plot_utils import TableError, abundance_matrix, read_standard_table, save_figure, skip, write_provenance

REQUIRED={"sample_id","function_source","function_namespace","function_id","function_name","function_type","abundance","abundance_unit","relative_abundance","normalization_method","evidence_level","analysis_scope","database_profile","database_release"}

def render(frame, label, top_n, outdir):
    if frame.empty: return {"heatmap":skip("input table has no community-total rows"),"composition":skip("input table has no community-total rows")}
    if set(frame.function_source)!={"HUMAnN"} or set(frame.analysis_scope)!={"community_total"}: raise TableError(f"{label} must be HUMAnN community_total evidence")
    if frame.function_id.str.contains(r"\|",regex=True).any(): raise TableError(f"{label} contains stratified feature IDs; species/MAG attribution is prohibited")
    samples=sorted(frame.sample_id.unique())
    special_mask=frame.function_id.str.match(r'^(UNMAPPED|READS_UNMAPPED|UNINTEGRATED|UNGROUPED|UniRef\d+_unknown)(:|$)')
    special=frame[special_mask]
    special_entries={sample: dict(zip(group.function_id,group.abundance)) for sample,group in special.groupby('sample_id')}
    frame=frame[~special_mask & (frame.abundance > 0)].copy()
    no_signal=sorted(set(samples)-set(frame.sample_id))
    evidence={'sample_ids':samples,'samples_without_named_signal':no_signal,'special_entries':special_entries,'special_entry_note':'Diagnostic categories are not named functions; values are reported scores, not read percentages. Zero named-signal samples have undefined composition (N/A).'}
    if frame.empty:
        return {**evidence,'heatmap':skip('no positive named functions; diagnostic categories excluded'),'composition':skip('no positive named functions; diagnostic categories excluded'),'selected_feature_ids':[]}
    coverage=label == 'pathway_coverage'
    if coverage and (frame.abundance > 1).any(): raise TableError('pathway coverage must lie within [0, 1]')
    # Missing features contribute zero to the cohort mean used for ranking.
    full=frame.pivot(index='sample_id',columns='function_id',values='abundance').reindex(samples).fillna(0)
    selected=full.mean().nlargest(top_n).index
    display=frame[frame.function_id.isin(selected)].copy() if coverage else frame.copy(); display["display_feature"]=np.where(display.function_id.isin(selected),display.function_id,"Other annotated (outside Top-N)")
    matrix=display.pivot_table(index="sample_id",columns="display_feature",values="abundance",aggfunc="sum",fill_value=0.0).reindex(samples).fillna(0)
    heat=outdir/f"functional_{label}_top{top_n}_heatmap.png"; figure,axis=plt.subplots(figsize=(max(6,matrix.shape[1]*.55),max(3,matrix.shape[0]*.45))); image=axis.imshow(matrix if coverage else np.log1p(matrix),aspect="auto",cmap="magma",**({'vmin':0,'vmax':1} if coverage else {})); axis.set(xticks=range(matrix.shape[1]),xticklabels=matrix.columns,yticks=range(matrix.shape[0]),yticklabels=matrix.index,title=f"{label}: Top-{top_n} " + ('coverage' if coverage else 'annotated abundance (log1p)')); axis.tick_params(axis="x",labelrotation=65,labelsize=8); figure.colorbar(image,ax=axis,label="HUMAnN pathway coverage (0-1)" if coverage else "log1p(HUMAnN reported abundance)"); save_figure(figure,heat)
    if coverage:
        return {**evidence,'heatmap':{'status':'generated','output':heat.name},'composition':skip('Coverage is not additive abundance; no composition plot'), 'selected_feature_ids':list(selected),'display_transform':'identity (0-1 coverage)'}
    composition=matrix.div(matrix.sum(axis=1).replace(0,np.nan),axis=0).fillna(0); comp=outdir/f"functional_{label}_top{top_n}_composition.png"; figure,axis=plt.subplots(figsize=(max(7,len(matrix)*.85),5)); composition.plot(kind="bar",stacked=True,ax=axis,colormap="tab20"); axis.set(ylabel="Composition within displayed Top-N + Other",xlabel="Sample (N/A: no named signal)",title=f"{label}: Top-{top_n} composition"); axis.set_xticklabels([f'{sample} (N/A)' if sample in no_signal else sample for sample in matrix.index]); axis.legend(loc="upper left",bbox_to_anchor=(1,1),fontsize=7); save_figure(figure,comp)
    return {**evidence,"heatmap":{"status":"generated","output":heat.name},"composition":{"status":"generated","output":comp.name},"selected_feature_ids":list(selected),"abundance_unit":sorted(frame.abundance_unit.unique()),"normalization_method":sorted(frame.normalization_method.unique()),'composition_denominator':'sum of named annotated features only; diagnostic categories excluded'}

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--gene-families",type=Path,required=True); parser.add_argument("--ko",type=Path,required=True); parser.add_argument("--ec",type=Path,required=True); parser.add_argument("--pathway-abundance",type=Path,required=True); parser.add_argument("--pathway-coverage",type=Path); parser.add_argument("--outdir",type=Path,required=True); parser.add_argument("--top-n",type=int,default=20); args=parser.parse_args(); args.outdir.mkdir(parents=True,exist_ok=True)
    inputs={"gene_families":(args.gene_families,"gene_family","UniRef90"),"ko":(args.ko,"ko","KO"),"ec":(args.ec,"ec","EC"),"pathway_abundance":(args.pathway_abundance,"pathway_abundance","MetaCyc")}
    if args.pathway_coverage: inputs["pathway_coverage"]=(args.pathway_coverage,"pathway_coverage","MetaCyc")
    provenance={"analysis":"functional_plots","analysis_version":"2.0","top_n":args.top_n,"top_n_rule":"select positive named features by mean community-total HUMAnN abundance; exclude diagnostic categories; coverage is never summed or renormalized","attribution_boundary":"All plots describe community_total reads-layer HUMAnN estimates of functional potential, not expression or activity. They do not attribute functions to a species or MAG.","plots":{}}
    try:
        for label,(path,kind,namespace) in inputs.items():
            frame=read_standard_table(path,REQUIRED,["sample_id","function_id"])
            if not frame.empty and (set(frame.function_type)!={kind} or set(frame.function_namespace)!={namespace}): raise TableError(f"{label} function_type or function_namespace mismatch")
            provenance["plots"][label]=render(frame,label,args.top_n,args.outdir)
    except TableError as exc:
        provenance["status"]="failed"; provenance["reason"]=str(exc); write_provenance(args.outdir/"functional_plots.provenance.json",provenance); return 65
    provenance["status"]="completed"; write_provenance(args.outdir/"functional_plots.provenance.json",provenance); return 0
if __name__=="__main__": raise SystemExit(main())
