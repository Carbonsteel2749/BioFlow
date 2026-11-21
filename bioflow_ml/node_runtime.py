"""Pinned Workflow adapters. No third-party imports or replacement algorithms."""
import hashlib
import json
import os
from pathlib import Path
import re
import types
import base64


RUNTIME_REVISION = 'bc048553867c4bddb4843bdbcc176904'
PINS = {
    'runtime': RUNTIME_REVISION,
    'scikit-bio': '4bbe122f68204e1e85c9924853f22502',
    'scientific-visualization': 'd4b851a6fd1d4df79dd6237dd14b661f',
    'bio-metagenomics-visualization': '344a4616dae64b76bffaee3f9f3dee41',
    'scibox-diagram': '8e2f1298fa62486583a6830e172affdd',
    'ngs-shotgun-metagenomics': '16fb58e5fa1543758f6e46bbaf2bc140',
    'ngs-amplicon-microbiome': '466c5f2990e5453aa19aa1b8662073b5',
    'scikit-learn': 'e0fad05048ee4de2b1b1ad432a2cb33e',
    'statistical-analysis': '9a3c1c6ebcc14549820a73cd36bd18e2',
}
REUSE = {
    'clr': ('microbiome_analysis/compositional.py', 'clr_transform'),
    'alpha_diversity': ('microbiome_analysis/diversity.py', 'alpha_diversity'),
    'pcoa': ('microbiome_analysis/diversity.py', 'pcoa'),
    'nmds': ('microbiome_analysis/diversity.py', 'nmds'),
    'permanova': ('microbiome_analysis/diversity.py', 'permanova'),
}
MAG_BLOCKED = {'assembly', 'binning', 'bin_refinement', 'bin_quantification', 'bin_reassembly', 'bin_annotation'}
INPUTS = {
    'fastq_input': ['manifest'],
    'fastqc': ['read1', 'read2', 'sample_id'],
    'fastp': ['read1', 'read2', 'sample_id'],
    'host_depletion': ['read1', 'read2', 'sample_id'],
    'taxonomy': ['read1', 'read2', 'sample_id', 'database_manifest'],
    'functional_annotation': ['read1', 'read2', 'sample_id', 'database_manifest'],
    'report': ['results_root', 'database_manifest', 'input_manifest', 'nextflow_work_root'],
}
TOOLS = {
    'fastq_input': ['bash', 'python3'],
    'fastqc': ['bash', 'python3', 'gzip', 'fastqc', 'java'],
    'fastp': ['bash', 'python3', 'gzip', 'fastp'],
    'host_depletion': ['bash', 'python3', 'gzip', 'bowtie2', 'samtools'],
    'taxonomy': ['bash', 'python3', 'kraken2', 'bracken'],
    'functional_annotation': ['bash', 'python3', 'gzip', 'humann', 'metaphlan', 'humann_regroup_table'],
    'report': ['bash', 'python3', 'multiqc'],
}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def load_module(path):
    # Avoid importlib's bytecode cache writes inside immutable published packages.
    module = types.ModuleType('pinned_parameters_' + sha(path)[:12])
    module.__file__ = str(path)
    exec(compile(Path(path).read_bytes(), str(path), 'exec'), module.__dict__)
    return module


def confined(value, roots):
    if not isinstance(value, str) or not value or any(c in value for c in '\n\r\x00\"\'`$'):
        raise ValueError('文件路径为空或包含不支持的字符')
    p = Path(value).resolve()
    if not Path(value).is_absolute() or not any(p.is_relative_to(Path(r).resolve()) for r in roots):
        raise ValueError('文件必须位于登记的共享数据目录内')
    return p


