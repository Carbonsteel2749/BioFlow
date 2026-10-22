"""Task-owned archive names; IDs and runtime directories remain immutable."""
from __future__ import annotations

import os
import json
from pathlib import Path
import re
import unicodedata
import tempfile

from ..models import TaskRepository, utc_now
from .input_lifecycle import input_lock

STOPPED = {'completed', 'succeeded', 'failed', 'paused', 'cancelled'}
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
SUFFIX = '_分析结果汇总.tar.gz'


def file_identity(path):
    value = Path(path).stat()
    return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns]


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def display_name(task):
    return (task.get('name') or '').strip() or Path(
        task.get('manifest_path') or task.get('input_manifest') or '未命名分析'
    ).name


def safe_name(name, byte_limit):
    value = ''.join('_' if c in '/\\:*?"<>|' or unicodedata.category(c).startswith('C')
                    else c for c in unicodedata.normalize('NFC', name))
    value = value.strip(' .') or '未命名分析'
    value = value.encode('utf-8')[:byte_limit].decode('utf-8', errors='ignore').rstrip(' .')
    return value or '分析'


class ArchiveNamingService:
    def __init__(self, settings, repository=None):
        self.settings = settings
        self.repository = repository or TaskRepository(settings.database_path)

    def task(self, task_id):
        if not IDENTIFIER.fullmatch(task_id):
            raise ValueError('unsafe task ID')
        with self.repository._connect() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if row is not None:
                return 'tasks', 'id', dict(row)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_runs'").fetchone():
                row = db.execute('SELECT * FROM workflow_runs WHERE task_id=?', (task_id,)).fetchone()
                if row is not None:
                    return 'workflow_runs', 'task_id', dict(row)
        raise KeyError(task_id)

    def filename(self, task_id, name=None):
        _, _, task = self.task(task_id)
        with self.repository._connect() as db:
            ids = [row[0] for row in db.execute('SELECT id FROM tasks')]
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_runs'").fetchone():
                ids.extend(row[0] for row in db.execute('SELECT task_id FROM workflow_runs'))
        length = min(8, len(task_id))
        while length < len(task_id) and any(other != task_id and other[:length] == task_id[:length] for other in ids):
            length += 1
        suffix = '__' + task_id[:length] + SUFFIX
        return safe_name(display_name(task) if name is None else name, 240 - len(suffix.encode())) + suffix

    def directory(self, task_id):
        if not IDENTIFIER.fullmatch(task_id):
            raise ValueError('unsafe task ID')
        directory = self.settings.state_root.resolve() / 'outputs' / task_id / 'deliverables'
        if directory.resolve() != directory:
            raise ValueError('unsafe archive directory')
        return directory

    def validate_path(self, task_id, path):
        path = Path(path)
        if path.is_symlink() or path.resolve() != path or path.parent != self.directory(task_id):
            raise ValueError('unsafe result archive path')
        valid = path.name == f'{task_id}.tar.gz' or any(
            path.name.endswith('__' + task_id[:length] + SUFFIX)
            for length in range(min(8, len(task_id)), len(task_id) + 1)
        )
        if not valid or (path.exists() and not path.is_file()):
            raise ValueError('unsafe result archive name or type')
        return path

    def existing(self, task_id):
        _, _, task = self.task(task_id)
        if task.get('result_archive'):
            stored = self.validate_path(task_id, task['result_archive'])
            if stored.exists():
                return stored
        legacy = self.validate_path(task_id, self.directory(task_id) / f'{task_id}.tar.gz')
        return legacy if legacy.exists() else None

    def register(self, task_id, path, name=None):
        table, key, _ = self.task(task_id)
        path = self.validate_path(task_id, path) if path is not None else None
        with self.repository._connect() as db:
            updates, values = ['updated_at=?'], [utc_now()]
            if path is not None:
                updates.append('result_archive=?')
                values.append(str(path))
            if name is not None:
                updates.append('name=?')
                values.append(name)
            db.execute(f'UPDATE {table} SET {",".join(updates)} WHERE {key}=?', [*values, task_id])

    def prepare_publication(self, task_id, target, temporary):
        """Durable inode evidence, so recovery never adopts a filename alone."""
        target = self.validate_path(task_id, target)
        intent = target.parent / '.archive-publication.json'
        if intent.exists() or intent.is_symlink():
            raise ValueError('unfinished archive publication requires recovery')
        data = {'task_id': task_id, 'target': str(target), 'identity': file_identity(temporary)}
        fd, pending = tempfile.mkstemp(prefix='.archive-intent-', dir=target.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, intent)
            sync_directory(target.parent)
        finally:
            if os.path.exists(pending):
                os.unlink(pending)

    def recover_publication(self, task_id, *, strict=False):
        intent = self.directory(task_id) / '.archive-publication.json'
        if intent.is_symlink():
            raise ValueError('unsafe archive publication record')
        if not intent.exists():
            if strict:
                raise ValueError('archive publication identity record is missing; original package retained')
            return
        if intent.stat().st_size > 8192:
            raise ValueError('invalid archive publication record')
        data = json.loads(intent.read_text())
        if data.get('task_id') != task_id:
            raise ValueError('archive publication belongs to another task')
        target = self.validate_path(task_id, data['target'])
        adopted = target.exists() and file_identity(target) == data['identity']
        if adopted:
            self.register(task_id, target)
        # A missing/different inode means publication never happened, or an
        # external file won the race. Neither may be claimed as this package.
        intent.unlink()
        sync_directory(intent.parent)
        if strict and not adopted:
            raise ValueError('archive publication identity changed; original package retained')

    def synchronize(self, task_id, *, name=None):
        """Link, commit pointer, then unlink the old alias; never recompress bytes."""
        with input_lock(self.settings):
            self.recover_publication(task_id)
            _, _, task = self.task(task_id)
            source = self.existing(task_id)
            if source is None:
                if name is not None:
                    self.register(task_id, None, name)
                return None
            if task['status'] not in STOPPED:
                raise ValueError('任务仍在运行，请停止后再同步压缩包名称')
            from .full_archive import _process_check
            _process_check(task_id, [self.settings.state_root.resolve() / part / task_id
                                     for part in ('outputs', 'work', 'logs')])
            target = source.with_name(self.filename(task_id, name))
            self.validate_path(task_id, target)
            created = False
            if source != target:
                try:
                    os.link(source, target)
                    created = True
                    sync_directory(target.parent)
                except FileExistsError:
                    if target.is_symlink() or not os.path.samefile(source, target):
                        raise ValueError('目标压缩包已存在且内容来源不同；未覆盖任何文件')
            try:
                self.register(task_id, target, name)
            except Exception:
                if created and os.path.samefile(source, target):
                    target.unlink()
                raise
            # Also finish a rename interrupted after the database commit. Only
            # proven same-inode aliases of this task may be removed.
            for alias in target.parent.glob('*.tar.gz'):
                if alias == target or alias.is_symlink():
                    continue
                try:
                    self.validate_path(task_id, alias)
                except ValueError:
                    continue
                if os.path.samefile(alias, target):
                    alias.unlink()
            sync_directory(target.parent)
            return target
