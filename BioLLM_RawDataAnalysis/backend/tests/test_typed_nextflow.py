"""Opt-in real Nextflow input adapters; no biological tools or databases."""
import json
import csv
import os
from pathlib import Path
import shutil
import subprocess
import pytest

from backend.app.config import Settings
from backend.app.services.input_datasets import InputDatasetService
from backend.app.workflows.compiler import compile_workflow
from backend.app.workflows.graph import WorkflowGraph
from backend.app.workflows.materializer import materialize_compilation
from backend.app.workflows.registry import default_node_registry

PROJECT=Path(__file__).resolve().parents[2]
pytestmark=pytest.mark.skipif(os.environ.get('BIOFLOW_TEST_NEXTFLOW')!='1',reason='Set BIOFLOW_TEST_NEXTFLOW=1 to run real tiny Nextflow tests')


@pytest.mark.parametrize('kind',['reads','contigs','mags','matrix'])
def test_actual_nextflow_adapter(tmp_path,kind):
    executable=shutil.which('nextflow')
    if not executable:pytest.fail('Opt-in Nextflow executable is unavailable')
    settings=Settings(input_root=tmp_path/'incoming',state_root=tmp_path/'state',workflow_path=PROJECT/'workflow/main.nf')
    settings.prepare();datasets=InputDatasetService(settings);folder=datasets.root/'example';folder.mkdir(parents=True)
    if kind=='reads':
        for mate in (1,2):(folder/f'r{mate}.fq').write_text(f'@r/{mate}\nACGT\n+\nIIII\n')
        spec={'kind':'reads','processing_status':'host_removed','processing_note':'external','confirmed':True,
              'samples':[{'sample_id':'s1','read1':'r1.fq','read2':'r2.fq'}]}
        source_type='host_removed_reads_input';expected='s1.R1.input.fastq'
    elif kind=='contigs':
        (folder/'c.fa').write_text('>c1\nACGTN\n');spec={'kind':kind,'cohort_id':'joint','fasta':'c.fa'}
        source_type='contigs_input';expected='contigs.fasta'
    elif kind=='mags':
        (folder/'m.fa').write_text('>c1\nACGTN\n');spec={'kind':kind,'cohort_id':'joint','mags':[{'mag_id':'MAG1','fasta':'m.fa'}]}
        source_type='mag_input';expected='mags/MAG1.fa'
    else:
        (folder/'m.tsv').write_text('sample_id\tfeature_id\tvalue\ns1\tK1\t4\ns2\tK1\t2\n')
        spec={'kind':kind,'table':'m.tsv','feature_type':'KO','unit':'counts','normalization':'none'}
        source_type='abundance_input';expected='source_payload/m.tsv'
    record=datasets.register('example',spec)
    snapshot=datasets.snapshot({'source':'example'},'tiny')
    nodes=[{'id':'source','type':source_type}];edges=[]
    if kind=='matrix':
        nodes.append({'id':'summary','type':'matrix_summary'})
        edges.append({'id':'e','source_node':'source','source_port':'matrix','target_node':'summary','target_port':'matrix'})
    elif kind in ('reads','mags'):
        port='reads' if kind=='reads' else 'mags'
        nodes.append({'id':'annotation','type':'taxonomy' if kind=='reads' else 'bin_annotation'})
        edges.append({'id':'e','source_node':'source','source_port':port,'target_node':'annotation','target_port':port})
    graph=WorkflowGraph.model_validate({'schema_version':'1.0','nodes':nodes,'edges':edges})
    compiled=compile_workflow(graph,default_node_registry(),project_root=PROJECT,
        input_contracts={'source':{**record,'source_root':str(folder)}})
    artifacts=materialize_compilation(compiled,task_id='tiny',state_root=settings.state_root,workflow_root=PROJECT/'workflow')
    # Replace expensive biology only after immutable production compilation:
    # actual adapters and exact downstream tuple signatures still execute.
    if kind=='reads':
        (artifacts.root/'workflow/modules/taxonomy.nf').write_text('''process TAXONOMY {
          input:
          tuple val(sample_id), path(read1), path(read2)
          path database_manifest
          output:
          path 'report.tsv', emit: reports
          script:
          assert sample_id == 's1'
          """
          grep -q '@r/1' "${read1}"
          grep -q '@r/2' "${read2}"
          echo verified > report.tsv
          """
        }
        ''')
    elif kind=='mags':
        (artifacts.root/'workflow/modules/bin_annotation.nf').write_text('''process BIN_ANNOTATION {
          input:
          tuple val(cohort_id), path(bins)
          path database_manifest
          output:
          path 'taxonomy.tsv', emit: mag_taxonomy
          path 'functions.tsv', emit: mag_functions
          script:
          assert cohort_id == 'joint'
          """
          grep -q 'ACGTN' "${bins}/MAG1.fa"
          echo verified > taxonomy.tsv
          echo verified > functions.tsv
          """
        }
        ''')
    command=[executable,'-c',str(artifacts.config_path),'run',str(artifacts.source_path),'-params-file',str(artifacts.parameters_path),
        '-work-dir',str(tmp_path/'work'),'--input_manifest',str(snapshot),'--outdir',str(tmp_path/'output'),'--task_id','tiny','-ansi-log','false']
    if kind in ('reads','mags'):
        database=tmp_path/'database.json';database.write_text('{}')
        command.extend(['--database_manifest',str(database)])
    run=subprocess.run(command+['-with-trace',str(tmp_path/'first.trace')],cwd=tmp_path,text=True,capture_output=True,timeout=180)
    assert run.returncode==0,run.stdout+'\n'+run.stderr
    outputs=list({path.resolve() for path in (tmp_path/'work').glob('**/dataset/'+expected)});assert len(outputs)==1
    assert outputs[0].stat().st_size>0
    if kind=='matrix':
        assert list((tmp_path/'output').glob('**/feature_means.png'))
        resumed=subprocess.run(command+['-resume','-with-trace',str(tmp_path/'resume.trace')],cwd=tmp_path,text=True,capture_output=True,timeout=180)
        assert resumed.returncode==0,resumed.stdout+'\n'+resumed.stderr
        with (tmp_path/'resume.trace').open() as handle:trace=list(csv.DictReader(handle,delimiter='\t'))
        assert len(trace)==2 and all(row['status']=='CACHED' for row in trace)
        changed_folder=datasets.root/'changed';changed_folder.mkdir()
        (changed_folder/'m.tsv').write_text('sample_id\tfeature_id\tvalue\ns1\tK1\t6\ns2\tK1\t2\n')
        changed=datasets.register('changed',spec)
        changed_snapshot=datasets.snapshot({'source':'changed'},'tiny2')
        new_compiled=compile_workflow(graph,default_node_registry(),project_root=PROJECT,
            input_contracts={'source':{**changed,'source_root':str(changed_folder)}})
        assert new_compiled.graph_hash!=compiled.graph_hash
        new_artifacts=materialize_compilation(new_compiled,task_id='tiny2',state_root=settings.state_root,workflow_root=PROJECT/'workflow')
        new_command=[executable,'-c',str(new_artifacts.config_path),'run',str(new_artifacts.source_path),'-params-file',str(new_artifacts.parameters_path),
            '-work-dir',str(tmp_path/'work'),'--input_manifest',str(changed_snapshot),'--outdir',str(tmp_path/'changed-output'),
            '--task_id','tiny2','-ansi-log','false','-resume','-with-trace',str(tmp_path/'changed.trace')]
        rerun=subprocess.run(new_command,cwd=tmp_path,text=True,capture_output=True,timeout=180)
        assert rerun.returncode==0,rerun.stdout+'\n'+rerun.stderr
        with (tmp_path/'changed.trace').open() as handle:trace=list(csv.DictReader(handle,delimiter='\t'))
        assert len(trace)==2 and all(row['status']=='COMPLETED' for row in trace)
        result=next((tmp_path/'changed-output').glob('**/canonical.tsv')).read_text()
        assert 's1\tK1\t6\tcounts' in result


