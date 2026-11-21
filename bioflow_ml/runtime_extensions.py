"""Explicit additional contracts; legacy fixed node definitions remain untouched."""
import json
import os
from pathlib import Path


VERSION='0.3.0'
def integer(default,minimum=1,maximum=256):return {'type':'integer','default':default,'minimum':minimum,'maximum':maximum}
def number(default,minimum=0,maximum=100):return {'type':'number','default':default,'minimum':minimum,'maximum':maximum}
def choice(default,values):return {'type':'string','default':default,'enum':values}
THREADS={'threads':integer(2,1,16)}
PHRED={'type':'integer','default':33,'enum':[33,64]}
PAIRED=['read1','read2','sample_id']
CONTRACTS={
 'mag_assembly':{'inputs':['manifest'],'tools':['megahit','spades.py'],'parameters':{**THREADS,'assembler':choice('megahit',['megahit','metaspades']),'memory_gb':integer(4,1,32),'min_contig_length':integer(1000,200,10000),'phred_offset':PHRED},'outputs':['assembly.fasta','assembly.json']},
 'mag_binning':{'inputs':['assembly','manifest'],'tools':['bowtie2','bowtie2-build','samtools','jgi_summarize_bam_contig_depths','metabat2'],'parameters':{**THREADS,'min_contig_length':integer(2500,1500,10000),'seed':integer(42,1,2147483647)},'outputs':['depth.tsv','binning.json','bins/*.fa']},
 'mag_quality':{'inputs':['bins'],'directories':['bins'],'tools':['checkm2','diamond','prodigal'],'databases':['mag.checkm2'],'parameters':{**THREADS,'completeness':number(70),'contamination':number(5)},'outputs':['quality_report.tsv','quality-filter.json','filtered_bins/*.fa']},
 'mag_quantification':{'inputs':['bins','manifest'],'directories':['bins'],'tools':['coverm','minimap2','samtools'],'parameters':{**THREADS},'outputs':['coverm.tsv','abundance-unit.json']},
 'mag_reassembly':{'inputs':['bins','manifest'],'directories':['bins'],'tools':['bowtie2','bowtie2-build','samtools','spades.py'],'parameters':{**THREADS,'memory_gb':integer(4,1,32),'phred_offset':PHRED},'outputs':['reassembled/*.fasta','reassembly.json']},
 'mag_annotation':{'inputs':['bins'],'directories':['bins'],'tools':['gtdbtk','emapper.py','diamond','prodigal','hmmsearch','pplacer','skani'],'databases':['mag.gtdb','mag.eggnog'],'parameters':{**THREADS},'outputs':['gtdb/*.summary.tsv','eggnog/*.emapper.annotations']},
 'amplicon_asv':{'inputs':['manifest'],'tools':['Rscript','cutadapt'],'parameters':{**THREADS,'primers_removed':{'type':'boolean','required':True},'primer_forward':{'type':'string','default':''},'primer_reverse':{'type':'string','default':''},'trunc_len_forward':integer(0,0,1000),'trunc_len_reverse':integer(0,0,1000),'max_ee':number(2,0.1,100),'min_overlap':integer(12,4,200),'seed':integer(42,1,2147483647)},'outputs':['asv_table.csv','asv_sequences.fasta','read_tracking.csv','dada2-session.txt']},
 'amplicon_taxonomy':{'inputs':['asv_sequences'],'tools':['Rscript'],'databases':['16s.training'],'parameters':{**THREADS,'min_boot':integer(80,1,100),'seed':integer(42,1,2147483647)},'outputs':['asv_taxonomy.csv','dada2-session.txt']},
 'clr':{'inputs':['table'],'tools':['scientific_python'],'parameters':{'pseudocount':number(0.000001,0.000000000001,1)},'outputs':['clr.csv']},
 'alpha_diversity':{'inputs':['table'],'tools':['scientific_python'],'parameters':{'metric':choice('shannon',['observed_features','shannon','simpson','chao1'])},'outputs':['alpha_diversity.csv']},
 'pcoa':{'inputs':['table'],'tools':['scientific_python'],'parameters':{'n_components':integer(2,1,10)},'outputs':['coordinates.csv','eigenvalues.csv']},
 'nmds':{'inputs':['table'],'tools':['scientific_python'],'parameters':{'n_components':integer(2,1,10),'random_state':integer(42,0,2147483647),'n_init':integer(4,1,20),'max_iter':integer(300,10,2000)},'outputs':['coordinates.csv','metrics.json']},
 'permanova':{'inputs':['table','groups'],'tools':['scientific_python'],'parameters':{'permutations':integer(999,1,9999),'random_state':integer(42,0,2147483647)},'outputs':['permanova.json']},
 'reads_plots':{'inputs':['metrics_dir'],'directories':['metrics_dir'],'tools':['scientific_python'],'parameters':{},'outputs':['*.png','*.provenance.json']},
 'taxonomy_plots':{'inputs':['table'],'tools':['scientific_python'],'parameters':{'top_n':integer(20,1,100)},'outputs':['*.png','*.provenance.json']},
 'functional_plots':{'inputs':['gene_families','ko','ec','pathways'],'tools':['scientific_python'],'parameters':{'top_n':integer(20,1,100)},'outputs':['*.png','*.provenance.json']},
 'mag_plots':{'inputs':['mags','abundance'],'tools':['scientific_python'],'parameters':{'top_n':integer(20,1,100)},'outputs':['*.png','*.provenance.json']},
}
WORKFLOW_MAP={'assembly':'mag_assembly','binning':'mag_binning','bin_refinement':'mag_quality','bin_quantification':'mag_quantification','bin_reassembly':'mag_reassembly','bin_annotation':'mag_annotation'}
import runpy
CONTRACTS.update(runpy.run_path(str(Path(__file__).with_name('analysis_contracts.py')))['CONTRACTS'])
CONTRACTS.update(runpy.run_path(str(Path(__file__).with_name('amplicon_contracts.py')))['CONTRACTS'])


