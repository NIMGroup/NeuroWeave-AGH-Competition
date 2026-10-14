"""Filesystem primitives anchored to directory descriptors on Linux.

resolve() checks scope; O_NOFOLLOW on every parent closes the usual symlink
replacement race between validation and opening. Exclusive publish never replaces
an unconfirmed destination. Regular files on NFS can use link/unlink when the
filesystem rejects renameat2; journals reconcile its two-name window. Windows rename is already
no-replace; Windows deployments retain canonical/reparse-point checks.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import ctypes
import errno
import os
from pathlib import Path
import shutil
import stat
from .operation_context import checkpoint, current_operation

copy_observer = ContextVar('workspace_copy_observer', default=None)


@contextmanager
def observe_copy(callback):
    token = copy_observer.set(callback)
    try:
        yield
    finally:
        copy_observer.reset(token)


@contextmanager
def directory_fd(path: Path):
    if os.name == "nt":
        yield None
        return
    path = path.absolute()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def rename(source: Path, target: Path):
    if os.name == "nt":
        os.rename(source, target)
        return
    with directory_fd(source.parent) as src, directory_fd(target.parent) as dst:
        source_stat = os.stat(source.name, dir_fd=src, follow_symlinks=False)
        if stat.S_ISLNK(source_stat.st_mode):
            raise PermissionError("源路径已变成符号链接")
        libc = ctypes.CDLL(None, use_errno=True)
        error = errno.ENOSYS
        if hasattr(libc, "renameat2"):
            call = libc.renameat2
            call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            call.restype = ctypes.c_int
            if not call(src, os.fsencode(source.name), dst, os.fsencode(target.name), 1):
                return
            error = ctypes.get_errno()
        if error not in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP}:
            raise OSError(error, os.strerror(error))
        if not stat.S_ISREG(source_stat.st_mode):
            raise OSError(errno.ENOTSUP, "共享存储不支持目录的不覆盖改名，请使用文件复制或支持该操作的存储")
        # NFS can reject RENAME_NOREPLACE. link is an atomic, exclusive publish
        # for regular files on one filesystem; never fall back to overwrite.
        # A crash between link and unlink leaves two names for one inode. Data
        # journals reconcile that state; no unrelated target is removed here.
        os.link(source.name, target.name, src_dir_fd=src, dst_dir_fd=dst, follow_symlinks=False)
        linked = os.stat(target.name, dir_fd=dst, follow_symlinks=False)
        current = os.stat(source.name, dir_fd=src, follow_symlinks=False)
        if not stat.S_ISREG(linked.st_mode) or (linked.st_dev, linked.st_ino) != (source_stat.st_dev, source_stat.st_ino) or (current.st_dev, current.st_ino) != (linked.st_dev, linked.st_ino):
            raise PermissionError("不覆盖改名期间源文件变化，请检查保留的文件记录")
        os.unlink(source.name, dir_fd=src)


def mkdir(path: Path, *, parents=False, exist_ok=False):
    if parents and not path.parent.exists():
        mkdir(path.parent, parents=True, exist_ok=True)
    with directory_fd(path.parent) as parent:
        try:
            os.mkdir(path if parent is None else path.name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            if not exist_ok or path.is_symlink() or not path.is_dir():
                raise


def open_read(path: Path):
    with directory_fd(path.parent) as parent:
        fd = os.open(path if parent is None else path.name,
                     os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=parent)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise PermissionError("不是普通文件")
    return fd


def copy(source: Path, target: Path, policy, budget=None):
    checkpoint()
    budget = budget if budget is not None else [100000]
    budget[0] -= 1
    if budget[0] < 0 or policy.sensitive(source) or source.is_symlink():
        raise PermissionError("复制目录超过限制或包含敏感内容/符号链接")
    with directory_fd(source.parent) as src, directory_fd(target.parent) as dst:
        source_name = source if src is None else source.name
        target_name = target if dst is None else target.name
        source_stat = os.stat(source_name, dir_fd=src, follow_symlinks=False)
        if stat.S_ISDIR(source_stat.st_mode):
            os.mkdir(target_name, mode=0o700, dir_fd=dst)
            with directory_fd(source) as source_dir:
                with os.scandir(source if source_dir is None else source_dir) as children:
                    for child in children:
                        copy(source / child.name, target / child.name, policy, budget)
        elif stat.S_ISREG(source_stat.st_mode):
            fd = os.open(source_name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=src)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise PermissionError("复制源已不再是普通文件")
                output = os.open(target_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=dst)
                with os.fdopen(output, "wb") as dest, os.fdopen(fd, "rb") as origin:
                    fd = -1
                    opened = os.fstat(origin.fileno())
                    source_identity = [opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns]
                    if source_identity != [source_stat.st_dev, source_stat.st_ino, source_stat.st_size, source_stat.st_mtime_ns]:
                        raise PermissionError('复制源在打开时发生变化')
                    observer = copy_observer.get()
                    if observer is None:
                        for block in iter(lambda: origin.read(1024 * 1024), b''):
                            checkpoint()
                            dest.write(block)
                    else:
                        copied, value = 0, hashlib.sha256()
                        observer(0)
                        for block in iter(lambda: origin.read(1024 * 1024), b''):
                            checkpoint()
                            dest.write(block)
                            value.update(block)
                            copied += len(block)
                            observer(copied)
                        dest.flush()
                        os.fsync(dest.fileno())
                        observer(copied)
                        info = os.fstat(dest.fileno())
                        current = os.fstat(origin.fileno())
                        if source_identity != [current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns]:
                            raise PermissionError('复制源在读取期间发生变化')
                        return {'bytes': copied, 'sha256': value.hexdigest(),
                                'source_stamp': source_identity,
                                'stamp': [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]}
            finally:
                if fd >= 0:
                    os.close(fd)
        else:
            raise PermissionError("拒绝复制符号链接或特殊文件")


def remove(path: Path):
    checkpoint()
    with directory_fd(path.parent) as parent:
        if path.is_dir():
            if current_operation.get() is not None:
                _remove_checked(path if parent is None else path.name, parent)
                return
            if parent is not None and not shutil.rmtree.avoids_symlink_attacks:
                raise OSError(errno.ENOTSUP, "此 Python 不支持安全递归删除")
            shutil.rmtree(path if parent is None else path.name, dir_fd=parent)
        else:
            os.unlink(path if parent is None else path.name, dir_fd=parent)


def _remove_checked(name, parent):
    """Stop between entries, retaining descriptor anchoring for recursive deletes."""
    checkpoint()
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if stat.S_ISLNK(info.st_mode):
        raise PermissionError("删除期间路径变成符号链接")
    if not stat.S_ISDIR(info.st_mode):
        if not stat.S_ISREG(info.st_mode):
            raise PermissionError("拒绝删除特殊文件")
        os.unlink(name, dir_fd=parent)
        return
    if parent is None:
        with os.scandir(name) as children:
            for child in children:
                _remove_checked(Path(name) / child.name, None)
    else:
        child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            opened = os.fstat(child_fd)
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise PermissionError("删除期间目录被替换")
            with os.scandir(child_fd) as children:
                for child in children:
                    _remove_checked(child.name, child_fd)
        finally:
            os.close(child_fd)
    checkpoint()
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
        raise PermissionError("删除期间目录被替换")
    os.rmdir(name, dir_fd=parent)
