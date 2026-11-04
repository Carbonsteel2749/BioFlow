#!/usr/bin/env python3
"""Preserve sample/mate identity across metaWRAP's *_1.fastq convention."""
from __future__ import annotations
import argparse
import csv
import gzip
import re
import shutil
from pathlib import Path

SAFE_ID=re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')


def stage_quantification_inputs(manifest,reads_list,out,alias_manifest):
    manifest=Path(manifest);out=Path(out);alias_manifest=Path(alias_manifest)
    with manifest.open(encoding='utf-8-sig',newline='') as handle:
        reader=csv.DictReader(handle)
        if reader.fieldnames!=['sample_id','read1','read2']:raise ValueError('Invalid quantification sample manifest')
        rows=list(reader)
    if not rows:raise ValueError('Quantification requires samples')
    actual=[Path(line).resolve(strict=True) for line in Path(reads_list).read_text(encoding='utf-8').splitlines() if line.strip()]
    if any(not path.is_file() or path.stat().st_size==0 for path in actual):raise ValueError('Empty or missing quantification reads')
    identities=[(p.stat().st_dev,p.stat().st_ino) for p in actual]
    if len(set(identities))!=len(actual):raise ValueError('Repeated quantification read file')
    seen=set();used=set();assignments=[]
    for row in rows:
        sid=row.get('sample_id','')
        if not SAFE_ID.fullmatch(sid) or sid.lower() in seen or None in row:raise ValueError('Invalid or ambiguous sample identity')
        seen.add(sid.lower());pair=[]
        for mate in (1,2):
            value=row.get(f'read{mate}')
            if not value:raise ValueError('Missing mate in sample manifest')
            declared=Path(value)
            if not declared.is_absolute():declared=manifest.parent/declared
            matching=[p for p in actual if declared.exists() and p.samefile(declared)]
            if not matching:
                # Legacy manifests are raw; existing pipeline outputs carry this
                # exact sample/mate convention, not an arbitrary filename guess.
                names={f'{sid}.R{mate}.{state}.fastq{suffix}' for state in ('clean','host_removed','input') for suffix in ('','.gz')}
                matching=[p for p in actual if p.name in names]
            if len(matching)!=1 or matching[0] in used:raise ValueError(f'Cannot uniquely bind actual reads to {sid} mate {mate}')
            used.add(matching[0]);pair.append(matching[0])
        assignments.append((sid,pair))
    if used!=set(actual):raise ValueError('Quantification reads contain files outside the sample manifest')
    out.mkdir(parents=True,exist_ok=True)
    aliases=[];staged=[];reserved=set(seen)
    for index,(sid,pair) in enumerate(assignments,1):
        label=f'bioflow_sample_{index:06d}'
        while True:
            producer_aliases={label,*[label+f'_{mate}'+suffix for mate in (1,2) for suffix in ('','.fastq')]}
            if not producer_aliases & reserved:break
            label+='_x'
        reserved.update(producer_aliases)
        record={'sample_id':sid}
        for mate,source in enumerate(pair,1):
            target=out/f'{label}_{mate}.fastq'
            opener=gzip.open if source.name.lower().endswith('.gz') else open
            with opener(source,'rb') as inp,target.open('xb') as dest:shutil.copyfileobj(inp,dest)
            if target.stat().st_size==0:raise ValueError('Empty staged reads')
            record[f'read{mate}']=str(target.resolve());staged.append(str(target.resolve()))
        aliases.append(record)
    alias_manifest.parent.mkdir(parents=True,exist_ok=True)
    with alias_manifest.open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=['sample_id','read1','read2']);writer.writeheader();writer.writerows(aliases)
    (out/'staged_reads.list').write_text('\n'.join(staged)+'\n',encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('sample-manifest','reads-list','outdir','alias-manifest'):parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args()
    stage_quantification_inputs(args.sample_manifest,args.reads_list,args.outdir,args.alias_manifest)
