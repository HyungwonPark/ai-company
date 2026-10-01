"""Broker boundaries; opt-in host tests execute the reviewed bwrap, no model."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from ai_company.adapters.workspace_files import FileToolError, WorkspaceFiles, task_path


class WorkspaceFileFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / 'repo'
        self.work.mkdir()
        (self.work / 'src').mkdir()
        (self.work / 'src/owned.py').write_text('original')
        self.files = WorkspaceFiles(self.work, ('src/owned.py', 'src/new.py'))


class WorkspaceFileBoundaries(WorkspaceFileFixture, unittest.TestCase):
    def test_paths_cannot_escape_or_name_secrets(self):
        for value in ('.', '..', '../outside', '/tmp/out', 'src/../out', 'src//out', 'src/./out',
                      '.git/config', 'src/.env', 'src/auth.json', 'src/a.key', 'src\\a.py', '', None, 'x\x00y'):
            with self.subTest(value=value), self.assertRaises(FileToolError):
                task_path(value)
        self.assertEqual(task_path('src/상태.py'), 'src/상태.py')

    def test_role_and_readonly_denials_happen_before_runtime(self):
        with patch.object(WorkspaceFiles, 'verify_runtime') as runtime:
            for files, name in ((self.files, 'src/other.py'), (WorkspaceFiles(self.work), 'src/owned.py')):
                with self.assertRaises(FileToolError):
                    files.call('write', name, 'changed')
            runtime.assert_not_called()
        self.assertEqual((self.work / 'src/owned.py').read_text(), 'original')

    def test_input_limit_and_protocol_are_not_executable(self):
        for operation, text in (('shell', None), ('write', 3), ('write', '한' * 30_000),
                                ('write', '\ud800'), ('read', 'overwrite')):
            with self.subTest(operation=operation, kind=type(text).__name__), self.assertRaises(FileToolError):
                self.files.call(operation, 'src/owned.py', text)

    def test_symlink_parent_target_hardlink_and_fifo_are_refused(self):
        outside = self.root / 'outside'
        outside.write_text('private fixture')
        (self.work / 'link').symlink_to(self.root, target_is_directory=True)
        (self.work / 'src/symlink.py').symlink_to(outside)
        os.link(outside, self.work / 'src/hardlink.py')
        os.mkfifo(self.work / 'src/fifo.py')
        with patch.object(WorkspaceFiles, 'verify_runtime'), patch.object(WorkspaceFiles, '_run') as run:
            for name in ('link/outside', 'src/symlink.py', 'src/hardlink.py', 'src/fifo.py'):
                with self.subTest(name=name), self.assertRaises(FileToolError):
                    self.files.call('read', name)
            run.assert_not_called()

    def test_root_replacement_cannot_redirect_the_operation(self):
        self.work.rename(self.root / 'old')
        self.work.mkdir()
        with patch.object(WorkspaceFiles, 'verify_runtime'), self.assertRaisesRegex(FileToolError, 'identity'):
            self.files.call('write', 'src/new.py', 'changed')
        self.assertFalse((self.work / 'src/new.py').exists())

    def test_runtime_failure_does_not_create_a_placeholder(self):
        with patch.object(WorkspaceFiles, 'verify_runtime', side_effect=FileToolError('unavailable')):
            with self.assertRaises(FileToolError):
                self.files.call('write', 'src/new.py', 'changed')
        self.assertFalse((self.work / 'src/new.py').exists())

    def test_failed_content_write_keeps_visible_partial_work(self):
        with patch.object(WorkspaceFiles, 'verify_runtime'), patch.object(WorkspaceFiles, '_run', side_effect=FileToolError('failed')):
            for name in ('src/owned.py', 'src/new.py'):
                with self.assertRaises(FileToolError):
                    self.files.call('write', name, 'changed')
        self.assertEqual((self.work / 'src/owned.py').read_text(), 'original')
        self.assertEqual((self.work / 'src/new.py').read_bytes(), b'')

    def test_inode_is_pinned_across_host_rename(self):
        with ExitStack() as stack:
            _, target = self.files._target(stack, 'src/owned.py', False)
            (self.work / 'src/owned.py').rename(self.work / 'src/old.py')
            (self.work / 'src/owned.py').write_text('replacement')
            self.assertEqual(os.read(target, 100), b'original')


@unittest.skipUnless(os.environ.get('AI_COMPANY_TEST_BWRAP') == '1', 'requires reviewed server bwrap/profile')
class WorkspaceFileHostIsolation(WorkspaceFileFixture, unittest.TestCase):
    def test_real_read_write_and_literal_source(self):
        text = "# 한글\nvalue = '$(`touch /outside`)\"; __import__(\"os\")'\n"
        result = self.files.call('write', 'src/new.py', text)
        self.assertEqual(result['sha256'], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(self.files.call('read', 'src/new.py')['text'], text)
        self.assertEqual((self.work / 'src/owned.py').read_text(), 'original')

    def diagnostic(self, source, timeout=12):
        with ExitStack() as stack:
            root, target = self.files._target(stack, 'src/owned.py', False)
            argv = self.files._command(root, target, 'src/owned.py', True)
            argv[-1] = source  # trusted test only; never supplied by a tool request
            return self.files._run(argv, {}, (root, target), timeout_seconds=timeout)

    def test_real_filesystem_network_and_privilege_boundaries(self):
        outside = self.root / 'private.txt'
        outside.write_text('private fixture')
        with socket.socket() as tcp, socket.socket(socket.AF_UNIX) as unix:
            tcp.bind(('127.0.0.1', 0)); tcp.listen()
            name = '\x00ai-company-files-' + uuid4().hex
            unix.bind(name); unix.listen()
            with socket.create_connection(tcp.getsockname(), timeout=2): pass
            source = '''import json,pathlib,socket
r={}
for name in ['/workspace/src/owned.py','/workspace/src/neighbor.py',__OUTSIDE__]:
 try:pathlib.Path(name).write_text('owned fixture');r[name]=True
 except OSError:r[name]=False
try:r['outside_read']=pathlib.Path(__OUTSIDE__).read_text()
except OSError:r['outside_read']=False
for label,family,address in [('tcp',socket.AF_INET,__ADDRESS__),('unix',socket.AF_UNIX,__UNIX__)]:
 s=socket.socket(family);s.settimeout(1)
 try:s.connect(address);r[label]=True
 except OSError:r[label]=False
 finally:s.close()
r['profile']=pathlib.Path('/proc/self/attr/current').read_text().strip()
r['status']={k:v.strip() for k,v in (s.split(':',1) for s in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in s) if k in ['CapEff','CapBnd','NoNewPrivs']}
print(json.dumps(r))
'''.replace('__OUTSIDE__', repr(str(outside))).replace('__ADDRESS__', repr(tcp.getsockname())).replace('__UNIX__', repr(name))
            result = self.diagnostic(source)
        self.assertTrue(result['/workspace/src/owned.py'])
        for key in ('/workspace/src/neighbor.py', str(outside), 'outside_read', 'tcp', 'unix'):
            self.assertFalse(result[key], key)
        self.assertEqual(outside.read_text(), 'private fixture')
        self.assertEqual(result['profile'], 'bwrap//&unpriv_bwrap (enforce)')
        self.assertEqual(result['status']['NoNewPrivs'], '1')
        self.assertEqual(int(result['status']['CapEff'], 16), 0)
        self.assertEqual(int(result['status']['CapBnd'], 16), 0)

    def child_pids(self, marker):
        found = []
        for path in Path('/proc').glob('[0-9]*/cmdline'):
            try:
                args = path.read_bytes().split(b'\0')
                if marker.encode() in args: found.append(int(path.parent.name))
            except (OSError, ValueError): pass
        return found

    def assert_children_stopped(self, marker):
        for _ in range(20):
            live = self.child_pids(marker)
            if not live: return
            time.sleep(.05)
        for pid in live:
            try: os.kill(pid, signal.SIGKILL)
            except ProcessLookupError: pass
        self.fail('sandbox left live diagnostic children')

    def test_real_detached_child_dies_on_success_and_timeout(self):
        for timeout in (False, True):
            marker = 'ai-company-file-child-' + uuid4().hex
            source = "import subprocess,time,json;subprocess.Popen(['/usr/bin/python3','-c','import time;time.sleep(600)'," + repr(marker) + "],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
            source += "time.sleep(600)" if timeout else "print(json.dumps({'spawned':True}))"
            try:
                if timeout:
                    with self.assertRaisesRegex(FileToolError, 'timed out'):
                        self.diagnostic(source, timeout=.3)
                else:
                    self.assertTrue(self.diagnostic(source)['spawned'])
            finally:
                self.assert_children_stopped(marker)
