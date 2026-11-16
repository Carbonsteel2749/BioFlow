"""Real shell/normalizer boundary, replacing only the expensive metaWRAP tool."""
import csv
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

PROJECT=Path(__file__).resolve().parents[2]
NORMALIZER=PROJECT/'workflow/bin/mag/normalize_mag_abundance.py'


def fixture(tmp_path,legacy=False):
    reads=[];manifest=tmp_path/'samples.csv'
    with manifest.open('w',newline='') as handle:
        writer=csv.writer(handle);writer.writerow(['sample_id','read1','read2'])
        for sid in ('sample_A','sample_B'):
            pair=[]
            for mate in (1,2):
                path=tmp_path/f'{sid}.R{mate}.{"clean" if legacy else "input"}.fastq.gz'
                with gzip.open(path,'wt') as f:f.write(f'@{sid}/{mate}\nACGT\n+\nIIII\n')
                pair.append(path);reads.append(path)
            writer.writerow([sid,*([str(tmp_path/f'{sid}_raw_{m}.fastq') for m in (1,2)] if legacy else map(str,pair))])
    read_list=tmp_path/'reads.list';read_list.write_text('\n'.join(str(p) for p in reversed(reads))+'\n')
    database=tmp_path/'database.json';database.write_text(json.dumps({'database_profile':'tiny','resolved_manifest_sha256':'a'*64}))
    return manifest,read_list,database,reads


@pytest.mark.parametrize('legacy',[False,True])
def test_staging_maps_each_real_mate_to_the_correct_sample(tmp_path,legacy):
    import importlib.util
    spec=importlib.util.spec_from_file_location('quant_inputs',PROJECT/'workflow/bin/mag/quantification_inputs.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    manifest,read_list,_,reads=fixture(tmp_path,legacy)
    staged=tmp_path/'staged';aliases=tmp_path/'sample_aliases.csv'
    module.stage_quantification_inputs(manifest,read_list,staged,aliases)
    with aliases.open() as handle:rows=list(csv.DictReader(handle))
    assert {r['sample_id'] for r in rows}=={'sample_A','sample_B'}
    for row in rows:
        for mate in (1,2):
            file=Path(row[f'read{mate}'])
            assert file.name.endswith(f'_{mate}.fastq')
            assert file.read_text().startswith('@'+row['sample_id']+f'/{mate}\n')
    assert len((staged/'staged_reads.list').read_text().splitlines())==4


def test_generated_alias_cannot_shadow_a_real_sample_id(tmp_path):
    import importlib.util
    spec=importlib.util.spec_from_file_location('quant_inputs',PROJECT/'workflow/bin/mag/quantification_inputs.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    manifest,read_list,database,_=fixture(tmp_path)
    manifest.write_text(manifest.read_text().replace('sample_A,','bioflow_sample_000002,'))
    staged=tmp_path/'staged';aliases=tmp_path/'aliases.csv'
    module.stage_quantification_inputs(manifest,read_list,staged,aliases)
    with aliases.open() as handle:rows=list(csv.DictReader(handle))
    labels=[Path(row['read1']).name.removesuffix('_1.fastq') for row in rows]
    source=tmp_path/'source.tsv';source.write_text('Genomic bins\t'+'\t'.join(labels)+'\nMAG1\t4\t2\n')
    output=tmp_path/'normalized.tsv'
    result=subprocess.run([sys.executable,str(NORMALIZER),'--source',str(source),'--sample-manifest',str(aliases),
        '--database-manifest',str(database),'--output',str(output),'--task-id','tiny'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    with output.open() as handle:normalized=list(csv.DictReader(handle,delimiter='\t'))
    assert {row['sample_id'] for row in normalized}=={'bioflow_sample_000002','sample_B'}


@pytest.mark.parametrize('header,values',[
    ('unknown_sample\tsample_B','4\t2'),
    ('sample_A\tsample_A','4\t2'),
    ('sample_A','4'),
    ('sample_A\tsample_B','4'),
])
def test_normalizer_rejects_unmatched_duplicate_missing_or_truncated_samples(tmp_path,header,values):
    manifest,_,database,_=fixture(tmp_path)
    source=tmp_path/'source.tsv';source.write_text('Genomic bins\t'+header+'\nMAG1\t'+values+'\n')
    output=tmp_path/'normalized.tsv'
    result=subprocess.run([sys.executable,str(NORMALIZER),'--source',str(source),'--sample-manifest',str(manifest),
        '--database-manifest',str(database),'--output',str(output),'--task-id','tiny'],capture_output=True,text=True)
    assert result.returncode!=0,'Unmatched/ambiguous/incomplete quantification must not succeed'
    assert not output.exists(),'No partial normalized table should be published'


@pytest.mark.skipif(sys.platform=='win32',reason='Real bash/metaWRAP process boundary runs on Linux')
@pytest.mark.parametrize('legacy',[False,True])
def test_real_quantification_shell_preserves_multisample_identity(tmp_path,legacy):
    manifest,read_list,database,reads=fixture(tmp_path,legacy)
    bins=tmp_path/'bins';bins.mkdir();(bins/'MAG1.fa').write_text('>c\nACGT\n')
    assembly=tmp_path/'assembly.fa';assembly.write_text('>c\nACGT\n')
    tools=tmp_path/'tools';tools.mkdir()
    producer=tools/'metawrap'
    producer.write_text('''#!/usr/bin/env python3
import csv,json,os,sys
from pathlib import Path
if sys.argv[1:]==['--version']:
    print('test producer');raise SystemExit(0)
assert sys.argv[1]=='quant_bins'
args=sys.argv[2:];out=Path(args[args.index('-o')+1])
reads=[Path(p) for p in args if p.endswith('.fastq')]
forward=[p for p in reads if p.name.endswith('_1.fastq')]
assert len(reads)==4 and len(forward)==2, 'Producer requires two true paired samples'
values={}
for r1 in forward:
    label=r1.name[:-len('_1.fastq')];r2=r1.with_name(label+'_2.fastq')
    assert r2 in reads and r2.is_file()
    sample=r1.read_text().splitlines()[0].removeprefix('@').removesuffix('/1')
    assert r2.read_text().splitlines()[0]=='@'+sample+'/2'
    values[label]=4 if sample=='sample_A' else 2
with (out/'bin_abundance_table.tab').open('w') as handle:
    labels=list(reversed(sorted(values)))
    handle.write('Genomic bins\\t'+'\\t'.join(labels)+'\\nMAG1\\t'+'\\t'.join(str(values[x]) for x in labels)+'\\n')
''')
    producer.chmod(0o755)
    output=tmp_path/'out'
    result=subprocess.run(['bash',str(PROJECT/'workflow/bin/mag/bin_quantification.sh'),'--bins',str(bins),
        '--assembly',str(assembly),'--reads-list',str(read_list),'--sample-manifest',str(manifest),
        '--database-manifest',str(database),'--outdir',str(output),'--threads','1','--task-id','tiny'],
        env={**os.environ,'PATH':str(tools)+os.pathsep+os.environ['PATH']},capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    with (output/'sample_mag_abundance.tsv').open() as handle:rows=list(csv.DictReader(handle,delimiter='\t'))
    assert {(r['sample_id'],r['mag_id'],r['abundance']) for r in rows}=={('sample_A','MAG1','4'),('sample_B','MAG1','2')}
    assert len(rows)==2
    provenance=json.loads((output/'sample_mag_abundance.provenance.json').read_text())
    assert provenance['tool_name']=='MetaWRAP quant_bins'
