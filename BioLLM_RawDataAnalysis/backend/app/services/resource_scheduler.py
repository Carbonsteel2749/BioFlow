"""One FIFO admission queue shared by standard and node-based workflows."""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from collections import deque
from pathlib import Path
from typing import Callable


class ResourceScheduler:
    def __init__(self, state_root: Path, *, default_limit: int = 1,
                 minimum_free_bytes: int = 10 * 1024**3):
        self.root = state_root
        self.minimum_free_bytes = minimum_free_bytes
        self.config_path = state_root / 'state' / 'resource_limits.json'
        self._condition = threading.Condition()
        self._pending = deque()
        self._running: set[str] = set()
        self._disk_reservations: dict[str, dict[str, int]] = {}
        self._thread_budgets: dict[str, int] = {}
        self._closed = False
        self._dispatcher = None
        self.limit = self._validate(default_limit)
        if self.config_path.is_file():
            saved = json.loads(self.config_path.read_text(encoding='utf-8'))
            self.limit = self._validate(saved['max_concurrent_tasks'])

    @staticmethod
    def _validate(limit):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 16:
            raise ValueError('max concurrent tasks must be an integer between 1 and 16')
        return limit

    def configure(self, limit: int, *, confirm_resource_risk: bool = False) -> dict:
        limit = self._validate(limit)
        if limit > 1 and not confirm_resource_risk:
            raise ValueError('confirm resource competition risk before enabling concurrent analyses')
        with self._condition:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.config_path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'max_concurrent_tasks': limit}) + '\n', encoding='utf-8')
            os.replace(temporary, self.config_path)
            self.limit = limit
            self._condition.notify_all()
        return self.snapshot()

    def snapshot(self) -> dict:
        from .resource_preflight import effective_cpu_count
        maximum = effective_cpu_count()
        with self._condition:
            return {'limit': self.limit, 'running': sorted(self._running),
                    'queued': [job[0] for job in self._pending],
                    'minimum_free_bytes': self.minimum_free_bytes,
                    'reserved_minimum_bytes': self._reserved_bytes(),
                    'reservation_scope': 'process_local_estimated_minimum_not_filesystem_quota',
                    'scope': 'single_backend_process_shared_standard_and_dynamic',
                    'cpu': self._cpu_headroom(maximum),
                    'warning': 'Multiple analyses may compete for CPU, RAM and disk.' if self.limit > 1 else None}

    def _cpu_headroom(self, maximum: int) -> dict:
        configured = sum(self._thread_budgets.get(key, 0) for key in self._running)
        unknown = sum(key not in self._thread_budgets for key in self._running)
        return {'running_configured_threads': configured,
                'suggested_remaining_threads': None if unknown else max(0, maximum-configured),
                'unknown_running_jobs': unknown, 'observed_running_jobs': len(self._running),
                'max_threads': maximum,
                'basis': 'highest_node_thread_configuration_per_task_not_os_idle_cpu',
                'scope': 'single_backend_scheduler_only'}

    def cpu_headroom(self, *, maximum: int | None = None) -> dict:
        if maximum is None:
            from .resource_preflight import effective_cpu_count
            maximum = effective_cpu_count()
        with self._condition:
            return self._cpu_headroom(maximum)

    def submit(self, key: str, callback: Callable, *, on_rejected: Callable | None = None,
               on_finished: Callable | None = None,
               resource_preflight: Callable[[], dict] | None = None,
               thread_budget: Callable[[], int | None] | None = None) -> bool:
        with self._condition:
            if self._closed or key in self._running or any(job[0] == key for job in self._pending):
                return False
            self._pending.append((key, callback, on_rejected, on_finished, resource_preflight, thread_budget))
            if self._dispatcher is None:
                self._dispatcher = threading.Thread(target=self._dispatch, daemon=True, name='analysis-admission')
                self._dispatcher.start()
            self._condition.notify_all()
            return True

    def cancel_pending(self, key: str) -> bool:
        with self._condition:
            removed = [job for job in self._pending if job[0] == key]
            self._pending = deque(job for job in self._pending if job[0] != key)
            self._condition.notify_all()
            return bool(removed)

    def _dispatch(self):
        with self._condition:
            while not self._closed:
                if not self._pending or len(self._running) >= self.limit:
                    self._condition.wait()
                    continue
                job = self._pending.popleft()
                self._running.add(job[0])
                threading.Thread(target=self._execute, args=(job,), daemon=True,
                                 name='analysis-worker').start()

    def _execute(self, job):
        key, callback, on_rejected, on_finished, resource_preflight, thread_budget = job
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with self._condition:
                reason = self._reserve_resources(key, resource_preflight)
            if reason:
                if on_rejected:
                    on_rejected(reason)
                else:
                    raise RuntimeError(reason)
            else:
                # Advisory accounting only: never turn an unreadable budget
                # into zero CPU use or change the existing admission policy.
                try:
                    threads = thread_budget() if thread_budget else None
                except Exception:
                    logging.warning('advisory thread configuration unavailable for %s', key)
                    threads = None
                if isinstance(threads, int) and not isinstance(threads, bool) and threads > 0:
                    with self._condition:
                        self._thread_budgets[key] = threads
                callback()
        except Exception:
            logging.exception('analysis worker failed for %s', key)
        finally:
            with self._condition:
                self._disk_reservations.pop(key, None)
                self._thread_budgets.pop(key, None)
                self._running.discard(key)
                self._condition.notify_all()
            if on_finished:
                try:
                    on_finished()
                except Exception:
                    logging.exception('analysis handoff failed for %s', key)

    def _reserved_bytes(self) -> dict[str, int]:
        reserved: dict[str, int] = {}
        for requirements in self._disk_reservations.values():
            for filesystem, size in requirements.items():
                reserved[filesystem] = reserved.get(filesystem, 0) + size
        return reserved

    def _reserve_resources(self, key: str, preflight: Callable[[], dict] | None) -> str | None:
        """Caller holds the condition: observe/check/reserve is one admission step.

        Conservative: consumed reservation bytes are not discounted. External
        writes and other backend processes remain outside this estimate guard.
        """
        if preflight is None:
            free = shutil.disk_usage(self.root).free
            if free < self.minimum_free_bytes:
                return f'insufficient disk space: available={free} bytes; required reserve={self.minimum_free_bytes} bytes'
            return None
        try:
            report = preflight()
        except (ValueError, OSError) as exc:
            return f'Resource preflight: {exc}'
        if report.get('blockers'):
            return 'Resource preflight: ' + '; '.join(item['message'] for item in report['blockers'])
        reserved = self._reserved_bytes()
        requirements = {}
        for mount in report['filesystems']:
            filesystem = mount['filesystem_id']
            size = mount['required_min_bytes']
            if mount['free_bytes'] - mount['reserve_bytes'] - reserved.get(filesystem, 0) < size:
                return 'insufficient disk space after reserve and active task estimated minimum reservations'
            requirements[filesystem] = size
        self._disk_reservations[key] = requirements
        return None

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
