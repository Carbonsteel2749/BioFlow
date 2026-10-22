"""Explicit selection and two-step confirmation for nonessential task logs."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..models import utc_now
from .redaction import redact_log
from .tasks import _manifest_sample_ids
from .task_cleanup import CleanupConflict


class LogSelection(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)


class LogDeletion(LogSelection):
    confirmation: str
    token: str


class LogCleanupService:
    def __init__(self, cleanup):
        self.cleanup = cleanup
        self.repository = cleanup.repository
        self.root = cleanup.settings.state_root.resolve()
        with self.repository._connect() as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS log_deletions (
                task_id TEXT NOT NULL, file_id TEXT NOT NULL, relative_path TEXT NOT NULL,
                step TEXT NOT NULL, deleted_at TEXT NOT NULL,
                PRIMARY KEY(task_id, file_id), FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE)''')

    def _entries(self, task, connection):
        task_id = task['id']
        self.cleanup._paths(task_id, 'delete')  # fixed roots, no symlink components
        steps = {s['name']: s['status'] for s in task['steps']}
        stopped = task['status'] in {'paused', 'failed', 'cancelled', 'completed'}
        entries = []
        for directory in [self.root / 'logs' / task_id, self.root / 'outputs' / task_id / 'logs']:
            if directory.resolve() != directory or not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                if path.resolve() != path or not path.is_file() or path.suffix not in {'.log', '.jsonl'}:
                    continue
                step = path.stem.split('.')[-1]
                reason = ''
                if not stopped:
                    reason = '任务未停止，禁止清理'
                elif path.suffix != '.log' or step not in steps:
                    reason = '诊断审计或未知用途日志，受保护'
                elif steps[step] != 'succeeded':
                    reason = '未成功阶段日志，保留用于排错'
                elif path.name == 'validate.log':
                    reason = '主运行日志可能含历次重试与失败证据，始终受保护'
                stat = path.stat()
                relative = str(path.relative_to(self.root))
                entries.append(dict(id=hashlib.sha256(relative.encode()).hexdigest()[:24], name=path.name,
                                    relative_path=relative, step=step, size_bytes=stat.st_size,
                                    modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                                    selectable=not reason, reason=reason, deleted=False))
        ids = {e['id'] for e in entries}
        for row in connection.execute('SELECT * FROM log_deletions WHERE task_id=?', (task_id,)):
            if row['file_id'] not in ids:
                entries.append(dict(id=row['file_id'], name=Path(row['relative_path']).name,
                                    relative_path=row['relative_path'], step=row['step'], size_bytes=0,
                                    modified_at=row['deleted_at'], selectable=False, reason='该日志已由用户清理', deleted=True))
        return entries

    def list(self, task_id):
        task = self.repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        with self.repository._connect() as connection:
            return {'files': self._entries(task, connection), 'scope': '只清理所选普通日志；原始数据、结果、工作缓存、诊断审计和运行溯源保持不变。'}

    def read(self, task_id, file_id):
        entries = self.list(task_id)['files']
        entry = next((e for e in entries if e['id'] == file_id), None)
        if entry is None:
            raise KeyError(file_id)
        if entry['deleted']:
            return {'text': '该日志已由用户清理。'}
        path = self.root / entry['relative_path']
        with path.open('rb') as handle:
            handle.seek(max(0, path.stat().st_size - 65536))
            text = handle.read(65536).decode('utf-8', errors='replace')
        task = self.repository.get_task(task_id)
        return {'text': redact_log(text, input_root=str(self.cleanup.settings.input_root),
                                   sample_identifiers=_manifest_sample_ids(Path(task['manifest_path'])))}

    def _plan(self, connection, task_id, ids):
        # Database write lock is held by caller, preventing restart/deletion races.
        task = self.repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if task['status'] not in {'paused', 'failed', 'cancelled', 'completed'}:
            raise CleanupConflict('任务未停止，禁止清理日志')
        self.cleanup._process_check(task_id)
        selected = set(ids)
        if len(selected) != len(ids):
            raise CleanupConflict('选择的日志重复')
        entries = [e for e in self._entries(task, connection) if e['id'] in selected]
        if len(entries) != len(selected) or any(not e['selectable'] for e in entries):
            raise CleanupConflict('日志不存在、已变化或受到保护，请重新选择')
        paths = [self.root / e['relative_path'] for e in entries]
        self.cleanup._references(connection, task_id, paths, 'cache')
        fingerprints = []
        for path in paths:
            stat = path.stat()
            if stat.st_nlink != 1:
                raise CleanupConflict('日志存在共享硬链接，拒绝清理')
            digest = hashlib.sha256()
            with path.open('rb') as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(block)
            fingerprints.append([str(path.relative_to(self.root)), stat.st_ino, stat.st_size, stat.st_mtime_ns, digest.hexdigest()])
        return entries, hashlib.sha256(json.dumps(fingerprints).encode()).hexdigest()

    def preview(self, task_id, request):
        with self.repository._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            entries, fingerprint = self._plan(connection, task_id, request.ids)
        expiry = int(time.time()) + 300
        token = f"{expiry}.{self.cleanup._signature(task_id, 'logs', expiry, fingerprint)}"
        return dict(files=entries, estimated_bytes=sum(e['size_bytes'] for e in entries), token=token)

    def delete(self, task_id, request):
        if request.confirmation != task_id:
            raise CleanupConflict('请确认完整任务 ID')
        removed, failure = [], None
        with self.repository._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            entries, fingerprint = self._plan(connection, task_id, request.ids)
            try:
                expiry, signature = request.token.split('.', 1)
                expiry = int(expiry)
            except ValueError as exc:
                raise CleanupConflict('确认信息无效，请重新预览') from exc
            if expiry < time.time() or not hmac.compare_digest(signature, self.cleanup._signature(task_id, 'logs', expiry, fingerprint)):
                raise CleanupConflict('预览过期或文件内容已变化，请重新预览')
            for entry in entries:
                try:
                    (self.root / entry['relative_path']).unlink()
                except OSError:
                    failure = '部分文件未能清理，请刷新列表检查；已清理文件已记录。'
                    break
                removed.append(entry)
                connection.execute('INSERT OR REPLACE INTO log_deletions VALUES(?,?,?,?,?)',
                                   (task_id, entry['id'], entry['relative_path'], entry['step'], utc_now()))
            connection.execute('INSERT INTO alerts(task_id,level,message,created_at) VALUES(?,?,?,?)',
                               (task_id, 'info', f'用户选择清理了 {len(removed)} 个普通日志；分析数据、结果及溯源记录未删除。', utc_now()))
        if failure:
            raise CleanupConflict(failure)
        return {'removed_count': len(removed), 'removed_bytes': sum(e['size_bytes'] for e in removed)}


def create_log_cleanup_router(service):
    router = APIRouter()

    def call(method, *args):
        try:
            return method(*args)
        except KeyError as exc:
            raise HTTPException(404, '任务或日志不存在') from exc
        except (CleanupConflict, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get('/api/tasks/{task_id}/log-files')
    def files(task_id: str):
        return call(service.list, task_id)

    @router.post('/api/tasks/{task_id}/log-files/cleanup-preview')
    def preview(task_id: str, request: LogSelection):
        return call(service.preview, task_id, request)

    @router.post('/api/tasks/{task_id}/log-files/cleanup')
    def delete(task_id: str, request: LogDeletion):
        return call(service.delete, task_id, request)

    @router.get('/api/tasks/{task_id}/log-files/{file_id}')
    def read(task_id: str, file_id: str):
        return call(service.read, task_id, file_id)

    return router
