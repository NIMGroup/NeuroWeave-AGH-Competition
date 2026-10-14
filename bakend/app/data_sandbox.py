"""Generated Python runs in a bounded Docker container; no host-shell fallback."""
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
import subprocess
import tarfile
from threading import Thread
from pathlib import Path
from uuid import uuid4
from .data_environments import DataEnvironments

configuration = ContextVar('data_sandbox_configuration', default=None)


class SandboxCancelled(Exception):
    pass


# Docker cp does not reliably export tmpfs mounts. Export through a separate
# trusted Python process and validate the archive on the host before extraction.
EXPORT = """
import os, stat, sys, tarfile
from pathlib import Path
root = Path('/output')
files, total, visits = 0, 0, 0
maximum = int(sys.argv[1])
with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as archive:
    for parent, dirs, names in os.walk(root, followlinks=False):
        for name in sorted(dirs + names):
            visits += 1
            if visits > 100000: raise ValueError('Too many output entries')
            path = Path(parent) / name
            info = path.lstat()
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ValueError('Output links and special files are forbidden')
            if stat.S_ISREG(info.st_mode):
                files += 1
                total += info.st_size
                if files > 10000 or total > maximum:
                    raise ValueError('Output exceeds limit')
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, 'rb') as stream:
                    opened = os.fstat(stream.fileno())
                    if not stat.S_ISREG(opened.st_mode): raise ValueError('Not a file')
                    member = tarfile.TarInfo(path.relative_to(root).as_posix())
                    member.size, member.mode = opened.st_size, 0o600
                    archive.addfile(member, stream)
"""


def extract_outputs(archive, destination, max_bytes=512 * 1024 * 1024, checkpoint=None):
    count, total = 0, 0
    with tarfile.open(archive, mode="r|") as source:
        for member in source:
            parts = member.name.split("/")
            if (not member.isfile() or member.name.startswith("/") or
                    any(p in {"", ".", ".."} or ":" in p or "\\" in p for p in parts)):
                raise PermissionError("隔离输出含不安全的归档路径或文件类型")
            count += 1
            total += member.size
            if count > 10000 or total > max_bytes:
                raise ValueError("隔离输出超过数量或大小限制")
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Every parent is created beneath a fresh private directory; links
            # and repeated destination names are never accepted from the archive.
            with target.open("xb") as output, source.extractfile(member) as stream:
                remaining = member.size
                while remaining:
                    if checkpoint:
                        checkpoint()
                    block = stream.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ValueError("隔离输出归档不完整")
                    output.write(block)
                    remaining -= len(block)


class DataSandbox:
    def __init__(self):
        self.image = os.getenv("NEUROWEAVE_DATA_PYTHON_IMAGE", "")
        self.docker = os.getenv("NEUROWEAVE_DOCKER", "docker")
        self.environments = DataEnvironments()

    def describe(self, environment='stdlib', resource_profile='small'):
        return self.environments.describe(environment, resource_profile, self.image)

    @contextmanager
    def execution(self, environment='stdlib', resource_profile='small', cancel_event=None):
        value = self.describe(environment, resource_profile)
        value['cancel_event'] = cancel_event
        token = configuration.set(value)
        try:
            yield value
        finally:
            configuration.reset(token)

    def run(self, source: Path, directory: Path, code: str):
        selected = configuration.get() or self.describe()
        limits = selected['resources']
        maximum = limits['output_mb'] * 1024 * 1024
        def checkpoint():
            event = selected.get('cancel_event')
            if event and event.is_set():
                raise SandboxCancelled('脚本已取消；未修改输入文件')
        checkpoint()
        if any(c in str(source) + str(directory) for c in (",", "\n", "\r")):
            raise ValueError("隔离挂载路径含不支持的字符")
        directory.mkdir(parents=True, exist_ok=False)
        script = directory / "script.py"
        script.write_text(code, encoding="utf-8")
        script.chmod(0o644)
        artifacts = directory / "artifacts"
        artifacts.mkdir()
        name = "neuroweave-data-" + uuid4().hex
        command = [self.docker, "create", "--pull=never", "--name", name,
                   "--network=none", "--read-only", "--cap-drop=ALL",
                   "--security-opt=no-new-privileges", "--pids-limit=" + str(limits['pids']),
                   "--memory=" + str(limits['memory_mb']) + 'm', "--cpus=" + str(limits['cpus']),
                   "--user=" + (f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") and os.getuid() != 0 else "65534:65534"),
                   "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=" + str(limits['tmp_mb']) + 'm,mode=1777',
                   "--tmpfs", "/output:rw,nosuid,nodev,size=" + str(limits['output_mb']) + 'm,mode=1777',
                   "--env=OPENBLAS_NUM_THREADS=1", "--env=OMP_NUM_THREADS=1", "--env=MKL_NUM_THREADS=1",
                   "--env=NUMEXPR_NUM_THREADS=1",
                   "--mount", f"type=bind,src={source},dst=/input,readonly",
                   "--mount", f"type=bind,src={script},dst=/script.py,readonly",
                   "--workdir=/output", selected['image'], "python", "-I", "-c",
                   "import time; time.sleep(" + str(limits['timeout_seconds'] + 180) + ")"]
        def execute(args, timeout=20, log=None, output_limit=65536, strict=False):
            checkpoint()
            if log:
                process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                overflow = []
                def drain():
                    from .bounded_log import HeadTailLog
                    capture = HeadTailLog(log, output_limit) if not strict else None
                    remaining = output_limit
                    while block := process.stdout.read1(4096):
                        if capture:
                            capture.write(block)
                            continue
                        if strict and len(block) > remaining:
                            overflow.append(True)
                            process.kill()
                            break
                        if remaining:
                            log.write(block[:remaining])
                            log.flush()
                            remaining = max(0, remaining - len(block))
                        elif strict:
                            overflow.append(True)
                            process.kill()
                            break
                    if capture:
                        capture.finish()
                thread = Thread(target=drain, daemon=True)
                thread.start()
                try:
                    deadline = time.monotonic() + timeout
                    while process.poll() is None:
                        checkpoint()
                        if time.monotonic() >= deadline:
                            raise subprocess.TimeoutExpired(args, timeout)
                        try:
                            process.wait(timeout=0.2)
                        except subprocess.TimeoutExpired:
                            pass
                    status = process.returncode
                finally:
                    if process.poll() is None:
                        process.kill()
                    thread.join(timeout=5)
                if status or overflow:
                    saved_log = directory / "execution.log"
                    detail = saved_log.read_text(encoding="utf-8", errors="replace")[-2000:] if saved_log.is_file() else ""
                    raise ValueError("隔离 Python 执行失败；未修改输入文件。\n" + detail)
                return
            result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=log or subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=timeout, check=False)
            if result.returncode:
                raise ValueError("隔离 Python 执行失败，请查看任务日志")
            return result
        try:
            execute(command)
            execute([self.docker, "start", name])
            with (directory / "execution.log").open("wb") as log:
                execute([self.docker, "exec", name, "python", "-I", "/script.py"], timeout=limits['timeout_seconds'], log=log)
            archive = directory / "output.tar"
            with archive.open("wb") as output:
                execute([self.docker, "exec", name, "python", "-I", "-c", EXPORT, str(maximum)], timeout=60,
                        log=output, output_limit=maximum + 16 * 1024 * 1024, strict=True)
            extract_outputs(archive, artifacts, maximum, checkpoint)
            archive.unlink()
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("隔离执行环境不可用或超时；未修改输入文件") from exc
        finally:
            try:
                subprocess.run([self.docker, "rm", "--force", name], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        return artifacts
