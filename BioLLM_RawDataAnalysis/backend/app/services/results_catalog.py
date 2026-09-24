"""Paginated cross-task view of registered result artifacts."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Query

from .artifacts import ARTIFACT_TYPES
from .stage_figures import publish_stage_figures


class ResultsCatalog:
    def __init__(self, repository, artifacts):
        self.repository = repository
        self.artifacts = artifacts

    def refresh(self, *, offset=0, limit=20):
        with self.repository._connect() as db:
            total = db.execute('SELECT count(*) FROM tasks').fetchone()[0]
            rows = db.execute('SELECT id FROM tasks ORDER BY created_at DESC,id LIMIT ? OFFSET ?', (limit, offset)).fetchall()
        warnings = []
        for row in rows:
            try:
                publish_stage_figures(self.artifacts, row['id'])
            except (OSError, ValueError, sqlite3.Error):
                warnings.append({'task_id': row['id'], 'message': '阶段产物登记失败，可到任务详情核对'})
        return {'processed': len(rows), 'next_offset': offset + len(rows),
                'remaining': max(0, total - offset - len(rows)), 'warnings': warnings}

    def list(self, *, task_id='', sample_id='', artifact_type='', q='', limit=20, offset=0):
        joins = '''FROM artifacts a LEFT JOIN tasks t ON t.id=a.task_id
            LEFT JOIN workflow_runs w ON w.task_id=a.task_id'''
        clauses = ["a.status='active'", "a.artifact_type NOT LIKE 'reads.%'",
                   '(t.id IS NOT NULL OR w.task_id IS NOT NULL)']
        args = []
        for column, value in (('a.task_id', task_id), ('a.sample_id', sample_id), ('a.artifact_type', artifact_type)):
            if value:
                clauses.append(column + '=?')
                args.append(value)
        if q:
            clauses.append('(instr(lower(a.task_id),lower(?))>0 OR instr(lower(COALESCE(a.sample_id,\'\')),lower(?))>0 OR instr(lower(a.artifact_type),lower(?))>0)')
            args.extend([q, q, q])
        where = ' WHERE ' + ' AND '.join(clauses)
        with self.repository._connect() as db:
            total = db.execute('SELECT count(*) ' + joins + where, args).fetchone()[0]
            rows = db.execute('''SELECT a.*,COALESCE(t.status,w.status) AS task_status,
                CASE WHEN t.id IS NULL THEN 'workflow' ELSE 'standard' END AS task_kind,
                t.parameters_json, t.result_archive, t.name AS task_name ''' + joins + where +
                ' ORDER BY a.created_at DESC,a.artifact_id LIMIT ? OFFSET ?', [*args, limit, offset]).fetchall()
            parents = {}
            if rows:
                ids = [row['artifact_id'] for row in rows]
                for relation in db.execute('SELECT artifact_id,parent_artifact_id FROM artifact_relations WHERE artifact_id IN (' + ','.join('?' for _ in ids) + ') ORDER BY parent_artifact_id', ids):
                    parents.setdefault(relation['artifact_id'], []).append(relation['parent_artifact_id'])
        items = []
        for row in rows:
            record = dict(row)
            record['derived_from'] = parents.get(record['artifact_id'], [])
            item = self.artifacts._public(record)
            path = Path(record['path'])
            try:
                if path.resolve() != path or not path.is_relative_to(self.artifacts.output_root / record['task_id']):
                    raise ValueError('unsafe file')
                if not path.is_file():
                    item['status'] = 'missing'
                elif path.stat().st_size != record['size_bytes']:
                    item['status'] = 'validation_failed'
            except (OSError, ValueError, RuntimeError):
                item['status'] = 'validation_failed'
            if item['status'] != 'active':
                item.update(downloadable=False, download_url=None)
            item.update(task_status=record['task_status'], task_kind=record['task_kind'], task_name=record['task_name'],
                        partial=record['task_status'] not in ('completed', 'succeeded'),
                        has_result_archive=bool(record['result_archive']) and record['task_status'] == 'completed')
            # Only non-path operational parameters are exposed by the aggregate view.
            parameters = json.loads(record['parameters_json'] or '{}')
            item['run_parameters'] = {k: v for k, v in parameters.items()
                                      if k in ('threads', 'read_length', 'enable_mags', 'assembler',
                                               'fastp_length_required', 'fastp_qualified_quality_phred', 'host_filter_mode')}
            items.append(item)
        return {'items': items, 'total': total, 'limit': limit, 'offset': offset}


def create_results_router(service):
    router = APIRouter()

    @router.post('/api/results/refresh')
    def refresh(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=20)):
        return service.refresh(offset=offset, limit=limit)

    @router.get('/api/results')
    def results(task_id: str = Query('', max_length=128), sample_id: str = Query('', max_length=128),
                artifact_type: str = Query('', max_length=128), q: str = Query('', max_length=200),
                limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
        return service.list(task_id=task_id, sample_id=sample_id, artifact_type=artifact_type, q=q, limit=limit, offset=offset)

    @router.get('/api/result-types')
    def types():
        return [name for name in ARTIFACT_TYPES if not name.startswith('reads.')]

    return router
