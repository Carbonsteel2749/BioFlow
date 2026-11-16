#!/usr/bin/env python3
"""Frozen-source validator and descriptive external matrix adapter."""
from __future__ import annotations
import argparse
import base64
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
import math

from _input_contracts import SAFE_ID, validate_input_contract, matrix_value, NUMERIC_POLICY


def mean_value(total,count):
    with localcontext() as context:
        context.prec=28;context.rounding=ROUND_HALF_EVEN
        return total/count


def plot_coordinate(value):
    try:result=float(value)
    except (ValueError,OverflowError):return None
    return result if math.isfinite(result) and (not value or result!=0) else None


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def copy_fasta(source,destination):
    opener=gzip.open if source.name.lower().endswith('.gz') else open
    with opener(source,'rb') as inp, destination.open('wb') as out:shutil.copyfileobj(inp,out)


def materialize(manifest,node,paths,out):
    body=json.loads(Path(manifest).read_text(encoding='utf-8'))
    if body.get('snapshot_sha256')!=digest({k:v for k,v in body.items() if k!='snapshot_sha256'}):
        raise ValueError('Binding snapshot checksum mismatch')
    if not SAFE_ID.fullmatch(node):raise ValueError('Unsafe node ID')
    binding=body['bindings'][node];contract=binding['contract']
    if len(paths)!=len(contract['files']):raise ValueError('Source file count mismatch')
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    payload=out/'source_payload';payload.mkdir()
    # Copy into private task work, never symlink to mutable global storage.
    for entry,path in zip(contract['files'],paths):
        relative=Path(entry['path'])
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('Unsafe source path')
        target=payload/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)
    observed=validate_input_contract(binding['spec'],payload)
    if observed!=contract:raise ValueError('Source content changed after registration')
    if contract['kind']=='contigs':copy_fasta(payload/contract['fasta'],out/'contigs.fasta')
    if contract['kind']=='mags':
        (out/'mags').mkdir()
        for row in contract['mags']:copy_fasta(payload/row['fasta'],out/'mags'/(row['mag_id']+'.fa'))
    if contract['kind']=='reads':
        for row in contract['samples']:
            for mate in ('read1','read2'):
                source=payload/row[mate]
                target=out/(row['sample_id']+'.R'+mate[-1]+'.input'+('.fastq.gz' if row[mate].endswith('.gz') else '.fastq'))
                try:os.link(source,target)
                except OSError:shutil.copyfile(source,target)
    (out/'input.provenance.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding='utf-8')


def matrix_summary(dataset,out):
    dataset=Path(dataset);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    contract=json.loads((dataset/'input.provenance.json').read_text(encoding='utf-8'))
    coverage=contract['feature_type']=='pathway_coverage'
    source=(dataset/'source_payload' if (dataset/'source_payload').is_dir() else dataset)/contract['table'];samples={};features={}
    # Streaming reductions avoid a potentially enormous sparse-to-dense pivot.
    with source.open(encoding='utf-8-sig',newline='') as handle, (out/'canonical.tsv').open('w',encoding='utf-8',newline='') as target:
        reader=csv.DictReader(handle,delimiter='\t' if source.suffix.lower()=='.tsv' else ',')
        writer=csv.DictWriter(target,fieldnames=['sample_id','feature_id','value','unit'],delimiter='\t');writer.writeheader()
        for row in reader:
            value=matrix_value(row['value']);sid=row['sample_id'];fid=row['feature_id']
            writer.writerow({'sample_id':sid,'feature_id':fid,'value':row['value'],'unit':contract['unit']})
            for mapping,key in ((samples,sid),(features,fid)):
                acc=mapping.setdefault(key,[Decimal(0),0,0])
                with localcontext() as context:
                    context.prec=237
                    acc[0]+=value if value else Decimal(0)
                acc[1]+=1;acc[2]+=int(value>0)
    for name,mapping,key in [('sample_summary.tsv',samples,'sample_id'),('feature_summary.tsv',features,'feature_id')]:
        with (out/name).open('w',encoding='utf-8',newline='') as handle:
            writer=csv.writer(handle,delimiter='\t');writer.writerow([key,'sum','observed_features' if key=='sample_id' else 'observed_samples','positive_values','mean_observed'])
            for item,(total,count,positive) in sorted(mapping.items()):writer.writerow([item,'' if coverage else str(total),count,positive,str(mean_value(total,count))])
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'visualization'))
    from plot_utils import styled_plots, save_figure, write_provenance, plt, OKABE_ITO
    top=sorted(features.items(),key=lambda item:(mean_value(item[1][0],item[1][1]).copy_negate(),item[0]))[:20]
    coordinates=[plot_coordinate(mean_value(v[0],v[1])) for _,v in reversed(top)]
    @styled_plots
    def render():
        plot={'status':'skipped','reason':'Coordinates are not representable as finite non-underflowing display floats'}
        if all(v is not None for v in coordinates):
            fig,ax=plt.subplots(figsize=(8,max(3,len(top)*.3)))
            ax.barh([key for key,_ in reversed(top)],coordinates,color=OKABE_ITO[0])
            ax.set_xlabel('Mean observed value ('+contract['unit']+')');ax.set_title('External '+contract['feature_type']+' matrix')
            save_figure(fig,out/'feature_means.png');plt.close(fig)
            plot={'status':'generated','output':'feature_means.png'}
        provenance={**contract,'producer':'external_matrix_descriptive_summary','transformation':'none',
            'missing_values':'unobserved entries remain missing; means use observed rows only',
            'numeric_policy':NUMERIC_POLICY,
            'sum_policy':'non_additive_coverage_confidence' if coverage else 'exact observed sum',
            'mean_policy':'descriptive confidence average over observed rows; not whole-community probability or abundance; no zero imputation' if coverage else 'descriptive mean over observed rows',
            'plots':{'feature_means':plot,
                     'ordination':{'status':'skipped','reason':'No distance transform or missing-value convention was specified'},
                     'differential':{'status':'skipped','reason':'Descriptive analysis only; no statistical inference'}},
            'dense_matrix':'not constructed; streaming reductions', 'sample_count':len(samples),'feature_count':len(features)}
        write_provenance(out/'summary.provenance.json',provenance)
    render()
    (out/'feature_means.caption.md').write_text('Top 20 features ranked by mean of observed values. Original '+contract['unit']+' units; no transformation or normalization. Missing entries are not zero-filled. External, unverified source; not HUMAnN/Bracken evidence. Descriptive only. '+('non_additive_coverage_confidence: sum unavailable; descriptive confidence average over observed rows, not whole-community probability or abundance.' if coverage else '')+'\n',encoding='utf-8')
    (out/'summary.txt').write_text(f'{len(samples)} samples; {len(features)} features. Unit: {contract["unit"]}. Normalization declared by source: {contract["normalization"]}. No additional normalization. Missing entries are not assumed zero.\n',encoding='utf-8')


def reads_manifest(samples,paths,out):
    if not samples or len(paths)!=2*len(samples) or len(set(samples))!=len(samples) or any(not SAFE_ID.fullmatch(s) for s in samples):
        raise ValueError('Invalid connected reads collection')
    with Path(out).open('w',encoding='utf-8',newline='') as handle:
        writer=csv.writer(handle);writer.writerow(['sample_id','read1','read2'])
        for i,sample in enumerate(samples):writer.writerow([sample,str(Path(paths[2*i]).resolve(strict=True)),str(Path(paths[2*i+1]).resolve(strict=True))])


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--manifest');parser.add_argument('--node');parser.add_argument('--paths-base64');parser.add_argument('--matrix');parser.add_argument('--out',required=True)
    parser.add_argument('--samples-base64')
    args=parser.parse_args()
    if args.samples_base64:reads_manifest(json.loads(base64.b64decode(args.samples_base64)),json.loads(base64.b64decode(args.paths_base64)),args.out)
    elif args.matrix:matrix_summary(args.matrix,args.out)
    else:materialize(args.manifest,args.node,json.loads(base64.b64decode(args.paths_base64)),args.out)
