#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from plot_utils import TableError, read_standard_table, save_figure, skip, write_provenance

MAG_REQUIRED={"cohort_id","mag_id","taxonomy","completeness","contamination"}
ABUNDANCE_REQUIRED={"sample_id","mag_id","detected","abundance","abundance_unit","coverage","mapped_reads","mapping_method","detection_threshold","association_meaning"}

def quality_grade(completeness, contamination):
    if completeness>=90 and contamination<5: return "high_quality"
    if completeness>=50 and contamination<10: return "medium_quality"
    return "low_quality"

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--mag-annotation",type=Path,required=True); parser.add_argument("--sample-mag-abundance",type=Path,required=True); parser.add_argument("--outdir",type=Path,required=True); parser.add_argument("--detection-threshold",type=float,default=0.0); parser.add_argument("--top-n",type=int,default=20); args=parser.parse_args(); args.outdir.mkdir(parents=True,exist_ok=True)
    provenance={"analysis":"mag_plots","detection_threshold":args.detection_threshold,"top_n":args.top_n,"attribution_boundary":"MAGs are cohort/coassembly entities. MAG annotation tables never imply sample ownership; sample-MAG links are read-mapping abundance rows only.","plots":{}}
    try:
        mag=read_standard_table(args.mag_annotation,MAG_REQUIRED,["cohort_id","mag_id"],abundance="completeness")
        abundance=read_standard_table(args.sample_mag_abundance,ABUNDANCE_REQUIRED,["sample_id","mag_id"])
        if not mag.empty and mag.cohort_id.nunique() != 1: raise TableError("MAG plot input must contain exactly one cohort_id; sample-MAG abundance has no cohort ownership key")
        for name in ("completeness","contamination"):
            values=np.asarray(mag[name],dtype=float) if not mag.empty else np.array([])
            if not np.isfinite(values).all() or (values<0).any() or (values>100).any(): raise TableError(f"{name} must be finite and within [0,100]")
        if not abundance.empty and abundance.detected.astype(str).str.lower().isin(["true","false"]).all()==False: raise TableError("detected must be true or false")
    except TableError as exc:
        provenance["status"]="failed"; provenance["reason"]=str(exc); write_provenance(args.outdir/"mag_plots.provenance.json",provenance); return 65
    if mag.empty:
        provenance["status"]="completed"; provenance["plots"]={name:skip("no MAG rows were produced for this cohort") for name in ("quality_scatter","quality_grades","abundance_heatmap","taxonomy_composition")}; write_provenance(args.outdir/"mag_plots.provenance.json",provenance); return 0
    mag=mag.copy(); mag["quality_grade"]=[quality_grade(float(c),float(x)) for c,x in zip(mag.completeness,mag.contamination)]
    scatter=args.outdir/"mag_completeness_contamination.png"; figure,axis=plt.subplots(figsize=(6,5)); axis.scatter(mag.contamination,mag.completeness,c=mag.completeness,cmap="viridis"); axis.axhline(50,color="grey",ls="--",lw=.8); axis.axvline(10,color="grey",ls="--",lw=.8); axis.set(xlabel="Contamination (%)",ylabel="Completeness (%)",title="MAG quality by cohort"); save_figure(figure,scatter); provenance["plots"]["quality_scatter"]={"status":"generated","output":scatter.name}
    grades=mag.quality_grade.value_counts().reindex(["high_quality","medium_quality","low_quality"],fill_value=0); quality=args.outdir/"mag_quality_grade_counts.png"; figure,axis=plt.subplots(figsize=(6,4)); grades.plot.bar(ax=axis,color=["#3a9d23","#f0ad4e","#c84630"]); axis.set(ylabel="MAG count",xlabel="Quality grade",title="MAG quality grade counts"); save_figure(figure,quality); provenance["plots"]["quality_grades"]={"status":"generated","output":quality.name,"rules":"high: completeness>=90 and contamination<5; medium: completeness>=50 and contamination<10; low: otherwise"}
    classified=mag[mag.taxonomy.astype(str).str.strip()!=""]
    if classified.empty: provenance["plots"]["taxonomy_composition"]=skip("MAG taxonomy is absent")
    else:
        ranked=classified.taxonomy.value_counts(); selected=ranked.head(args.top_n); other=ranked.iloc[args.top_n:].sum(); taxonomy=args.outdir/"mag_taxonomy_composition.png"; figure,axis=plt.subplots(figsize=(max(6,len(selected)*.6),4)); values=selected.copy();
        if other: values.loc["Other (outside Top-N)"]=other
        values.plot.bar(ax=axis); axis.set(ylabel="MAG count",xlabel="Taxonomy",title=f"MAG taxonomy composition: Top-{args.top_n}"); axis.tick_params(axis="x",labelrotation=55); save_figure(figure,taxonomy); provenance["plots"]["taxonomy_composition"]={"status":"generated","output":taxonomy.name,"top_n_rule":"count MAGs by taxonomy; ranks outside Top-N merge into Other"}
    detected=abundance[(abundance.detected.astype(str).str.lower()=="true") & (abundance.abundance>=args.detection_threshold)]
    matrix=detected.pivot_table(index="sample_id",columns="mag_id",values="abundance",aggfunc="sum",fill_value=0.0)
    if len(matrix)<2 or matrix.empty: provenance["plots"]["abundance_heatmap"]=skip("sample-MAG heatmap requires at least two samples with detected MAG abundance at the threshold")
    else:
        means=matrix.mean().nlargest(args.top_n).index; matrix=matrix.loc[:,means]; heat=args.outdir/"sample_mag_abundance_heatmap.png"; figure,axis=plt.subplots(figsize=(max(6,len(means)*.55),max(3,len(matrix)*.45))); image=axis.imshow(np.log1p(matrix),aspect="auto",cmap="Blues"); axis.set(xticks=range(matrix.shape[1]),xticklabels=matrix.columns,yticks=range(matrix.shape[0]),yticklabels=matrix.index,title="Sample-MAG read-mapping abundance (log1p)"); axis.tick_params(axis="x",labelrotation=55); figure.colorbar(image,ax=axis,label="log1p(tool-reported abundance)"); save_figure(figure,heat); provenance["plots"]["abundance_heatmap"]={"status":"generated","output":heat.name,"top_n_rule":"select MAGs by mean detected abundance; no MAG is assigned to a sample"}
    provenance["status"]="completed"; provenance["cohort_ids"]=sorted(mag.cohort_id.unique()); write_provenance(args.outdir/"mag_plots.provenance.json",provenance); return 0
if __name__=="__main__": raise SystemExit(main())
