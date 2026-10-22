"""Task-local redraw jobs; input snapshots and figure versions are immutable."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import sqlite3
import threading
import uuid
from pathlib import Path

from pydantic import ValidationError

from ..models import utc_now
from .artifacts import ArtifactIntegrityError, ArtifactValidationError, SAFE_IDENTIFIER
from .figure_contracts import CHARTS, SOURCE_TYPES, FigureParameters, FigureRequest
from .input_lifecycle import input_operation

BUSY = {'queued', 'running', 'archiving'}
STOPPED = {'completed', 'failed', 'paused', 'cancelled'}
STANDARD_SOURCES = {
    'taxonomy': ('tables/species_abundance.tsv', 'taxonomy/*/species_abundance.tsv'),
    'gene_families': ('tables/gene_families.tsv', 'functional_annotation/*/read_genefamilies.tsv'),
    'ko': ('tables/ko_abundance/read_ko.tsv', 'functional_annotation/*/read_ko.tsv'),
    'ec': ('tables/ec_abundance/read_ec.tsv', 'functional_annotation/*/read_ec.tsv'),
    'pathway_abundance': ('tables/pathway_abundance.tsv', 'functional_annotation/*/read_pathabundance.tsv'),
    'pathway_coverage': ('tables/pathway_coverage/read_pathcoverage.tsv', 'functional_annotation/*/read_pathcoverage.tsv'),
}


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024*1024), b''):
            result.update(block)
    return result.hexdigest()


class FigureWorkbench:
    def __init__(self, settings, repository, artifacts, scheduler, execution_manager=None, metadata_service=None):
        self.settings, self.repository = settings, repository
        self.artifacts, self.scheduler = artifacts, scheduler
        self.execution_manager = execution_manager
        self.metadata_service = metadata_service
        self._lock = threading.RLock()
        self._processes = {}

    def initialize(self):
        with self.repository._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS figure_jobs (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL,
                    panel_key TEXT NOT NULL, status TEXT NOT NULL, request_json TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}', error TEXT,
                    created_at TEXT NOT NULL, finished_at TEXT,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE);
                CREATE INDEX IF NOT EXISTS figure_jobs_task ON figure_jobs(task_id,created_at);
                CREATE TABLE IF NOT EXISTS figure_current (
                    task_id TEXT NOT NULL, panel_key TEXT NOT NULL, job_id TEXT NOT NULL,
                    PRIMARY KEY(task_id,panel_key),
                    FOREIGN KEY(job_id) REFERENCES figure_jobs(id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS figure_archive_state (
                    task_id TEXT PRIMARY KEY, selection_sha256 TEXT NOT NULL,
                    archive_sha256 TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS task_file_references (
                    task_id TEXT NOT NULL, consumer TEXT NOT NULL, reference_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(task_id,consumer,reference_id),
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE RESTRICT);
            ''')
            db.execute("UPDATE figure_jobs SET status='interrupted',error=?,finished_at=? WHERE status IN ('queued','running','archiving')",
                ('服务重启中断了此次操作，请新建重绘或重新更新结果包。',utc_now()))
            db.execute("DELETE FROM task_file_references WHERE consumer='figure_jobs'")

    def task(self, task_id):
        if not SAFE_IDENTIFIER.fullmatch(task_id):
            raise ValueError('任务 ID 不合法')
        task = self.repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def job_dir(self, task_id, job_id):
        if not all(SAFE_IDENTIFIER.fullmatch(value) for value in (task_id, job_id)):
            raise ValueError('版本 ID 不合法')
        path = self.artifacts.output_root/task_id/'figure_workbench'/job_id
        if path.resolve() != path:
            raise ValueError('绘图目录含不安全链接')
        return path

    def _source(self, task_id, artifact_id, data_type):
        record = self.artifacts.repository.get(artifact_id)
        if not record or record['task_id'] != task_id:
            raise ValueError('绘图来源必须属于同一任务')
        if record['status'] != 'active' or record['artifact_type'] != SOURCE_TYPES[data_type]:
            raise ValueError('来源不是可用的对应类型标准化结果表')
        try:
            path = self.artifacts.resolve_local(artifact_id)
        except (ArtifactIntegrityError, ArtifactValidationError, OSError) as exc:
            raise ValueError('来源文件完整性校验失败，请核对分析结果') from exc
        if path.stat().st_size > 100_000_000:
            raise ValueError('单张结果表超过当前重绘上限 100 MB')
        content=path.read_bytes()
        if hashlib.sha256(content).hexdigest()!=record['sha256']:
            raise ValueError('来源文件完整性校验失败')
        fields, rows = self._read_table(content, data_type)
        return record, fields, rows

    @staticmethod
    def _read_table(content, data_type):
        with io.StringIO(content.decode('utf-8-sig'), newline='') as handle:
            reader = csv.DictReader(handle, delimiter='\t')
            fields = reader.fieldnames or []
            required = {'sample_id', 'abundance', 'abundance_unit','database_profile','database_release'}
            required |= {'taxid','taxonomy_system','taxonomy_rank'} if data_type == 'taxonomy' else {'function_id','normalization_method'}
            if not required.issubset(fields) or len(fields) != len(set(fields)):
                raise ValueError('来源缺少标准化列或包含重复列，不能仅按文件名猜测格式')
            rows = []
            for row in reader:
                if len(rows) >= 1_000_000:
                    raise ValueError('结果表超过当前重绘行数上限')
                if None in row or any(value is None for value in row.values()):
                    raise ValueError('结果表列数不一致')
                try:
                    number = float(row['abundance'])
                except ValueError as exc:
                    raise ValueError('丰度必须为有限非负数字') from exc
                if not math.isfinite(number) or number < 0 or not row['sample_id']:
                    raise ValueError('丰度或样本标识不合法')
                rows.append(row)
        return fields, rows

    def _register_standard_sources(self, task_id):
        if self.task(task_id)['status'] not in STOPPED:
            return []
        output = self.artifacts.output_root / task_id
        existing = {row['path'] for row in self.artifacts.repository.list_for_task(task_id)}
        warnings = []
        for data_type, (canonical, fallback) in STANDARD_SOURCES.items():
            path = output / canonical
            # Stage tables retain every sample even when a legacy report used
            # a same-named copy instead of a cohort merge (KO/EC/coverage).
            candidates = sorted(output.glob(fallback))[:1000]
            if not candidates and (path.exists() or path.is_symlink()):
                candidates = [path]
            for path in candidates:
                if str(path) in existing:
                    continue
                try:
                    if path.resolve() != path or not path.is_file() or path.stat().st_size > 100_000_000:
                        raise ValueError('unsafe or oversized result table')
                    content = path.read_bytes()
                    fields, rows = self._read_table(content, data_type)
                    if not rows:
                        continue
                    self.artifacts.register(task_id=task_id, producer='standard.' + data_type,
                        artifact_type=SOURCE_TYPES[data_type], path=path, media_type='text/tab-separated-values',
                        sample_scope='cohort', cohort_id=task_id,
                        expected_sha256=hashlib.sha256(content).hexdigest(), expected_size_bytes=len(content),
                        metadata={'standard_source': True, 'relative_path': path.relative_to(output).as_posix()})
                    existing.add(str(path))
                except (ValueError, OSError):
                    warnings.append('一份标准流程结果表未通过格式或路径校验，未登记为绘图来源。')
        return warnings

    @input_operation
    def catalog(self, task_id):
        self.task(task_id)
        sources, warnings = [], self._register_standard_sources(task_id)
        for item in self.artifacts.list_for_task(task_id):
            data_type = next((key for key,value in SOURCE_TYPES.items() if value == item['artifact_type']),None)
            if data_type is None or item['status'] != 'active':
                continue
            try:
                _, _, rows = self._source(task_id,item['artifact_id'],data_type)
                sources.append({'artifact_id':item['artifact_id'],'file_name':item['file_name'],
                    'data_type':data_type,'sample_ids':sorted({r['sample_id'] for r in rows}),
                    'row_count':len(rows), 'sha256':item['sha256']})
            except (ValueError,OSError):
                warnings.append('一份结果表未通过标准格式或完整性校验，已禁止用于重绘。')
        groups={}
        if self.metadata_service:
            snapshot=self.metadata_service.get_task_snapshot(task_id)
            if snapshot:
                from .sample_metadata import _tsv
                if hashlib.sha256(_tsv(snapshot['rows'])).hexdigest()==snapshot['sha256']:
                    groups={row['sample_id']:row['group'] for row in snapshot['rows']}
                else:
                    warnings.append('原任务分组快照未通过校验，请重新填写本次绘图分组。')
        return {'sources':sources,'charts':CHARTS,'parameters':FigureParameters.model_json_schema()['properties'],
                'default_groups':groups,
                'warnings':warnings,'scope':'single_standard_task','statistical_tests':'not_performed'}

    @input_operation
    def create(self, task_id, payload):
        task = self.task(task_id)
        if task['status'] not in STOPPED:
            raise ValueError('请先等待或停止分析任务，再进行重绘')
        try:
            request = FigureRequest.model_validate(payload)
        except ValidationError as exc:
            raise ValueError('绘图参数不合法：'+str(exc.errors(include_url=False)[0]['msg'])) from exc
        if len(set(request.sample_ids)) != len(request.sample_ids) or len(set(request.source_artifact_ids)) != len(request.source_artifact_ids):
            raise ValueError('样本与来源不能重复选择')
        if request.groups and (set(request.groups) != set(request.sample_ids) or
                any(not label.strip() or len(label)>80 for label in request.groups.values())):
            raise ValueError('分组信息必须完整对应本次选择的样本')
        recipe = next((r for r in CHARTS[request.data_type] if r['id']==request.chart),None)
        if not recipe:
            raise ValueError('未登记的绘图方法')
        if request.data_type == 'pathway_coverage' and request.chart in {'composition','functional_clustered_heatmap'}:
            raise ValueError('通路覆盖度不是可相加的丰度；该图型不适用')
        fields, rows, seen, checksums = None, [], set(), {}
        contexts = set()
        source_bytes,source_count=0,0
        for identifier in request.source_artifact_ids:
            record=self.artifacts.repository.get(identifier)
            if not record or record['task_id']!=task_id:
                raise ValueError('绘图来源必须属于同一任务')
            source_bytes+=record['size_bytes']
        if source_bytes>100_000_000:
            raise ValueError('所选结果表合计超过当前重绘上限 100 MB，请减少来源')
        for identifier in request.source_artifact_ids:
            record, names, source_rows = self._source(task_id,identifier,request.data_type)
            source_count+=len(source_rows)
            if source_count>1_000_000:
                raise ValueError('所选结果表合计超过当前重绘行数上限，请减少来源')
            if fields is not None and set(fields) != set(names):
                raise ValueError('来源表结构不一致')
            fields = fields or names
            checksums[identifier] = record['sha256']
            for row in source_rows:
                if row['sample_id'] not in request.sample_ids:
                    continue
                key = (row['sample_id'],row.get('taxonomy_system'),row.get('taxid')) if request.data_type == 'taxonomy' else (row['sample_id'],row['function_id'])
                if key in seen:
                    raise ValueError('来源表存在重复样本条目，请勿同时选择汇总表和其单样本表')
                seen.add(key); rows.append(row)
                contexts.add(tuple(row.get(k,'') for k in ('abundance_unit','database_profile','database_release','taxonomy_system','normalization_method')))
        if set(request.sample_ids) != {r['sample_id'] for r in rows}:
            raise ValueError('选择的样本不在对应来源结果中')
        if len(contexts) != 1 or any(not row[k] for row in rows for k in ('abundance_unit','database_profile','database_release')):
            raise ValueError('结果单位、数据库版本或标准化方法不兼容')
        positive = [r for r in rows if float(r['abundance'])>0 and not r.get('function_id','').split(':')[0] in {'UNMAPPED','READS_UNMAPPED','UNGROUPED','UNINTEGRATED'}]
        sample_count = len({r['sample_id'] for r in positive})
        feature_count = len({r.get('taxid',r.get('function_id')) for r in positive})
        if sample_count < recipe['minimum_samples'] or feature_count < recipe['minimum_features']:
            raise ValueError(f"该图至少需要 {recipe['minimum_samples']} 个有有效信号的样本及 {recipe['minimum_features']} 个条目；当前为 {sample_count} 个样本、{feature_count} 个条目")
        job_id = str(uuid.uuid4())
        directory = self.job_dir(task_id,job_id)
        directory.mkdir(parents=True,exist_ok=False)
        with (directory/'source.tsv').open('w',encoding='utf-8',newline='') as handle:
            writer = csv.DictWriter(handle,fieldnames=fields,delimiter='\t',lineterminator='\n')
            writer.writeheader(); writer.writerows(rows)
        spec = request.model_dump()
        spec.update(source_sha256=checksums,snapshot_sha256=digest(directory/'source.tsv'),
            profile={'sample_count':sample_count,'feature_count':feature_count},task_id=task_id,job_id=job_id)
        (directory/'request.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
        self._insert(task_id,job_id,'redraw',request.data_type+':'+request.chart,spec)
        if not self.scheduler.submit('figure:'+job_id,lambda:self._run(task_id,job_id),
                on_rejected=lambda reason:self._finish(task_id,job_id,'failed','资源检查未通过：'+reason)):
            self._finish(task_id,job_id,'failed','无法加入运行队列')
        return self.get(task_id,job_id)

    def _insert(self,task_id,job_id,kind,panel_key,spec):
        with self.repository._connect() as db:
            db.execute('INSERT INTO figure_jobs(id,task_id,kind,panel_key,status,request_json,created_at) VALUES(?,?,?,?,?,?,?)',
                (job_id,task_id,kind,panel_key,'queued',json.dumps(spec,ensure_ascii=False),utc_now()))
            db.execute("INSERT INTO task_file_references VALUES(?,'figure_jobs',?,?)",(task_id,job_id,utc_now()))

    def get(self,task_id,job_id):
        with self.repository._connect() as db:
            row = db.execute('SELECT j.*,EXISTS(SELECT 1 FROM figure_current c WHERE c.job_id=j.id) AS is_current FROM figure_jobs j WHERE j.task_id=? AND j.id=?',(task_id,job_id)).fetchone()
        if row is None:
            raise KeyError(job_id)
        item = dict(row)
        spec = json.loads(item.pop('request_json'))
        item['request'] = {key:value for key,value in spec.items() if key not in {'source_sha256','snapshot_sha256','task_id','job_id'}}
        item['result'] = json.loads(item.pop('result_json'))
        item['is_current'] = bool(item['is_current'])
        return item

    def list(self,task_id):
        self.task(task_id)
        with self.repository._connect() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM figure_jobs WHERE task_id=? ORDER BY created_at DESC LIMIT 100',(task_id,))]
        return {'items':[self.get(task_id,identifier) for identifier in ids], 'archive':self.archive_status(task_id)}

    def _finish(self,task_id,job_id,status,error=None,result=None,*,release_reference=True):
        with self.repository._connect() as db:
            db.execute('UPDATE figure_jobs SET status=?,error=?,result_json=?,finished_at=? WHERE id=? AND task_id=?',
                (status,error,json.dumps(result or {},ensure_ascii=False),utc_now(),job_id,task_id))
            if release_reference:
                db.execute("DELETE FROM task_file_references WHERE task_id=? AND consumer='figure_jobs' AND reference_id=?",(task_id,job_id))

    def cancel(self,task_id,job_id):
        with self._lock:
            job = self.get(task_id,job_id)
            if job['kind'] == 'archive' and job['status'] in {'running','archiving'}:
                raise ValueError('结果包正在校验与发布，请等待完成')
            if job['status'] in BUSY:
                self.scheduler.cancel_pending('figure:'+job_id)
                self._finish(task_id,job_id,'cancelled',release_reference=job['status']=='queued')
        return self.get(task_id,job_id)

    def _run(self,task_id,job_id):
        from .figure_worker import run_redraw
        run_redraw(self,task_id,job_id)

    def archive_status(self,task_id):
        with self.repository._connect() as db:
            selection = dict(db.execute('SELECT panel_key,job_id FROM figure_current WHERE task_id=? ORDER BY panel_key',(task_id,)))
            row = db.execute('SELECT selection_sha256 FROM figure_archive_state WHERE task_id=?',(task_id,)).fetchone()
        checksum = hashlib.sha256(json.dumps(selection,sort_keys=True).encode()).hexdigest()
        archive=self.task(task_id).get('result_archive')
        available=bool(archive and Path(archive).is_file())
        return {'current_versions':selection,'selection_sha256':checksum,
                'stale':bool(selection) and (not available or not row or row[0]!=checksum),
                'has_archive':available}

    @input_operation
    def select(self,task_id,job_id):
        self.task(task_id)
        job=self.get(task_id,job_id)
        if job['kind']!='redraw' or job['status']!='succeeded':
            raise ValueError('只能选用成功生成的图表版本')
        for identifier in job['result']['artifact_ids']:
            self.artifacts.resolve_local(identifier)
        with self.repository._connect() as db:
            db.execute('INSERT INTO figure_current(task_id,panel_key,job_id) VALUES(?,?,?) ON CONFLICT(task_id,panel_key) DO UPDATE SET job_id=excluded.job_id',
                (task_id,job['panel_key'],job_id))
            rows=db.execute("SELECT artifact_id,metadata_json FROM artifacts WHERE task_id=? AND producer='figure_workbench'",(task_id,)).fetchall()
            for row in rows:
                metadata=json.loads(row['metadata_json'])
                if metadata.get('panel_key')==job['panel_key']:
                    metadata['current_version']=metadata.get('figure_job_id')==job_id
                    db.execute('UPDATE artifacts SET metadata_json=? WHERE artifact_id=?',(json.dumps(metadata,ensure_ascii=False),row['artifact_id']))
        return self.get(task_id,job_id)

    @input_operation
    def refresh_archive(self,task_id):
        task=self.task(task_id)
        if task['status'] not in STOPPED:
            raise ValueError('请先停止分析任务，再更新结果包')
        with self.repository._connect() as db:
            if db.execute("SELECT 1 FROM figure_jobs WHERE task_id=? AND status IN ('queued','running','archiving')",(task_id,)).fetchone():
                raise ValueError('请先等待当前重绘或打包操作完成')
        identifier=str(uuid.uuid4())
        self.job_dir(task_id,identifier).mkdir(parents=True,exist_ok=False)
        self._insert(task_id,identifier,'archive','archive',{})
        if not self.scheduler.submit('figure:'+identifier,lambda:self._run_archive(task_id,identifier),
                on_rejected=lambda reason:self._finish(task_id,identifier,'failed','资源检查未通过：'+reason)):
            self._finish(task_id,identifier,'failed','无法加入运行队列')
        return self.get(task_id,identifier)

    def _build_archive(self,task_id):
        from .full_archive import FullArchiveService
        return FullArchiveService(self.settings,self.repository).build(task_id)

    @input_operation
    def _run_archive(self,task_id,job_id):
        try:
            with self._lock:
                if self.get(task_id,job_id)['status']!='queued':
                    return
                with self.repository._connect() as db:
                    changed=db.execute("UPDATE figure_jobs SET status='archiving' WHERE id=? AND task_id=? AND status='queued'",(job_id,task_id)).rowcount
                if not changed:
                    return
            state=self.archive_status(task_id)
            selected={}
            for panel,identifier in state['current_versions'].items():
                job=self.get(task_id,identifier)
                files=[]
                for artifact_id in job['result']['artifact_ids']:
                    path=self.artifacts.resolve_local(artifact_id)
                    record=self.artifacts.get(artifact_id)
                    files.append({'artifact_id':artifact_id,
                        'path':path.relative_to(self.artifacts.output_root/task_id).as_posix(),
                        'archive_path':'intermediates/outputs/'+path.relative_to(self.artifacts.output_root/task_id).as_posix(),
                        'sha256':record['sha256'],'media_type':record['media_type']})
                selected[panel]={'job_id':identifier,'artifact_ids':job['result']['artifact_ids'],
                    'primary_artifact_id':job['result']['primary_artifact_id'],'request':job['request'],
                    'files':files,'path_base':'task results root; archive_path is relative to the full archive root'}
            root=self.artifacts.output_root/task_id/'figure_workbench'
            manifest={'schema_version':'1.0','task_id':task_id,'selection_sha256':state['selection_sha256'],
                'figures':selected,'meaning':'Selected figure versions; retained historical versions are not the selected analysis.',
                'created_at':utc_now()}
            temporary=root/('selection-'+job_id+'.tmp')
            temporary.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
            os.replace(temporary,root/'selected_figures.json')
            previous=self.task(task_id).get('result_archive')
            if previous:
                previous=Path(previous)
                if previous.resolve()!=previous or not previous.is_relative_to(self.artifacts.output_root/task_id) or not previous.is_file():
                    raise ValueError('旧结果包路径或完整性异常')
                checksum=digest(previous)
                backup=self.artifacts.output_root/task_id/'deliverables/.figure_backups'/(checksum+'.tar.gz')
                if backup.resolve()!=backup:
                    raise ValueError('备份路径不安全')
                backup.parent.mkdir(parents=True,exist_ok=True)
                marker=backup.parent/'.sensitive-archive'
                if not marker.exists():
                    with marker.open('x',encoding='utf-8') as handle:
                        handle.write('Private historical task archive; authorized SSH/SFTP only.\n')
                if not backup.exists():
                    os.link(previous,backup)
                if digest(backup)!=checksum:
                    raise ValueError('旧结果包备份校验失败')
            self._build_archive(task_id)
            archive=Path(self.task(task_id)['result_archive'])
            checksum=digest(archive)
            with self.repository._connect() as db:
                db.execute('INSERT INTO figure_archive_state VALUES(?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET selection_sha256=excluded.selection_sha256,archive_sha256=excluded.archive_sha256,updated_at=excluded.updated_at',
                    (task_id,state['selection_sha256'],checksum,utc_now()))
            self._finish(task_id,job_id,'succeeded',result={'archive_sha256':checksum,'selection_sha256':state['selection_sha256']})
        except Exception as exc:
            self._finish(task_id,job_id,'failed','结果包更新失败；原包保留：'+str(exc)[:1000])
