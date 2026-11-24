"""Linux computation boundary: private namespaces and explicitly mounted files."""
import functools
import ctypes
import shutil
import subprocess
import sys
from pathlib import Path


def _base():
    executable = shutil.which('bwrap')
    if not executable:
        raise RuntimeError('Linux 隔离计算环境缺少 bubblewrap，请安装 bubblewrap。')
    command = [executable, '--unshare-all', '--die-with-parent', '--new-session',
               '--cap-drop', 'ALL', '--clearenv', '--ro-bind', '/usr', '/usr']
    for name in ('lib', 'lib64', 'bin', 'sbin'):
        path = Path('/') / name
        if path.is_symlink():
            command += ['--symlink', str(path.readlink()), str(path)]
        elif path.exists():
            command += ['--ro-bind', str(path), str(path)]
    command += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/etc']
    for name in ('ld.so.cache', 'fonts'):
        path = Path('/etc') / name
        if path.exists():
            command += ['--ro-bind', str(path), str(path)]
    for prefix in sorted({str(Path(sys.prefix).resolve()), str(Path(sys.base_prefix).resolve())}):
        if prefix == '/':
            raise RuntimeError('隔离计算环境的 Python 安装位置无效。')
        if prefix != '/usr' and not prefix.startswith('/usr/'):
            command += ['--ro-bind', prefix, prefix]
    return command


@functools.lru_cache(maxsize=1)
def capability():
    if not sys.platform.startswith('linux'):
        return {'available':False, 'backend':'bubblewrap', 'reason':'当前运行环境不是 Linux。'}
    try:
        command = _base() + ['--', sys.executable, '-I', '-c',
            'import ctypes; ctypes.CDLL("libseccomp.so.2"); print("bioflow-isolated")']
        result = subprocess.run(command, capture_output=True, text=True, timeout=8,
                                env={'PATH':'/usr/bin:/bin', 'LC_ALL':'C.UTF-8'})
        if result.returncode or result.stdout.strip() != 'bioflow-isolated':
            return {'available':False, 'backend':'bubblewrap',
                    'reason':'Linux 隔离计算环境自检未通过：'+result.stderr.strip()[:500]}
        return {'available':True, 'backend':'bubblewrap', 'reason':''}
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        return {'available':False, 'backend':'bubblewrap', 'reason':str(error)[:500]}


def command(job, environment):
    job = Path(job).resolve()
    args = _base() + ['--ro-bind', str(job), str(job),
                     '--bind', str(job / 'output'), str(job / 'output'),
                     '--bind', str(job / 'tmp'), str(job / 'tmp'), '--chdir', str(job)]
    for key, value in environment.items():
        args += ['--setenv', key, str(value)]
    return args + ['--', sys.executable, '-I', str(job / 'bootstrap.py'), str(job)]


def restrict_processes():
    """Irreversible syscall filter installed by the trusted bootstrap, before user code."""
    class Comparison(ctypes.Structure):
        _fields_=[('arg',ctypes.c_uint),('op',ctypes.c_int),
                  ('datum_a',ctypes.c_uint64),('datum_b',ctypes.c_uint64)]
    library=ctypes.CDLL('libseccomp.so.2',use_errno=True)
    library.seccomp_init.argtypes=[ctypes.c_uint32];library.seccomp_init.restype=ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes=[ctypes.c_char_p]
    library.seccomp_rule_add_array.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_int,
                                            ctypes.c_uint,ctypes.POINTER(Comparison)]
    library.seccomp_load.argtypes=[ctypes.c_void_p]
    library.seccomp_release.argtypes=[ctypes.c_void_p]
    context=library.seccomp_init(0x7fff0000)  # SCMP_ACT_ALLOW
    if not context:raise RuntimeError('不能初始化 Linux 进程隔离过滤器。')
    try:
        for name,error in ((b'fork',1),(b'vfork',1),(b'clone3',38)):
            syscall=library.seccomp_syscall_resolve_name(name)
            if syscall>=0 and library.seccomp_rule_add_array(context,0x00050000|error,syscall,0,None)!=0:
                raise RuntimeError('不能限制 Linux 子进程创建。')
        syscall=library.seccomp_syscall_resolve_name(b'clone')
        # Keep numerical-library threads; deny clone without CLONE_THREAD.
        condition=Comparison(0,7,0x00010000,0)
        if syscall<0 or library.seccomp_rule_add_array(context,0x00050001,syscall,1,ctypes.byref(condition))!=0:
            raise RuntimeError('不能限制 Linux 进程复制。')
        if library.seccomp_load(context)!=0:
            raise RuntimeError('不能启用 Linux 进程隔离过滤器。')
    finally:
        library.seccomp_release(context)
