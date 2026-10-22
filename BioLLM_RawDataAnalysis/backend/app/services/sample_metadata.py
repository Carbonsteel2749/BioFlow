"""Versioned dataset groups and task-bound, immutable plotting inputs."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

from ..models import utc_now
from .input_lifecycle import input_operation

MAX_IMPORT_BYTES = 5 * 1024 * 1024


class MetadataConflictError(ValueError):
    pass


def _tsv(rows):
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter='\t', lineterminator='\n')
    writer.writerow(['sample_id', 'group'])
    writer.writerows((row['sample_id'], row['group']) for row in rows)
    return output.getvalue().encode('utf-8')


class SampleMetadataService:
    def __init__(self, settings, datasets):
        self.settings = settings
        self.datasets = datasets
        self.repository = datasets.repository

    def initialize(self):
        with self.repository._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sample_metadata_versions (
                    dataset_id TEXT NOT NULL, version INTEGER NOT NULL,
                    sha256 TEXT NOT NULL, rows_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(dataset_id, version),
                    FOREIGN KEY(dataset_id) REFERENCES datasets(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS task_sample_metadata (
                    task_id TEXT PRIMARY KEY, manifest_path TEXT NOT NULL,
                    snapshot_json TEXT
                );
            ''')

    def _dataset(self, db, dataset_id):
        dataset = db.execute('SELECT * FROM datasets WHERE id=?', (dataset_id,)).fetchone()
        if dataset is None:
            raise KeyError(dataset_id)
        sample_ids = [row[0] for row in db.execute(
            'SELECT sample_id FROM dataset_samples WHERE dataset_id=? ORDER BY sample_id', (dataset_id,))]
        return dataset, sample_ids

    def _verify_manifest(self, dataset):
        try:
            content = self.datasets._manifest_bytes(Path(dataset['manifest_path']))
        except (OSError, ValueError, RuntimeError) as exc:
            raise ValueError('样本清单无法验证，请重新核对数据集') from exc
        if dataset['issue'] or hashlib.sha256(content).hexdigest() != dataset['manifest_sha256']:
            raise ValueError('样本清单已变化或登记不完整，请重新核对数据集')

    def _get(self, db, dataset_id):
        _, sample_ids = self._dataset(db, dataset_id)
        row = db.execute('SELECT * FROM sample_metadata_versions WHERE dataset_id=? ORDER BY version DESC LIMIT 1', (dataset_id,)).fetchone()
        if row is None:
            return {'dataset_id': dataset_id, 'version': 0, 'sha256': None, 'updated_at': None,
                    'rows': [{'sample_id': sid, 'group': ''} for sid in sample_ids]}
        return {'dataset_id': dataset_id, 'version': row['version'], 'sha256': row['sha256'],
                'updated_at': row['updated_at'], 'rows': json.loads(row['rows_json'])}

    def get(self, dataset_id):
        with self.repository._connect() as db:
            return self._get(db, dataset_id)

    @staticmethod
    def _validate(rows, sample_ids):
        if not isinstance(rows, list) or len(rows) > 10000:
            raise ValueError('样本分组必须是最多 10000 行的列表')
        expected, seen, normalized = set(sample_ids), set(), []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'sample_id', 'group'}:
                raise ValueError('每行必须且只能包含 sample_id 和 group 两列')
            sid, group = row['sample_id'], row['group']
            if not isinstance(sid, str) or sid not in expected:
                raise ValueError(f'未知样本 ID：{str(sid)[:128]}')
            if sid in seen:
                raise ValueError(f'重复样本 ID：{sid}')
            seen.add(sid)
            if not isinstance(group, str) or not group.strip() or len(group) > 128 or any(ord(c) < 32 or ord(c) == 127 for c in group):
                raise ValueError(f'样本 {sid} 的分组不能为空，限 128 字符且不能包含制表符或换行')
            normalized.append({'sample_id': sid, 'group': group.strip()})
        missing = expected - seen
        if missing:
            raise ValueError('缺少样本 ID：' + ', '.join(sorted(missing)[:20]))
        if not normalized:
            raise ValueError('数据集没有可分组的已登记样本')
        return sorted(normalized, key=lambda row: row['sample_id'])

    @input_operation
    def replace(self, dataset_id, rows, expected_version):
        with self.repository._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            dataset, sample_ids = self._dataset(db, dataset_id)
            self._verify_manifest(dataset)
            normalized = self._validate(rows, sample_ids)
            current = self._get(db, dataset_id)
            if type(expected_version) is not int or expected_version != current['version']:
                raise MetadataConflictError('分组已被其他编辑更新，请重新加载后再保存')
            digest = hashlib.sha256(_tsv(normalized)).hexdigest()
            result = {'dataset_id': dataset_id, 'version': current['version'] + 1,
                      'sha256': digest, 'updated_at': utc_now(), 'rows': normalized}
            db.execute('INSERT INTO sample_metadata_versions VALUES(?,?,?,?,?)',
                       (dataset_id, result['version'], digest, json.dumps(normalized, ensure_ascii=False), result['updated_at']))
            return result

    @staticmethod
    def _parse_text(text, format):
        if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_IMPORT_BYTES:
            raise ValueError('分组文件必须为 UTF-8 文本，且不超过 5 MiB')
        if format not in ('csv', 'tsv'):
            raise ValueError('只支持 CSV 或 TSV 分组文件')
        reader = csv.DictReader(io.StringIO(text.removeprefix('\ufeff'), newline=''),
                                delimiter=',' if format == 'csv' else '\t', strict=True)
        try:
            if reader.fieldnames != ['sample_id', 'group']:
                raise ValueError('文件表头必须为 sample_id,group（TSV 使用制表符）')
            parsed = []
            for row in reader:
                if len(parsed) >= 10000 or None in row:
                    raise ValueError('分组文件包含多余列或超过 10000 行')
                parsed.append(row)
        except csv.Error as exc:
            raise ValueError('分组文件格式不正确') from exc
        return parsed

    def import_text(self, dataset_id, text, format, expected_version):
        return self.replace(dataset_id, self._parse_text(text, format), expected_version)

    @input_operation
    def preview(self, dataset_id, expected_version, rows=None, text=None, format=None):
        if (rows is None) == (text is None):
            raise ValueError('请提供 rows 或 text 中的一项')
        parsed = self._parse_text(text, format) if text is not None else rows
        if not isinstance(parsed, list) or len(parsed) > 10000:
            raise ValueError('样本分组必须是最多 10000 行的列表')
        with self.repository._connect() as db:
            dataset, sample_ids = self._dataset(db, dataset_id)
            self._verify_manifest(dataset)
            current = self._get(db, dataset_id)
        if type(expected_version) is not int or expected_version != current['version']:
            raise MetadataConflictError('分组已被其他编辑更新，请重新加载后再预览；当前编辑尚未保存')
        errors, warnings, seen, normalized = [], [], set(), []
        expected = set(sample_ids)
        def issue(code, message, ids):
            return {'code': code, 'message': message, 'sample_ids': ids[:20]}
        for row in parsed:
            if not isinstance(row, dict) or set(row) != {'sample_id', 'group'}:
                errors.append(issue('columns', '每行必须且只能包含 sample_id 和 group 两列', []))
                continue
            sid, group = row['sample_id'], row['group']
            if not isinstance(sid, str) or sid not in expected:
                errors.append(issue('unknown', f'未知样本 ID：{str(sid)[:128]}', [str(sid)[:128]]))
                continue
            if sid in seen:
                errors.append(issue('duplicate', f'重复样本 ID：{sid}', [sid]))
                continue
            seen.add(sid)
            if not isinstance(group, str) or not group.strip() or len(group) > 128 or any(ord(c) < 32 or ord(c) == 127 for c in group):
                errors.append(issue('invalid_group', f'样本 {sid} 的分组不能为空，限 128 字符且不能包含控制字符', [sid]))
                continue
            normalized.append({'sample_id': sid, 'group': group.strip()})
        missing = sorted(expected - seen)
        if missing:
            errors.append(issue('missing', '缺少样本 ID：' + ', '.join(missing[:20]), missing))
        if not sample_ids:
            errors.append(issue('empty_dataset', '数据集没有已登记样本', []))
        normalized.sort(key=lambda row: row['sample_id'])
        counts = {}
        for row in normalized:
            counts[row['group']] = counts.get(row['group'], 0) + 1
        folded = {}
        for group in counts:
            folded.setdefault(group.casefold(), []).append(group)
        for labels in folded.values():
            if len(labels) > 1:
                warnings.append(issue('case_collision', '这些分组标签仅大小写不同，不会自动合并：' + ' / '.join(labels), []))
        for group, count in counts.items():
            if count < 2:
                warnings.append(issue('small_group', f'分组 {group} 只有 {count} 个样本，不支持估计可靠的组内分布或据此进行组间推断', []))
        if len(counts) < 2 and counts:
            warnings.append(issue('single_group', '只有一个分组，仅作描述性展示，不进行组间比较', []))
        return {'dataset_id': dataset_id, 'version': current['version'], 'valid': not errors,
                'errors': errors, 'warnings': warnings, 'group_counts': counts,
                'rows': normalized if not errors else [],
                'sha256': hashlib.sha256(_tsv(normalized)).hexdigest() if not errors else None}

    def get_task_snapshot(self, task_id):
        with self.repository._connect() as db:
            row = db.execute('SELECT snapshot_json FROM task_sample_metadata WHERE task_id=?', (task_id,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def _materialize(self, snapshot):
        if snapshot is None:
            return None
        groups = _tsv(snapshot['rows'])
        if hashlib.sha256(groups).hexdigest() != snapshot['sha256']:
            raise ValueError('任务分组快照摘要不匹配')
        contents = ((Path(snapshot['groups_path']), groups),
                    (Path(snapshot['path']), (json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n').encode('utf-8')))
        for path, content in contents:
            # Never replace a previously materialized task input, even on a retry.
            if path.is_symlink():
                raise ValueError('任务分组快照不能是符号链接')
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open('xb') as handle:
                    handle.write(content)
            except FileExistsError:
                if path.read_bytes() != content:
                    raise ValueError('任务分组快照已变化，拒绝覆盖')
        return snapshot

    @input_operation
    def snapshot_for_manifest(self, manifest_path, task_id, output_dir):
        manifest_path = str(Path(manifest_path).absolute())
        with self.repository._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT * FROM task_sample_metadata WHERE task_id=?', (task_id,)).fetchone()
            if existing:
                if existing['manifest_path'] != manifest_path:
                    raise ValueError('任务分组快照已绑定其他样本清单')
                snapshot = json.loads(existing['snapshot_json']) if existing['snapshot_json'] else None
            else:
                dataset = db.execute('SELECT * FROM datasets WHERE manifest_path=?', (manifest_path,)).fetchone()
                snapshot = None
                if dataset:
                    self._verify_manifest(dataset)
                    metadata = self._get(db, dataset['id'])
                    if metadata['version']:
                        output = Path(output_dir).absolute()
                        snapshot = {**metadata, 'task_id': task_id, 'manifest_sha256': dataset['manifest_sha256'],
                                    'path': str(output / 'sample_metadata.json'), 'groups_path': str(output / 'groups.tsv')}
                db.execute('INSERT INTO task_sample_metadata VALUES(?,?,?)',
                           (task_id, manifest_path, json.dumps(snapshot, ensure_ascii=False) if snapshot else None))
        return self._materialize(snapshot)
