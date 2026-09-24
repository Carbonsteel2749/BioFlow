"""A catalog of explicit manifests, never a filesystem-wide FASTQ discovery job."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
from pathlib import Path

from ..models import utc_now
from .tasks import TaskValidationError
from .input_lifecycle import input_operation

MANIFEST_LIMIT = 5 * 1024 * 1024
SAMPLE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')


class DatasetService:
    def __init__(self, settings, repository):
        self.settings = settings
        self.repository = repository
        self.root = settings.input_root.resolve()

    @input_operation
    def initialize(self):
        with self.repository._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    manifest_path TEXT NOT NULL UNIQUE, manifest_sha256 TEXT,
                    source TEXT NOT NULL, stage TEXT NOT NULL, issue TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dataset_samples (
                    dataset_id TEXT NOT NULL, sample_id TEXT NOT NULL,
                    read1_json TEXT NOT NULL, read2_json TEXT NOT NULL,
                    PRIMARY KEY(dataset_id, sample_id),
                    FOREIGN KEY(dataset_id) REFERENCES datasets(id) ON DELETE CASCADE
                );
            ''')
            paths = {row[0] for row in db.execute('SELECT manifest_path FROM tasks')}
            paths.update(row[0] for row in db.execute('SELECT input_manifest FROM workflow_runs WHERE input_manifest IS NOT NULL'))
        # Only the existing server-managed manifest directory is enumerated.
        managed = self.root / 'manifests'
        if not managed.is_symlink():
            paths.update(str(p) for p in managed.glob('*.csv'))
        uploads = self._uploads_by_inode()
        for path in sorted(paths):
            self.register_manifest(Path(path), source='historical', uploads=uploads)

    def _safe_file(self, path):
        path = Path(path)
        if not path.is_absolute():
            raise ValueError('relative path is not allowed here')
        # Reject links in every component, not just the final filename.
        relative = path.relative_to(self.root)
        current = self.root
        for part in relative.parts:
            if part in ('.', '..'):
                raise ValueError('unsafe path')
            current /= part
            if current.is_symlink():
                raise ValueError('symbolic link')
        resolved = path.resolve(strict=True)
        resolved.relative_to(self.root)
        if not resolved.is_file():
            raise ValueError('not a file')
        return resolved

    def _manifest_bytes(self, path):
        safe = self._safe_file(path)
        with safe.open('rb') as handle:
            content = handle.read(MANIFEST_LIMIT + 1)
        if len(content) > MANIFEST_LIMIT:
            raise ValueError('manifest too large')
        return content

    def _uploads_by_inode(self):
        with self.repository._connect() as db:
            rows = db.execute('SELECT * FROM uploads').fetchall()
        result = {}
        for row in rows:
            try:
                stat = self._safe_file(row['stored_path']).stat()
                result[(stat.st_dev, stat.st_ino)] = dict(row)
            except (OSError, ValueError, RuntimeError):
                continue
        return result

    def _snapshot(self, path, uploads):
        record = {'path': str(path), 'name': path.name, 'size_bytes': 0,
                  'upload_id': None, 'checksum': None, 'validation': 'not_recorded'}
        try:
            safe = self._safe_file(path)
            stat = safe.stat()
            if not safe.name.lower().endswith(('.fastq', '.fq', '.fastq.gz', '.fq.gz')) or stat.st_size == 0:
                raise ValueError('not FASTQ')
            record.update(size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns,
                          device=stat.st_dev, inode=stat.st_ino)
            upload = uploads.get((stat.st_dev, stat.st_ino))
            if upload and upload['size_bytes'] == stat.st_size:
                record.update(upload_id=upload['id'], name=upload['original_name'],
                              checksum='sha256:' + upload['sha256'],
                              validation='upload_initial_check')
        except (OSError, ValueError, RuntimeError):
            record['issue'] = '文件缺失、为空、格式后缀不支持或路径不在允许范围'
        return record

    @input_operation
    def register_manifest(self, path, *, source='historical', name=None, uploads=None):
        path = Path(path).absolute()
        # Do not catalog arbitrary external paths, even if an old record refers to one.
        try:
            path.relative_to(self.root)
        except ValueError:
            return None
        dataset_id = uuid.uuid5(uuid.NAMESPACE_URL, 'biollm-dataset:' + str(path)).hex
        with self.repository._connect() as db:
            existing = db.execute('SELECT id FROM datasets WHERE manifest_path=?', (str(path),)).fetchone()
        if existing:
            return existing['id']  # Never silently bless an altered manifest.
        digest, issue, samples = None, None, []
        uploads = self._uploads_by_inode() if uploads is None else uploads
        try:
            content = self._manifest_bytes(path)
            digest = hashlib.sha256(content).hexdigest()
            reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
            if reader.fieldnames != ['sample_id', 'read1', 'read2']:
                raise ValueError('header')
            seen, physical = set(), set()
            for row in reader:
                sid = row.get('sample_id') or ''
                if not SAMPLE_ID.fullmatch(sid) or sid in seen or len(seen) >= 10000 or None in row:
                    raise ValueError('ambiguous samples')
                seen.add(sid)
                reads = []
                for mate in ('read1', 'read2'):
                    raw = row.get(mate) or ''
                    if not raw.strip():
                        raise ValueError('missing mate')
                    read = Path(raw)
                    if not read.is_absolute():
                        read = path.parent / read
                    snapshot = self._snapshot(read, uploads)
                    if 'inode' in snapshot:
                        identity = (snapshot['device'], snapshot['inode'])
                        if identity in physical:
                            raise ValueError('reused mate')
                        physical.add(identity)
                    reads.append(snapshot)
                samples.append((sid, *reads))
            if not samples:
                raise ValueError('no samples')
        except (OSError, ValueError, UnicodeError, csv.Error, RuntimeError):
            issue = '样本清单缺失、格式不正确或配对关系不明确，需要人工确认'
            samples = []
        with self.repository._connect() as db:
            standard = db.execute('SELECT 1 FROM tasks WHERE manifest_path=? LIMIT 1', (str(path),)).fetchone()
            stage = 'raw' if source == 'uploaded' or (samples and all(r['upload_id'] for _, *reads in samples for r in reads)) or standard else 'unknown'
            # A known processed artifact must never be offered to the raw-input workflow.
            read_paths = {r['path'] for _, *reads in samples for r in reads}
            for row in db.execute("SELECT path FROM artifacts WHERE artifact_type IN ('reads.clean','reads.host_removed')"):
                if row['path'] in read_paths:
                    stage = 'processed'
            changed = db.execute('''INSERT OR IGNORE INTO datasets
                (id,name,manifest_path,manifest_sha256,source,stage,issue,created_at)
                VALUES(?,?,?,?,?,?,?,?)''', (dataset_id, name or path.stem, str(path), digest,
                                            source, stage, issue, utc_now())).rowcount
            if changed:
                db.executemany('INSERT INTO dataset_samples VALUES(?,?,?,?)', [
                    (dataset_id, sid, json.dumps(r1), json.dumps(r2)) for sid, r1, r2 in samples
                ])
        return dataset_id

    def _file_view(self, snapshot):
        view = {k: snapshot.get(k) for k in ('name', 'size_bytes', 'upload_id', 'checksum', 'validation')}
        view['availability'] = 'available'
        try:
            stat = self._safe_file(snapshot['path']).stat()
            if snapshot.get('issue') or any(snapshot.get(key) != value for key, value in (
                ('size_bytes', stat.st_size), ('mtime_ns', stat.st_mtime_ns),
                ('device', stat.st_dev), ('inode', stat.st_ino),
            )):
                view['availability'] = 'changed'
        except (OSError, ValueError, RuntimeError):
            view['availability'] = 'missing'
        return view

    def get(self, dataset_id, *, include_samples=True, sample_offset=0, task_offset=0, sample_q=''):
        with self.repository._connect() as db:
            row = db.execute('SELECT * FROM datasets WHERE id=?', (dataset_id,)).fetchone()
            if row is None:
                raise KeyError(dataset_id)
            samples = db.execute('SELECT * FROM dataset_samples WHERE dataset_id=? ORDER BY sample_id', (dataset_id,)).fetchall()
            tasks = [dict(r) for r in db.execute('''SELECT t.id,t.status,t.created_at,'standard' AS kind,t.name,
                (SELECT status FROM steps WHERE task_id=t.id AND name='validate') AS validation_status
                FROM tasks t WHERE t.manifest_path=?
                UNION ALL SELECT task_id,status,created_at,'workflow',NULL,NULL FROM workflow_runs
                WHERE input_manifest=? ORDER BY created_at DESC''', (row['manifest_path'], row['manifest_path']))]
            processed = [dict(r) for r in db.execute('''SELECT a.artifact_id,a.task_id,a.sample_id,
                a.artifact_type,a.node_id,a.producer,a.status FROM artifacts a
                WHERE a.status='active' AND a.artifact_type IN ('reads.clean','reads.host_removed')
                AND (a.task_id IN (SELECT id FROM tasks WHERE manifest_path=?) OR
                     a.task_id IN (SELECT task_id FROM workflow_runs WHERE input_manifest=?))
                ORDER BY a.created_at DESC,a.artifact_id LIMIT 100''', (row['manifest_path'], row['manifest_path']))]
        public_samples = [{'sample_id': r['sample_id'],
                           'read1': self._file_view(json.loads(r['read1_json'])),
                           'read2': self._file_view(json.loads(r['read2_json']))} for r in samples]
        status, reason = 'available', None
        if row['issue'] or row['stage'] != 'raw':
            status, reason = 'needs_review', row['issue'] or '数据阶段未经确认或不是原始 reads，不能直接复用到标准流程'
        else:
            try:
                if hashlib.sha256(self._manifest_bytes(Path(row['manifest_path']))).hexdigest() != row['manifest_sha256']:
                    status, reason = 'changed', '样本清单已发生变化，请人工核对'
            except (OSError, ValueError, RuntimeError):
                status, reason = 'unavailable', '样本清单不可用'
            if status == 'available' and any(s[m]['availability'] != 'available' for s in public_samples for m in ('read1', 'read2')):
                status, reason = 'unavailable', '部分配对文件缺失或发生变化，不能复用'
        result = {k: row[k] for k in ('id', 'name', 'source', 'stage', 'created_at')}
        result.update(status=status, reason=reason, sample_count=len(samples),
                      size_bytes=sum(s[m]['size_bytes'] for s in public_samples for m in ('read1', 'read2')),
                      task_count=len(tasks))
        if include_samples:
            filtered = [sample for sample in public_samples if sample_q.lower() in sample['sample_id'].lower()]
            result.update(samples=filtered[sample_offset:sample_offset + 20],
                          sample_total=len(filtered), sample_offset=sample_offset,
                          tasks=tasks[task_offset:task_offset + 20], task_offset=task_offset, processed_reads=processed,
                          verification_note='上传初检仅检查首条 FASTQ 记录；当前可用性检查为路径及文件属性检查，不代表完整配对校验或质控通过。')
        return result

    @input_operation
    def list(self, *, q='', limit=20, offset=0):
        # Pick up tasks created by the workflow runner or another local module.
        # Limit discovery work per request; never crawl reads directories.
        with self.repository._connect() as db:
            pending = db.execute('''SELECT manifest_path AS path FROM tasks
                WHERE manifest_path NOT IN (SELECT manifest_path FROM datasets)
                UNION SELECT input_manifest AS path FROM workflow_runs
                WHERE input_manifest IS NOT NULL AND input_manifest NOT IN (SELECT manifest_path FROM datasets)
                LIMIT 100''').fetchall()
        if pending:
            uploads = self._uploads_by_inode()
            for item in pending:
                self.register_manifest(Path(item['path']), uploads=uploads)
        with self.repository._connect() as db:
            clause = "WHERE instr(lower(name), lower(?)) > 0 OR EXISTS (SELECT 1 FROM dataset_samples s WHERE s.dataset_id=datasets.id AND instr(lower(s.sample_id),lower(?)) > 0)"
            total = db.execute('SELECT count(*) FROM datasets ' + clause, (q, q)).fetchone()[0]
            ids = db.execute('SELECT id FROM datasets ' + clause + ' ORDER BY created_at DESC,id LIMIT ? OFFSET ?', (q, q, limit, offset)).fetchall()
        return {'items': [self.get(row['id'], include_samples=False) for row in ids], 'total': total, 'limit': limit, 'offset': offset}

    def unpaired_uploads(self, *, limit=20, offset=0):
        with self.repository._connect() as db:
            clause = '''WHERE NOT EXISTS (SELECT 1 FROM dataset_samples s
                WHERE json_extract(s.read1_json,'$.upload_id')=u.id OR json_extract(s.read2_json,'$.upload_id')=u.id)'''
            total = db.execute('SELECT count(*) FROM uploads u ' + clause).fetchone()[0]
            rows = db.execute('SELECT id,original_name,size_bytes,created_at FROM uploads u ' + clause + ' ORDER BY created_at DESC,id LIMIT ? OFFSET ?', (limit, offset)).fetchall()
        return {'items': [dict(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset}

    @input_operation
    def reuse(self, dataset_id):
        detail = self.get(dataset_id)
        if detail['status'] != 'available':
            raise TaskValidationError(detail['reason'])
        with self.repository._connect() as db:
            row = db.execute('SELECT manifest_path FROM datasets WHERE id=?', (dataset_id,)).fetchone()
            if row is None:
                raise KeyError(dataset_id)
        return {'dataset_id': dataset_id, 'manifest_path': row[0], 'sample_count': detail['sample_count'], 'name': detail['name']}
