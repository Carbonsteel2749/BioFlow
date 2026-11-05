"""Typed execution wiring; biological processes remain the existing modules."""
import hashlib
import json
from pathlib import Path

from . import compiler as c
from .input_contracts import SAFE_ID


def compile_typed_workflow(graph,registry,project_root,bindings):
    roots={n.id:n for n in graph.nodes if n.type in registry and registry[n.type].category=='input'}
    if not roots or set(bindings)!=set(roots):
        raise c.WorkflowCompilationError('invalid_input_bindings','Every input root requires exactly one binding; no extra bindings allowed')
    for node_id,node in roots.items():
        binding=bindings[node_id]
        if not SAFE_ID.fullmatch(node_id) or not SAFE_ID.fullmatch(binding.get('dataset_id','')):
            raise c.WorkflowCompilationError('invalid_input_bindings','Unsafe binding identifier')
        if binding.get('contract',{}).get('data_type')!=registry[node.type].outputs[0].data_type:
            raise c.WorkflowCompilationError('incompatible_input_binding','Input binding type does not match source port')
    validation=c.validate_workflow_graph(graph,registry)
    if not validation.can_execute:
        raise c.WorkflowCompilationError('invalid_workflow_graph','Typed workflow requires valid connections and confirmed risks',validation=validation)
    if any(n.type=='report' for n in graph.nodes):
        raise c.WorkflowCompilationError('typed_report_unsupported','Legacy REPORT requires a reads CSV; use node outputs or matrix_summary for typed workflows')
    order=c._deterministic_topological_order(graph)
    nodes={n.id:n for n in graph.nodes}
    normalized=c._normalized_node_parameters(graph,registry,validation)
    versions=c._node_method_versions(graph,registry)
    aliases={n.id:c._node_alias(n.type,n.id) for n in graph.nodes}
    runtime=c._node_runtime_parameters(graph,registry,normalized)
    for key in roots:runtime.setdefault(key,{})
    out_roots={key:f'nodes/{alias}' for key,alias in aliases.items()}
    identity={key:{'dataset_id':b['dataset_id'],'contract':b['contract']} for key,b in bindings.items()}
    # Cohort identity follows the scientific payload, never companion reads or
    # a task-wide label. Keep biological module tuple contracts unchanged.
    cohorts={}
    for key in order:
        node=nodes[key]
        if key in roots:
            contract=bindings[key]['contract']
            if contract.get('kind') in {'contigs','mags'}:
                cohort=contract.get('cohort_id')
                if not isinstance(cohort,str) or not SAFE_ID.fullmatch(cohort):
                    raise c.WorkflowCompilationError('invalid_input_bindings','A safe declared cohort is required')
                cohorts[key]=cohort
        elif node.type=='assembly':cohorts[key]='coassembly'
        else:
            incoming=[e for e in graph.edges if e.target_node==key]
            primary=[e for e in incoming if e.target_port in {'assembly','bins','mags'}]
            inherited={cohorts[e.source_node] for e in (primary or incoming) if e.source_node in cohorts}
            if len(inherited)==1:cohorts[key]=inherited.pop()
            elif len(inherited)>1:
                raise c.WorkflowCompilationError('ambiguous_cohort_lineage','Node combines distinct cohort lineages')
    payload=c._canonical_graph_payload(graph,normalized,registry)
    payload['input_bindings']=identity
    payload['node_cohorts']=cohorts
    digest=lambda value:hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    graph_hash=digest(payload)
    fingerprints={}
    for key in order:
        fingerprints[key]=digest({'parameters':normalized[key],'method':versions[key],
            'input':identity.get(key),'cohort':cohorts.get(key),'parents':sorted(fingerprints[e.source_node] for e in graph.edges if e.target_node==key)})
    parameters=c._map_nextflow_parameters(order,nodes,normalized,validation,registry)
    parameters['typed_inputs']={key:{**value,'source_root':str(value.get('source_root',''))} for key,value in bindings.items()}
    parameters['bioflow_node_cohorts']=cohorts
    parameters['bioflow_nodes']={key:{'type':nodes[key].type,'alias':aliases[key],'parameters':normalized[key],
        'runtime_parameters':runtime[key],'method_version':versions[key],'output_root':out_roots[key]} for key in order}
    includes=[];lines=['workflow {','    binding_manifest_ch = Channel.value(file(params.input_manifest, checkIfExists: true))']
    needs_db=any(n.type in c._DATABASE_MANIFEST_INPUTS for n in graph.nodes)
    if needs_db:lines.append('    database_manifest_ch = Channel.value(file(params.database_manifest, checkIfExists: true))')
    outputs={}
    for key in order:
        node=nodes[key];definition=registry[node.type];alias=aliases[key]
        module='workflow/modules/input_datasets.nf' if key in roots else definition.nextflow_module
        process='INPUT_DATASET' if key in roots else definition.process_name
        c._module_path(project_root,module)
        includes.append(f"include {{ {process} as {alias} }} from './{module}'")
        incoming={e.target_port:outputs[e.source_node][e.source_port] for e in graph.edges if e.target_node==key}
        if key in roots:
            contract=bindings[key]['contract'];port=definition.outputs[0].id
            lines.extend([f"    {alias}(Channel.value(tuple('{key}', params.typed_inputs['{key}'].contract.files.collect {{ file(params.typed_inputs['{key}'].source_root + '/' + it.path, checkIfExists: true) }})), binding_manifest_ch)"])
            channel=c._channel_name(key,port)
            if contract['kind']=='reads':
                expr=f"{alias}.out.dataset.flatMap {{ dir -> params.typed_inputs['{key}'].contract.samples.collect {{ row -> tuple(row.sample_id, dir.resolve(row.sample_id + '.R1.input' + (row.read1.endsWith('.gz') ? '.fastq.gz' : '.fastq')), dir.resolve(row.sample_id + '.R2.input' + (row.read2.endsWith('.gz') ? '.fastq.gz' : '.fastq'))) }} }}"
            elif contract['kind']=='contigs':expr=f"{alias}.out.dataset.map {{ dir -> tuple(params.typed_inputs['{key}'].contract.cohort_id, dir.resolve('contigs.fasta')) }}"
            elif contract['kind']=='mags':expr=f"{alias}.out.dataset.map {{ dir -> tuple(params.typed_inputs['{key}'].contract.cohort_id, dir.resolve('mags')) }}"
            else:expr=f"{alias}.out.dataset"
            lines.append(f'    {channel} = {expr}');outputs[key]={port:channel};continue
        if node.type=='assembly':
            args=[incoming['reads']+'.map { sid, r1, r2 -> [r1,r2] }.flatten().collect()']
        elif node.type=='binning':
            source=next(nodes[e.source_node] for e in graph.edges if e.target_node==key and e.target_port=='assembly')
            if source.type=='contigs_input':
                args=[incoming['assembly']+".combine("+incoming['reads']+".map { sid, r1, r2 -> [r1,r2] }.flatten().collect().map { files -> [reads: files] }).map { cohort, assembly, companion -> tuple(assembly, companion.reads) }"]
            else:args=[incoming['assembly']]
        elif node.type=='bin_annotation':
            source=next(nodes[e.source_node] for e in graph.edges if e.target_node==key and e.target_port=='mags')
            channel=incoming['mags']
            if source.type!='mag_input':channel+=f".map {{ bins, assembly, reads -> tuple(params.bioflow_node_cohorts['{key}'], bins) }}"
            args=[channel,'database_manifest_ch']
        elif node.type=='bin_quantification':
            manifest_alias=alias+'_READS'
            includes.append(f"include {{ CONNECTED_READS_MANIFEST as {manifest_alias} }} from './workflow/modules/input_datasets.nf'")
            lines.append(f"    {manifest_alias}({incoming['reads']}.collect(flat: false).map {{ rows -> tuple(rows.collect {{ it[0] }}, rows.collectMany {{ [it[1],it[2]] }}) }})")
            combined=incoming['bins']+f'.map {{ bins, assembly, inherited -> [bins: bins, assembly: assembly] }}.combine({manifest_alias}.out.inputs.map {{ manifest, actual -> [reads: actual] }}).map {{ bins, companion -> tuple(bins.bins, bins.assembly, companion.reads) }}'
            args=[combined,manifest_alias+'.out.manifest','database_manifest_ch']
        else:
            args=[incoming[p.id] for p in definition.inputs if p.id in incoming]
            if node.type in c._DATABASE_MANIFEST_INPUTS:args.append('database_manifest_ch')
        lines.append(f"    {alias}({', '.join(args)})")
        emits={'report':'report'} if node.type=='matrix_summary' else c._OUTPUT_EMITS[node.type]
        outputs[key]={port:f'{alias}.out.{emit}' for port,emit in emits.items()}
    lines.append('}')
    config=c._render_nextflow_config(order,nodes,registry,aliases,normalized,project_root,runtime,out_roots,fingerprints)
    return c.CompiledWorkflow(source='\n'.join(['nextflow.enable.dsl=2',*includes,*lines,'']),nextflow_config=config,
        graph_hash=graph_hash,registry_version=c.REGISTRY_VERSION,compiler_version=c.COMPILER_VERSION,
        topological_order=order,node_aliases=aliases,node_parameters=normalized,nextflow_parameters=parameters,
        node_output_channels=outputs,node_method_versions=versions,node_runtime_parameters=runtime,
        node_output_roots=out_roots,node_fingerprints=fingerprints,node_cohorts=cohorts)
