"""Per-attempt user-systemd containment; never signal a remembered PID.

The caller must serialize backend startup/reconciliation against new launches.
These leases protect against stale identities and accidental cross-run kills,
not against a hostile process running as the same Unix user.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Mapping, Sequence
import uuid


class ExecutionUnitError(RuntimeError):
    pass


class ExecutionUnitUnavailable(ExecutionUnitError):
    pass


class ExecutionUnitOwnershipError(ExecutionUnitError):
    pass


class ExecutionBackendOwner:
    """One live backend may reconcile/launch units for a state root at a time."""
    def __init__(self, state_root: Path):
        import fcntl
        directory = Path(state_root).resolve() / 'state'
        directory.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(directory / 'execution-owner.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self.fd)
            self.fd = None
            raise ExecutionUnitOwnershipError('another backend owns this state root; refusing reconciliation') from exc

    def close(self):
        if getattr(self, 'fd', None) is not None:
            os.close(self.fd)
            self.fd = None

    def __del__(self):
        self.close()


def _uid() -> int:
    return os.getuid() if hasattr(os, 'getuid') else -1


def _boot_id() -> str:
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        return 'unavailable'


def _sync_directory(path: Path) -> None:
    if os.name == 'posix':
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _atomic_json(path: Path, data: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(data, stream, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


# Executed by systemd, not by the backend. The payload cannot run in the
# pre-registration crash window: it needs an invocation-specific durable grant.
_ENTRY = '''import json, os, re, time
from pathlib import Path
root = Path(__file__).resolve().parent
invocation = os.environ.get("INVOCATION_ID", "")
if not re.fullmatch(r"[0-9a-f]{32}", invocation):
    raise SystemExit(125)
with (root / "payload.json").open() as stream:
    payload = json.load(stream)
receipt = root / "receipt.json"
temporary = root / "receipt.tmp"
fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as stream:
    json.dump({"invocation_id": invocation, "pid": os.getpid()}, stream)
    stream.flush()
    os.fsync(stream.fileno())
os.replace(temporary, receipt)
fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
os.fsync(fd)
os.close(fd)
deadline = time.monotonic() + payload["grant_timeout"]
while time.monotonic() < deadline:
    try:
        lease = json.loads((root / "lease.json").read_text())
        if lease["status"] in ("revoked", "stopped", "finished"):
            raise SystemExit(125)
        permit = json.loads((root / "permit.json").read_text())
        if (permit.get("invocation_id") == invocation
                and lease.get("invocation_id") == invocation
                and lease.get("status") == "running"):
            os.execvpe(payload["command"][0], payload["command"], payload["env"])
    except FileNotFoundError:
        pass
    time.sleep(0.02)
raise SystemExit(125)
'''


class ExecutionUnitProcess:
    """Popen-compatible observation; PID is informational, stop is unit-only."""

    def __init__(self, manager: 'ExecutionUnitManager', monitor: subprocess.Popen, lease: dict):
        self.manager = manager
        self.monitor = monitor
        self.lease = lease
        self.pid = lease['pid']
        self.returncode = None

    def _finished(self, code: int) -> int:
        if self.returncode is None:
            # A dead observer does not prove the workload is gone. Check and
            # stop any still-live *verified* unit before exposing completion.
            self.manager.stop(self.lease)
            self.returncode = code
        return self.returncode

    def poll(self):
        code = self.monitor.poll()
        return None if code is None else self._finished(code)

    def wait(self, timeout=None):
        return self._finished(self.monitor.wait(timeout=timeout))

    def stop(self, timeout=None):
        return self.manager.stop(self.lease, timeout=timeout)


class ExecutionUnitManager:
    def __init__(self, state_root: Path, *, stop_timeout: float = 10.0, launch_timeout: float = 10.0):
        if stop_timeout <= 0 or launch_timeout <= 0:
            raise ValueError('execution timeouts must be positive')
        self.state_root = Path(state_root).resolve()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.uid = _uid()
        self.boot_id = _boot_id()
        info = self.state_root.stat()
        identity = f'{self.state_root}\0{self.uid}\0{info.st_dev}\0{info.st_ino}'
        self.root_fingerprint = hashlib.sha256(identity.encode()).hexdigest()[:24]
        self.root = self.state_root / 'execution_units'
        if self.root.is_symlink():
            raise ExecutionUnitOwnershipError('execution-unit directory is a symlink')
        self.root.mkdir(mode=0o700, exist_ok=True)
        self._private(self.root)
        self.stop_timeout = float(stop_timeout)
        self.launch_timeout = float(launch_timeout)
        self._lock = threading.RLock()

    def _private(self, path: Path) -> None:
        if path.is_symlink():
            raise ExecutionUnitOwnershipError(f'owned path is a symlink: {path}')
        if os.name == 'posix':
            info = path.stat()
            if info.st_uid != self.uid or stat.S_IMODE(info.st_mode) & 0o077:
                raise ExecutionUnitOwnershipError(f'owned path must be private to UID {self.uid}: {path}')

    def _manager_environment(self) -> dict[str, str]:
        result = dict(os.environ)
        result.update(XDG_RUNTIME_DIR=f'/run/user/{self.uid}',
                      DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{self.uid}/bus',
                      LC_ALL='C', SYSTEMD_COLORS='0', SYSTEMD_PAGER='')
        return result

    def _run(self, arguments: Sequence[str], *, timeout=None):
        try:
            return subprocess.run(['systemctl', '--user', '--no-pager', *arguments],
                                  capture_output=True, text=True,
                                  timeout=timeout or self.launch_timeout,
                                  env=self._manager_environment(), check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ExecutionUnitUnavailable(f'user systemd control failed: {error}') from error

    def check_capability(self) -> None:
        if sys.platform != 'linux' or self.uid < 0:
            raise ExecutionUnitUnavailable('execution-unit containment requires Linux user systemd')
        if not Path('/sys/fs/cgroup/cgroup.controllers').is_file():
            raise ExecutionUnitUnavailable('execution-unit containment requires cgroup v2')
        if self.boot_id == 'unavailable':
            raise ExecutionUnitUnavailable('Linux boot identity is unavailable')
        for executable in ('systemctl', 'systemd-run'):
            if not shutil.which(executable):
                raise ExecutionUnitUnavailable(f'{executable} is unavailable')
        bus = Path(f'/run/user/{self.uid}/bus')
        if not bus.exists() or bus.stat().st_uid != self.uid:
            raise ExecutionUnitUnavailable('owned user systemd bus is unavailable')
        result = self._run(['show', '--property=Version'])
        if result.returncode != 0 or not result.stdout.startswith('Version='):
            raise ExecutionUnitUnavailable(f'user systemd manager is unavailable: {result.stderr.strip()}')

    def _unit_name(self, task_id: str, attempt: int, nonce: str) -> str:
        task = hashlib.sha256(task_id.encode()).hexdigest()[:16]
        return f'bioflow-{self.root_fingerprint}-{task}-{attempt}-{nonce}.service'

    def _validate_lease(self, lease: Mapping[str, Any], *, previous_boot=False) -> Path:
        try:
            nonce = lease['nonce']
            valid = (lease['version'] == 1 and lease['uid'] == self.uid
                     and isinstance(lease['boot_id'], str) and bool(lease['boot_id'])
                     and (previous_boot or lease['boot_id'] == self.boot_id)
                     and lease['root_fingerprint'] == self.root_fingerprint
                     and re.fullmatch(r'[0-9a-f]{32}', nonce)
                     and isinstance(lease['attempt'], int) and lease['attempt'] >= 1
                     and lease['unit_name'] == self._unit_name(lease['task_id'], lease['attempt'], nonce)
                     and lease['description'] == f'BioFlow {self.root_fingerprint} {nonce}')
            if not valid:
                raise ValueError('lease identity differs')
            directory = self.root / nonce
            self._private(directory)
            self._private(directory / 'lease.json')
            entry = directory / 'entry.py'
            self._private(entry)
            if str(entry) != lease['entry_path'] or hashlib.sha256(entry.read_bytes()).hexdigest() != lease['entry_sha256']:
                raise ValueError('entrypoint identity differs')
            return directory
        except (KeyError, TypeError, ValueError, OSError) as error:
            raise ExecutionUnitOwnershipError(f'invalid execution-unit lease: {error}') from error

    def _show(self, name: str) -> dict[str, str]:
        properties = ('Id,LoadState,ActiveState,SubState,InvocationID,Description,WorkingDirectory,'
                      'ExecStart,MainPID,KillMode,SendSIGKILL,Transient,User,ControlGroup')
        result = self._run(['show', '--property=' + properties, '--', name])
        values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if values.get('LoadState') == 'not-found':
            return values
        if result.returncode != 0 or not values:
            raise ExecutionUnitUnavailable(f'cannot inspect owned unit {name}: {result.stderr.strip()}')
        return values

    def _validate_unit(self, lease: Mapping[str, Any], properties: Mapping[str, str], *, allow_pending=False):
        entry = lease['entry_path']
        expected_start = '{ path=' + entry + ' ; argv[]=' + entry + ' ; ignore_errors=no ;'
        invocation = properties.get('InvocationID', '')
        group = properties.get('ControlGroup', '')
        inactive = properties.get('ActiveState') in {'inactive', 'failed'}
        checks = {
            'Id': properties.get('Id') == lease['unit_name'],
            'Description': properties.get('Description') == lease['description'],
            'WorkingDirectory': properties.get('WorkingDirectory') == lease['cwd'],
            'ExecStart': properties.get('ExecStart', '').startswith(expected_start),
            'KillMode': properties.get('KillMode') == 'control-group',
            'SendSIGKILL': properties.get('SendSIGKILL') == 'yes',
            'Transient': properties.get('Transient') == 'yes',
            'User': properties.get('User', '') in {'', str(self.uid)},
            'ControlGroup': (inactive and not group) or (
                group.startswith(f'/user.slice/user-{self.uid}.slice/user@{self.uid}.service/')
                and group.endswith('/' + lease['unit_name'])),
            'InvocationID': bool(re.fullmatch(r'[0-9a-f]{32}', invocation)) and (
                invocation == lease.get('invocation_id') or (allow_pending and not lease.get('invocation_id'))),
        }
        failures = [name for name, passed in checks.items() if not passed]
        if failures:
            raise ExecutionUnitOwnershipError('unit ownership/containment mismatch: ' + ', '.join(failures))

    def launch(self, command: Sequence[str], *, cwd: Path, log_handle, task_id: str,
               attempt: int, env: Mapping[str, str] | None = None) -> ExecutionUnitProcess:
        self.check_capability()
        if isinstance(command, (str, bytes)) or not command or not all(isinstance(x, str) and '\0' not in x for x in command):
            raise ValueError('command must be a nonempty argument sequence')
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
            raise ValueError('attempt must be a positive integer')
        cwd = Path(cwd).resolve(strict=True)
        # systemctl renders ExecStart without a machine-readable argv schema.
        # Reject ambiguous property delimiters/specifiers instead of guessing.
        if any(c in str(self.root) + str(cwd) for c in '\n\r\t;{}$%'):
            raise ValueError('execution paths contain unsupported systemd property characters')
        if os.name == 'posix' and any(c.isspace() for c in sys.executable):
            raise ExecutionUnitUnavailable('Python executable path cannot contain whitespace')
        nonce = uuid.uuid4().hex
        directory = self.root / nonce
        directory.mkdir(mode=0o700)
        entry = directory / 'entry.py'
        source = ('#!' + sys.executable + ' -I\n' + _ENTRY).encode()
        fd = os.open(entry, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(source)
            stream.flush()
            os.fsync(stream.fileno())
        lease = dict(version=1, nonce=nonce, uid=self.uid, boot_id=self.boot_id,
                     root_fingerprint=self.root_fingerprint, task_id=str(task_id), attempt=attempt,
                     unit_name=self._unit_name(str(task_id), attempt, nonce), cwd=str(cwd),
                     description=f'BioFlow {self.root_fingerprint} {nonce}',
                     entry_path=str(entry), entry_sha256=hashlib.sha256(source).hexdigest(),
                     invocation_id=None, pid=None, status='pending')
        _atomic_json(directory / 'payload.json', {'command': list(command),
                     'env': dict(os.environ if env is None else env),
                     'grant_timeout': self.launch_timeout + 5})
        _atomic_json(directory / 'lease.json', lease)
        _sync_directory(self.root)
        arguments = ['systemd-run', '--user', '--quiet', '--wait', '--pipe', '--collect',
                     '--service-type=exec', '--unit=' + lease['unit_name'],
                     '--description=' + lease['description'], '--working-directory=' + str(cwd),
                     '--property=KillMode=control-group', '--property=SendSIGKILL=yes',
                     '--property=Restart=no', '--property=Delegate=no',
                     f'--property=TimeoutStopSec={self.stop_timeout}s', '--', str(entry)]
        try:
            try:
                monitor = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=log_handle,
                                           stderr=subprocess.STDOUT, env=self._manager_environment(),
                                           close_fds=True)
            except OSError as error:
                raise ExecutionUnitUnavailable(f'cannot start user systemd observer: {error}') from error
            deadline = time.monotonic() + self.launch_timeout
            while time.monotonic() < deadline:
                properties = self._show(lease['unit_name'])
                receipt = directory / 'receipt.json'
                if properties.get('LoadState') != 'not-found' and receipt.exists():
                    self._private(receipt)
                    observed = json.loads(receipt.read_text())
                    self._validate_unit(lease, properties, allow_pending=True)
                    if observed.get('invocation_id') != properties['InvocationID'] or str(observed.get('pid')) != properties['MainPID']:
                        raise ExecutionUnitOwnershipError('wrapper receipt differs from systemd identity')
                    lease.update(invocation_id=properties['InvocationID'], pid=int(properties['MainPID']), status='running')
                    _atomic_json(directory / 'lease.json', lease)
                    _atomic_json(directory / 'permit.json', {'invocation_id': lease['invocation_id']})
                    return ExecutionUnitProcess(self, monitor, lease)
                if monitor.poll() is not None:
                    raise ExecutionUnitUnavailable('systemd-run exited before workload launch; inspect runner log')
                time.sleep(.02)
            raise ExecutionUnitUnavailable('timed out waiting for owned user systemd unit')
        except BaseException:
            # No grant remains for a delayed StartTransientUnit call. Keep the
            # durable lease so a later startup can retry verified cleanup.
            (directory / 'permit.json').unlink(missing_ok=True)
            lease['status'] = 'revoked'
            _atomic_json(directory / 'lease.json', lease)
            try:
                self.stop(lease)
            except ExecutionUnitError:
                pass
            raise

    def stop(self, lease_or_handle, *, timeout=None) -> bool:
        supplied = lease_or_handle.lease if isinstance(lease_or_handle, ExecutionUnitProcess) else lease_or_handle
        with self._lock:
            directory = self._validate_lease(supplied)
            lease = json.loads((directory / 'lease.json').read_text())
            self._validate_lease(lease)
            if supplied.get('invocation_id') and supplied['invocation_id'] != lease.get('invocation_id'):
                raise ExecutionUnitOwnershipError('persisted invocation changed')
            (directory / 'permit.json').unlink(missing_ok=True)
            lease['status'] = 'revoked'
            _atomic_json(directory / 'lease.json', lease)
            properties = self._show(lease['unit_name'])
            if properties.get('LoadState') == 'not-found':
                return False
            self._validate_unit(lease, properties, allow_pending=not lease.get('invocation_id'))
            if not lease.get('invocation_id'):
                receipt_path = directory / 'receipt.json'
                if receipt_path.exists():
                    self._private(receipt_path)
                    receipt = json.loads(receipt_path.read_text())
                    if receipt.get('invocation_id') != properties['InvocationID']:
                        raise ExecutionUnitOwnershipError('pending wrapper receipt has different invocation')
                # Exact pre-persisted random unit + owner marker + entrypoint
                # identify the registration gap even before receipt creation.
                lease['invocation_id'] = properties['InvocationID']
                _atomic_json(directory / 'lease.json', lease)
            if properties.get('ActiveState') in {'inactive', 'failed'}:
                lease['status'] = 'finished'
                _atomic_json(directory / 'lease.json', lease)
                return False
            result = self._run(['stop', '--', lease['unit_name']], timeout=timeout or self.stop_timeout + 5)
            if result.returncode != 0:
                # A collected unit may disappear between inspection and stop.
                after = self._show(lease['unit_name'])
                if after.get('LoadState') != 'not-found':
                    raise ExecutionUnitError(f'failed to stop owned unit: {result.stderr.strip()}')
            after = self._show(lease['unit_name'])
            if after.get('LoadState') != 'not-found':
                self._validate_unit(lease, after)
                if after.get('ActiveState') not in {'inactive', 'failed'}:
                    raise ExecutionUnitError('owned unit remains active after stop')
            lease['status'] = 'stopped'
            _atomic_json(directory / 'lease.json', lease)
            return True

    def reconcile(self) -> list[dict[str, Any]]:
        """Stop abandoned owned attempts; report mismatches without touching them."""
        results = []
        for path in sorted(self.root.glob('*/lease.json')):
            result: dict[str, Any] = {'lease_path': str(path)}
            try:
                self._private(path.parent)
                self._private(path)
                lease = json.loads(path.read_text())
                directory = self._validate_lease(lease, previous_boot=True)
                if directory != path.parent:
                    raise ExecutionUnitOwnershipError('lease stored in another attempt directory')
                result.update(task_id=lease['task_id'], attempt=lease['attempt'], unit_name=lease['unit_name'])
                if lease['boot_id'] != self.boot_id:
                    # No process from a previous kernel boot can still exist.
                    # Never address a same-named unit from the current boot.
                    result['outcome'] = 'absent'
                else:
                    result['outcome'] = 'stopped' if self.stop(lease) else 'absent'
            except (ExecutionUnitOwnershipError, ValueError, KeyError) as error:
                result.update(outcome='rejected', error=str(error))
            except (ExecutionUnitError, OSError) as error:
                result.update(outcome='error', error=str(error))
            results.append(result)
        return results
