"""Reviewed direct-tool and shared-library execution; inputs come from validated plans."""
import csv
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def run(argv, stdout=None, env=None):
    print('COMMAND',json.dumps([str(x) for x in argv]),flush=True)
    subprocess.run([str(x) for x in argv],check=True,stdout=stdout,env=env)


def dump(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False))


def records(manifest):
    with Path(manifest).open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    for row in rows:
        for key in ['read1','read2']:
            path=Path(row[key])
            row[key]=str(path if path.is_absolute() else (Path(manifest).parent/path).resolve())
    return rows


def bins(path):
    files=sorted(p for p in Path(path).iterdir() if p.is_file() and p.suffix in {'.fa','.fna','.fasta'})
    if not files:raise ValueError('没有可用的 MAG FASTA，不能继续运行')
    if len({p.stem for p in files})!=len(files):raise ValueError('MAG 标识重复')
    return files


def concatenate(rows,folder):
    paths=[]
    for key in ['read1','read2']:
        dest=folder/(key+'.fastq')
        with dest.open('wb') as out:
            for row in rows:
                source=Path(row[key]);opener=gzip.open if source.suffix=='.gz' else open
                with opener(source,'rb') as f:shutil.copyfileobj(f,out)
        paths.append(dest)
    return paths


def materialize_output_links(out):
    """GTDB-Tk uses relative summary links; export their verified local bytes."""
    for link in sorted(out.rglob('*')):
        if not link.is_symlink():continue
        target=link.resolve(strict=True)
        if not target.is_relative_to(out.resolve()) or not target.is_file():raise ValueError('工具输出链接指向结果目录之外或不是文件')
        link.unlink();shutil.copyfile(target,link)


def scientific(plan,out):
    import numpy as np
    import pandas as pd
    from bioflow_ml.microbiome_analysis.compositional import clr_transform
    from bioflow_ml.microbiome_analysis.diversity import alpha_diversity,pcoa,nmds,permanova
    i=plan['inputs'];p=plan['parameters'];node=plan['node_type']
    frame=pd.read_csv(i['table'],index_col=0,dtype=str)
    if frame.index.has_duplicates or frame.columns.has_duplicates or frame.empty:raise ValueError('样本及特征标识必须唯一，表格不能为空')
    frame=frame.apply(pd.to_numeric,errors='raise')
    if not np.isfinite(frame.to_numpy()).all():raise ValueError('不接受非有限数值')
    if node=='clr':clr_transform(frame,**p).to_csv(out/'clr.csv')
    elif node=='alpha_diversity':alpha_diversity(frame,metrics=(p['metric'],)).to_csv(out/'alpha_diversity.csv')
    elif node in {'pcoa','nmds'}:
        # These shared functions accept a distance matrix; no hidden abundance transform.
        result={'pcoa':pcoa,'nmds':nmds}[node](frame,**p)
        result.coordinates.to_csv(out/'coordinates.csv')
        if result.eigenvalues is not None:result.eigenvalues.to_csv(out/'eigenvalues.csv')
        if result.stress is not None:dump(out/'metrics.json',{'stress':result.stress})
    else:
        groups=pd.read_csv(i['groups'],dtype=str)
        if not {'sample_id','group'}.issubset(groups.columns) or groups.sample_id.duplicated().any():raise ValueError('分组表必须包含唯一 sample_id 与 group')
        labels=groups.set_index('sample_id')['group'].reindex(frame.index)
        result=permanova(frame,labels,**p)
        dump(out/'permanova.json',vars(result))
    dump(out/'methods.json',{'method':node,'implementation':'existing bioflow_ml.microbiome_analysis','parameters':p,'input_semantics':'sample-by-feature for CLR/alpha; sample-by-sample distances for ordination/PERMANOVA','inference_limit':'探索性分析；分组交换性及多重检验由研究设计确认'})