class NodeRuntime:
    def __init__(self, bundle, environment, input_roots, ml_root):
        self.bundle = Path(bundle)
        self.root = self.bundle / 'bioflow-metagenomics-node-runtime'
        self.environment = environment
        self.input_roots = input_roots
        self.ml_root = Path(ml_root)
        self.bindings = json.loads((self.root / 'references/node-bindings.json').read_text())

    def catalog(self):
        reuse = {}
        for name, (file, symbol) in REUSE.items():
            p = self.ml_root / file
            reuse[name] = {'file': file, 'symbol': symbol, 'sha256': sha(p) if p.is_file() else None}
        extensions=load_module(self.ml_root/'runtime_extensions.py') if (self.ml_root/'runtime_extensions.py').is_file() else None
        return {'source_revision_id': RUNTIME_REVISION, 'source_commit': self.bindings['source_commit'],
                'skill_pins': PINS, 'algorithm_reuse': reuse, 'nodes': list(self.bindings['bindings'].values()),
                'additional_capabilities':extensions.CONTRACTS if extensions else {},
                'workflow_extension_mapping':extensions.WORKFLOW_MAP if extensions else {},
                'extension_revision_id':self.environment.get('runtime_extension_revision_id'),
                'cutover': False}

    def preflight(self, node, payload):
        if (self.ml_root/'runtime_extensions.py').is_file():
            ext=load_module(self.ml_root/'runtime_extensions.py')
            if node in ext.CONTRACTS:return ext.preflight(self,node,payload)
        if node not in self.bindings['bindings']: raise ValueError('未知节点；16S 后端尚未接入')
        if not isinstance(payload, dict) or set(payload) - {'parameters', 'inputs', 'engine'}: raise ValueError('请求包含未声明字段')
        binding = self.bindings['bindings'][node]
        supplied = payload.get('parameters', {})
        if not isinstance(supplied, dict): raise ValueError('parameters 必须为对象')
        validator = load_module(self.root / 'scripts/backend/app/workflows/parameters.py')
        params = validator.validate_parameter_values(binding['parameters_schema'], supplied, owner=node)
        engine = payload.get('engine', 'cli')
        if engine not in {'cli', 'nextflow'}: raise ValueError('engine 仅支持 cli 或 nextflow')
        errors, inputs, evidence = [], {}, {}
        if node in MAG_BLOCKED: errors.append('MAG_CONTRACT_MISMATCH: 固定源码调用 MetaWRAP，尚未与目标工具链核对一致，禁止执行')
        given = payload.get('inputs', {})
        if not isinstance(given, dict): raise ValueError('inputs 必须为对象')
        allowed = set(INPUTS.get(node, []))
        if set(given) - allowed: raise ValueError('包含未声明输入字段')
        for key in INPUTS.get(node, []):
            if key not in given:
                errors.append('MISSING_INPUT: ' + key); continue
            if key == 'sample_id':
                if not isinstance(given[key], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', given[key]): raise ValueError('样本标识不合法')
                inputs[key] = given[key]; continue
            p = confined(given[key], self.input_roots)
            inputs[key] = str(p)
            if not p.exists(): errors.append('MISSING_FILE: ' + key); continue
            if key in {'results_root','nextflow_work_root'}:
                if not p.is_dir(): errors.append('EXPECTED_DIRECTORY: ' + key)
                elif key=='results_root':
                    resource=load_module(self.ml_root/'runtime_resources.py')
                    evidence[key]=resource.tree_evidence(p,self.input_roots)
            elif not p.is_file(): errors.append('EXPECTED_FILE: ' + key)
            else: evidence[key] = {'path': str(p), 'size': p.stat().st_size, 'sha256': sha(p)}
        # A manifest can contain additional paths. Validate their scope before the pinned script reads them.
        if node == 'fastq_input' and 'manifest' in evidence:
            import csv
            with Path(inputs['manifest']).open(encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f)
                if reader.fieldnames != ['sample_id', 'read1', 'read2']: errors.append('INVALID_MANIFEST_HEADER')
                else:
                    rows = list(reader)
                    if not rows: errors.append('EMPTY_MANIFEST')
                    identifiers=[row.get('sample_id','') for row in rows]
                    if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}',x or '') for x in identifiers):errors.append('INVALID_SAMPLE_ID')
                    if len(set(identifiers))!=len(identifiers):errors.append('DUPLICATE_SAMPLE_ID')
                    for i, row in enumerate(rows):
                        for mate in ['read1', 'read2']:
                            value = row.get(mate)
                            if not value: errors.append('EMPTY_MATE'); continue
                            p = Path(value)
                            if not p.is_absolute(): p = Path(inputs['manifest']).parent / p
                            p = confined(str(p), self.input_roots)
                            if not p.is_file(): errors.append('MISSING_MATE'); continue
                            evidence[f'manifest:{i}:{mate}'] = {'path': str(p), 'size': p.stat().st_size, 'sha256': sha(p)}
        requirements = list(TOOLS.get(node, []))
        if engine == 'nextflow':
            requirements += ['nextflow', 'java']
            if node not in {'fastqc', 'fastp'}: errors.append('NEXTFLOW_ADAPTER_NOT_VALIDATED_FOR_NODE')
        tools = {}
        for name in requirements:
            record = self.environment.get('tools', {}).get(name, {})
            p = Path(record.get('executable', '/nonexistent'))
            ready = p.is_absolute() and p.is_file() and os.access(p, os.X_OK) and bool(record.get('version'))
            if ready and record.get('sha256') and sha(p) != record['sha256']: ready = False
            minimum = {'fastp': (0,23), 'fastqc': (0,12)}.get(name)
            if minimum:
                version = re.search(r'\d+(?:\.\d+)+', str(record.get('version', '')))
                if not version or tuple(map(int, version.group().split('.'))) < minimum: ready = False
            tools[name] = {**record, 'ready': ready}
            if not ready: errors.append('TOOL_NOT_REGISTERED_OR_CHANGED: ' + name)
        databases = {}
        database_keys=list(binding['database_requirements'])
        if node=='functional_annotation':database_keys+=['function_reads.ko_mapping','function_reads.ec_mapping']
        resources=load_module(self.ml_root/'runtime_resources.py')
        for key in database_keys:
            record = self.environment.get('databases', {}).get(key, {})
            path = record.get('path')
            checks=resources.database_check(record) if record else ['DATABASE_NOT_REGISTERED']
            ready = not checks
            databases[key] = {**record, 'ready': ready, 'checks':checks}
            if not ready: errors.append('DATABASE_NOT_READY: ' + key)
        if 'database_manifest' in inputs and 'database_manifest' in evidence:
            errors.extend(resources.manifest_check(inputs['database_manifest'],self.environment,database_keys))
        if node not in INPUTS and node not in MAG_BLOCKED: errors.append('CLI_ADAPTER_PENDING')
        if engine not in self.environment.get('enabled_adapters', {}).get(node, []): errors.append('ADAPTER_ACCEPTANCE_PENDING')
        plan = {'node_type': node, 'source_revision_id': RUNTIME_REVISION, 'engine': engine,
                'node_version': binding['version'],
                'method_id': binding['method_id'], 'method_version': binding['method_version'],
                'parameters': params, 'inputs': inputs, 'input_evidence': evidence,
                'expected_outputs': binding['outputs'], 'script': binding['script'], 'module': binding['module'],
                'tools': tools, 'databases': databases, 'errors': sorted(set(errors)), 'dry_run': True,
                'ready': not errors, 'skill_pins': PINS}
        plan['fingerprint'] = digest(plan)
        return plan

    def command(self, plan, job_root, source_root=None):
        """Only reviewed pinned entrypoints; shell=False at the process boundary."""
        node = plan['node_type'];root = Path(source_root or self.root);folder = Path(job_root)
        out = folder / 'output';state = folder / 'state';i = plan['inputs'];p = plan['parameters']
        argv = [plan['tools']['bash']['executable'], str(root / plan['script']), '--outdir', str(out), '--task-id', folder.name, '--state-root', str(state)]
        if node == 'fastq_input': argv += ['--manifest', i['manifest']]
        elif node in {'fastqc', 'fastp', 'host_depletion', 'taxonomy', 'functional_annotation'}:
            argv += ['--sample', i['sample_id'], '--r1', i['read1'], '--r2', i['read2']]
            for key, value in p.items(): argv += ['--' + key.replace('_', '-'), str(value).lower() if isinstance(value, bool) else str(value)]
            if 'database_manifest' in i: argv += ['--database-manifest', i['database_manifest']]
            if node=='host_depletion':argv+=['--host-index',self.environment['databases']['host.grch38_bowtie2']['index_prefix']]
        elif node=='report':
            for key in INPUTS['report']:argv+=['--'+key.replace('_','-'),i[key]]
            argv+=['--parameters-base64',base64.b64encode(json.dumps({'source_revision_id':RUNTIME_REVISION}).encode()).decode(),
                   '--run-name',folder.name,'--project-root',str(root/'scripts')]
        else: raise ValueError('该节点执行适配尚未通过验收')
        return argv

    def nextflow_files(self, plan, folder, source_root):
        """Single reviewed paired-read process. Workflow graph remains with its owner."""
        folder = Path(folder);root = Path(source_root)
        node = plan['node_type'];binding = self.bindings['bindings'][node]
        if node not in {'fastqc', 'fastp'}: raise ValueError('Nextflow 节点尚未适配')
        process_name = {'fastqc': 'FASTQC_RAW', 'fastp': 'FASTP'}[node]
        params = {'outdir': str(folder / 'output'), 'task_id': folder.name,
                  'threads': plan['parameters']['threads']}
        for key, value in plan['parameters'].items():
            params[binding['parameters_schema'][key].get('nextflow_parameter', key)] = value
        # Inactive fastp flags still appear in its shell wrapper; preserve registered defaults.
        for key, spec in binding['parameters_schema'].items():
            params.setdefault(spec.get('nextflow_parameter', key), spec.get('default'))
        source = "nextflow.enable.dsl=2\ninclude { " + process_name + " } from '" + str(root / binding['module']) + "'\n"
        i = plan['inputs']
        source += "workflow { " + process_name + "(Channel.of(tuple('" + i['sample_id'] + "', file('" + i['read1'] + "'), file('" + i['read2'] + "')))) }\n"
        (folder / 'main.nf').write_text(source)
        (folder / 'nextflow.config').write_text("process { withName: '" + process_name + "' { ext.bioflow_script_root = '" + str(root / 'scripts/workflow/bin') + "' } }\n")
        (folder / 'parameters.json').write_text(json.dumps(params))
        return [plan['tools']['nextflow']['executable'], 'run', str(folder / 'main.nf'), '-c', str(folder / 'nextflow.config'), '-params-file', str(folder / 'parameters.json'), '-work-dir', str(folder / 'work')]
