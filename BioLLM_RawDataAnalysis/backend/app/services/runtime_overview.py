"""Bounded, read-only environment and storage observations; never certify unknowns."""
import json
import os
import shutil
import time
from pathlib import Path

import httpx
from fastapi import APIRouter

from ..config import Settings
from .resource_preflight import effective_cpu_count


def probe_model(settings: Settings) -> dict:
    try:
        response = httpx.get(settings.ollama_url.rstrip('/') + '/api/tags', timeout=3)
        response.raise_for_status()
        names = {entry.get('name') for entry in response.json().get('models', [])}
        present = settings.ollama_model in names
        return {'model': settings.ollama_model, 'status': 'available' if present else 'missing',
                'message': 'Model listed by Ollama; inference/GPU health not tested.' if present else 'Configured model is not listed by Ollama.',
                'vision': 'not_verified'}
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        return {'model': settings.ollama_model, 'status': 'unavailable', 'message': type(exc).__name__, 'vision': 'not_verified'}


def memory_available() -> int | None:
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def measure_files(root: Path, deadline: float) -> dict:
    count = size = 0
    complete = True
    if not root.is_dir() or root.is_symlink():
        return {'bytes': 0, 'files': 0, 'complete': True}
    pending = [root]
    while pending:
        if time.monotonic() > deadline:
            return {'bytes': size, 'files': count, 'complete': False}
        try:
            with os.scandir(pending.pop()) as entries:
                while True:
                    if time.monotonic() > deadline:
                        return {'bytes': size, 'files': count, 'complete': False}
                    try:
                        entry = next(entries)
                    except StopIteration:
                        break
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            size += entry.stat(follow_symlinks=False).st_size
                            count += 1
                    except OSError:
                        complete = False
        except OSError:
            complete = False
    return {'bytes': size, 'files': count, 'complete': complete}


def create_runtime_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix='/api/runtime', tags=['runtime'])
    cached = {'at': 0.0, 'value': None}

    @router.get('/environment')
    def environment():
        usage = shutil.disk_usage(settings.state_root)
        manifest = settings.default_database_manifest
        database = {'profile': settings.default_database_profile,
                    'status': 'missing' if manifest is None or not manifest.is_file() else 'configured',
                    'message': 'Resolved manifest presence only; each workflow still performs database and version preflight.'}
        if database['status'] == 'configured':
            try:
                json.loads(manifest.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                database['status'] = 'invalid'
        tools = [{'name': name, 'status': 'located' if shutil.which(name) else 'missing'}
                 for name in (settings.nextflow_bin, 'java', 'fastqc', 'fastp', 'bowtie2', 'kraken2', 'bracken', 'humann', 'multiqc')]
        return {'backend': {'status': 'available'}, 'model': probe_model(settings),
                'database': database, 'tools': tools,
                'resources': {'cpu_count': effective_cpu_count(), 'memory_available_bytes': memory_available(),
                              'disk_free_bytes': usage.free, 'disk_total_bytes': usage.total},
                'limitation': 'Executable presence is not a version compatibility or GPU inference test.'}

    @router.get('/storage')
    def storage():
        if cached['value'] is not None and time.monotonic() - cached['at'] < 30:
            return cached['value']
        deadline = time.monotonic() + 3
        roots = {'uploads': settings.input_root, 'cache': settings.state_root/'work',
                 'results': settings.state_root/'outputs', 'logs': settings.state_root/'logs'}
        value = {'categories': {key: measure_files(root, deadline) for key, root in roots.items()},
                 'measurement': 'apparent_file_bytes_excluding_symlinks_not_quota',
                 'warning': 'Incomplete scans are lower bounds; hard links may be counted more than once. A stalled filesystem syscall can exceed the scan budget. Cleanup requires task/reference checks.',
                 'observed_at': time.time()}
        cached.update(at=time.monotonic(), value=value)
        return value

    return router
