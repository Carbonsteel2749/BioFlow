#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.spatial.distance import pdist, squareform

from plot_utils import TableError, abundance_matrix, read_standard_table, save_figure, skip, write_provenance
import matplotlib.pyplot as plt

REQUIRED = {"sample_id","taxid","taxonomy","taxonomy_rank","taxonomy_system","read_count_or_estimated_reads","abundance","abundance_unit","classification_source","database_profile","database_release"}

def heatmap(matrix, title, path):
    figure, axis = plt.subplots(figsize=(max(6, matrix.shape[1] * .55), max(3, matrix.shape[0] * .45)))
    image = axis.imshow(matrix.to_numpy(), aspect="auto", cmap="viridis")
    axis.set(xticks=range(matrix.shape[1]), xticklabels=matrix.columns, yticks=range(matrix.shape[0]), yticklabels=matrix.index, title=title)
    axis.tick_params(axis="x", labelrotation=65, labelsize=8)
    figure.colorbar(image, ax=axis, label="Relative abundance (fraction of reads)")
    save_figure(figure, path)

def diversity(matrix, path):
    proportions = matrix.div(matrix.sum(axis=1), axis=0).replace(0, np.nan)
    shannon = -(proportions * np.log(proportions)).sum(axis=1)
    simpson = 1 - (proportions ** 2).sum(axis=1)
    figure, axis = plt.subplots(figsize=(max(6, len(matrix) * .8), 4))
    positions = np.arange(len(matrix)); axis.bar(positions - .2, shannon, .4, label="Shannon"); axis.bar(positions + .2, simpson, .4, label="Simpson (1-D)")
    axis.set(xticks=positions, xticklabels=matrix.index, ylabel="Diversity index", title="Alpha diversity from species relative abundance")
    axis.legend(); save_figure(figure, path)

def pcoa(matrix, path):
    distances = squareform(pdist(matrix.to_numpy(), metric="braycurtis"))
    n = len(matrix); centered = np.eye(n) - np.ones((n,n))/n; eigenvalues, eigenvectors = np.linalg.eigh(-.5 * centered @ (distances ** 2) @ centered)
    order = np.argsort(eigenvalues)[::-1]; eigenvalues=eigenvalues[order]; eigenvectors=eigenvectors[:,order]; positive=np.clip(eigenvalues,0,None)
    coordinates=eigenvectors[:,:2] * np.sqrt(positive[:2]); total=positive.sum(); labels=[f"PC{i+1} ({positive[i]/total*100:.1f}%)" if total else f"PC{i+1}" for i in range(2)]
    figure, axis=plt.subplots(figsize=(6,5)); axis.scatter(coordinates[:,0],coordinates[:,1])
    for label,x,y in zip(matrix.index,coordinates[:,0],coordinates[:,1]): axis.annotate(label,(x,y),xytext=(4,4),textcoords="offset points")
    axis.set(xlabel=labels[0],ylabel=labels[1],title="Bray-Curtis PCoA of species composition"); save_figure(figure,path)

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--input",type=Path,required=True); parser.add_argument("--outdir",type=Path,required=True); parser.add_argument("--top-n",type=int,default=20); parser.add_argument("--low-abundance-threshold",type=float,default=0.01); args=parser.parse_args()
    args.outdir.mkdir(parents=True,exist_ok=True); provenance={"analysis":"taxonomy_plots","input":str(args.input),"top_n":args.top_n,"low_abundance_rule":f"features below {args.low_abundance_threshold:g} mean relative abundance merge into Other; remaining highest mean features limited to Top-{args.top_n}","abundance_unit":"fraction_of_reads","plots":{}}
    try:
        frame=read_standard_table(args.input,REQUIRED,["sample_id","taxid"])
        if not frame.empty and set(frame["abundance_unit"]) != {"fraction_of_reads"}: raise TableError("taxonomy abundance_unit must be fraction_of_reads")
        if not frame.empty and set(frame["taxonomy_rank"]) != {"species"}: raise TableError("taxonomy plot input must contain species-rank rows only")
    except TableError as exc:
        provenance["status"]="failed"; provenance["reason"]=str(exc); write_provenance(args.outdir/"taxonomy_plots.provenance.json",provenance); return 65
    if frame.empty:
        provenance["status"]="completed"; provenance["plots"]={name:skip("input table has no species rows") for name in ("stacked","heatmap","diversity","pcoa")}; write_provenance(args.outdir/"taxonomy_plots.provenance.json",provenance); return 0
    means=frame.groupby("taxonomy").abundance.mean(); selected=means[means>=args.low_abundance_threshold].sort_values(ascending=False).head(args.top_n).index
    display=frame.copy(); display["display_taxonomy"]=np.where(display.taxonomy.isin(selected),display.taxonomy,"Other (low abundance or outside Top-N)")
    table=display.pivot_table(index="sample_id",columns="display_taxonomy",values="abundance",aggfunc="sum",fill_value=0.0)
    stacked=args.outdir/"taxonomy_top20_stacked.png"; figure,axis=plt.subplots(figsize=(max(7,len(table)*.85),5)); table.plot(kind="bar",stacked=True,ax=axis,colormap="tab20"); axis.set(ylabel="Relative abundance (fraction of reads)",xlabel="Sample",title=f"Species composition: Top-{args.top_n} plus Other"); axis.legend(loc="upper left",bbox_to_anchor=(1,1),fontsize=7); save_figure(figure,stacked)
    heat=args.outdir/"taxonomy_species_heatmap.png"; heatmap(table,"Species relative abundance heatmap",heat)
    alpha=args.outdir/"taxonomy_alpha_diversity.png"; diversity(table,alpha)
    provenance["plots"]={"stacked":{"status":"generated","output":str(stacked.name)},"heatmap":{"status":"generated","output":str(heat.name)},"diversity":{"status":"generated","output":str(alpha.name)}}
    if len(table)<2 or (table.sum(axis=1)<=0).any(): provenance["plots"]["pcoa"]=skip("Bray-Curtis PCoA requires at least two samples with positive abundance totals")
    else:
        output=args.outdir/"taxonomy_bray_curtis_pcoa.png"; pcoa(table,output); provenance["plots"]["pcoa"]={"status":"generated","output":str(output.name)}
    provenance["status"]="completed"; provenance["sample_count"]=len(table); provenance["selected_taxa"]=list(selected); write_provenance(args.outdir/"taxonomy_plots.provenance.json",provenance); return 0
if __name__=="__main__": raise SystemExit(main())