def preflight(runtime,node,payload):
    base=runtime
    if set(payload)-{'inputs','parameters','engine'}:raise ValueError('请求包含未声明字段')
    if payload.get('engine','cli')!='cli':raise ValueError('新增能力通过 CLI 适配，保留 Workflow 负责节点编排')
    spec=CONTRACTS[node]
    # Load the original fixed parameter validator, without writing into its package.
    import importlib.util
    module_spec=importlib.util.spec_from_file_location('adapter_runtime_helpers',base.ml_root/'node_runtime.py')
    helper=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(helper)
    validator=helper.load_module(base.root/'scripts/backend/app/workflows/parameters.py')
    resources=helper.load_module(base.ml_root/'runtime_resources.py')
    supplied=payload.get('parameters',{})
    if not isinstance(supplied,dict):raise ValueError('parameters 必须为对象')
    params=validator.validate_parameter_values(spec['parameters'],supplied,owner=node)
    if any(isinstance(v,str) and any(c in v for c in '\r\n\x00') for v in params.values()):raise ValueError('参数不能包含换行或空字符')
    if node=='amplicon_asv':
        import re
        for k in ['primer_forward','primer_reverse']:
            if (params[k] or not params['primers_removed']) and not re.fullmatch('[ACGTRYSWKMBDHVNacgtryswkmbdhvn]{6,100}',params[k]):raise ValueError('引物必须为 6～100 个 IUPAC 碱基；未去引物时必须提供前后向引物')
    given=payload.get('inputs',{})
    if not isinstance(given,dict) or set(given)-set(spec['inputs']):raise ValueError('存在未声明输入')
    inputs={};evidence={};errors=[]
    for key in spec['inputs']:
        if key not in given:errors.append('MISSING_INPUT:'+key);continue
        if key=='sample_id':
            import re
            if not isinstance(given[key],str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,100}',given[key]):raise ValueError('样本标识无效')
            inputs[key]=given[key];continue
        path=helper.confined(given[key],base.input_roots)
        if any(c in str(path) for c in '\r\n\x00'):raise ValueError('输入路径不能包含控制字符')
        inputs[key]=str(path)
        if key in spec.get('directories',[]):
            if not path.is_dir():errors.append('MISSING_DIRECTORY:'+key)
            else:evidence[key]=resources.tree_evidence(path,base.input_roots)
        elif not path.is_file():errors.append('MISSING_FILE:'+key)
        else:evidence[key]={'path':str(path),'size':path.stat().st_size,'sha256':helper.sha(path)}
    if 'manifest' in evidence:
        inherited=base.preflight('fastq_input',{'inputs':{'manifest':inputs['manifest']}})
        evidence.update(inherited['input_evidence']);errors+=inherited['errors']
    requirements=list(spec['tools'])
    if 'scientific_python' not in requirements:requirements.append('scientific_python')
    if node=='mag_assembly':requirements=[('megahit' if params['assembler']=='megahit' else 'spades.py'),'scientific_python']
    if spec.get('worker_python'):requirements=[x for x in requirements if x!='scientific_python']
    tools={}
    for key in requirements:
        record=base.environment.get('tools',{}).get(key,{})
        path=Path(record.get('executable','/nonexistent'))
        ready=path.is_absolute() and path.is_file() and os.access(path,os.X_OK) and bool(record.get('version'))
        if ready and record.get('sha256')!=helper.sha(path):ready=False
        tools[key]={**record,'ready':ready}
        if not ready:errors.append('TOOL_NOT_REGISTERED_OR_CHANGED:'+key)
    if spec.get('worker_python'):tools['scientific_python']=tools[spec['worker_python']]
    # The worker itself uses the registered scientific Python, even for external tool chains.
    if not tools.get('scientific_python',{}).get('ready'):errors.append('WORKER_PYTHON_NOT_REGISTERED_OR_CHANGED')
    databases={}
    for key in spec.get('databases',[]):
        record=base.environment.get('databases',{}).get(key,{})
        checks=resources.database_check(record) if record else ['DATABASE_NOT_REGISTERED']
        file_field={'mag.checkm2':'database_file','16s.training':'training_file','16s.silva':'training_file'}.get(key)
        if key=='16s.silva' and str(record.get('taxonomy_system','')).upper()!='SILVA':checks.append('SILVA_TAXONOMY_SYSTEM_REQUIRED')
        if file_field and record:
            value=record.get(file_field)
            target=(Path(record['path'])/value).resolve() if isinstance(value,str) else None
            if not target or not target.is_relative_to(Path(record['path']).resolve()) or not target.is_file() or not target.stat().st_size:checks.append('DATABASE_FILE_NOT_READY:'+file_field)
        databases[key]={**record,'ready':not checks,'checks':checks}
        if checks:errors.append('DATABASE_NOT_READY:'+key)
    rid=base.environment.get('runtime_extension_revision_id')
    if not rid:errors.append('EXTENSION_VERSION_NOT_REGISTERED')
    if 'cli' not in base.environment.get('enabled_adapters',{}).get(node,[]):errors.append('ADAPTER_ACCEPTANCE_PENDING')
    inspection={}
    if spec.get('analysis') and not errors:
        import subprocess
        probe=subprocess.run([tools['scientific_python']['executable'],'-B',str(base.ml_root/'analysis_nodes.py'),'--preflight'],input=json.dumps({'node_type':node,'inputs':inputs,'parameters':params}),text=True,capture_output=True,timeout=30,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','OMP_NUM_THREADS':'2'})
        try:inspection=json.loads(probe.stdout)
        except ValueError:inspection={'error':'输入检查器未返回有效 JSON: '+probe.stderr[-400:]}
        if probe.returncode or inspection.get('error'):errors.append('ANALYSIS_NOT_APPLICABLE:'+inspection.get('error','输入检查失败'))
    return {'node_type':node,'node_version':VERSION,'method_id':node,'method_version':VERSION,'engine':'cli',
            'extension':True,'extension_revision_id':rid,'source_revision_id':helper.RUNTIME_REVISION,
            'script':'scripts/runtime_worker.py','module':None,'inputs':inputs,'input_evidence':evidence,'parameters':params,
            'tools':tools,'databases':databases,'expected_outputs':spec['outputs'],'errors':sorted(set(errors)),'input_inspection':inspection,
            'ready':not errors,'dry_run':True,'skill_pins':helper.PINS}
