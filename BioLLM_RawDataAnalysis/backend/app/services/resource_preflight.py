"""Live admission observations and explicitly heuristic (not quota) storage estimates."""
from __future__ import annotations

import csv
import hashlib
import os
import shutil
import sqlite3
import time
from pathlib import Path

from ..config import Settings
from .tasks import TaskService, TaskValidationError
from .resumable_uploads import reserved_upload_bytes

MAX_HINT_BYTES = 2**53 - 1
MIB = 1024**2


def _read_limit(path: Path) -> str:
    try:
        return path.read_text(encoding='ascii').strip()
    except (OSError, UnicodeError):
        return ''


def _cpuset_count(value: str) -> int | None:
    try:
        intervals = []
        for item in value.split(','):
            ends = item.split('-')
            first, last = int(ends[0]), int(ends[-1])
            if len(ends) > 2 or first < 0 or last < first:
                return None
            intervals.append((first, last))
        # Count unions without expanding large CPU ranges.
        count, right = 0, -1
        for first, last in sorted(intervals):
            count += max(0, last - max(first, right + 1) + 1)
            right = max(right, last)
        return count or None
    except ValueError:
        return None


def effective_cpu_count(*, cgroup_root: Path = Path('/sys/fs/cgroup'),
                        proc_cgroup: Path = Path('/proc/self/cgroup')) -> int:
    """Integer worker budget constrained by affinity and v1/v2 ancestor limits."""
    limits = [max(1, os.cpu_count() or 1)]
    try:
        limits.append(max(1, len(os.sched_getaffinity(0))))
    except (AttributeError, OSError):
        pass
    roots = {cgroup_root}
    controllers = [('cpu', cgroup_root/'cpu'), ('cpu', cgroup_root/'cpu,cpuacct'),
                   ('cpuset', cgroup_root/'cpuset')]
    roots.update(root for _, root in controllers)
    for line in _read_limit(proc_cgroup).splitlines():
        fields = line.split(':', 2)
        if len(fields) != 3:
            continue
        relative = Path(fields[2].lstrip('/'))
        if '..' in relative.parts:
            continue
        selected = [cgroup_root] if fields[0] == '0' and not fields[1] else [
            root for controller, root in controllers if controller in fields[1].split(',')]
        for base in selected:
            current = base/relative
            while True:
                roots.add(current)
                if current == base:
                    break
                current = current.parent
    for root in roots:
        for name in ('cpuset.cpus.effective', 'cpuset.effective_cpus', 'cpuset.cpus'):
            cpus = _cpuset_count(_read_limit(root/name))
            if cpus:
                limits.append(cpus)
        quota = _read_limit(root/'cpu.max').split()
        try:
            if len(quota) == 2 and quota[0] != 'max' and int(quota[0]) > 0 and int(quota[1]) > 0:
                limits.append(max(1, int(quota[0]) // int(quota[1])))
            v1_quota = int(_read_limit(root/'cpu.cfs_quota_us') or '-1')
            v1_period = int(_read_limit(root/'cpu.cfs_period_us') or '0')
            if v1_quota > 0 and v1_period > 0:
                limits.append(max(1, v1_quota // v1_period))
        except ValueError:
            pass
    return min(limits)


def _filesystem_snapshot(path: Path) -> dict:
    current = path.resolve()
    while not current.exists():
        if current == current.parent:
            raise OSError('storage root is unavailable')
        current = current.parent
    usage = shutil.disk_usage(current)
    # Linux st_dev identifies shared storage even through bind mounts/symlinks.
    identity = str(current.stat().st_dev)
    if os.name == 'nt':
        identity = current.anchor.lower() + ':' + identity
    return {'filesystem_id': hashlib.sha256(identity.encode()).hexdigest()[:16],
            'free_bytes': usage.free, 'total_bytes': usage.total}


class ResourcePreflight:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _manifest_size(self, raw_path: str) -> tuple[int, int]:
        approved = self.settings.input_root.resolve(strict=True)
        try:
            candidate = Path(raw_path).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise TaskValidationError('manifest_path is invalid') from exc
        # Authorize before strict existence checks: do not disclose whether an
        # arbitrary file outside input_root exists.
        if not candidate.is_relative_to(approved):
            raise TaskValidationError('manifest_path is outside the approved input root')
        manifest = TaskService(self.settings, None).validate_manifest_path(raw_path)
        if manifest.stat().st_size > 16 * MIB:
            raise TaskValidationError('manifest is too large for resource preflight')
        size = samples = 0
        identifiers, reads = set(), set()
        try:
            with manifest.open(encoding='utf-8-sig', newline='') as handle:
                for row in csv.DictReader(handle):
                    samples += 1
                    if samples > 100000:
                        raise TaskValidationError('manifest has too many samples')
                    if None in row or not all((row.get(key) or '').strip() for key in ('sample_id', 'read1', 'read2')):
                        raise TaskValidationError('manifest contains an invalid sample row')
                    sample = row['sample_id'].strip()
                    if sample in identifiers:
                        raise TaskValidationError('manifest contains duplicate sample identifiers')
                    identifiers.add(sample)
                    for role in ('read1', 'read2'):
                        path = Path(row[role].strip()).expanduser()
                        path = (manifest.parent/path if not path.is_absolute() else path).resolve()
                        if not path.is_relative_to(approved):
                            raise TaskValidationError('manifest reads are outside the approved input root')
                        if not path.is_file() or path.stat().st_size == 0:
                            raise TaskValidationError('manifest reads must be existing nonempty regular files')
                        if not path.name.lower().endswith(('.fastq', '.fq', '.fastq.gz', '.fq.gz')):
                            raise TaskValidationError('manifest reads must be FASTQ files')
                        if path in reads:
                            raise TaskValidationError('manifest reuses a read file')
                        reads.add(path)
                        size += path.stat().st_size
        except (OSError, UnicodeError, csv.Error, RuntimeError) as exc:
            raise TaskValidationError('manifest or read files cannot be inspected') from exc
        if not samples:
            raise TaskValidationError('manifest must contain at least one sample')
        return size, samples

    def inspect(self, *, manifest_path: str | None = None, input_bytes: int | None = None,
                threads: int | None = None, enable_mags: bool = False) -> dict:
        blockers, warnings = [], []
        maximum = effective_cpu_count()
        recommended = min(4, maximum)
        if threads is not None and (isinstance(threads, bool) or not isinstance(threads, int) or threads < 1):
            raise TaskValidationError('threads must be a positive integer')
        if threads is not None and threads > maximum:
            blockers.append({'code': 'threads_exceed_cpu', 'message': f'Requested threads exceed the effective CPU limit ({maximum}).'})
        if input_bytes is not None and (isinstance(input_bytes, bool) or not isinstance(input_bytes, int) or not 0 <= input_bytes <= MAX_HINT_BYTES):
            raise TaskValidationError('input_bytes must be a nonnegative safe integer')
        source, sample_count = 'unknown', None
        raw = input_bytes or 0
        if manifest_path:
            raw, sample_count = self._manifest_size(manifest_path)
            source = 'server_manifest'
        elif input_bytes is not None:
            source = 'client_hint'
            warnings.append({'code': 'client_hint', 'message': 'Before upload, input size is a client hint; submission remeasures server files.'})
        else:
            warnings.append({'code': 'input_unknown', 'message': 'Select files or a server manifest to estimate task storage.'})
        # These are planning envelopes, not scientifically calibrated bounds.
        # Work products and published outputs coexist; archive copies both + raw.
        work_min, work_max = raw * (6 if enable_mags else 3) + 256*MIB, raw * (48 if enable_mags else 12) + 1024*MIB
        output_min, output_max = raw * (2 if enable_mags else 1) + 256*MIB, raw * (12 if enable_mags else 4) + 1024*MIB
        archive_min, archive_max = raw + work_min + output_min, raw + work_max + output_max
        additional_raw = raw if source == 'client_hint' else 0
        estimate = {'raw_bytes': raw, 'raw_already_uploaded': source == 'server_manifest',
                    'additional_raw_bytes': additional_raw, 'raw_included_in_archive': True,
                    'basis': 'additional_disk_bytes_excluding_already_uploaded_raw_including_raw_archive_copy',
                    'intermediates_min_bytes': work_min + output_min,
                    'intermediates_max_bytes': work_max + output_max,
                    'archive_min_bytes': archive_min, 'archive_max_bytes': archive_max,
                    'min_bytes': additional_raw + work_min + output_min + archive_min,
                    'max_bytes': additional_raw + work_max + output_max + archive_max,
                    'includes': ['raw', 'intermediates', 'full_archive'],
                    'method': 'uncalibrated_storage_planning_envelope_v1'}
        warnings.append({'code': 'estimate_uncertainty', 'message': 'Storage range is a planning heuristic, not a guaranteed bound. Compression, host fraction, workflow retries and archive duplication can exceed it.'})
        if enable_mags:
            warnings.append({'code': 'mag_uncertainty', 'message': 'MAG assembly/binning/reassembly varies greatly with read depth and complexity; actual disk and RAM may exceed the estimate.'})
        roots = [('input', self.settings.input_root, additional_raw, additional_raw),
                 ('work', self.settings.state_root/'work', work_min, work_max),
                 ('output', self.settings.state_root/'outputs', output_min + archive_min, output_max + archive_max)]
        filesystems = {}
        try:
            upload_reserve = reserved_upload_bytes(self.settings)
        except (OSError, ValueError, sqlite3.Error):
            upload_reserve = 0
            blockers.append({'code':'storage_unavailable','message':'Unfinished upload reservations cannot be verified; admission is blocked.'})
        for role, path, low, high in roots:
            try:
                observed = _filesystem_snapshot(path)
            except OSError:
                blockers.append({'code': 'storage_unavailable', 'message': f'{role} storage cannot be measured.'})
                continue
            key = observed['filesystem_id']
            mount = filesystems.setdefault(key, {**observed, 'roles': [], 'reserve_bytes': max(0, self.settings.minimum_free_disk_bytes),
                                                 'required_min_bytes': 0, 'required_max_bytes': 0})
            mount['free_bytes'] = min(mount['free_bytes'], observed['free_bytes'])
            mount['roles'].append(role)
            if role == 'input':
                mount['upload_reserved_bytes'] = upload_reserve
            mount['required_min_bytes'] += low
            mount['required_max_bytes'] += high
        for mount in filesystems.values():
            available = mount['free_bytes'] - mount['reserve_bytes'] - mount.get('upload_reserved_bytes',0)
            mount['available_after_reserve_bytes'] = max(0, available)
            roles = '/'.join(mount['roles'])
            if available < mount['required_min_bytes']:
                blockers.append({'code': 'disk_minimum', 'message': f'Insufficient {roles} disk space for the estimated minimum plus reserve.'})
            elif available < mount['required_max_bytes']:
                warnings.append({'code': 'disk_upper_estimate', 'message': f'{roles} disk space is below the upper planning estimate plus reserve.'})
        return {'cpu': {'max_threads': maximum, 'recommended_threads': recommended, 'requested_threads': threads},
                'input': {'bytes': raw, 'source': source, 'sample_count': sample_count},
                'estimate': estimate, 'filesystems': list(filesystems.values()),
                'blockers': blockers, 'warnings': warnings, 'can_submit': not blockers,
                'observed_at': time.time(),
                'limitation': 'Live filesystem free space is not a user quota or reservation. CPU limits do not guarantee performance; RAM, GPU and database readiness are not certified. Recheck at submission and worker admission.'}

    def require(self, *, manifest_path: str | None = None, threads: int | None = None,
                enable_mags: bool = False, input_bytes: int | None = None) -> dict:
        if not manifest_path:
            raise TaskValidationError('a server manifest is required for authoritative resource preflight')
        report = self.inspect(manifest_path=manifest_path, threads=threads, enable_mags=enable_mags)
        if report['blockers']:
            raise TaskValidationError('Resource preflight: ' + '; '.join(item['message'] for item in report['blockers']))
        return report
