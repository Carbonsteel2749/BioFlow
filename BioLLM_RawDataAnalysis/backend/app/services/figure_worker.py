"""Resource-admitted renderer execution and verified artifact publication."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .input_lifecycle import input_lock
from .redaction import redact_log


def stop(process):
    if hasattr(process,'stop'):
        process.stop(); return
    if os.name == 'posix':
        for sig in (signal.SIGTERM,signal.SIGKILL):
            try:
                os.killpg(process.pid,sig)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
    elif process.poll() is None:
        process.kill(); process.wait(timeout=5)


def run_redraw(service,task_id,job_id):
    process = None
    try:
        with service._lock:
            if service.get(task_id,job_id)['status'] != 'queued':
                return
            with service.repository._connect() as db:
                db.execute("UPDATE figure_jobs SET status='running' WHERE id=?",(job_id,))
        directory = service.job_dir(task_id,job_id)
        command=[sys.executable,'-m','backend.app.services.figure_render',str(directory)]
        env={**os.environ,'BIOLLM_OLLAMA_MODEL':service.settings.ollama_model,'MPLBACKEND':'Agg',
            'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'}
        with (directory/'render.log').open('wb') as log:
            if service.execution_manager is not None:
                process=service.execution_manager.launch(command,cwd=service.settings.project_root,
                    log_handle=log,task_id='figure-'+job_id,attempt=1,env=env)
            else:
                if service.settings.execution_isolation!='process_group':
                    raise RuntimeError('owned execution manager unavailable')
                process=subprocess.Popen(command,cwd=service.settings.project_root,env=env,
                    stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            with service._lock:
                service._processes[job_id]=process
            started=time.monotonic()
            while process.poll() is None:
                if service.get(task_id,job_id)['status']=='cancelled':
                    stop(process); return
                if time.monotonic()-started>600:
                    stop(process); raise ValueError('绘图超过 10 分钟上限，请减少样本或展示条目')
                time.sleep(.1)
            if process.returncode != 0:
                excerpt=redact_log((directory/'render.log').read_text(encoding='utf-8',errors='replace')[-3000:])
                lines=[line.strip() for line in excerpt.splitlines() if line.strip()]
                diagnostic=lines[-1][:300] if lines else '绘图进程未提供错误详情'
                raise ValueError('绘图失败，请检查输入和参数：'+diagnostic)
        with input_lock(service.settings), service._lock:
            if service.get(task_id,job_id)['status']=='cancelled':
                return
            manifest=json.loads((directory/'result.json').read_text(encoding='utf-8'))
            job=service.get(task_id,job_id)
            artifact_ids=[]
            primary=None
            for name in manifest['files']:
                path=directory/'rendered'/name
                if Path(name).name!=name or path.resolve()!=path or not path.is_file():
                    raise ValueError('绘图产物清单不合法')
                media={'.png':'image/png','.svg':'image/svg+xml','.pdf':'application/pdf',
                    '.tsv':'text/tab-separated-values','.json':'application/json','.md':'text/markdown'}[path.suffix]
                if path.suffix=='.svg':
                    from .stage_figures import _safe_svg
                    if not _safe_svg(path.read_bytes()):
                        raise ValueError('SVG 内容未通过安全校验')
                metadata={'figure_job_id':job_id,'panel_key':job['panel_key'],'current_version':False,
                    'category':'taxonomy' if job['request']['data_type']=='taxonomy' else 'functional',
                    'parameters':manifest['parameters'],'figure_plan':manifest['figure_plan'],
                    'sample_ids':job['request']['sample_ids'],'sample_groups':job['request']['groups'],
                    'title':name,'paper_sections':['Result'],'provenance_file':manifest['files'][-1]}
                artifact=service.artifacts.register(task_id=task_id,producer='figure_workbench',
                    artifact_type='figure.'+metadata['category'] if path.suffix in {'.png','.svg','.pdf'} else 'figure.data',
                    path=path,media_type=media,sample_scope='cohort',cohort_id=task_id,metadata=metadata,
                    parent_artifact_ids=job['request']['source_artifact_ids'])
                artifact_ids.append(artifact['artifact_id'])
                if name==manifest['primary']:
                    primary=artifact['artifact_id']
            if primary is None:
                raise ValueError('缺少主图')
            service._finish(task_id,job_id,'succeeded',result={'artifact_ids':artifact_ids,
                'primary_artifact_id':primary,'figure_plan':manifest['figure_plan']},release_reference=False)
    except Exception as exc:
        with service._lock:
            if service.get(task_id,job_id)['status']!='cancelled':
                service._finish(task_id,job_id,'failed',str(exc)[:2000],release_reference=False)
            with service.repository._connect() as db:
                db.execute("UPDATE artifacts SET status='generation_failed' WHERE task_id=? AND producer='figure_workbench' AND json_extract(metadata_json,'$.figure_job_id')=?",(task_id,job_id))
    finally:
        if process is not None:
            stop(process)
        with service._lock:
            service._processes.pop(job_id,None)
            with service.repository._connect() as db:
                db.execute("DELETE FROM task_file_references WHERE task_id=? AND consumer='figure_jobs' AND reference_id=?",(task_id,job_id))
