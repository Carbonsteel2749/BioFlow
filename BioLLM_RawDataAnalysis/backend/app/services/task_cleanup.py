from __future__ import annotations

import csv
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..models import utc_now
from .dataset_cleanup import DatasetCleanup
from .input_lifecycle import input_operation


class CleanupRequest(BaseModel):
    mode: Literal["cache", "delete"]
    confirmation: str
    token: str
    delete_dataset: bool = False


class DatasetCleanupRequest(BaseModel):
    confirmation: str
    token: str


class CleanupConflict(ValueError):
    pass


class TaskCleanupService:
    """Only standard tasks and their fixed runtime directories are in scope."""

    def __init__(self, settings, repository, runner):
        self.settings = settings
        self.repository = repository
        self.runner = runner
        self.secret = secrets.token_bytes(32)
        self.datasets = DatasetCleanup(settings, repository)
        with repository._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS task_file_references (
                task_id TEXT NOT NULL, consumer TEXT NOT NULL, reference_id TEXT NOT NULL,
                created_at TEXT NOT NULL, PRIMARY KEY(task_id, consumer, reference_id),
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE RESTRICT)""")

    def _paths(self, task_id, mode):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", task_id):
            raise CleanupConflict("任务 ID 不合法")
        root = self.settings.state_root.resolve()
        categories = ("work",) if mode == "cache" else ("work", "outputs", "logs")
        paths = []
        for category in categories:
            parent = root / category
            path = parent / task_id
            if parent.is_symlink() or path.is_symlink() or path.resolve() != path:
                raise CleanupConflict("任务目录含符号链接，拒绝清理；请管理员核对")
            if path.exists() and (not path.is_dir() or os.path.ismount(path)):
                raise CleanupConflict("任务目录类型或挂载点异常，拒绝清理")
            paths.append(path)
        return paths

    def _process_check(self, task_id):
        with self.runner._lock:
            if task_id in self.runner._active:
                raise CleanupConflict("任务后台线程尚未退出，请稍后再试")
        if not Path("/proc").is_dir():
            raise CleanupConflict("无法核实残留进程，拒绝清理")
        owned = self._paths(task_id, "delete")
        for process in Path("/proc").iterdir():
            if not process.name.isdigit():
                continue
            try:
                if process.stat().st_uid != os.getuid():
                    continue
                command = (process / "cmdline").read_bytes().split(b"\0")
                try:
                    cwd = (process / "cwd").resolve(strict=True)
                except FileNotFoundError:
                    cwd = None  # exited process or zombie
                except PermissionError:
                    # Privilege-separated login helpers are not workflow workers;
                    # their child shells/workers are checked independently.
                    if (process / "comm").read_text().strip() not in {"sshd", "(sd-pam)"}:
                        raise
                    cwd = None
                if (task_id.encode() in command or
                        any(cwd is not None and cwd.is_relative_to(path) for path in owned) or
                        any(str(path).encode() in arg for path in owned for arg in command)):
                    raise CleanupConflict(f"任务仍有残留进程（PID {process.name}），请先停止并等待退出")
            except (FileNotFoundError, ProcessLookupError):
                continue
            except PermissionError as exc:
                raise CleanupConflict("无法核实同账户进程状态，拒绝清理") from exc

    def _references(self, connection, task_id, paths, mode):
        if mode == "delete":
            refs = connection.execute(
                "SELECT consumer, reference_id FROM task_file_references WHERE task_id=?", (task_id,)
            ).fetchall()
            if refs:
                names = ", ".join(f"{r['consumer']}/{r['reference_id']}" for r in refs)
                raise CleanupConflict(f"结果仍被其他模块引用：{names}；请先解除引用")
            relation = connection.execute("""SELECT child.task_id FROM artifact_relations r
                JOIN artifacts parent ON r.parent_artifact_id=parent.artifact_id
                JOIN artifacts child ON r.artifact_id=child.artifact_id
                WHERE parent.task_id=? AND child.task_id<>? LIMIT 1""", (task_id, task_id)).fetchone()
            if relation:
                raise CleanupConflict(f"结果仍被任务 {relation[0]} 的衍生产物引用")

        def contained(value):
            path = Path(value).resolve()
            return any(path.is_relative_to(root) for root in paths)

        for table in ("artifacts", "workflow_artifacts"):
            for row in connection.execute(f"SELECT task_id, path FROM {table}"):
                if contained(row["path"]) and (mode == "cache" or row["task_id"] != task_id):
                    raise CleanupConflict(f"目录中有已登记共享产物（任务 {row['task_id']}），拒绝清理")
        manifests = list(connection.execute("SELECT id, manifest_path FROM tasks"))
        manifests += list(connection.execute("SELECT task_id, input_manifest FROM workflow_runs"))
        for other_id, manifest in manifests:
            if not manifest:
                continue
            if contained(manifest):
                raise CleanupConflict(f"任务 {other_id} 的输入清单引用此目录")
            source = Path(manifest)
            if not source.is_file():
                continue
            try:
                with source.open(encoding="utf-8-sig", newline="") as handle:
                    for row in csv.DictReader(handle):
                        for column in ("read1", "read2"):
                            value = row.get(column)
                            if value:
                                read = Path(value)
                                if contained(read if read.is_absolute() else source.parent / read):
                                    raise CleanupConflict(f"任务 {other_id} 的输入数据引用此目录")
            except (OSError, UnicodeError, csv.Error) as exc:
                raise CleanupConflict(f"无法核实任务 {other_id} 的输入引用，拒绝清理") from exc

    @staticmethod
    def _inventory(paths):
        digest = hashlib.sha256()
        inodes = {}
        for root in paths:
            digest.update(str(root).encode())
            if not root.exists():
                continue
            device = root.stat().st_dev
            for directory, dirs, files in os.walk(root, followlinks=False):
                for name in [".", *sorted(dirs), *sorted(files)]:
                    path = Path(directory) / name
                    info = path.lstat()
                    if info.st_dev != device or (name != "." and path.is_dir() and not path.is_symlink() and os.path.ismount(path)):
                        raise CleanupConflict("目录包含其他文件系统或挂载点，拒绝清理")
                    digest.update(f"{path}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}:{info.st_nlink}".encode())
                    if not stat.S_ISDIR(info.st_mode):
                        key = (info.st_dev, info.st_ino)
                        data = inodes.setdefault(key, [0, info.st_nlink, getattr(info, "st_blocks", 0) * 512])
                        data[0] += 1
        # Shared hard links don't release their underlying data blocks.
        size = sum(blocks for count, links, blocks in inodes.values() if count >= links)
        return size, digest.hexdigest()

    def _plan(self, connection, task_id, mode):
        task = connection.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if task is None:
            raise KeyError(task_id)
        if task["status"] not in {"cancelled", "completed", "failed", "paused"}:
            raise CleanupConflict("请先取消排队或运行中的任务，并等待进程退出")
        paths = self._paths(task_id, mode)
        self._process_check(task_id)
        self._references(connection, task_id, paths, mode)
        size, fingerprint = self._inventory(paths)
        return paths, size, fingerprint

    def _signature(self, task_id, mode, expiry, fingerprint):
        payload = json.dumps([task_id, mode, expiry, fingerprint]).encode()
        return hmac.new(self.secret, payload, hashlib.sha256).hexdigest()

    @input_operation
    def preview(self, task_id, mode, delete_dataset=False):
        if delete_dataset and mode != 'delete':
            raise CleanupConflict('只有删除任务时才能同步删除数据')
        with self.repository._connect() as connection:
            paths, size, fingerprint = self._plan(connection, task_id, mode)
            data = self.datasets.plan(connection, task_id=task_id) if delete_dataset else None
        if data:
            fingerprint += data['fingerprint']
            size += data['public']['estimated_bytes']
        expiry = int(time.time()) + 300
        return dict(task_id=task_id, mode=mode, estimated_bytes=size,
                    scope=[f"{p.parent.name}/{task_id}" for p in paths] + (data['public']['scope'] if data else []),
                    token=f"{expiry}.{self._signature(task_id, mode, expiry, fingerprint)}",
                    dataset_cleanup=data['public'] if data else None,
                    preserves_raw_reads=not (data and data['public']['will_delete']), reference_policy="registered references and task inputs")

    @input_operation
    def execute(self, task_id, request):
        if request.confirmation != task_id:
            raise CleanupConflict("请确认完整任务 ID")
        with self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            paths, size, fingerprint = self._plan(connection, task_id, request.mode)
            if request.delete_dataset and request.mode != 'delete':
                raise CleanupConflict('只有删除任务时才能同步删除数据')
            data = self.datasets.plan(connection, task_id=task_id) if request.delete_dataset else None
            if data:
                fingerprint += data['fingerprint']
                size += data['public']['estimated_bytes']
            try:
                expiry_text, signature = request.token.split(".", 1)
                expiry = int(expiry_text)
            except ValueError as exc:
                raise CleanupConflict("请重新预览删除范围并确认") from exc
            if (expiry < time.time() or not hmac.compare_digest(
                    signature, self._signature(task_id, request.mode, expiry, fingerprint))):
                raise CleanupConflict("预览已过期或文件已变化，请重新预览并确认")
            # Fixed validated paths only; shutil never follows nested symlinks.
            # Keep the database record if any removal fails, so partial cleanup is visible.
            try:
                for path in paths:
                    if path.exists():
                        shutil.rmtree(path)
                if data:
                    self.datasets.execute(connection, data)
            except OSError as exc:
                raise CleanupConflict("文件清理失败，任务记录仍保留；可能已清理部分文件，请检查权限后重新预览") from exc
            if request.mode == "delete":
                connection.execute("""DELETE FROM artifact_relations WHERE artifact_id IN
                    (SELECT artifact_id FROM artifacts WHERE task_id=?)""", (task_id,))
                connection.execute("DELETE FROM artifacts WHERE task_id=?", (task_id,))
                connection.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            else:
                connection.execute("UPDATE tasks SET retry_allowed=0, updated_at=? WHERE id=?", (utc_now(), task_id))
                connection.execute("INSERT INTO alerts(task_id,level,message,created_at) VALUES(?,?,?,?)",
                                   (task_id, "info", "中间缓存已清理，不能再利用该缓存断点续跑；原始输入和结果仍保留。", utc_now()))
        return dict(task_id=task_id, mode=request.mode, removed_bytes=size,
                    dataset_cleanup=data['public'] if data else None,
                    status="deleted" if request.mode == "delete" else "cleaned")

    @input_operation
    def dataset_preview(self, dataset_id):
        with self.repository._connect() as connection:
            plan = self.datasets.plan(connection, dataset_id=dataset_id)
        expiry = int(time.time()) + 300
        return {'dataset_cleanup': plan['public'], 'estimated_bytes': plan['public']['estimated_bytes'],
                'scope': plan['public']['scope'],
                'token': f"{expiry}.{self._signature(dataset_id, 'dataset', expiry, plan['fingerprint'])}"}

    @input_operation
    def dataset_execute(self, dataset_id, request):
        if request.confirmation != dataset_id:
            raise CleanupConflict('请输入完整数据集 ID 确认删除')
        with self.repository._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            plan = self.datasets.plan(connection, dataset_id=dataset_id)
            if not plan['public']['will_delete']:
                raise CleanupConflict('；'.join(plan['public']['reasons']))
            try:
                expiry_text, signature = request.token.split('.', 1)
                expiry = int(expiry_text)
            except ValueError as exc:
                raise CleanupConflict('请重新预览删除范围') from exc
            if expiry < time.time() or not hmac.compare_digest(signature, self._signature(dataset_id, 'dataset', expiry, plan['fingerprint'])):
                raise CleanupConflict('引用或文件已变化，请重新预览并确认')
            try:
                self.datasets.execute(connection, plan)
            except OSError as exc:
                raise CleanupConflict('部分文件清理失败，记录保留；请检查权限和文件状态') from exc
        return {'status': 'deleted', 'removed_bytes': plan['public']['estimated_bytes']}

    def reference(self, task_id, consumer, reference_id, remove=False):
        if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) for value in (consumer, reference_id)):
            raise CleanupConflict("引用标识只能使用字母、数字、点、横线和下划线")
        with self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone() is None:
                raise KeyError(task_id)
            if remove:
                connection.execute("DELETE FROM task_file_references WHERE task_id=? AND consumer=? AND reference_id=?", (task_id, consumer, reference_id))
            else:
                connection.execute("INSERT OR IGNORE INTO task_file_references VALUES(?,?,?,?)", (task_id, consumer, reference_id, utc_now()))
        return {"status": "released" if remove else "referenced"}


def create_cleanup_router(service):
    router = APIRouter()

    def call(function, *args):
        try:
            return function(*args)
        except KeyError as exc:
            raise HTTPException(404, "task not found") from exc
        except (CleanupConflict, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/api/tasks/{task_id}/cleanup-preview")
    def preview(task_id: str, mode: Literal["cache", "delete"], delete_dataset: bool = False):
        return call(service.preview, task_id, mode, delete_dataset)

    @router.post("/api/tasks/{task_id}/cleanup")
    def cleanup(task_id: str, request: CleanupRequest):
        return call(service.execute, task_id, request)

    @router.get('/api/datasets/{dataset_id}/cleanup-preview')
    def dataset_preview(dataset_id: str):
        return call(service.dataset_preview, dataset_id)

    @router.post('/api/datasets/{dataset_id}/cleanup')
    def dataset_cleanup(dataset_id: str, request: DatasetCleanupRequest):
        return call(service.dataset_execute, dataset_id, request)

    @router.put("/api/tasks/{task_id}/references/{consumer}/{reference_id}")
    def reference(task_id: str, consumer: str, reference_id: str):
        return call(service.reference, task_id, consumer, reference_id)

    @router.delete("/api/tasks/{task_id}/references/{consumer}/{reference_id}")
    def release(task_id: str, consumer: str, reference_id: str):
        return call(service.reference, task_id, consumer, reference_id, True)

    return router
