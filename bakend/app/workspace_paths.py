"""One path policy shared by HTTP tools, model tools and workspace Runner output.

Allowed roots grant filesystem scope, not OS privileges. No subprocess is used.
Special files and sensitive locations are excluded even when a root is '/'.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path


def within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


class WorkspacePaths:
    def __init__(self, default: Path, *, allowed_roots: tuple[Path, ...] | None = None):
        # Dataset readers receive a server-resolved scope, independently of the
        # output-directory allowlist. Never widen the shared output policy.
        self.default = (default if allowed_roots is not None else Path(os.getenv("WORKSPACE_DEFAULT_ROOT", str(default)))).expanduser().resolve()
        raw = os.getenv("WORKSPACE_ALLOWED_ROOTS", "")
        separator = ";" if ";" in raw or os.name == "nt" else os.pathsep
        self.roots = (tuple(p.resolve() for p in allowed_roots) if allowed_roots is not None else
                      tuple(Path(p.strip()).expanduser().resolve() for p in raw.split(separator) if p.strip()) or (self.default,))
        if not any(within(self.default, root) for root in self.roots):
            raise ValueError("WORKSPACE_DEFAULT_ROOT must be within WORKSPACE_ALLOWED_ROOTS")
        self.page_size = max(1, min(int(os.getenv("WORKSPACE_PAGE_SIZE", "80")), 200))
        self.text_max = max(1, min(int(os.getenv("WORKSPACE_TEXT_MAX_CHARS", "12000")), 100000))
        self.trash_name = os.getenv("WORKSPACE_TRASH_DIR", ".neuroweave-trash")

    def sensitive(self, path: Path) -> bool:
        # An explicitly allowed root can itself live below a hidden application
        # directory; inspect descendants, not unrelated ancestors of that root.
        allowed = next((root for root in self.roots if within(path, root)), None)
        parts = (path.relative_to(allowed).parts if allowed and path != allowed else (path.name,))
        for part in parts:
            value = part.lower()
            if (value in {".ssh", ".aws", ".azure", ".kube", ".gnupg", ".git", ".codex", ".agents",
                          "shadow", "gshadow", "sudoers", "passwd", "master.passwd", "keychain"}
                    or value.startswith((".env", "id_rsa", "id_ed25519", "id_ecdsa"))
                    or re.search(r"\.env(?:\.|$)", value)
                    or re.search(r"(?:^|[._-])(credentials?|secrets?|tokens?|private[-_]?key)(?:[._-]|$)", value)
                    or value.endswith((".pem", ".key", ".p12", ".pfx"))):
                return True
        return False

    @staticmethod
    def special(path: Path) -> bool:
        return os.name != "nt" and any(within(path, Path(p)) for p in ("/proc", "/sys", "/dev", "/run", "/boot"))

    def resolve(self, root: Path, value: str = ".", *, write: bool = False,
                sensitive: bool = False, internal: bool = False) -> Path:
        root = root.resolve()
        raw = Path(value)
        if ".." in raw.parts or "\x00" in value:
            raise PermissionError("不允许使用 .. 或空字符路径")
        candidate = raw if raw.is_absolute() else root / raw
        if not within(candidate, root):
            raise PermissionError("路径超出当前授权范围")
        try:
            resolved = candidate.resolve()
        except (RuntimeError, OSError) as exc:
            raise PermissionError("无法安全解析路径（符号链接循环或路径不可达）") from exc
        if not within(resolved, root) or not any(within(resolved, allowed) for allowed in self.roots):
            raise PermissionError("路径超出工作区或允许访问的服务器根目录")
        if not write and candidate != resolved:
            # Validate every hop, including a chain that leaves the root and
            # returns to it. A resolved final target alone is not authorization.
            pending, current, hops = list(candidate.relative_to(root).parts), root, 0
            while pending:
                current = current / pending.pop(0)
                try:
                    info = current.lstat()
                except FileNotFoundError:
                    break
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    hops += 1
                    if hops > 40:
                        raise PermissionError("符号链接循环或层数过多")
                    link = Path(os.readlink(current))
                    target = Path(os.path.abspath(link if link.is_absolute() else current.parent / link))
                    if not within(target, root) or self.special(target):
                        raise PermissionError("符号链接目标超出当前授权范围")
                    if not sensitive and self.sensitive(target):
                        raise PermissionError("符号链接目标属于敏感路径")
                    if not internal and any(p.startswith('.neuroweave-') for p in target.relative_to(root).parts):
                        raise PermissionError("符号链接目标属于平台内部目录")
                    pending, current = list(target.relative_to(root).parts) + pending, root
        if self.special(resolved) or self.special(candidate):
            raise PermissionError("不允许访问服务器特殊文件系统")
        if not internal:
            if any(p.startswith((".neuroweave-data-", ".neuroweave-restore-")) for p in (*candidate.parts, *resolved.parts)):
                raise PermissionError("请通过数据任务访问内部临时文件")
            configured_trash = Path(self.trash_name)
            in_trash = within(resolved, configured_trash.resolve()) if configured_trash.is_absolute() else configured_trash.name in candidate.parts
            if in_trash or ".neuroweave-trash" in candidate.parts:
                raise PermissionError("请通过回收站接口访问已删除文件")
        if not sensitive and (self.sensitive(candidate) or self.sensitive(resolved)):
            raise PermissionError("敏感路径默认禁止访问；读取需在文件面板再次确认")
        if write:
            # Never write through a symlink, including a dangling final link.
            for part in (candidate, *candidate.parents):
                if part.is_symlink() or (os.name == "nt" and part.exists() and
                        getattr(part.lstat(), "st_file_attributes", 0) & 0x400):
                    raise PermissionError("写操作不允许经过符号链接")
        if resolved.exists() and not (resolved.is_dir() or stat.S_ISREG(resolved.stat().st_mode)):
            raise PermissionError("仅允许普通文件和目录")
        return resolved

    def browse_root(self, value: str | None) -> Path:
        root = Path(value).expanduser().resolve() if value else self.default
        allowed = next((p for p in self.roots if within(root, p)), None)
        if allowed is None:
            raise PermissionError("服务器目录不在 WORKSPACE_ALLOWED_ROOTS 中")
        return self.resolve(allowed, str(root))

    def output_root(self, workspace_root: str, relative: str = "outputs") -> Path:
        root = self.browse_root(workspace_root)
        output = self.resolve(root, relative, write=True)
        return output