def test_external_contigs_actual_tuple_with_companion_reads(tmp_path):
    settings=Settings(input_root=tmp_path/'incoming',state_root=tmp_path/'state',workflow_path=PROJECT/'workflow/main.nf')
    settings.prepare();datasets=InputDatasetService(settings);bindings={}
    for name in ('contigs','reads'):
        folder=datasets.root/name;folder.mkdir(parents=True)
        if name=='contigs':
            (folder/'c.fa').write_text('>c\nACGT\n');spec={'kind':'contigs','cohort_id':'joint','fasta':'c.fa'}
        else:
            for mate in (1,2):(folder/f'r{mate}.fq').write_text(f'@r/{mate}\nACGT\n+\nIIII\n')
            spec={'kind':'reads','processing_status':'clean','processing_note':'external','confirmed':True,
                'samples':[{'sample_id':'s1','read1':'r1.fq','read2':'r2.fq'}]}
        bindings[name]={**datasets.register(name,spec),'source_root':str(folder)}
    snapshot=datasets.snapshot({'contigs':'contigs','reads':'reads'},'tiny')
    graph=WorkflowGraph.model_validate({'schema_version':'1.0','nodes':[{'id':'contigs','type':'contigs_input'},
        {'id':'reads','type':'clean_reads_input'},{'id':'bins','type':'binning'},
        {'id':'refine','type':'bin_refinement'},{'id':'quant','type':'bin_quantification'}],
        'edges':[{'id':'a','source_node':'contigs','source_port':'assembly','target_node':'bins','target_port':'assembly'},
                 {'id':'r','source_node':'reads','source_port':'reads','target_node':'bins','target_port':'reads'},
                 {'id':'b','source_node':'bins','source_port':'bins','target_node':'refine','target_port':'bins'},
                 {'id':'q','source_node':'refine','source_port':'bins','target_node':'quant','target_port':'bins'},
                 {'id':'qr','source_node':'reads','source_port':'reads','target_node':'quant','target_port':'reads'}]})
    compiled=compile_workflow(graph,default_node_registry(),project_root=PROJECT,input_contracts=bindings)
    artifacts=materialize_compilation(compiled,task_id='tiny',state_root=settings.state_root,workflow_root=PROJECT/'workflow')
    # Isolate expensive biology while exercising production compiler and real
    # Nextflow path staging at the exact existing BINNING tuple boundary.
    (artifacts.root/'workflow/modules/binning.nf').write_text('''process BINNING {
      input:
      tuple path(assembly), path(reads)
      output:
      tuple path('binA'), path('binB'), path('binC'), path(assembly), path(reads), emit: bins
      script:
      assert reads.size() == 2
      """
      grep -q 'ACGT' "${assembly}"
      grep -q 'IIII' "${reads[0]}"
      grep -q 'IIII' "${reads[1]}"
      mkdir binA binB binC
      echo actual-contigs-and-reads > verified.txt
      """
    }
    ''')
    (artifacts.root/'workflow/modules/bin_refinement.nf').write_text('''process BIN_REFINEMENT {
      input:
      tuple path(a), path(b), path(c), path(assembly), path(reads)
      output:
      tuple path(a), path(assembly), path(reads), emit: refined
      script:
      """
      echo refinement-boundary
      """
    }
    ''')
    (artifacts.root/'workflow/modules/bin_quantification.nf').write_text('''process BIN_QUANTIFICATION {
      input:
      tuple path(bins), path(assembly), path(reads)
      path sample_manifest
      path database_manifest
      output:
      path 'quantified.tsv', emit: abundance
      script:
      assert reads.size() == 2
      """
      grep -q 'ACGT' "${assembly}"
      grep -q 'IIII' "${reads[0]}"
      grep -q 'IIII' "${reads[1]}"
      grep -q 's1,' "${sample_manifest}"
      echo actual-connected-reads > quantified.tsv
      """
    }
    ''')
    command=['nextflow','-c',str(artifacts.config_path),'run',str(artifacts.source_path),'-params-file',str(artifacts.parameters_path),
        '-work-dir',str(tmp_path/'work'),'--input_manifest',str(snapshot),'--outdir',str(tmp_path/'output'),'--task_id','tiny','-ansi-log','false']
    database=tmp_path/'database.json';database.write_text('{}');command.extend(['--database_manifest',str(database)])
    result=subprocess.run(command,cwd=artifacts.root,text=True,capture_output=True,timeout=180)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    assert len(list((tmp_path/'work').glob('**/verified.txt')))==1
    assert len(list((tmp_path/'work').glob('**/quantified.tsv')))==1