def main(request_path):
    request=json.loads(Path(request_path).read_text());plan=request['plan'];node=plan['node_type'];i=plan['inputs'];p=plan['parameters']
    out=Path(request['output']);out.mkdir(parents=True,exist_ok=True);work=out.parent/'intermediate';work.mkdir(exist_ok=True)
    tool=lambda name:plan['tools'][name]['executable']
    threads=str(p.get('threads',1));base=Path(request['base_source']);extension=Path(__file__).parent
    if node in __import__('runpy').run_path(str(extension/'amplicon_contracts.py'))['CONTRACTS']:
        from bioflow_ml.amplicon_nodes import execute
        execute(plan,out,work,extension)
    elif node in __import__('runpy').run_path(str(extension/'analysis_contracts.py'))['CONTRACTS']:
        from bioflow_ml.analysis_nodes import execute,validate
        info=validate(plan)
        if 'R_statistics' in plan['tools']:
            p={**p,'reference':info['reference']}
            dcf=work/'statistics.dcf';dcf.write_text('\n'.join(k+': '+str(v) for k,v in {**i,**p,'node':node,'output':str(out)}.items())+'\n')
            run([tool('R_statistics'),extension/'statistics_worker.R',dcf])
            dump(out/'analysis-methods.json',{'node':node,'parameters':p,'inspection':info,'real_execution':True,'CI':'method-specific or approximate Wald; not synthesized by a model'})
        else:execute(plan,out)
    elif node in {'clr','alpha_diversity','pcoa','nmds','permanova'}:scientific(plan,out)
    elif node.endswith('_plots'):
        script=base/'scripts/workflow/bin/visualization'/(node+'.py')
        argv=[sys.executable,script,'--outdir',out]
        if node=='reads_plots':argv+=['--metrics-dir',i['metrics_dir']]
        elif node=='taxonomy_plots':argv+=['--input',i['table'],'--top-n',str(p['top_n'])]
        elif node=='functional_plots':
            for key,flag in [('gene_families','--gene-families'),('ko','--ko'),('ec','--ec'),('pathways','--pathway-abundance')]:argv+=[flag,i[key]]
            argv+=['--top-n',str(p['top_n'])]
        elif node=='mag_plots':argv+=['--mag-annotation',i['mags'],'--sample-mag-abundance',i['abundance'],'--top-n',str(p['top_n'])]
        run(argv)
    elif node=='mag_assembly':
        rows=records(i['manifest']);r1,r2=concatenate(rows,work)
        assembly=work/'assembly'
        if assembly.exists():shutil.rmtree(assembly)
        if p['assembler']=='megahit':
            run([tool('megahit'),'-1',r1,'-2',r2,'-o',assembly,'-t',threads,'-m',str(p['memory_gb']*1024**3),'--min-contig-len',str(p['min_contig_length'])])
            contigs=assembly/'final.contigs.fa'
        else:
            run([tool('spades.py'),'--meta','-1',r1,'-2',r2,'--phred-offset',str(p['phred_offset']),'-o',assembly,'-t',threads,'-m',str(p['memory_gb'])])
            contigs=assembly/'contigs.fasta'
        if not contigs.is_file() or contigs.stat().st_size==0:raise ValueError('组装器未生成非空 contigs')
        kept=0
        with contigs.open() as source,(out/'assembly.fasta').open('w') as target:
            header=None;sequence=[]
            def write_contig(h,seq):
                if h is not None and len(''.join(seq))>=p['min_contig_length']:
                    target.write(h+'\n'+''.join(seq)+'\n');return 1
                return 0
            for line in source:
                if line.startswith('>'):kept+=write_contig(header,sequence);header=line.strip();sequence=[]
                else:sequence.append(line.strip())
            kept+=write_contig(header,sequence)
        if not kept:raise ValueError('组装后没有达到最小长度的 contig')
        dump(out/'assembly.json',{'assembler':p['assembler'],'samples':[r['sample_id'] for r in rows],'coassembly':len(rows)>1,'minimum_contig_length':p['min_contig_length'],'retained_contigs':kept})
    elif node=='mag_binning':
        index=work/'assembly-index';run([tool('bowtie2-build'),i['assembly'],index]);bams=[]
        for row in records(i['manifest']):
            sam=work/(row['sample_id']+'.sam');bam=work/(row['sample_id']+'.bam')
            run([tool('bowtie2'),'-x',index,'-1',row['read1'],'-2',row['read2'],'-p',threads,'-S',sam])
            run([tool('samtools'),'sort','-@',threads,'-o',bam,sam]);bams.append(bam);sam.unlink()
        run([tool('jgi_summarize_bam_contig_depths'),'--outputDepth',out/'depth.tsv',*bams])
        (out/'bins').mkdir(exist_ok=True)
        run([tool('metabat2'),'-i',i['assembly'],'-a',out/'depth.tsv','-o',out/'bins/bin','-t',threads,'-m',str(p['min_contig_length']),'--seed',str(p['seed'])])
        dump(out/'binning.json',{'method':'MetaBAT2 only','bins':[x.name for x in (out/'bins').glob('*.fa')],'no_bins_is_not_a_positive_discovery':True})
    elif node=='mag_quality':
        originals=bins(i['bins']);db=plan['databases']['mag.checkm2'];dbfile=Path(db['path'])/db['database_file']
        run([tool('checkm2'),'predict','--threads',threads,'--input',*originals,'--output-directory',work/'checkm2','--database_path',dbfile])
        report=work/'checkm2/quality_report.tsv';shutil.copyfile(report,out/'quality_report.tsv');kept=[]
        (out/'filtered_bins').mkdir(exist_ok=True)
        with report.open() as f:
            for row in csv.DictReader(f,delimiter='\t'):
                if float(row['Completeness'])>=p['completeness'] and float(row['Contamination'])<=p['contamination']:
                    source=next((x for x in originals if x.stem==row['Name']),None)
                    if source is None:raise ValueError('CheckM2 输出标识与输入 MAG 不匹配')
                    dest=out/'filtered_bins'/(source.stem+'.fa');shutil.copyfile(source,dest);kept.append(source.stem)
        dump(out/'quality-filter.json',{'parameters':p,'kept_bins':kept,'mimag_certification':False})
    elif node=='mag_quantification':
        originals=bins(i['bins']);combined=[]
        for row in records(i['manifest']):
            target=out/(row['sample_id']+'.coverm.tsv')
            run([tool('coverm'),'genome','--coupled',row['read1'],row['read2'],'--genome-fasta-files',*originals,'--methods','relative_abundance','--threads',threads,'--output-file',target])
            with target.open() as f:
                rows=list(csv.reader(f,delimiter='\t'))
                if len(rows)<2:raise ValueError('CoverM 未输出丰度记录')
                for value in rows[1:]:combined.append({'sample_id':row['sample_id'],'mag_id':value[0],'tool_value':value[-1]})
        with (out/'coverm.tsv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['sample_id','mag_id','tool_value'],delimiter='\t');w.writeheader();w.writerows(combined)
        dump(out/'abundance-unit.json',{'method':'CoverM genome relative_abundance','units':'percent, directly reported by CoverM','unmapped':'retained as tool-reported unmapped row; not renormalized to detected MAGs'})
    elif node=='mag_reassembly':
        originals=bins(i['bins']);r1,r2=concatenate(records(i['manifest']),work);(out/'reassembled').mkdir(exist_ok=True);results=[]
        for original in originals:
            folder=work/original.stem;folder.mkdir(exist_ok=True);index=folder/'index';sam=folder/'mapped.sam';bam=folder/'mapped.bam';collated=folder/'names.bam'
            run([tool('bowtie2-build'),original,index]);run([tool('bowtie2'),'-x',index,'-1',r1,'-2',r2,'-p',threads,'-S',sam])
            run([tool('samtools'),'view','-b','-F','12','-o',bam,sam]);run([tool('samtools'),'collate','-o',collated,bam])
            recruited1=folder/'R1.fastq';recruited2=folder/'R2.fastq'
            run([tool('samtools'),'fastq','-n','-1',recruited1,'-2',recruited2,'-0','/dev/null','-s','/dev/null',collated])
            if not recruited1.stat().st_size or not recruited2.stat().st_size:
                results.append({'mag_id':original.stem,'status':'skipped_no_paired_recruited_reads'});continue
            target=folder/'spades'
            if target.exists():shutil.rmtree(target)
            run([tool('spades.py'),'--careful','-1',recruited1,'-2',recruited2,'--phred-offset',str(p['phred_offset']),'-o',target,'-t',threads,'-m',str(p['memory_gb'])])
            shutil.copyfile(target/'contigs.fasta',out/'reassembled'/(original.stem+'.fasta'));results.append({'mag_id':original.stem,'status':'reassembled_not_quality_ranked'})
        dump(out/'reassembly.json',{'recruitment':'pairs with at least one mapped read; paired reads retained','results':results,'note':'SPAdes output is not automatically superior to the original bin; CheckM2 re-evaluation is required'})
    elif node=='mag_annotation':
        originals=bins(i['bins']);staged=work/'annotation-bins';staged.mkdir(exist_ok=True)
        for f in originals:shutil.copyfile(f,staged/(f.stem+'.fa'))
        env={**os.environ,'GTDBTK_DATA_PATH':plan['databases']['mag.gtdb']['path']}
        run([tool('gtdbtk'),'classify_wf','--genome_dir',staged,'--out_dir',out/'gtdb','--extension','fa','--cpus',threads],env=env)
        (out/'eggnog').mkdir(exist_ok=True)
        for f in originals:run([tool('emapper.py'),'-i',f,'--itype','genome','--genepred','prodigal','-m','diamond','--data_dir',plan['databases']['mag.eggnog']['path'],'--cpu',threads,'--output',f.stem,'--output_dir',out/'eggnog'])
        materialize_output_links(out)
    elif node in {'amplicon_asv','amplicon_taxonomy'}:
        if node=='amplicon_asv':
            rows=records(i['manifest']);normalized=[]
            if p['primers_removed']:normalized=rows
            else:
                for row in rows:
                    left=work/(row['sample_id']+'.R1.fastq.gz');right=work/(row['sample_id']+'.R2.fastq.gz')
                    run([tool('cutadapt'),'-g','^'+p['primer_forward'],'-G','^'+p['primer_reverse'],'--discard-untrimmed','--pair-filter','any','-j',threads,'-o',left,'-p',right,row['read1'],row['read2']]);normalized.append({'sample_id':row['sample_id'],'read1':str(left),'read2':str(right)})
            manifest=work/'amplicon-input.csv'
            with manifest.open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=['sample_id','read1','read2']);w.writeheader();w.writerows(normalized)
            i={**i,'manifest':str(manifest)}
        else:
            db=plan['databases']['16s.training'];i={**i,'training_fasta':str(Path(db['path'])/db['training_file'])}
        config=work/'dada2-config.json';dump(config,{'node':node,'inputs':i,'parameters':p,'output':str(out)})
        # The R worker uses base-R DCF input to avoid assuming an extra JSON package.
        dcf=work/'dada2-config.dcf';dcf.write_text('\n'.join(k+': '+str(v) for k,v in {**i,**p,'node':node,'output':str(out)}.items())+'\n')
        run([tool('Rscript'),extension/'dada2_worker.R',dcf])
    else:raise ValueError('未知扩展能力')
    import importlib.metadata
    versions={}
    for name in ['numpy','pandas','scipy','scikit-learn','matplotlib']:
        try:versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:pass
    dump(out/'adapter-methods.json',{'adapter_version':'0.3.0','node':node,'parameters':p,'source_revision_id':plan['source_revision_id'],'extension_revision_id':plan['extension_revision_id'],'real_execution':True,'scientific_environment':versions})


if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    main(sys.argv[1])
