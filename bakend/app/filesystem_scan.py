"""Scoped traversal with explicit link exclusions and optional read-only following."""
from __future__ import annotations

import os
import stat
import time
from pathlib import Path

from .operation_context import checkpoint, OperationCancelled


def is_link(path, info=None):
    info = info or path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


class ScopedScan:
    def __init__(self, root, policy, *, follow_links=False, max_entries=100000,
                 max_depth=None, seconds=None, strict=True, skip_hidden=False, check=checkpoint):
        self.root, self.policy = Path(root).resolve(), policy
        self.follow_links, self.max_entries = follow_links, max_entries
        self.max_depth, self.seconds, self.strict, self.check = max_depth, seconds, strict, check
        self.skip_hidden = skip_hidden
        self.scanned = self.skipped_symlinks = self.followed_symlinks = self.duplicate_targets = 0
        self.excluded_entries = 0
        self.examples, self.exclusions, self.reasons = [], [], {}
        self.stop_reason = None
        self.pending = None
        self.visited = set()
        self.current = None

    def savepoint(self):
        pending = list(self.pending or [])
        if self.current is not None:
            pending.append(self.current)
        return {'pending': [[str(p), d, list(a)] for p, d, a in pending],
                'visited': list(self.visited), 'scanned': self.scanned,
                **{k: getattr(self, k) for k in ('skipped_symlinks', 'followed_symlinks',
                    'duplicate_targets', 'excluded_entries', 'examples', 'exclusions', 'reasons')}}

    def restore(self, state):
        self.pending = [(Path(p), d, frozenset(tuple(v) for v in a)) for p, d, a in state['pending']]
        self.visited = {tuple(v) for v in state['visited']}
        for k in ('scanned', 'skipped_symlinks', 'followed_symlinks', 'duplicate_targets',
                  'excluded_entries', 'examples', 'exclusions', 'reasons'):
            setattr(self, k, state[k])

    def exclude(self, path, reason, *, link=False):
        relative = path.relative_to(self.root).as_posix()
        self.reasons[reason] = self.reasons.get(reason, 0) + 1
        row = {'path': relative, 'reason': reason}
        if link:
            self.skipped_symlinks += 1
            if len(self.examples) < 50:
                self.examples.append(row)
        else:
            self.excluded_entries += 1
        if len(self.exclusions) < 50:
            self.exclusions.append(row)

    def walk(self, start=None):
        start = Path(start) if start is not None else self.root
        # Lexical selection remains scoped even before resolving a link.
        if '..' in start.parts or not start.is_relative_to(self.root):
            raise PermissionError('扫描选择超出当前授权范围')
        if self.pending is None:
            self.pending = [(start, -1, frozenset())]
        stack, visited = self.pending, self.visited
        self.stop_reason = None
        deadline = time.monotonic() + self.seconds if self.seconds else None
        while stack:
            self.check()
            if self.scanned >= self.max_entries or deadline and time.monotonic() >= deadline:
                self.stop_reason = '扫描数量或时间上限'
                if self.strict:
                    raise ValueError('扫描超过数量或时间上限，请选择更小范围；未生成不完整操作')
                break
            current, depth, ancestors = stack.pop()
            self.current = (current, depth, ancestors)
            self.scanned += 1
            link = False
            try:
                if self.skip_hidden and depth >= 0 and current.name.startswith('.'):
                    self.exclude(current, 'hidden')
                    continue
                raw = current.lstat()
                link = is_link(current, raw)
                if link and not self.follow_links:
                    self.exclude(current, 'not_followed', link=True)
                    continue
                if self.policy.sensitive(current) or any(p.startswith('.neuroweave-') for p in current.relative_to(self.root).parts):
                    self.exclude(current, 'protected', link=link)
                    continue
                target = self.policy.resolve(self.root, str(current), write=not self.follow_links)
                info = target.stat() if self.follow_links else raw
                identity = (info.st_dev, info.st_ino)
                directory = stat.S_ISDIR(info.st_mode)
                if directory and identity in ancestors:
                    self.exclude(current, 'cycle', link=link)
                    continue
                if self.follow_links and identity in visited:
                    self.duplicate_targets += 1
                    self.exclude(current, 'duplicate_target', link=link)
                    continue
                if not (directory or stat.S_ISREG(info.st_mode)):
                    raise PermissionError('扫描范围含特殊文件')
                visited.add(identity)
                if link:
                    self.followed_symlinks += 1
                if directory and (self.max_depth is None or depth < self.max_depth):
                    # Real entries precede aliases so stable real paths win deduplication.
                    children = sorted(current.iterdir(), key=lambda p: (p.is_symlink() if self.follow_links else False, p.name))
                    stack.extend((p, depth + 1, ancestors | {identity}) for p in reversed(children))
                self.current = None
                if depth >= 0 or not directory:
                    yield current, target, info, depth if depth >= 0 else 0
            except OperationCancelled:
                if self.current is not None:
                    stack.append(self.current)
                    self.scanned -= 1
                raise
            except (OSError, ValueError, RuntimeError) as exc:
                if link:
                    reason = 'broken' if isinstance(exc, FileNotFoundError) else 'unauthorized' if isinstance(exc, PermissionError) else 'unresolvable'
                    self.exclude(current, reason, link=True)
                elif self.strict:
                    raise
                else:
                    self.exclude(current, 'unreadable')
            finally:
                self.current = None

    def report(self):
        warnings = []
        if self.skipped_symlinks:
            warnings.append(f'跳过 {self.skipped_symlinks} 个符号链接；未通过这些链接展开目标，真实路径下的普通文件仍可计入。')
        if self.followed_symlinks:
            warnings.append(f'只读跟随 {self.followed_symlinks} 个授权范围内的符号链接；相同目标仅统计一次。')
        if self.duplicate_targets:
            warnings.append(f'排除 {self.duplicate_targets} 个重复目标。')
        if self.excluded_entries:
            warnings.append(f'另排除 {self.excluded_entries} 个受保护、不可读或重复条目，详见 exclusion_reasons。')
        return {'symlink_policy': 'follow_authorized_readonly' if self.follow_links else 'skip',
                'skipped_symlinks': self.skipped_symlinks, 'followed_symlinks': self.followed_symlinks,
                'duplicate_targets': self.duplicate_targets, 'symlink_examples': self.examples,
                'symlink_examples_truncated': self.skipped_symlinks > len(self.examples),
                'excluded_entries': self.excluded_entries, 'exclusion_reasons': self.reasons,
                'excluded_examples': self.exclusions, 'warnings': warnings,
                'complete_scan': self.stop_reason is None,
                'scope_complete': self.stop_reason is None and not self.skipped_symlinks and not self.excluded_entries,
                'coverage_note': '数量仅包含实际扫描的普通文件；不通过跳过的链接读取或修改目标，重复目标只统计一次。',
                'stop_reason': self.stop_reason}
