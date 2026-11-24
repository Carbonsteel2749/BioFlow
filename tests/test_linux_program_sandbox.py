import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/bioflow_platform/research_engine/src'))
from yanji import linux_sandbox


@unittest.skipUnless(sys.platform.startswith('linux'),'Linux isolation test')
class LinuxProgramSandboxTests(unittest.TestCase):
    def test_real_boundary_hides_host_files_environment_and_network(self):
        self.assertTrue(linux_sandbox.capability()['available'],linux_sandbox.capability())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); job=root/'job'; job.mkdir()
            (job/'output').mkdir(); (job/'tmp').mkdir()
            (root/'host-secret').write_text('synthetic-private-value')
            (job/'input.json').write_text('{}')
            import shutil
            shutil.copyfile(linux_sandbox.__file__,job/'isolation.py')
            (job/'bootstrap.py').write_text('''import json,os,socket,sys
from pathlib import Path
root=Path(sys.argv[1]); results={}
import runpy,threading
runpy.run_path(str(root/'isolation.py'))['restrict_processes']()
try:
    child=os.fork()
    if child==0:os._exit(0)
    os.waitpid(child,0);results['process_creation_denied']=False
except PermissionError:results['process_creation_denied']=True
thread=threading.Thread(target=lambda:None);thread.start();thread.join()
results['threads_allowed']=True
try:
    (root.parent/'host-secret').read_text(); results['host_hidden']=False
except OSError: results['host_hidden']=True
try:
    (root/'input.json').write_text('changed'); results['input_readonly']=False
except OSError: results['input_readonly']=True
results['environment_hidden']=os.getenv('BIOFLOW_TEST_SECRET') is None
sock=socket.socket(); sock.settimeout(.5)
try:
    sock.connect(('127.0.0.1',int(sys.argv[2]))); results['network_isolated']=False
except OSError: results['network_isolated']=True
(root/'output/result.json').write_text(json.dumps(results))
''')
            import socket
            listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen()
            try:
                env={'PATH':'/usr/bin:/bin','HOME':str(job/'tmp'),'LC_ALL':'C.UTF-8'}
                args=linux_sandbox.command(job,env)+[str(listener.getsockname()[1])]
                parent={**os.environ,'BIOFLOW_TEST_SECRET':'synthetic-token'}
                result=subprocess.run(args,env=parent,capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                checks=json.loads((job/'output/result.json').read_text())
                self.assertTrue(all(checks.values()),checks)
                self.assertEqual((job/'input.json').read_text(),'{}')
            finally:
                listener.close()

    def test_missing_runtime_does_not_fall_back_to_host_python(self):
        from unittest.mock import patch
        linux_sandbox.capability.cache_clear()
        try:
            with patch.object(linux_sandbox.shutil,'which',return_value=None):
                self.assertFalse(linux_sandbox.capability()['available'])
                with self.assertRaises(RuntimeError):
                    linux_sandbox.command(Path('/tmp/unused'),{})
        finally:
            linux_sandbox.capability.cache_clear()


if __name__=='__main__':unittest.main()
