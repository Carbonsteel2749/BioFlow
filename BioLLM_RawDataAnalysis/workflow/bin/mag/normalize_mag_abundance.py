#!/usr/bin/env python3
"""Validate complete metaWRAP output and map explicit producer aliases to samples."""
from __future__ import annotations
import argparse
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

FIELDS=['sample_id','mag_id','detected','abundance','abundance_unit','coverage','mapped_reads','mapping_method','detection_threshold','evidence_source','association_meaning','provenance_id']
FASTA_SUFFIXES=('.fasta','.fna','.fa')


def normalize_mag_id(value):
    cleaned=value.strip()
    for suffix in FASTA_SUFFIXES:
        if cleaned.lower().endswith(suffix):return cleaned[:-len(suffix)]
    return cleaned


def aliases(row):
    values={row['sample_id']}
    for field in ('read1','read2'):
        name=Path(row[field]).name
        stem=re.sub(r'\.(fastq|fq)(\.gz)?$','',name,flags=re.I)
        values.update((name,stem,re.sub(r'([_.-]R?[12])$','',stem)))
    return {value.lower():row['sample_id'] for value in values if value}


def normalized_rows(source,sample_manifest,task_id):
    mapping={};samples=set()
    with sample_manifest.open(encoding='utf-8-sig',newline='') as handle:
        reader=csv.DictReader(handle)
        if reader.fieldnames!=['sample_id','read1','read2']:raise ValueError('Invalid sample alias manifest')
        for row in reader:
            sid=row.get('sample_id')
            if not sid or sid in samples or any(not row.get(k) for k in ('read1','read2')):raise ValueError('Missing or duplicate sample identity')
            samples.add(sid)
            for alias,owner in aliases(row).items():
                if alias in mapping and mapping[alias]!=owner:raise ValueError('Ambiguous sample filename alias: '+alias)
                mapping[alias]=owner
    if not samples:raise ValueError('No samples in alias manifest')
    with source.open(encoding='utf-8',newline='') as handle:rows=list(csv.reader(handle,delimiter='\t'))
    if len(rows)<2 or len(rows[0])<2:raise ValueError('Quantification matrix is empty')
    header=rows[0];owners=[]
    for label in header[1:]:
        sample=mapping.get(label.strip().lower())
        if sample is None:raise ValueError('Unmatched quantification sample column: '+label)
        if sample in owners:raise ValueError('Ambiguous duplicate quantification sample column: '+label)
        owners.append(sample)
    if set(owners)!=samples:raise ValueError('Quantification is missing samples from the manifest')
    normalized=[];mags=set()
    for row in rows[1:]:
        if len(row)!=len(header):raise ValueError('Incomplete quantification matrix row')
        mag=normalize_mag_id(row[0])
        if not mag or mag in mags:raise ValueError('Missing or duplicate MAG ID')
        mags.add(mag)
        for sample,value in zip(owners,row[1:]):
            value=value.strip()
            try:number=Decimal(value)
            except InvalidOperation as exc:raise ValueError('Invalid quantification abundance') from exc
            if not number.is_finite() or number<0:raise ValueError('Abundance must be finite and non-negative')
            normalized.append({'sample_id':sample,'mag_id':mag,'detected':str(number>0).lower(),
                'abundance':value,'abundance_unit':'tool_reported_abundance','coverage':'','mapped_reads':'',
                'mapping_method':'MetaWRAP quant_bins','detection_threshold':'> 0 tool-reported abundance',
                'evidence_source':'MetaWRAP bin quantification','association_meaning':'MAG detected in sample by read mapping',
                'provenance_id':f'{task_id}:mag_quantification'})
    return normalized,dict(zip(header[1:],owners))


def main():
    parser=argparse.ArgumentParser()
    for name in ('source','sample-manifest','database-manifest','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--original-sample-manifest',type=Path);parser.add_argument('--task-id',required=True)
    args=parser.parse_args()
    rows,columns=normalized_rows(args.source,args.sample_manifest,args.task_id)
    data=json.loads(args.database_manifest.read_text(encoding='utf-8'))
    repo=Path(__file__).resolve().parents[3]
    revision=subprocess.run(['git','rev-parse','HEAD'],cwd=repo,capture_output=True,text=True).stdout.strip() or 'unavailable'
    version=subprocess.run(['metawrap','--version'],capture_output=True,text=True).stdout.strip() if shutil.which('metawrap') else 'unavailable'
    provenance={'workflow_revision':revision,'task_or_run_id':args.task_id,'tool_name':'MetaWRAP quant_bins','tool_version':version,
        'database_profile':data['database_profile'],'database_manifest_sha256':data['resolved_manifest_sha256'],
        'input_files':[str(args.source),str(args.sample_manifest)]+([str(args.original_sample_manifest)] if args.original_sample_manifest else []),
        'output_files':[str(args.output)],'sample_column_mapping':columns,
        'sample_alias_manifest_sha256':hashlib.sha256(args.sample_manifest.read_bytes()).hexdigest()}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    temporary=args.output.with_suffix(args.output.suffix+'.tmp')
    with temporary.open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=FIELDS,delimiter='\t');writer.writeheader();writer.writerows(rows)
    os.replace(temporary,args.output)
    args.output.with_suffix('.provenance.json').write_text(json.dumps(provenance,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
