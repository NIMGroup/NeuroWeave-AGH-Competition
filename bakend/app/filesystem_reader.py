"""Bounded read operations shared by dataset and managed-output browsing."""
from __future__ import annotations

import base64
import fnmatch
import heapq
import hashlib
import json
import os
import stat
import time
from datetime import datetime, timezone
from .operation_context import checkpoint
from pathlib import Path
from . import workspace_io as secure_io


def fingerprint(path: Path):
    try:
        s = path.lstat()
        return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_mode]
    except FileNotFoundError:
        return None


class FilesystemReader:
    def inspect(self, path=".", workspace_id=None, root_path=None, *, sensitive=False):
        root = self.root(workspace_id, root_path)
        target = self.paths.resolve(root, path, sensitive=sensitive)
        result = {"name": target.name, "path": str(target), "relative_path": str(target.relative_to(root)),
                  "exists": target.exists()}
        raw = Path(path)
        candidate = raw if raw.is_absolute() else root / raw
        if candidate != target:
            result.update(via_path=candidate.relative_to(root).as_posix(), resolved_path=str(target))
        if target.exists():
            with secure_io.directory_fd(target.parent) as parent:
                info = os.fstat(parent) if parent is not None and not target.name else os.stat(
                    target if parent is None else target.name, dir_fd=parent, follow_symlinks=False)
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise PermissionError("路径已变化或不是普通文件/目录")
            result.update(type="directory" if stat.S_ISDIR(info.st_mode) else "file", size=info.st_size,
                          modified_at=datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
                          permissions=stat.filemode(info.st_mode), readable=os.access(target, os.R_OK),
                          writable=os.access(target, os.W_OK), symlink=candidate != target or candidate.is_symlink())
        return result

    def list_directory(self, path=".", workspace_id=None, root_path=None, cursor=None, limit=None,
                       sort="name", filter="", show_hidden=False, filter_mode="text",
                       view="details", page_char_budget=None):
        root = self.root(workspace_id, root_path)
        directory = self.paths.resolve(root, path)
        if not directory.is_dir():
            raise ValueError("路径不是目录")
        limit = max(1, min(limit or self.paths.page_size, 200))
        if sort not in {"name", "modified", "size"}:
            raise ValueError("不支持的排序")
        if filter_mode not in {'text', 'glob'} or view not in {'names', 'details'}:
            raise ValueError('filter_mode 必须是 text/glob，view 必须是 names/details')
        if filter_mode == 'text' and any(c in filter for c in '*?['):
            raise ValueError('filter 是文本包含匹配，不能使用通配符；请设 filter_mode=glob，或使用 search 文件工具的 pattern')
        stamp = fingerprint(directory)
        context = [str(directory), sort, filter, show_hidden, stamp, filter_mode]
        digest = hashlib.sha256(json.dumps(context, ensure_ascii=False).encode()).hexdigest()
        from .directory_snapshots import snapshots
        def entries():
            with os.scandir(directory) as items:
                for item in items:
                    checkpoint()
                    matched = (fnmatch.fnmatchcase(item.name.casefold(), filter.casefold()) if filter_mode == 'glob'
                               else filter.casefold() in item.name.casefold()) if filter else True
                    if (not show_hidden and item.name.startswith(".")) or not matched:continue
                    try:
                        target=self.paths.resolve(root,item.path);info=target.stat()
                        primary=-info.st_mtime_ns if sort=='modified' else -info.st_size if sort=='size' else item.name.casefold()
                        yield (0 if stat.S_ISDIR(info.st_mode) else 1,primary,item.name),item.name
                    except (OSError,ValueError):continue
        offset=0;recovered=False;reused=False
        if cursor:
            try:
                payload=json.loads(base64.urlsafe_b64decode(cursor))
                identity=payload['snapshot'];offset=payload['offset']
                if type(offset) is not int or offset<0:raise ValueError('Invalid cursor')
                if payload.get('context', digest) != digest:
                    raise ValueError('目录或查询条件已变化，请移除 cursor 从第一页刷新')
                try:
                    indexed=snapshots.get(identity,context)
                    reused=True
                except ValueError:
                    # A cursor is navigation, never a grant. Recover only an unchanged
                    # directory/query; every returned path is re-resolved below.
                    if payload.get('context') != digest:raise
                    identity,indexed=snapshots.create(context,entries());recovered=True
            except (ValueError,KeyError,TypeError) as exc:raise ValueError('分页快照已失效或目录已变化；请移除 cursor 从第一页刷新') from exc
        else:
            identity,indexed,reused=snapshots.acquire(context,entries())
        page=indexed[offset:offset+limit]
        rows=[];consumed=0;used=0;positions=[]
        for _,name in page:
            checkpoint()
            # Re-resolve links and permissions for every returned item, even on cache hits.
            try:
                row=self.inspect(str(directory/name),workspace_id,root_path)
                if view == 'names':row={k:row[k] for k in ('relative_path','type','size','readable','writable','management') if k in row}
                size=len(json.dumps(row,ensure_ascii=False))+2
                if page_char_budget and rows and used+size>page_char_budget:break
                rows.append(row);used+=size;positions.append(offset+consumed+1)
            except (OSError,ValueError):pass
            consumed+=1
        next_offset=offset+consumed;more=next_offset<len(indexed)
        token=base64.urlsafe_b64encode(json.dumps({'snapshot':identity,'offset':next_offset,'context':digest}).encode()).decode() if more else None
        return {"root_path": str(root), "path": str(directory), "relative_path": str(directory.relative_to(root)),
                "items": rows, "has_more": more, "next_cursor": token, "limit": limit,
                'offset':offset,'total_items':len(indexed),'returned_count':len(rows),
                'view':view,'filter_mode':filter_mode,'snapshot_recovered':recovered,
                'snapshot_reused':reused,'snapshot_id':identity,'snapshot_context':digest,
                **({'_page_cursor':{'snapshot':identity,'offset':offset,'context':digest},
                    '_item_offsets':positions} if page_char_budget else {})}

    def read_text(self, path, workspace_id=None, root_path=None, offset=0, max_chars=None, *, sensitive=False):
        root = self.root(workspace_id, root_path)
        target = self.paths.resolve(root, path, sensitive=sensitive)
        name = target.name.lower()
        if any(name.endswith(ext) for ext in (".nii", ".nii.gz", ".mgz", ".mgh", ".mif", ".mih", ".dcm", ".ima", ".h5", ".hdf5", ".gz", ".zip", ".npy", ".npz")):
            raise ValueError("影像或二进制文件仅可查看元数据；请交给对应 Skill/Runner 分析")
        if not target.is_file():
            raise ValueError("只能读取普通文本文件")
        if offset < 0 or offset > 2_000_000:
            raise ValueError("文本 offset 必须在 0–2000000 字符之间")
        count = max(1, min(max_chars or self.paths.text_max, self.paths.text_max))
        # Bounded prefix inspection before decoding. Never open FIFOs/devices.
        fd = secure_io.open_read(target)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("不是普通文本文件")
            prefix = os.read(fd, 8192)
            if b"\x00" in prefix or (len(prefix) > 132 and prefix[128:132] == b"DICM") or any(b < 9 or 13 < b < 32 for b in prefix):
                raise ValueError("检测到二进制内容，不能作为文本返回")
            os.lseek(fd, 0, os.SEEK_SET)
            with os.fdopen(fd, "r", encoding="utf-8-sig", errors="strict") as stream:
                fd = -1
                stream.read(offset)
                value = stream.read(count + 1)
                if any(ord(char) < 9 or 13 < ord(char) < 32 for char in value):
                    raise ValueError("请求区间包含二进制控制字符，不能作为文本返回")
        except UnicodeError as exc:
            raise ValueError("文件不是可识别的 UTF-8 文本") from exc
        finally:
            if fd >= 0:
                os.close(fd)
        return {"path": str(target), "offset": offset, "text": value[:count], "truncated": len(value) > count,
                "next_offset": offset + min(len(value), count), "max_chars": count}

    def search(self, path=".", workspace_id=None, root_path=None, pattern="*", max_depth=2, max_results=100,
               *, follow_links=False):
        from .filesystem_scan import ScopedScan
        root = self.root(workspace_id, root_path)
        raw = Path(path)
        start = raw if raw.is_absolute() else root / raw
        if not start.is_symlink() and not self.paths.resolve(root, path).is_dir():
            raise ValueError("搜索起点必须是目录")
        if not 0 <= max_depth <= 5 or not 1 <= max_results <= 200 or type(follow_links) is not bool:
            raise ValueError("搜索深度限制 0–5，结果数限制 1–200，follow_links 必须是布尔值")
        scan = ScopedScan(root, self.paths, follow_links=follow_links, max_entries=20000,
                          max_depth=max_depth, seconds=5, strict=False, skip_hidden=True)
        results = []
        for logical, target, _, _ in scan.walk(start):
            if logical.name.startswith("."):
                continue
            if fnmatch.fnmatch(logical.name.casefold(), pattern.casefold()):
                row = self.inspect(str(target), workspace_id, root_path)
                if logical != target:
                    row.update(via_path=logical.relative_to(root).as_posix(), symlink=True)
                results.append(row)
                if len(results) >= max_results:
                    scan.stop_reason = "结果数量上限"
                    break
        return {**scan.report(), "items": results, "scanned": scan.scanned, "max_depth": max_depth,
                "truncated": scan.stop_reason is not None}
