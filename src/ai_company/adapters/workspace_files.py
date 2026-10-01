"""File-only Claude tool backend. It does not activate a provider or policy.

The trusted broker admits exact task paths. File contents are read/written by a
fixed Python worker inside the reviewed bwrap isolation. No model-supplied code,
shell, environment, mount, network option, or executable crosses this boundary.
"""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import resource
import signal
import stat
import subprocess
import tempfile


BWRAP_SHA256 = "ae27935781511400c65ebcc0b4669775d602f46251b8707c947a1ac1b160c1c8"
PROFILE_SHA256 = "11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9"
MAX_BYTES = 65_536

# Parameters arrive as JSON on stdin, never as Python or shell source.
WORKER = '''
import hashlib,json,os,pathlib,sys
profile=pathlib.Path('/proc/self/attr/current').read_text().strip()
status=dict(line.split(':',1) for line in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in line)
if profile!='bwrap//&unpriv_bwrap (enforce)' or any(int(status[k].strip(),16)!=0 for k in ('CapEff','CapBnd')) or status['NoNewPrivs'].strip()!='1':
    raise RuntimeError('reviewed child isolation is not active')
q=json.load(sys.stdin)
p=pathlib.Path('/workspace')/q['path']
if q['operation']=='read':
    with p.open('rb') as f: data=f.read(65537)
    if len(data)>65536: raise ValueError('file exceeds tool byte limit')
    result={'path':q['path'],'text':data.decode('utf-8'),'sha256':hashlib.sha256(data).hexdigest()}
else:
    data=q['text'].encode('utf-8')
    if len(data)>65536: raise ValueError('file exceeds tool byte limit')
    with p.open('wb') as f: f.write(data);f.flush();os.fsync(f.fileno())
    result={'path':q['path'],'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
print(json.dumps(result,ensure_ascii=False))
'''


class FileToolError(RuntimeError):
    pass


def task_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024 or "\\" in value or "\x00" in value:
        raise FileToolError("invalid task-relative path")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or str(path) != value or any(part in (".", "..") or part.startswith(".")
                                                    for part in path.parts):
        raise FileToolError("hidden, absolute or traversing paths are not file tools")
    if any(part.lower() in ("auth.json", "credentials", "credentials.json")
           or part.lower().endswith((".pem", ".key", ".p12", ".keystore")) for part in path.parts):
        raise FileToolError("credential paths are not file tools")
    return value


def _regular(info):
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()


def _limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (1_048_576, 1_048_576))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))


