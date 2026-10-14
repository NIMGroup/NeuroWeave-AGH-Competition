"""Cache expensive read-only checks by file identity; permission checks always run first."""

from collections import OrderedDict
from threading import RLock
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import time

_items = OrderedDict()
_lock = RLock()
_snapshot = ContextVar("input_check_snapshot", default=None)


@contextmanager
def content_cache(context):
    report = getattr(context.workflow, "dry_run_report", None)
    # The report is server-generated, never a model-supplied claim. It travels
    # with the approved workflow to remote workers; permissions run separately.
    saved = getattr(context.workflow, "input_check_cache", {}) or (
        getattr(report, "input_checks", {}) if report else {}
    )
    token = _snapshot.set((saved, context.input_checks))
    try:
        yield
    finally:
        _snapshot.reset(token)


def checked_file(path, kind, action, ttl=600):
    stat = path.stat()
    key = (
        str(path.resolve()),
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
        kind,
    )
    fingerprint = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
    snapshot = _snapshot.get()
    if snapshot and fingerprint in snapshot[0]:
        result = snapshot[0][fingerprint]
        snapshot[1][fingerprint] = result
        return result
    with _lock:
        item = _items.get(key)
        if item and time.monotonic() - item[0] < ttl:
            if snapshot:
                snapshot[1][fingerprint] = item[1]
            return item[1]
    result = action()
    after = path.stat()
    if (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError("检查期间文件发生变化，请重试")
    with _lock:
        _items[key] = (time.monotonic(), result)
        _items.move_to_end(key)
        while len(_items) > 4096:
            _items.popitem(last=False)
    if snapshot:
        snapshot[1][fingerprint] = result
    return result
