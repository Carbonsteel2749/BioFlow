"""Opt-in DIY repairs: deterministic validation, private copies, bounded requeue."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
from pathlib import Path

from .llm_diagnostics import OllamaDiagnosticService
from .redaction import redact_log
from .task_repairs import PATCH_TARGETS, TaskRepairService


class DynamicRepairService:
    def __init__(self, settings, repository, *, diagnostic_service=None):
        self.settings, self.repository = settings, repository
        self.diagnostic = diagnostic_service or OllamaDiagnosticService(base_url=settings.ollama_url,
            model=settings.ollama_model, timeout_seconds=settings.ollama_timeout_seconds)

    def _cancelled(self, task_id):
        run = self.repository.get_run(task_id)
        return run is None or run['status'] == 'cancelled'

    def _pause(self, task_id, original, reason):
        self.repository.pause_run(task_id, original + '\nAI 受限修复：' + redact_log(reason)[:1200])

    def _root(self, run):
        root = Path(run['compiled_root']).resolve(strict=True)
        expected = (self.settings.state_root / 'workflow_runs' / run['task_id']).resolve()
        if not root.is_relative_to(expected):
            raise ValueError('compilation is outside this task')
        return root

    def _repairs(self, run, node_id):
        return TaskRepairService(self.settings.state_root / 'workflow_runs' / run['task_id'] / 'repairs' / node_id,
            self._root(run) / 'repair_source' / 'main.nf', validation_timeout_seconds=60)

    def _source(self, root):
        # Copy only the captured scripts and trusted tests, never live project code or NF work files.
        source = root / 'repair_source'
        if not source.exists():
            source.mkdir(mode=0o700)
            for name in ('bin', 'tests'):
                origin = root / name
                if origin.is_dir():
                    if any(p.is_symlink() for p in origin.rglob('*')):
                        raise ValueError('unsafe repair input')
                    shutil.copytree(origin, source / name)
            shutil.copyfile(root / 'main.nf', source / 'main.nf')
        return source

    def prepare_retry(self, task_id):
        run = self.repository.get_run(task_id)
        if not run or run['status'] != 'failed' or not run.get('auto_repair'):
            return False
        original = str(run.get('error_message') or 'Nextflow analysis failed')
        round_number = self.repository.reserve_repair(task_id)
        if round_number is None:
            self._pause(task_id, original, '已达到两轮上限，请人工检查日志。')
            return False
        root = self._root(run)
        record_root = root.parent / 'repair_attempts' / f'{round_number:03d}'
        record_root.mkdir(parents=True, mode=0o700)
        record = {'round': round_number, 'original_error': original, 'status': 'diagnosing'}
        try:
            node = next((item for item in run['nodes'] if item['status'] == 'failed' and item['node_id'] == run.get('failure_node_id')), None)
            if node is None:
                raise ValueError('no failed calculation node; launcher failures require manual inspection')
            node_id = node['node_id']
            summary = json.loads((root / 'summary.json').read_text(encoding='utf-8'))
            runtime = dict(summary['node_runtime_parameters'][node_id])
            # CPU directives live outside shell parameter bindings in the compiler.
            # Read this node's normalized resource settings, never sibling globals.
            resource_parameters = summary.get('node_parameters', {}).get(node_id, {})
            if 'threads' in resource_parameters:
                runtime['threads'] = resource_parameters['threads']
            state = {}
            if run.get('execution_root'):
                state = self._verified_execution(run)['nodes']
                runtime.update(state.get(node_id, {}).get('parameters', {}))
            log = Path(str(run.get('log_path') or ''))
            if not log.is_file() or log.resolve() != log.absolute() or not log.resolve().is_relative_to((self.settings.state_root/'logs'/task_id).resolve()):
                raise ValueError('task-scoped failure log unavailable')
            with log.open('rb') as stream:
                stream.seek(0, 2); stream.seek(max(0, stream.tell()-16000))
                excerpt = redact_log(stream.read().decode('utf-8', errors='replace'), input_root=str(self.settings.input_root))
            relative = summary.get('node_output_roots', {}).get(node_id, '')
            output_root = (self.settings.state_root/'outputs'/task_id).resolve()
            node_root = output_root/relative
            if relative and node_root.resolve() == node_root and node_root.is_relative_to(output_root):
                for stage_log in sorted((node_root/'logs').rglob('*.log'))[:8]:
                    if stage_log.is_file() and stage_log.resolve() == stage_log:
                        with stage_log.open('rb') as stream:
                            stream.seek(0,2); stream.seek(max(0,stream.tell()-4000))
                            excerpt += '\n' + redact_log(stream.read().decode('utf-8', errors='replace'), input_root=str(self.settings.input_root))
            excerpt = excerpt[-24000:]
            step = 'fastqc_raw' if node['node_type'] == 'fastqc' else node['node_type']
            source = self._source(root)
            context = {}
            target = PATCH_TARGETS.get(step, ('', ''))[0]
            active_source = Path(state.get(node_id, {}).get('script_root') or source/'bin').parent
            if target and (active_source / target).is_file():
                content = (active_source / target).read_bytes()
                context = {'target_path': target, 'base_sha256': hashlib.sha256(content).hexdigest(),
                    'source': content.decode('utf-8')[:10000]}
            diagnostic = self.diagnostic.diagnose_failure(step=step, log_excerpt=excerpt, exit_code=None,
                retry_count=round_number-1, parameters=runtime, script_context=context)
            record.update(node_id=node_id, diagnosis=diagnostic.to_dict())
            record['diagnosis'].pop('raw_model_output', None)
            if self._cancelled(task_id):
                return False
            if diagnostic.diagnostic_service_error or diagnostic.confidence < self.settings.retry_minimum_confidence:
                raise ValueError('诊断不可用或置信度不足，未执行修复')
            if diagnostic.classification in {'missing_database','corrupted_input','biological_threshold','missing_dependency','unknown'}:
                raise ValueError('该故障类型必须人工处理')
            if any(item.requires_user_confirmation for item in diagnostic.parameter_adjustments):
                raise ValueError('参数建议需要用户确认，未自动修改')
            repairs = self._repairs(run, node_id)
            plan = repairs.create_plan(task={'id':task_id, 'status':'failed','parameters':runtime,
                'retry_count':round_number-1}, step=step, diagnostic=diagnostic,
                redacted_log_excerpt=excerpt, cancel_check=lambda:self._cancelled(task_id))
            record.update(plan_version=plan['version'], status=plan['status'], reason=plan['decision_reason'])
            if plan['status'] != 'validated':
                raise ValueError(plan['decision_reason'])
            execution = repairs.execution_plan(task_id, retry_count=round_number)
            if execution is None or self._cancelled(task_id):
                return False
            runtime.update(execution['runtime_overrides'])
            previous = state.get(node_id, {})
            private = execution.get('workflow_path')
            state[node_id] = {'parameters':runtime, 'alias':summary['node_aliases'][node_id],
                'script_root': str(private.parent/'bin') if private else previous.get('script_root'),
                'digest':execution['plan_sha256'], 'version':plan['version'], 'round':round_number}
            self._write_execution(run, record_root, state)
            self.repository.set_execution_root(task_id, record_root)
            if self._cancelled(task_id):
                return False
            self.repository.resume_run(task_id)
            record['status'] = 'queued'
            return True
        except Exception as exc:
            record.update(status='paused', reason=f'{type(exc).__name__}: {exc}')
            if not self._cancelled(task_id):
                self._pause(task_id, original, record['reason'])
            return False
        finally:
            (record_root/'diagnosis.json').write_text(json.dumps(record, ensure_ascii=False, default=str, indent=2), encoding='utf-8')

    def _write_execution(self, run, destination, nodes):
        base = str(self._root(run) / 'nextflow.config')
        if any(char in base for char in "'\n\r\\$"):
            raise ValueError('unsafe configuration path')
        lines = [f"includeConfig '{base}'", 'process {']
        for item in nodes.values():
            alias = item['alias']
            if not re.fullmatch(r'[A-Za-z0-9_]+', alias):
                raise ValueError('unsafe process alias')
            parameters = item['parameters']
            encoded = base64.b64encode(json.dumps(parameters, sort_keys=True).encode()).decode()
            lines += [f"  withName: '{alias}' {{", f"    cpus = {int(parameters.get('threads', parameters.get('mag_threads', 1)))}",
                f"    ext.bioflow_parameters = new groovy.json.JsonSlurper().parseText(new String('{encoded}'.decodeBase64(), 'UTF-8'))",
                f"    ext.bioflow_repair_digest = '{item['digest']}'"]
            if parameters.get('mag_memory_gb'):
                lines.append(f"    memory = '{int(parameters['mag_memory_gb'])} GB'")
            if item.get('script_root'):
                script_root = str(item['script_root'])
                if any(char in script_root for char in "'\n\r\\$"):
                    raise ValueError('unsafe private script path')
                lines.append(f"    ext.bioflow_script_root = '{script_root}'")
            lines.append('  }')
        lines.append('}')
        content = ('\n'.join(lines)+'\n').encode()
        (destination/'repair.config').write_bytes(content)
        (destination/'execution.json').write_text(json.dumps({'nodes':nodes,
            'config_sha256':hashlib.sha256(content).hexdigest()}, sort_keys=True), encoding='utf-8')

    def _verified_execution(self, run):
        root = Path(run['execution_root'])
        if root.resolve() != root or not root.is_relative_to((self.settings.state_root/'workflow_runs'/run['task_id']/'repair_attempts').resolve()):
            raise ValueError('repair configuration escaped task root')
        value = json.loads((root/'execution.json').read_text(encoding='utf-8'))
        if hashlib.sha256((root/'repair.config').read_bytes()).hexdigest() != value['config_sha256']:
            raise ValueError('repair configuration checksum mismatch')
        return value

    def execution_config(self, run):
        if not run.get('execution_root'):
            return None
        state = self._verified_execution(run)
        for node_id, item in state['nodes'].items():
            repairs = self._repairs(run, node_id)
            plan = repairs.get_attempt(run['task_id'], item['version'])
            applied = any(event.get('event') == 'execution_started' for event in plan['events'])
            if plan['plan_sha256'] != item['digest'] or (plan['status'] not in {'validated','authorized','retry_claimed'} and not applied):
                raise ValueError('private repair validation no longer matches')
            if plan['proposal'].get('script_patch'):
                repairs._verify_bundle(repairs._attempt_path(run['task_id'], item['version']), plan['proposal'])
        return Path(run['execution_root'])/'repair.config'

    def mark_started(self, run):
        if run.get('execution_root'):
            for node_id, item in self._verified_execution(run)['nodes'].items():
                self._repairs(run, node_id).mark_event(run['task_id'], item['version'], 'execution_started', 'retry_claimed')