@pytest.mark.parametrize('reassembly',[False,True])
def test_two_contig_branches_and_direct_mag_annotation_keep_actual_cohorts(tmp_path,reassembly):
    from backend.tests.test_typed_final_lineage import branched
    graph,_=branched(reassembly)
    settings=Settings(input_root=tmp_path/'incoming',state_root=tmp_path/'state',workflow_path=PROJECT/'workflow/main.nf')
    settings.prepare();datasets=InputDatasetService(settings);bindings={}
    for node in graph.nodes:
        if default_node_registry()[node.type].category!='input':continue
        folder=datasets.root/node.id;folder.mkdir(parents=True)
        if node.type in {'contigs_input','mag_input'}:
            (folder/'c.fa').write_text('>c\nACGT\n')
            cohort={'a':'cohort_A','b':'cohort_B','d':'direct_C'}[node.id[0]]
            spec={'kind':'contigs','cohort_id':cohort,'fasta':'c.fa'} if node.type=='contigs_input' else {
                'kind':'mags','cohort_id':cohort,'mags':[{'mag_id':'MAG1','fasta':'c.fa'}]}
        else:
            for mate in (1,2):(folder/f'r{mate}.fq').write_text(f'@r/{mate}\nACGT\n+\nIIII\n')
            spec={'kind':'reads','processing_status':'host_removed','processing_note':'fixture unverified','confirmed':True,
                'samples':[{'sample_id':'s1','read1':'r1.fq','read2':'r2.fq'}]}
        bindings[node.id]={**datasets.register(node.id,spec),'source_root':str(folder)}
    snapshot=datasets.snapshot({k:k for k in bindings},'branches')
    compiled=compile_workflow(graph,default_node_registry(),project_root=PROJECT,input_contracts=bindings)
    artifacts=materialize_compilation(compiled,task_id='branches',state_root=settings.state_root,workflow_root=PROJECT/'workflow')
    modules=artifacts.root/'workflow/modules'
    (modules/'binning.nf').write_text('''process BINNING {
      input:
      tuple path(assembly), path(reads)
      output:
      tuple path('binA'), path('binB'), path('binC'), path(assembly), path(reads), emit: bins
      script:
      assert reads.size() == 2
      """
      grep -q ACGT "${assembly}"
      mkdir binA binB binC
      printf '>c\\nACGT\\n' > binA/MAG1.fa
      """
    }''')
    (modules/'bin_refinement.nf').write_text('''process BIN_REFINEMENT {
      input:
      tuple path(a), path(b), path(c), path(assembly), path(reads)
      output:
      tuple path(a), path(assembly), path(reads), emit: refined
      script:
      """
      grep -q ACGT "${a}/MAG1.fa"
      """
    }''')
    (modules/'bin_reassembly.nf').write_text('''process BIN_REASSEMBLY {
      input:
      tuple path(bins), path(assembly), path(reads)
      output:
      tuple path(bins), path(assembly), path(reads), emit: reassembled
      script:
      """
      grep -q ACGT "${bins}/MAG1.fa"
      """
    }''')
    expected_cohorts=', '.join("'%s':'%s'" % (compiled.node_aliases[n],v) for n,v in [('aannotation','cohort_A'),('bannotation','cohort_B'),('directann','direct_C')])
    (modules/'bin_annotation.nf').write_text('''process BIN_ANNOTATION {
      input:
      tuple val(cohort_id), path(bins)
      path database_manifest
      output:
      path 'mag_annotation.tsv', emit: mag_taxonomy
      path 'mag_functions.tsv', emit: mag_functions
      script:
      assert cohort_id == [EXPECTED_COHORTS][task.process.tokenize(':')[-1]]
      """
      grep -q ACGT "${bins}/MAG1.fa"
      printf 'cohort_id\\n${cohort_id}\\n' > mag_annotation.tsv
      cp mag_annotation.tsv mag_functions.tsv
      """
    }'''.replace('EXPECTED_COHORTS',expected_cohorts))
    database=tmp_path/'database.json';database.write_text('{}')
    command=['nextflow','-c',str(artifacts.config_path),'run',str(artifacts.source_path),'-params-file',str(artifacts.parameters_path),
        '-work-dir',str(tmp_path/'work'),'--input_manifest',str(snapshot),'--outdir',str(tmp_path/'output'),
        '--database_manifest',str(database),'--task_id','branches','-ansi-log','false']
    result=subprocess.run(command,cwd=artifacts.root,text=True,capture_output=True,timeout=180)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    tables=list((tmp_path/'work').glob('**/mag_annotation.tsv'))
    assert len(tables)==3
    assert {p.read_text().splitlines()[1] for p in tables}=={'cohort_A','cohort_B','direct_C'}
