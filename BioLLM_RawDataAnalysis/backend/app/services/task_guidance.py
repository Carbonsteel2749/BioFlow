"""Read-only, bounded evidence for submission and task activity. No tool execution."""
import csv
import io
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .input_lifecycle import input_operation
from .resource_preflight import ResourcePreflight
from .tasks import TaskValidationError

TOOLS = {'validate': 'FASTQ validation', 'fastqc_raw': 'FastQC', 'fastp': 'fastp',
         'host_depletion': 'Bowtie2', 'taxonomy': 'Kraken2 / Bracken',
         'functional_annotation': 'HUMAnN', 'report': 'MultiQC / report packaging'}
PROCESSES = {'VALIDATE': 'validate', 'FASTQC_RAW': 'fastqc_raw', 'FASTQC': 'fastqc_raw',
             'FASTP': 'fastp', 'HOST_DEPLETION': 'host_depletion', 'TAXONOMY': 'taxonomy',
             'FUNCTIONAL_ANNOTATION': 'functional_annotation', 'REPORT': 'report',
             **{name.upper(): name for name in ('assembly', 'binning', 'bin_refinement',
                'bin_quantification', 'bin_reassembly', 'bin_annotation')}}


def _time(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def _read(path, root, limit):
    """Never follow evidence outside its task directory or read an unbounded log."""
    try:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            return None
        if not path.is_file() or path.stat().st_size > limit:
            return None
        with path.open('rb') as handle:
            data = handle.read(limit + 1)
        return data.decode('utf-8-sig') if len(data) <= limit else None
    except (OSError, UnicodeError):
        return None


def _trace_rows(text, warnings):
    try:
        for index, row in enumerate(csv.DictReader(io.StringIO(text), delimiter='\t')):
            if index >= 100000:
                warnings.append('计算轨迹超出行数上限，样本计数不是完整统计。')
                return
            yield row
    except csv.Error:
        warnings.append('部分计算轨迹格式无效，样本计数不是完整统计。')


class TaskGuidance:
    def __init__(self, settings, service, datasets, metadata, scheduler):
        self.settings, self.service = settings, service
        self.datasets, self.metadata, self.scheduler = datasets, metadata, scheduler

    @input_operation
    def review(self, request):
        if request.auto_repair and not self.settings.auto_retry_enabled:
            raise TaskValidationError('服务器尚未允许 AI 自动修复；请关闭任务开关。')
        groups = {'status': 'not_saved', 'version': 0, 'counts': {}}
        manifest = self.service.validate_manifest_path(request.manifest_path)
        dataset_id = request.dataset_id
        if request.dataset_id:
            try:
                selected = self.datasets.reuse(request.dataset_id)
            except KeyError as exc:
                raise TaskValidationError('数据集不存在') from exc
            if selected['manifest_path'] != request.manifest_path:
                raise TaskValidationError('数据集与样本清单不匹配')
        else:
            # Creation snapshots metadata by canonical manifest, even for legacy
            # callers which omit the optional dataset identity.
            with self.metadata.repository._connect() as db:
                dataset = db.execute('SELECT * FROM datasets WHERE manifest_path=?', (str(manifest),)).fetchone()
                if dataset:
                    try:
                        self.metadata._verify_manifest(dataset)
                    except ValueError as exc:
                        raise TaskValidationError(str(exc)) from exc
                    dataset_id = dataset['id']
        if dataset_id:
            saved = self.metadata.get(dataset_id)
            if saved['version']:
                groups = {'status': 'saved', 'version': saved['version'], 'sha256': saved['sha256'],
                          'counts': dict(Counter(row['group'] for row in saved['rows']))}
        parameters = self.service.prepare_parameters(request.parameters.model_dump(mode='json', exclude_unset=True))
        resources = ResourcePreflight(self.settings).require(manifest_path=request.manifest_path,
            threads=max(parameters['threads'], parameters['mag_threads']) if parameters['enable_mags'] else parameters['threads'],
            enable_mags=parameters['enable_mags'])
        return {'name': request.name, 'sample_count': resources['input']['sample_count'],
                'parameters': parameters, 'method_versions': parameters['method_versions'],
                'database_profile': self.service.capabilities().get('database_profile'),
                'auto_repair': request.auto_repair, 'groups': groups, 'resources': resources,
                'pair_validation': 'paths_only_content_pending', 'dataset_id': dataset_id}

    def activity(self, task_id):
        task = self.service.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        samples, warnings = set(), []
        try:
            manifest = self.service.validate_manifest_path(task['manifest_path'])
            text = _read(manifest, self.settings.input_root, 16 * 1024**2)
            if text is None:
                raise ValueError('manifest unavailable')
            for index, row in enumerate(csv.DictReader(io.StringIO(text))):
                if index >= 100000:
                    raise ValueError('manifest limit')
                sample = row['sample_id'].strip()
                if not sample or sample in samples:
                    raise ValueError('invalid or duplicate sample')
                samples.add(sample)
        except (ValueError, OSError, KeyError, csv.Error):
            samples.clear()
            warnings.append('样本清单未能完整读取，不推算样本完成比例。')
        outputs = self.settings.state_root / 'outputs'
        root = outputs / task_id
        safe_root = not root.is_symlink() and root.resolve().is_relative_to(outputs.resolve())
        states = {step['name']: {} for step in task['steps']}
        if safe_root:
            trace_path = root / 'trace.tsv'
            attempt = _time(task.get('started_at'))
            trace_fresh = task['status'] != 'queued'
            try:
                # Nextflow is configured with trace.overwrite=true. A retained
                # previous-attempt file must not suppress new live status.
                if attempt and trace_path.stat().st_mtime < attempt.timestamp():
                    trace_fresh = False
            except OSError:
                trace_fresh = False
            trace = _read(trace_path, root, 8 * 1024**2) if trace_fresh else None
            if trace is not None:
                for row in _trace_rows(trace, warnings):
                    name = row.get('name') or ''
                    process = (row.get('process') or name.split(' (', 1)[0]).rsplit(':', 1)[-1].upper()
                    step = PROCESSES.get(process)
                    tag = re.search(r'\(([^()]*)\)$', name)
                    sample = tag.group(1) if tag else None
                    status = (row.get('status') or '').upper()
                    if step in states and sample in samples and status in {'COMPLETED', 'CACHED', 'FAILED', 'ABORTED', 'RUNNING'}:
                        states[step][sample] = status
            elif trace_path.exists():
                warnings.append('计算轨迹尚未更新到本轮，或不可完整读取；以下计数仅采用可校验的状态文件。')
            status_root = root / 'status'
            if task['status'] != 'queued' and not status_root.is_symlink() and status_root.resolve().is_relative_to(root.resolve()):
                # Limit directory traversal as well as per-file reads.
                for index, path in enumerate(status_root.glob('*.json')):
                    if index >= 10000:
                        warnings.append('状态文件超出读取上限；未观察样本不代表尚未计算。')
                        break
                    try:
                        payload = json.loads(_read(path, root, 64 * 1024) or '{}')
                        step, sample = payload.get('step'), payload.get('sample_id')
                        started, attempt = _time(payload.get('started_at')), _time(task.get('started_at'))
                        if (payload.get('task_id') != task_id or step not in states or sample not in samples
                                or not started or (attempt and started < attempt.replace(microsecond=0))):
                            continue
                        if states[step].get(sample) in {'COMPLETED', 'CACHED', 'FAILED', 'ABORTED'}:
                            continue
                        status = {'running': 'RUNNING', 'completed': 'COMPLETED', 'succeeded': 'COMPLETED',
                                  'failed': 'FAILED'}.get(payload.get('status'))
                        if status:
                            states[step][sample] = status
                    except (ValueError, TypeError, AttributeError):
                        continue
        result = []
        for step, observed in states.items():
            if task['status'] not in {'running', 'validating'}:
                observed = {sample: status for sample, status in observed.items() if status != 'RUNNING'}
            result.append({'name': step, 'tool': TOOLS.get(step, step),
                'completed_samples': sorted(s for s, status in observed.items() if status == 'COMPLETED'),
                'cached_samples': sorted(s for s, status in observed.items() if status == 'CACHED'),
                'running_samples': sorted(s for s, status in observed.items() if status == 'RUNNING') if task['status'] in {'running', 'validating'} else [],
                'failed_samples': sorted(s for s, status in observed.items() if status in {'FAILED', 'ABORTED'}),
                'unobserved_count': len(samples - observed.keys()) if samples else None})
        snapshot = self.scheduler.snapshot()
        key = 'standard:' + task_id
        position = snapshot['queued'].index(key) + 1 if key in snapshot['queued'] else None
        building = False
        if safe_root:
            destination = root / 'deliverables'
            if not destination.is_symlink() and destination.resolve().is_relative_to(root.resolve()):
                building = any(p.is_file() and not p.is_symlink() for p in destination.glob('.full-archive-*.tmp'))
        phase = 'archiving' if building else {'queued': 'queued', 'running': 'computing', 'validating': 'computing'}.get(task['status'], task['status'])
        log_root = self.settings.state_root / 'logs' / task_id
        times = []
        if not log_root.is_symlink() and log_root.resolve().is_relative_to((self.settings.state_root / 'logs').resolve()):
            for step in states:
                path = log_root / f'{step}.log'
                try:
                    if path.is_file() and not path.is_symlink():
                        times.append(path.stat().st_mtime)
                except OSError:
                    pass
        return {'phase': phase, 'sample_count': len(samples) if samples else None, 'steps': result,
                'eta_seconds': None, 'latest_log_at': datetime.fromtimestamp(max(times), timezone.utc).isoformat() if times else None,
                'queue': {'limit': snapshot['limit'], 'running': len(snapshot['running']), 'position': position},
                'archive': {'state': 'building' if building else 'published_unverified' if task.get('result_archive') else 'not_ready'},
                'warnings': warnings, 'evidence_scope': 'observed_processes_not_wall_clock_progress'}
