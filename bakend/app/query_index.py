"""Scoped, resumable read indexes. Snapshots never authorize writes or execution."""
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import stat
from threading import RLock
import time
from uuid import uuid4

from .data_auth import principal
from .filesystem_scan import ScopedScan
from .filesystem_scan import is_link
from .operation_context import checkpoint
from .workspace_paths import WorkspacePaths


class QueryIndexes:
    def __init__(self, service, *, clock=time.time, slice_seconds=30):
        self.service, self.clock, self.slice_seconds = service, clock, slice_seconds
        self.entries, self.lock = OrderedDict(), RLock()
        self.scan_locks = {}
        self.invalidated = {}
        self.directory = service.directory / '.query-indexes'

    def acquire(self, scope, path='.', *, follow_links=False, refresh=False):
        source, _ = self.service._roots(scope)
        policy = WorkspacePaths(source, allowed_roots=(source,))
        start = source / path
        if follow_links:
            policy.resolve(source,path)
        else:
            # A selected alias is scanned as an excluded link, without reading
            # its target. Stop at the first link, including ancestor links.
            current=source
            for part in Path(path).parts:
                current=current/part
                if is_link(current,current.lstat()):
                    start=current;break
            if start==source/path and not is_link(start,start.lstat()):
                policy.resolve(source,path,write=True)
        info = start.stat() if follow_links else start.lstat()
        mutations=self.mutation_version(source)
        identity = [principal.get().name, scope['source_scope'], scope.get('dataset_id'),
                    scope.get('workspace_id'), str(source), follow_links]
        with self.lock:
            # A completed parent index can answer narrower selections without another walk.
            found = None
            for key, value in reversed(self.entries.items()):
                if (value['identity'] == identity and start.is_relative_to(Path(value['start']))
                        and not refresh and self.clock() - value.get('updated_at', value['observed_at']) < 900):
                    found = key, value
                    break
            key = hashlib.sha256(json.dumps([identity, str(start)], sort_keys=True).encode()).hexdigest()[:24]
            if found:
                key, value = found
            else:
                value = None if refresh else self.entries.get(key) or self.load(key)
            fingerprint = [info.st_dev, info.st_ino, info.st_mtime_ns]
            try:
                previous = (Path(value['start']).stat() if follow_links else Path(value['start']).lstat()) if value else None
                changed = previous and [previous.st_dev, previous.st_ino, previous.st_mtime_ns] != value['root_revision']
            except OSError:
                changed = True
            if value and (value.get('identity') != identity or changed or value.get('mutation_version')!=mutations or
                    value['observed_at'] <= self.invalidated.get(tuple(identity[:5]), -1) or
                    self.clock() - value.get('updated_at', value['observed_at']) >= 900):
                value = None
            reused = bool(value)
            if value is None:
                value = {'id': uuid4().hex, 'identity': identity, 'start': str(start),
                         'root_revision': fingerprint, 'observed_at': self.clock(), 'rows': [],
                         'mutation_version': mutations,
                         'scan_state': None, 'report': {}, 'complete': False, 'scanned': 0}
            self.entries[key] = value
            self.entries.move_to_end(key)
            # Bound live rows. Evicted snapshots remain retrievable from their owned file.
            while len(self.entries) > 16 or sum(len(v['rows']) for v in self.entries.values()) > 200000:
                if len(self.entries) == 1:
                    break
                self.entries.popitem(last=False)
            scan_lock = self.scan_locks.setdefault(key, RLock())
        # Independent datasets must not queue behind a large scan. Waiting for an
        # overlapping scan remains cancellable, as does the scan itself.
        while not scan_lock.acquire(timeout=0.2):
            checkpoint()
        try:
            if not value['complete'] and value['scanned'] < 100000:
                self.advance(key, value, source, policy, follow_links)
            return {**value, 'rows': list(value['rows']), 'index_reused': reused, 'source_root': str(source)}
        finally:
            scan_lock.release()

    def mutation_version(self, source):
        # Known platform mutations invalidate even a deep directory whose root
        # mtime stayed unchanged. Read-only plans/queries don't change this stamp.
        with self.service.store.connection() as db:
            data=[list(row) for row in db.execute("""SELECT status,count(*),max(json_extract(payload,'$.updated_at'))
                FROM data_operations WHERE status IN ('succeeded','rolled_back','failed','cancelled','interrupted')
                AND (json_extract(payload,'$.source_root')=? OR json_extract(payload,'$.target_root')=?) GROUP BY status ORDER BY status""",(str(source),str(source)))]
            workspace=[list(row) for row in db.execute("SELECT status,count(*),max(created_at) FROM workspace_operations WHERE status IN ('succeeded','failed','cancelled') GROUP BY status ORDER BY status")]
            runs=[list(row) for row in db.execute("SELECT status,count(*),max(updated_at) FROM runs WHERE status IN ('succeeded','failed','cancelled') GROUP BY status ORDER BY status")]
        return [data,workspace,runs]

    def invalidate(self, scope):
        source, _ = self.service._roots(scope)
        with self.lock:
            self.invalidated[tuple([principal.get().name, scope['source_scope'], scope.get('dataset_id'), scope.get('workspace_id'), str(source)])] = self.clock()
            keys = [k for k,v in self.entries.items() if v['identity'][:5] ==
                    [principal.get().name, scope['source_scope'], scope.get('dataset_id'), scope.get('workspace_id'), str(source)]]
            for key in keys:
                self.entries.pop(key)
                (self.directory / (key + '.json')).unlink(missing_ok=True)

    def load(self, key):
        path = self.directory / (key + '.json')
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 64 * 1024 * 1024:
            return None
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            return value if value.get('version') == 1 and self.clock() - value.get('updated_at', value['observed_at']) < 900 else None
        except (OSError, ValueError, KeyError):
            return None

    def save(self, key, value):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self.directory / (key + '.json')
        temporary = self.directory / ('.index-' + uuid4().hex[:16] + '.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump({**value, 'version': 1}, stream, ensure_ascii=False)
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        # Only remove expired files of this internal cache, never data or operations.
        for old in self.directory.glob('*.json'):
            try:
                if old != path and self.clock() - old.stat().st_mtime > 1800:
                    old.unlink(missing_ok=True)
            except FileNotFoundError:
                pass

    def advance(self, key, value, source, policy, follow_links):
        scan = ScopedScan(source, policy, follow_links=follow_links, seconds=self.slice_seconds, strict=False)
        if value['scan_state'] is not None:
            scan.restore(value['scan_state'])
        try:
            for logical, target, info, _ in scan.walk(Path(value['start'])):
                if stat.S_ISREG(info.st_mode):
                    row = {'path': logical.relative_to(source).as_posix(), 'size': info.st_size,
                           'revision': [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]}
                    if logical != target:
                        row['resolved_path'] = target.relative_to(source).as_posix()
                    value['rows'].append(row)
                if scan.scanned % 100 == 0:
                    checkpoint(f'建立只读索引：已扫描 {scan.scanned} 项，发现 {len(value["rows"])} 个文件')
        finally:
            value.update(scan_state=scan.savepoint(), scanned=scan.scanned,
                         complete=not scan.pending and scan.current is None, report=scan.report())
            value['updated_at'] = self.clock()
            if value['complete']:
                value['observed_at'] = self.clock()
            if not value['complete']:
                value['report']['complete_scan'] = False
                value['report']['scope_complete'] = False
            self.save(key, value)


def query_index(service, scope, path='.', *, follow_links=False, refresh=False):
    # Construct once per service. The lock also serializes overlapping scans of one index.
    with service.query_index_lock:
        indexes = getattr(service, '_query_indexes', None)
        if indexes is None:
            indexes = service._query_indexes = QueryIndexes(service)
    return indexes.acquire(scope, path, follow_links=follow_links, refresh=refresh)