class WorkspaceFiles:
    def __init__(self, worktree: Path, writable_paths: tuple[str, ...] = ()):
        original = Path(worktree)
        self.worktree = original.resolve(strict=True)
        if original.is_symlink() or not self.worktree.is_dir():
            raise FileToolError("worktree must be a real directory")
        info = self.worktree.stat()
        if info.st_uid != os.getuid():
            raise FileToolError("worktree belongs to another user")
        self.identity = (info.st_dev, info.st_ino)
        self.writable_paths = tuple(task_path(p) for p in writable_paths)
        if len(set(self.writable_paths)) != len(self.writable_paths):
            raise FileToolError("duplicate writable task paths")

    @staticmethod
    def verify_runtime():
        for path, expected in ((Path('/usr/bin/bwrap'), BWRAP_SHA256),
                               (Path('/etc/apparmor.d/bwrap-userns-restrict'), PROFILE_SHA256)):
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o6022
                    or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
                raise FileToolError("reviewed isolation runtime differs")
        for key in ('unprivileged_userns_clone', 'apparmor_restrict_unprivileged_userns'):
            if Path('/proc/sys/kernel', key).read_text().strip() != '1':
                raise FileToolError("reviewed namespace restrictions differ")

    def _target(self, stack, path, create):
        """Pin the root, every parent and target inode with no symlink follows."""
        def opened(name, flags, *, parent=None):
            fd = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600, dir_fd=parent)
            stack.callback(os.close, fd)
            return fd
        root = opened(self.worktree, os.O_RDONLY | os.O_DIRECTORY)
        info = os.fstat(root)
        if (info.st_dev, info.st_ino) != self.identity:
            raise FileToolError("worktree identity changed")
        parent = root
        parts = PurePosixPath(path).parts
        for part in parts[:-1]:
            parent = opened(part, os.O_RDONLY | os.O_DIRECTORY, parent=parent)
        # A new allowed file is an exclusive empty placeholder. Its content is
        # written only in bwrap. On failure it remains visible as partial work;
        # the broker never rolls back or deletes another execution's changes.
        try:
            target = opened(parts[-1], os.O_RDONLY, parent=parent)
        except FileNotFoundError:
            if not create:
                raise
            created = opened(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, parent=parent)
            target = opened(parts[-1], os.O_RDONLY, parent=parent)
            if os.fstat(created).st_ino != os.fstat(target).st_ino:
                raise FileToolError("new target identity changed")
        if not _regular(os.fstat(target)):
            raise FileToolError("target must be an owned, singly linked regular file")
        return root, target

    @staticmethod
    def _command(root_fd, target_fd, path, write):
        # The host root/home/run and credentials are absent. Mount only the
        # system runtime and pinned repository. The child sees no host PIDs.
        argv = ['/usr/bin/bwrap', '--new-session', '--die-with-parent', '--unshare-all',
                '--cap-drop', 'ALL', '--clearenv', '--ro-bind', '/usr', '/usr',
                '--ro-bind', '/lib', '/lib', '--symlink', '/usr/bin', '/bin',
                '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
                '--ro-bind', '/proc/self/fd/' + str(root_fd), '/workspace']
        if write:
            argv += ['--bind', '/proc/self/fd/' + str(target_fd), '/workspace/' + path]
        else:
            argv += ['--ro-bind', '/proc/self/fd/' + str(target_fd), '/workspace/' + path]
        return argv + ['--chdir', '/workspace', '--setenv', 'PATH', '/usr/bin:/bin',
                       '--setenv', 'HOME', '/tmp', '--remount-ro', '/', '--',
                       '/usr/bin/python3', '-I', '-B', '-c', WORKER]

    @staticmethod
    def _run(argv, payload, fds, *, timeout_seconds=12):
        if not 0 < timeout_seconds <= 12:
            raise FileToolError("invalid isolated operation timeout")
        # Temporary output files bound memory and disk even on unexpected child
        # output. They are not mounted or named inside the sandbox.
        with tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            stdin.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
            stdin.seek(0)
            process = subprocess.Popen(argv, stdin=stdin, stdout=stdout, stderr=stderr,
                                       pass_fds=fds, start_new_session=True, preexec_fn=_limits,
                                       env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
            try:
                code = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
                raise FileToolError("isolated file operation timed out") from exc
            if code != 0:
                # Do not copy arbitrary file contents or traceback lines into
                # a provider error. Private host evidence can inspect stderr.
                raise FileToolError("isolated file operation failed (exit " + str(code) + ")")
            stdout.seek(0)
            raw = stdout.read(1_048_577)
            if len(raw) > 1_048_576:
                raise FileToolError("isolated file result exceeds limit")
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeDecodeError) as exc:
                raise FileToolError("invalid isolated file result") from exc
            if not isinstance(result, dict):
                raise FileToolError("invalid isolated file result")
            return result

    def call(self, operation: str, path: str, text: str | None = None) -> dict:
        path = task_path(path)
        if operation not in ('read', 'write'):
            raise FileToolError("only read and write file operations are available")
        if operation == 'write' and path not in self.writable_paths:
            raise FileToolError("path is outside this role's write assignment")
        if operation == 'write':
            try:
                if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_BYTES:
                    raise FileToolError("write content exceeds tool byte limit")
            except UnicodeError as exc:
                raise FileToolError("write content must be UTF-8") from exc
        if operation == 'read' and text is not None:
            raise FileToolError("read does not accept write content")
        try:
            self.verify_runtime()
            with ExitStack() as stack:
                root, target = self._target(stack, path, operation == 'write')
                argv = self._command(root, target, path, operation == 'write')
                result = self._run(argv, {'operation': operation, 'path': path, 'text': text}, (root, target))
                if result.get('path') != path:
                    raise FileToolError("isolated result path differs from the request")
                return result
        except OSError as exc:
            raise FileToolError("file target or isolation runtime is unavailable") from exc
