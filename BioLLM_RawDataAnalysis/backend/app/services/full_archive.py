"""Task-scoped, verified archives. Never delete inputs or follow shared databases."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tarfile
import tempfile

from ..config import Settings
from ..models import TaskRepository, utc_now
from ..workflows.state import WorkflowRunRepository
from .input_lifecycle import input_lock
from .archive_naming import ArchiveNamingService, display_name, sync_directory
from .interpretation import InterpretationService

STOPPED = {'completed', 'succeeded', 'failed', 'paused', 'cancelled'}
GENERATED = ('raw/', 'intermediates/', 'execution/', 'interpretation/')
CONTROL = {'manifest.json', 'SHA256SUMS', 'archive-metadata.json', 'ARCHIVE-README.txt'}
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')


def is_private_archive(path):
    return (Path(path).parent / '.sensitive-archive').exists()


def archive_download_response(path, *, filename, media_type, headers=None):
    """Pin the public inode before checking privacy; never reopen a replaced path."""
    from fastapi import HTTPException
    from fastapi.responses import StreamingResponse
    from starlette.background import BackgroundTask
    from urllib.parse import quote
    handle = Path(path).open('rb')
    if is_private_archive(path):
        handle.close()
        raise HTTPException(status_code=403, detail='完整归档包含原始 FASTQ，暂不通过无认证接口下载；请使用授权的 SSH/SFTP 获取。')
    def chunks():
        try:
            while block := handle.read(1024 * 1024):
                yield block
        finally:
            handle.close()
    response_headers = dict(headers or {})
    response_headers['Content-Length'] = str(os.fstat(handle.fileno()).st_size)
    response_headers['Content-Disposition'] = "attachment; filename*=utf-8''" + quote(filename)
    return StreamingResponse(chunks(), media_type=media_type, headers=response_headers,
                             background=BackgroundTask(handle.close))


def _name(value):
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or '..' in path.parts or
            '\\' in value or any(ord(c) < 32 for c in value) or str(path) != value):
        raise ValueError('unsafe archive path')
    return value


def _json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()


def _signature(path):
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


class _HashReader:
    def __init__(self, stream):
        self.stream, self.digest = stream, hashlib.sha256()

    def read(self, size=-1):
        data = self.stream.read(size)
        self.digest.update(data)
        return data


def _process_check(task_id, roots):
    """A stopped DB row alone does not prove subprocesses have stopped."""
    if not Path('/proc').is_dir():
        raise ValueError('cannot verify stopped processes on this platform')
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            args = (proc / 'cmdline').read_bytes().split(b'\0')
            try:
                cwd = (proc / 'cwd').resolve(strict=True)
            except (FileNotFoundError, PermissionError):
                cwd = None
            if (task_id.encode() in args or
                    any(cwd is not None and cwd.is_relative_to(root) for root in roots) or
                    any(str(root).encode() in arg for root in roots for arg in args)):
                raise ValueError(f'task processes have not stopped: PID {proc.name}')
        except (FileNotFoundError, ProcessLookupError):
            continue


class FullArchiveService:
    def __init__(self, settings, repository=None):
        self.settings = settings
        self.repository = repository or TaskRepository(settings.database_path)

    def _task(self, task_id):
        task = self.repository.get_task(task_id)
        if task is not None:
            return 'standard', task
        try:
            task = WorkflowRunRepository(self.settings.database_path).get_run(task_id)
        except sqlite3.OperationalError:
            task = None
        if task is None:
            raise KeyError(task_id)
        return 'workflow', task

    def build(self, task_id):
        if not IDENTIFIER.fullmatch(task_id):
            raise ValueError('unsafe task ID')
        # Serializes archive publication with task/input deletion and manual retry.
        with input_lock(self.settings):
            return self._build(task_id)

    def _build(self, task_id):
        kind, task = self._task(task_id)
        if task['status'] not in STOPPED:
            raise ValueError('task must be stopped before archiving')
        naming = ArchiveNamingService(self.settings, self.repository)
        naming.recover_publication(task_id)
        kind, task = self._task(task_id)
        state = self.settings.state_root.resolve()
        roots = [
            state / name / task_id
            for name in (
                'outputs',
                'work',
                'logs',
                'retry_validation',
                'task_repairs',
            )
        ]
        for root in roots:
            if root.resolve() != root or root.is_symlink():
                raise ValueError('unsafe task directory')
        _process_check(task_id, roots)
        output, work, logs, retry_validation, task_repairs = roots
        destination = output / 'deliverables'
        if destination.resolve() != destination:
            raise ValueError('unsafe archive directory')
        destination.mkdir(parents=True, exist_ok=True)
        stored = naming.existing(task_id)
        legacy = naming.validate_path(task_id, destination / f'{task_id}.tar.gz')
        base = legacy if legacy.exists() else stored
        archive = naming.validate_path(task_id, destination / naming.filename(task_id))
        if archive.exists() and archive not in (stored, base):
            raise ValueError('target archive already exists; refusing to overwrite an unregistered file')
        target_signature = _signature(archive) if archive.exists() else None
        missing, excluded, warnings, files, generated = [], [], [], {}, {}
        raw_paths, raw_map = set(), []

        def missing_file(name, reason):
            missing.append({'path': name, 'reason': reason})

        def add_file(path, name, allowed_roots=(), exact=()):
            _name(name)
            try:
                resolved = path.resolve(strict=True)
                if not (resolved in exact or any(resolved.is_relative_to(r) for r in allowed_roots)):
                    excluded.append({'path': name, 'reason': 'external reference; database/environment/other task not copied',
                                     'source': str(path), 'target': str(resolved)})
                    return
                if not stat.S_ISREG(resolved.stat().st_mode):
                    excluded.append({'path': name, 'reason': 'not a regular file'})
                    return
                files[name] = (resolved, _signature(resolved))
            except (FileNotFoundError, OSError, RuntimeError) as exc:
                missing_file(name, type(exc).__name__)

        manifest_value = task.get('manifest_path') or task.get('input_manifest')
        manifest = Path(manifest_value) if manifest_value else None
        input_root = self.settings.input_root.resolve()
        if manifest is None or not manifest.is_file():
            missing_file('raw/samples.csv', 'input manifest missing')
        else:
            if not manifest.resolve().is_relative_to(input_root):
                raise ValueError('unsafe input manifest outside approved root')
            add_file(manifest, 'execution/original-samples.csv', (input_root,))
            with manifest.open(encoding='utf-8-sig', newline='') as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames != ['sample_id', 'read1', 'read2']:
                    raise ValueError('invalid input manifest header')
                rows = list(reader)
            seen = set()
            portable = io.StringIO()
            writer = csv.writer(portable, lineterminator='\n')
            writer.writerow(['sample_id', 'read1', 'read2'])
            for row in rows:
                sample = row['sample_id']
                if not IDENTIFIER.fullmatch(sample) or sample in seen:
                    raise ValueError('invalid or duplicate sample ID')
                seen.add(sample)
                relative_reads = []
                for mate, column in ((1, 'read1'), (2, 'read2')):
                    source = Path(row[column])
                    if not source.is_absolute():
                        source = manifest.parent / source
                    suffix = '.fastq.gz' if source.name.endswith('.gz') else '.fastq'
                    relative = f'{sample}/R{mate}{suffix}'
                    name = 'raw/' + relative
                    resolved = source.resolve()
                    if not resolved.is_relative_to(input_root):
                        raise ValueError('unsafe raw input outside approved root')
                    raw_paths.add(resolved)
                    add_file(source, name, exact=raw_paths)
                    raw_map.append({'sample_id': sample, 'role': column, 'path': name,
                                    'original_path': str(source), 'available': name in files})
                    relative_reads.append(relative)
                writer.writerow([sample, *relative_reads])
            generated['raw/samples.csv'] = portable.getvalue().encode()

        def scan(root, prefix):
            if not root.is_dir():
                missing_file(prefix, 'directory missing; may have been cleaned or never generated')
                return
            # Do not walk symlink directories: they can be very large shared databases.
            def walk_error(exc):
                missing_file(prefix, 'directory traversal failed: ' + type(exc).__name__)
            for parent, directories, filenames in os.walk(root, followlinks=False, onerror=walk_error):
                parent = Path(parent)
                for item in list(directories):
                    path = parent / item
                    if item in {'deliverables', '.git', '__pycache__'}:
                        directories.remove(item)
                    elif path.is_symlink():
                        directories.remove(item)
                        excluded.append({'path': prefix + '/' + path.relative_to(root).as_posix(),
                                         'reason': 'directory link not followed', 'target': str(path.resolve())})
                for item in sorted(filenames):
                    path = parent / item
                    name = prefix + '/' + path.relative_to(root).as_posix()
                    if (item == archive.name or item in {'.command.env', '.env'} or
                            item.endswith(('.pem', '.key', '.lock')) or item.startswith('.full-archive-')):
                        excluded.append({'path': name, 'reason': 'archive itself, environment secrets or live lock excluded'})
                        continue
                    add_file(path, name, roots, raw_paths)

        scan(output, 'intermediates/outputs')
        scan(work, 'intermediates/work')
        scan(logs, 'execution/logs')
        if retry_validation.exists():
            scan(retry_validation, 'execution/retry_validation')
        if task_repairs.exists():
            scan(task_repairs, 'execution/task_repairs')
        snapshot = task.get('compiled_root')
        if snapshot:
            compiled = Path(snapshot)
            approved = {
                state / 'workflow_runs' / task_id / 'compiled',
                state / 'workflow_state' / 'execution_snapshots' / task_id,
            }
            if compiled in approved and compiled.resolve() == compiled:
                roots.append(compiled)
                scan(compiled, 'execution/workflow')
                for directory, prefix in (('repairs', 'dynamic_repairs'),
                                          ('repair_attempts', 'dynamic_repair_attempts')):
                    repair_root = state / 'workflow_runs' / task_id / directory
                    if repair_root.is_symlink() or repair_root.resolve() != repair_root:
                        excluded.append({'path': 'execution/' + prefix,
                                         'reason': 'repair directory outside approved task root'})
                    elif repair_root.exists():
                        roots.append(repair_root)
                        scan(repair_root, 'execution/' + prefix)
            else:
                missing_file('execution/workflow', 'compiled snapshot outside approved directory')
        generated['execution/task.json'] = _json(task)
        if kind == 'standard':
            interpretation = InterpretationService(self.settings, self.repository).generate(task_id)
            generated['interpretation/preliminary.json'] = _json(interpretation)
            generated['interpretation/preliminary.txt'] = interpretation['text'].encode()
        else:
            generated['interpretation/preliminary.json'] = _json({'task_id': task_id, 'status': 'not_available',
                'reason': 'dynamic workflow interpretation is not implemented; use node results'})
        generated['interpretation/ai.json'] = _json({'status': 'not_generated',
            'reason': 'No persisted AI interpretation is available. Archiving never invents or requests model output.'})

        # Preserve the original delivered tables/figures/report paths, including future formats.
        base_records, base_signature = [], None
        if base is not None:
            base_signature = _signature(base)
            with tarfile.open(base, 'r:gz') as old:
                for member in old:
                    if member.isdir():
                        continue
                    _name(member.name)
                    if member.name in CONTROL or member.name.startswith(GENERATED):
                        continue
                    if not member.isfile():
                        raise ValueError('unsafe member in existing result archive')
                    if member.name in {entry.name for entry in base_records}:
                        raise ValueError('duplicate member in existing result archive')
                    base_records.append(member)
        else:
            missing_file('original_result_package', 'report package not generated')

        estimated = sum(value[1][2] for value in files.values()) + sum(m.size for m in base_records)
        if shutil.disk_usage(destination).free < estimated * 1.05 + 64 * 1024 * 1024:
            raise ValueError('insufficient disk space for verified archive; old files retained')
        info = {'schema_version': 2, 'task_id': task_id, 'task_kind': kind,
                'task_name_at_packaging': display_name(task),
                'task_status': task['status'], 'generated_at': utc_now(),
                'archive_path': str(archive), 'contains_sensitive_raw_data': True,
                'complete': not missing and task['status'] in {'completed', 'succeeded'},
                'missing': missing, 'excluded': excluded, 'warnings': warnings, 'raw_files': raw_map,
                'resume_note': 'Original runtime files are retained. Extraction alone does not guarantee portable Nextflow resume.'}
        generated['archive-metadata.json'] = _json(info)
        generated['ARCHIVE-README.txt'] = (
            'Private task archive: contains raw FASTQ (possibly host sequences) and internal execution files.\n'
            'Do not publish or share without authorization. This is not a de-identified public result package.\n'
            'Read archive-metadata.json for missing files and exclusions; a failed task is not a complete analysis.\n'
            'raw/: FASTQ and portable samples.csv; intermediates/: work/cache and published outputs;\n'
            'interpretation/: preliminary text, evidence and limits; existing tables/figures/reports retain their paths.\n'
            'Check all files after extraction: sha256sum -c SHA256SUMS\n'
            'Public databases, environments, secrets and other tasks are not included.\n'
            'No original runtime files were removed. Re-running or re-plotting requires a new archive snapshot.\n'
        ).encode()
        records, dedup = [], {}
        fd, temporary = tempfile.mkstemp(prefix='.full-archive-', suffix='.tmp', dir=destination)
        os.close(fd)
        try:
            with tarfile.open(temporary, 'w:gz', compresslevel=1, format=tarfile.PAX_FORMAT) as tar:
                def put(name, stream, size, link=None):
                    entry = tarfile.TarInfo(_name(name))
                    entry.mode = 0o600
                    entry.size = size
                    if link:
                        entry.type, entry.linkname, entry.size = tarfile.LNKTYPE, link['path'], 0
                        tar.addfile(entry)
                        digest = link['sha256']
                    else:
                        reader = _HashReader(stream)
                        tar.addfile(entry, reader)
                        digest = reader.digest.hexdigest()
                    record = {'path': name, 'size_bytes': size, 'sha256': digest}
                    records.append(record)
                    return record

                if base_records:
                    with tarfile.open(base, 'r:gz') as old:
                        for member in base_records:
                            with old.extractfile(member) as stream:
                                put(member.name, stream, member.size)
                for name, (path, signature) in sorted(files.items()):
                    if _signature(path) != signature:
                        raise ValueError('source changed during archiving')
                    if signature in dedup:
                        put(name, None, signature[2], dedup[signature])
                    else:
                        with path.open('rb') as stream:
                            dedup[signature] = put(name, stream, signature[2])
                for name, data in generated.items():
                    put(name, io.BytesIO(data), len(data))
                manifest_data = _json({'schema_version': 2, 'task_id': task_id,
                    'checksum_algorithm': 'sha256', 'checksum_file': 'SHA256SUMS',
                    'artifacts': list(records)})
                put('manifest.json', io.BytesIO(manifest_data), len(manifest_data))
                sums = ''.join(f"{r['sha256']}  {r['path']}\n" for r in records).encode()
                put('SHA256SUMS', io.BytesIO(sums), len(sums))
            self.verify(Path(temporary))
            if self._task(task_id)[1]['status'] != task['status']:
                raise ValueError('task changed during archiving')
            _process_check(task_id, roots[:4])
            for path, signature in files.values():
                if _signature(path) != signature:
                    raise ValueError('source changed during archiving')
            if base_signature and _signature(base) != base_signature:
                raise ValueError('result archive changed during archiving')
            with open(temporary, 'rb') as handle:
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            # Mark private BEFORE publication so existing unauthenticated routes fail closed.
            marker = destination / '.sensitive-archive'
            if not marker.exists():
                marker_fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(marker_fd, 'w') as handle:
                    handle.write('Contains raw FASTQ; retrieve through authorized SSH/SFTP only.\n')
            if (archive.is_symlink() or
                    (_signature(archive) if archive.exists() else None) != target_signature):
                raise ValueError('target archive changed during compression; no file overwritten')
            naming.prepare_publication(task_id, archive, temporary)
            if target_signature is None:
                # Atomic no-clobber publication for a previously absent name.
                os.link(temporary, archive)
            else:
                os.replace(temporary, archive)
            sync_directory(destination)
            naming.recover_publication(task_id, strict=True)
            # The verified new package contains the original report members.
            # Remove only its consumed task-owned staging package, not runtime inputs.
            if base is not None and base != archive:
                base.unlink()
            return info
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def verify(path):
        with tarfile.open(path, 'r:gz') as tar:
            seen, hashes = set(), {}
            for member in tar:
                _name(member.name)
                if member.name in seen:
                    raise ValueError('duplicate archive member')
                seen.add(member.name)
                if not (member.isfile() or member.islnk() and member.linkname in hashes):
                    raise ValueError('unsafe archive member')
                if member.islnk():
                    hashes[member.name] = hashes[member.linkname]
                else:
                    digest = hashlib.sha256()
                    with tar.extractfile(member) as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b''):
                            digest.update(block)
                    hashes[member.name] = (member.size, digest.hexdigest())
            manifest = json.load(tar.extractfile('manifest.json'))
            expected = set()
            for record in manifest['artifacts']:
                expected.add(record['path'])
                if hashes.get(record['path']) != (record['size_bytes'], record['sha256']):
                    raise ValueError('archive checksum verification failed')
            if expected | {'manifest.json', 'SHA256SUMS'} != seen:
                raise ValueError('archive contains unlisted files')
            for line in tar.extractfile('SHA256SUMS').read().decode().splitlines():
                checksum, name = line.split('  ', 1)
                if hashes[name][1] != checksum:
                    raise ValueError('archive checksum file mismatch')
        return True


def archive_after_run(settings, task_id):
    """Called only after the runner has reaped Nextflow, not inside REPORT."""
    try:
        return FullArchiveService(settings).build(task_id)
    except Exception as exc:
        logging.getLogger(__name__).exception('Full archive failed for task %s', task_id)
        repo = TaskRepository(settings.database_path)
        if repo.get_task(task_id):
            repo.add_alert(task_id, 'warning', f'完整归档未生成，原文件保留：{type(exc).__name__}: {exc}')
        return None


def main():
    parser = argparse.ArgumentParser(description='Build private full archives for stopped tasks only')
    parser.add_argument('--task-id')
    parser.add_argument('--all-stopped', action='store_true')
    args = parser.parse_args()
    settings = Settings.from_env()
    repo = TaskRepository(settings.database_path)
    service = FullArchiveService(settings, repo)
    if args.all_stopped:
        tasks = [(t['id'], t['status']) for t in repo.list_tasks()]
        with repo._connect() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_runs'").fetchone():
                tasks += [(r['task_id'], r['status']) for r in db.execute('SELECT task_id,status FROM workflow_runs')]
    elif args.task_id:
        tasks = [(args.task_id, service._task(args.task_id)[1]['status'])]
    else:
        parser.error('specify --task-id or --all-stopped')
    failed = 0
    for task_id, status in tasks:
        if status not in STOPPED:
            print(json.dumps({'task_id': task_id, 'skipped': status}), flush=True)
            continue
        try:
            result = service.build(task_id)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as exc:
            failed += 1
            print(json.dumps({'task_id': task_id, 'error': str(exc)}, ensure_ascii=False), flush=True)
    return int(failed > 0)


if __name__ == '__main__':
    raise SystemExit(main())
