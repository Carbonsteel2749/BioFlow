"""Distinguish historical task completion from locally available file contents."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


def annotate(task):
    if task.get('status') not in {'completed','failed','paused','cancelled'}:
        return task
    from .assets import existing_outputs,read_snapshot
    if not read_snapshot(task['id']):
        task['file_availability']='unknown'
        task['file_availability_label']='成果目录待刷新，请打开项目资料'
        return task
    rows=[r for r in existing_outputs([task['id']],include_inputs=False) if r['kind'] in {'results','charts'}]
    available=sum(r['status']!='empty' for r in rows)
    charts=sum(r['status']!='empty' and r['kind']=='charts' for r in rows)
    task['available_results']=available-charts
    task['available_charts']=charts
    task['file_availability']='not_checked' if available else 'unknown'
    task['file_availability_label']=f'{available-charts} 项结果 · {charts} 张图表已登记，打开时检查文件' if available else '历史记录 · 目录待同步'
    return task
