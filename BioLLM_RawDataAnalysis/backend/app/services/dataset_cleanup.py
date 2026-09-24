"""Fail-closed cleanup of catalogued, platform-managed uploaded inputs only."""
import csv
import hashlib
import json
import os
from pathlib import Path
import re

from .datasets import DatasetService, MANIFEST_LIMIT


class DatasetCleanup:
    def __init__(self, settings, repository):
        self.settings = settings
        self.catalog = DatasetService(settings, repository)
        self.root = settings.input_root.resolve()

    def plan(self, db, dataset_id=None, task_id=None):
        if dataset_id:
            row = db.execute('SELECT * FROM datasets WHERE id=?', (dataset_id,)).fetchone()
            if row is None:
                raise KeyError(dataset_id)
        else:
            row = db.execute('SELECT d.* FROM datasets d JOIN tasks t ON t.manifest_path=d.manifest_path WHERE t.id=?', (task_id,)).fetchone()
        public = {'dataset_id': row['id'] if row else None, 'name': row['name'] if row else None,
                  'will_delete': False, 'reasons': [], 'scope': [], 'estimated_bytes': 0}
        files, uploads = [], []
        if row is None:
            public['reasons'].append('任务未关联已登记数据集，原始输入保留')
        else:
            try:
                files, uploads = self._candidates(db, row)
                self._check_references(db, row, files, task_id)
                self._check_processes(files)
                public['will_delete'] = True
            except (OSError, ValueError, RuntimeError, csv.Error, UnicodeError) as exc:
                public['reasons'].append(str(exc) if isinstance(exc, ValueError) else '无法完整核实输入文件或引用，保留数据；请管理员检查')
        if public['will_delete']:
            public['scope'] = [str(path.relative_to(self.root)) for path in files]
            inodes = {}
            for path in files:
                info = path.stat()
                inodes[(info.st_dev, info.st_ino)] = getattr(info, 'st_blocks', 0) * 512
            public['estimated_bytes'] = sum(inodes.values())
        # Bind every decision and file identity to the confirmation, including keep reasons.
        identities = []
        for path in files:
            try:
                info = path.lstat()
                identities.append([str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink])
            except OSError:
                identities.append([str(path), 'missing'])
        fingerprint = hashlib.sha256(json.dumps([public, identities], sort_keys=True).encode()).hexdigest()
        return {'public': public, 'files': files, 'uploads': uploads, 'fingerprint': fingerprint}

    def _candidates(self, db, dataset):
        manifest = Path(dataset['manifest_path'])
        if manifest.parent != self.root / 'manifests' or not re.fullmatch(r'[a-f0-9]{32}\.csv', manifest.name):
            raise ValueError('历史或手工管理的输入不在平台托管删除范围内，保留数据')
        if dataset['issue'] or dataset['stage'] != 'raw':
            raise ValueError('数据阶段或登记信息不完整，保留数据')
        self._managed_file(manifest)
        content = self.catalog._manifest_bytes(manifest)
        if hashlib.sha256(content).hexdigest() != dataset['manifest_sha256']:
            raise ValueError('样本清单已变化，保留数据；请重新核对')
        reads_dir = manifest.with_suffix('.reads')
        samples = db.execute('SELECT * FROM dataset_samples WHERE dataset_id=?', (dataset['id'],)).fetchall()
        if not samples:
            raise ValueError('样本登记为空，无法确认删除范围')
        files, upload_ids, aliases = {manifest}, set(), set()
        for sample in samples:
            for column in ('read1_json', 'read2_json'):
                snap = json.loads(sample[column])
                if self.catalog._file_view(snap)['availability'] != 'available':
                    raise ValueError('原始文件缺失或已变化，保留数据')
                path = self._managed_file(snap['path'])
                if path.parent != reads_dir or not snap.get('upload_id'):
                    raise ValueError('存在非平台上传的原始文件，保留数据')
                upload = db.execute('SELECT * FROM uploads WHERE id=?', (snap['upload_id'],)).fetchone()
                if upload is None or not re.fullmatch(r'[a-f0-9]{32}', upload['id']):
                    raise ValueError('上传登记缺失，保留数据')
                original = self._managed_file(upload['stored_path'])
                if original.parent != self.root / 'uploads' / upload['id'] or not original.samefile(path):
                    raise ValueError('上传路径或文件身份不符，保留数据')
                if set(original.parent.iterdir()) != {original}:
                    raise ValueError('上传目录含额外文件，保留数据')
                files.update((path, original))
                aliases.add(path)
                upload_ids.add(upload['id'])
        if reads_dir.is_symlink() or set(reads_dir.iterdir()) != aliases:
            raise ValueError('配对目录包含额外文件或链接，保留数据')
        return sorted(files), sorted(upload_ids)

    def _managed_file(self, path):
        path = self.catalog._safe_file(path)
        for part in (path, *path.parents):
            if part == self.root:
                break
            if os.path.ismount(part):
                raise ValueError('输入路径含挂载点，保留数据；请管理员核对')
        return path

    def _check_references(self, db, dataset, files, excluded_task):
        manifest = dataset['manifest_path']
        refs = list(db.execute('SELECT id,manifest_path FROM tasks WHERE id<>?', (excluded_task or '',)))
        refs += list(db.execute('SELECT task_id,input_manifest FROM workflow_runs WHERE input_manifest IS NOT NULL'))
        for task_id, path in refs:
            if Path(path).resolve() == Path(manifest):
                raise ValueError(f'数据仍被任务 {task_id} 引用，保留数据集及原始文件')
        keys = {(p.stat().st_dev, p.stat().st_ino) for p in files}

        def overlaps(value):
            path = Path(value)
            if path in files:
                return True
            if path.exists():
                info = path.stat()
                return (info.st_dev, info.st_ino) in keys
            return False

        for sample in db.execute('SELECT * FROM dataset_samples WHERE dataset_id<>?', (dataset['id'],)):
            if any(overlaps(json.loads(sample[c])['path']) for c in ('read1_json', 'read2_json')):
                raise ValueError(f"原始文件仍被数据集 {sample['dataset_id']} 引用，保留数据")
        manifests = {Path(path) for _, path in refs}
        manifests.update(Path(r[0]) for r in db.execute('SELECT manifest_path FROM datasets WHERE id<>?', (dataset['id'],)))
        manifests.update((self.root / 'manifests').glob('*.csv'))
        for other in sorted(manifests):
            if other == Path(manifest):
                continue
            if overlaps(other):
                raise ValueError('样本清单存在其他引用，保留数据')
            # An unreadable known manifest is not evidence that there are no references.
            with other.open('rb') as handle:
                data = handle.read(MANIFEST_LIMIT + 1)
            if len(data) > MANIFEST_LIMIT:
                raise ValueError('其他清单过大，无法核实引用，保留数据')
            rows = csv.DictReader(data.decode('utf-8-sig').splitlines())
            if rows.fieldnames != ['sample_id', 'read1', 'read2']:
                raise ValueError('其他清单格式异常，无法核实引用，保留数据')
            for record in rows:
                for col in ('read1', 'read2'):
                    value = record.get(col)
                    if not value:
                        raise ValueError('其他清单内容不完整，无法核实引用，保留数据')
                    path = Path(value)
                    if overlaps(path if path.is_absolute() else other.parent / path):
                        raise ValueError('原始文件仍被其他样本清单引用，保留数据')
        for table in ('artifacts', 'workflow_artifacts'):
            for row in db.execute(f'SELECT path FROM {table}'):
                if overlaps(row[0]):
                    raise ValueError('文件仍有已登记的跨模块产物引用，保留数据')
        counts = {}
        for path in files:
            info = path.stat()
            key = (info.st_dev, info.st_ino)
            counts[key] = counts.get(key, 0) + 1
        if any(p.stat().st_nlink != counts[(p.stat().st_dev, p.stat().st_ino)] for p in files):
            raise ValueError('文件存在范围外硬链接，无法确认无人使用，保留数据')

    @staticmethod
    def _check_processes(files):
        if not Path('/proc').is_dir():
            raise ValueError('无法检查输入文件使用进程，保留数据')
        keys = {(p.stat().st_dev, p.stat().st_ino) for p in files}
        for process in Path('/proc').iterdir():
            if not process.name.isdigit():
                continue
            try:
                if process.stat().st_uid != os.getuid():
                    continue
                command = (process / 'cmdline').read_bytes()
                if any(str(path).encode() in command for path in files):
                    raise ValueError(f'输入文件仍被进程 {process.name} 使用，保留数据')
                try:
                    for descriptor in (process / 'fd').iterdir():
                        try:
                            info = descriptor.stat()
                        except FileNotFoundError:
                            continue
                        if (info.st_dev, info.st_ino) in keys:
                            raise ValueError(f'输入文件仍被进程 {process.name} 打开，保留数据')
                except PermissionError:
                    if (process / 'comm').read_text().strip() not in {'sshd', '(sd-pam)'}:
                        raise
            except (FileNotFoundError, ProcessLookupError):
                continue

    @staticmethod
    def execute(db, plan):
        if not plan['public']['will_delete']:
            return
        # No recursive deletion: remove only individually checked files, then empty directories.
        for path in plan['files']:
            path.unlink()
        for parent in sorted({p.parent for p in plan['files'] if p.parent.name != 'manifests'}):
            parent.rmdir()
        db.execute('DELETE FROM datasets WHERE id=?', (plan['public']['dataset_id'],))
        db.executemany('DELETE FROM uploads WHERE id=?', [(uid,) for uid in plan['uploads']])
