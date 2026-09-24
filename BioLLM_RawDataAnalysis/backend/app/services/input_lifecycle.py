"""Serialize platform input registration/use/deletion across threads and workers."""
from contextlib import contextmanager
from functools import wraps
import os
from pathlib import Path
import threading

_lock = threading.RLock()
_local = threading.local()


@contextmanager
def input_lock(settings):
    with _lock:
        depth = getattr(_local, 'depth', 0)
        _local.depth = depth + 1
        try:
            if depth:
                yield
            else:
                path = Path(settings.state_root) / 'state' / 'input-lifecycle.lock'
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('a') as handle:
                    if os.name == 'posix':
                        import fcntl
                        fcntl.flock(handle, fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        if os.name == 'posix':
                            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            _local.depth = depth


def input_operation(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with input_lock(self.settings):
            return method(self, *args, **kwargs)
    return wrapped
