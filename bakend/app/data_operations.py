"""Durable batches with exact grants, conflict checks, journals and guarded undo.

Generated code cannot commit changes. The trusted executor resolves scopes again,
checks file identity, and reserves paths against scientific submissions.
"""
import difflib
import hashlib
import json
import logging
import os
import shutil
import stat
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
from threading import RLock, Event, BoundedSemaphore
from uuid import uuid4

from .operation_context import checkpoint as request_checkpoint, current_operation, OperationContext, OperationCancelled
from .data_auth import principal
from .data_sandbox import DataSandbox
from .data_operation_store import DataOperationStore, STATIC_FIELDS
from .data_python import DataPythonMixin
from .workspace_paths import WorkspacePaths, within
from . import workspace_io as io

logger = logging.getLogger(__name__)


class CopyCancelled(Exception):
    """Cooperative cancellation leaves the prepared journal for explicit undo."""


def stamp(path):
    if not path.exists():
        return None
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("批量操作仅支持普通文件")
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def root_stamp(path):
    info = path.stat()
    return [info.st_dev, info.st_ino]


def digest(path):
    value = hashlib.sha256()
    with io.directory_fd(path.parent) as parent:
        fd = os.open(path if parent is None else path.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise PermissionError("仅允许普通文件")
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                request_checkpoint()
                value.update(block)
    return value.hexdigest()


class DataOperations(DataPythonMixin):
    def __init__(self, workspace, datasets):
        self.workspace, self.datasets, self.store = workspace, datasets, workspace.store
        self.lock = RLock()
        self.query_index_lock = RLock()
        self.run_lock = workspace.run_lock
        self.protected_paths = workspace.protected_paths
        self.directory = self.store.database_path.parent / "data-operations"
        self.directory.mkdir(exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="data-operation")
        self.script_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='data-script')
        self.script_slots = BoundedSemaphore(2)
        self.file_workers = max(1, min(4, int(os.getenv('NEUROWEAVE_DATA_COPY_WORKERS', '2'))))
        self.file_pool = ThreadPoolExecutor(max_workers=self.file_workers, thread_name_prefix='data-file')
        self._inflight_bytes = {}
        self._cancel_events = {}
        self.futures = {}
        self.sandbox = DataSandbox()
        self.repository = DataOperationStore(self.store)
        for identity in self.repository.interrupted():
            task = self._raw(identity)
            task.update(status="interrupted", error="服务重启中断；请检查记录并恢复，任务不会自动重复执行")
            self._save(task)

    def shutdown(self):
        self.script_pool.shutdown(wait=True)
        self.pool.shutdown(wait=True)
        self.file_pool.shutdown(wait=True)

    def _cancel_event(self, identity):
        return self._cancel_events.setdefault(identity, Event())

    def _save(self, task):
        with self.store._lock:
            if task.get('status') in {'validating','queued','analyzing','running','rolling_back'}:
                from .task_management import assert_plan_open
                assert_plan_open(self.store,'data',task['id'])
            self.repository.save(task)

    def _raw(self, identity, detail='full'):
        return self.repository.load(identity, detail)

    def _save_item(self, task, entry):
        state = {k: v for k, v in task.items() if k not in {'manifest', 'journal'} | STATIC_FIELDS}
        state['journal'] = [entry]
        self._save(state)
        task.update(updated_at=state['updated_at'], _journal_count=state['_journal_count'])

    def _owner(self, task, conversation_id):
        if task["actor"] != principal.get().name or task["conversation_id"] != conversation_id:
            raise PermissionError("操作不属于当前账户和会话")
        # Reading records and revoking grants must remain possible if a mount
        # disappears. Actual writes/undo still re-resolve and validate roots.
        scope = task["scope"]
        if scope["source_scope"] == "dataset":
            principal.get().check("dataset", scope["dataset_id"])
        if scope["source_scope"] == "output" or scope["mode"] == "copy":
            principal.get().check("output", scope["workspace_id"])

    def get(self, identity, conversation_id):
        task = self._raw(identity, 'preview')
        self._owner(task, conversation_id)
        result = self.public(task)
        if task.get('scan') and not task.get('recipe'):
            from .data_organization import legacy_recipe
            recipe = legacy_recipe(self, task)
            if recipe:
                result['recipe'] = recipe
        return result

    def progress(self, identity, conversation_id):
        with self.lock:
            task = self._raw(identity, 'state')
            self._owner(task, conversation_id)
            if task.get('_storage_version') == 2:
                with self.store.connection() as db:
                    task.update({r[0]: json.loads(r[1]) for r in db.execute(
                        "SELECT key,payload FROM data_operation_metadata WHERE operation_id=? AND key IN ('scan','recipe','quality','transform')", (identity,))})
            result = self.public(task)
            result.pop('items', None)
            return result

    def list(self, conversation_id):
        self.store.get_conversation(conversation_id)
        permitted = []
        for identity in self.repository.identities(principal.get().name, conversation_id):
            task = self._raw(identity, 'preview')
            try:
                self._owner(task, conversation_id)
            except PermissionError:
                continue
            permitted.append(self.public(task))
        return permitted[:50]

    def context(self, conversation_id):
        return [{k: t.get(k) for k in ("id", "title", "status", "mode", "file_count", "completed", "error")}
                for t in self.list(conversation_id)]

    def public(self, task):
        result = {k: v for k, v in task.items() if not k.startswith('_') and k not in {"scope", "journal", "grant", "manifest", "root_stamps"}}
        result["items"] = [{**{k: v for k, v in item.items() if k in {"action", "path", "destination", "size", "diff"}},
                            "has_artifact": bool(item.get("artifact")) or item.get("content") is not None, "index": index}
                           for index, item in enumerate(task["manifest"][:100])]
        result["action_counts"] = task['_stats']['action_counts']
        result['effect_preview'] = [{k: v for k, v in row.items() if k in {'action', 'path', 'destination', 'diff'}}
                                    for row in result['items'][:8]]
        result["preview_truncated"] = task['file_count'] > 100
        result["source_scope"] = task["scope"]["source_scope"]
        script_pending = task.get("phase") == "script" and not task.get("manifest_ready", True)
        result["requires_inplace_confirmation"] = not script_pending and task["mode"] == "inplace" and result["source_scope"] == "dataset"
        result["requires_delete_confirmation"] = bool(result['action_counts'].get('delete'))
        result["requires_overwrite_confirmation"] = self._output_overwrite(task)
        result["execution_policy"] = task.get("execution_policy", "manual")
        result["preview_expired"] = task["status"] == "pending" and task["expires_at"] < time.time()
        result["can_confirm"] = task.get('manifest_ready', True) and not task["conflicts"] and task["status"] == "pending" and not result["preview_expired"] and not task.get("quality", {}).get("blocking_issues") and bool(task["file_count"])
        result["can_run_script"] = script_pending and task["status"] == "pending" and not result["preview_expired"] and not task.get('quality', {}).get('blocking_issues') and bool(task.get("script_plan_hash"))
        result["can_undo"] = bool(task.get('_journal_count', len(task.get('journal', [])))) and task["status"] in {"succeeded", "failed", "cancelled", "interrupted"}
        result["can_cancel"] = task["status"] in {"pending", "validating", "queued", "analyzing", "running", "rolling_back", "cancel_requested"}
        result["cancel_requested"] = task["status"] in {"validating", "queued", "analyzing", "running", "rolling_back", "cancel_requested"} and self._cancel_event(task["id"]).is_set()
        if task.get('script_hash'):
            log = self.directory / task['id'] / 'execution.log'
            result['execution_log_available'] = False
            if log.is_file():
                try:
                    self._artifact_path(log)
                    result['execution_log_available'] = True
                    result['execution_log_bytes'] = log.stat().st_size
                    result['execution_log_truncated'] = len(task.get('execution_log', '').encode('utf8')) < result['execution_log_bytes']
                    # The sandbox drains stdout after its 64 KiB capture limit.
                    # Reaching it cannot prove that the saved result is complete.
                    result['execution_log_capture_limited'] = result['execution_log_bytes'] >= 65536
                except (OSError, ValueError):
                    pass
        with self.lock:
            result['processed_bytes'] = min(task['total_bytes'], task.get('completed_bytes', 0) + sum(self._inflight_bytes.get(task['id'], {}).values()))
        return result

    def _roots(self, scope):
        conversation = self.store.get_conversation(scope["conversation_id"])
        if not conversation:
            raise KeyError("会话不存在")
        if scope.get("workspace_id") and conversation.workspace_id != scope["workspace_id"]:
            raise PermissionError("输出目录与当前会话不一致")
        if scope["source_scope"] == "dataset":
            if not scope.get("dataset_id"):
                raise ValueError("请先选择数据集")
            principal.get().check("dataset", scope.get("dataset_id"))
            source = self.datasets.reader(scope["dataset_id"]).root()
        else:
            if not scope.get("workspace_id"):
                raise ValueError("请先选择输出目录")
            principal.get().check("output", scope.get("workspace_id"))
            source = self.workspace.root(scope["workspace_id"])
        if scope["mode"] == "copy":
            if not scope.get("workspace_id"):
                raise ValueError("复制整理需要选择输出目录")
            principal.get().check("output", scope["workspace_id"])
            target = self.workspace.validate_output_directory(self.workspace.root(scope["workspace_id"]))
        else:
            target = source
        # Grants cannot silently follow a replaced registered root.
        for root in (source, target):
            if not root.is_dir():
                raise ValueError("数据目录不可用")
        return source, target

    def _path(self, root, relative):
        if Path(relative).is_absolute():
            raise PermissionError("数据操作仅接受范围内的相对路径")
        policy = WorkspacePaths(root, allowed_roots=(root,))
        path = policy.resolve(root, relative, write=True)
        if path == root or any(part.startswith(".neuroweave-") for part in path.relative_to(root).parts):
            raise PermissionError("不能修改根目录或平台内部目录")
        return path

    def _guard(self, *paths, task_id=None):
        for path in paths:
            for protected in self.protected_paths():
                protected = Path(protected).resolve()
                if within(path, protected) or within(protected, path):
                    raise PermissionError("路径正被科学任务或平台使用，请待任务结束后操作")
        with self.store._connect() as db:
            others = [json.loads(r[0]) for r in db.execute(
                "SELECT payload FROM data_operations WHERE status IN ('validating','queued','analyzing','running','rolling_back','cancel_requested','interrupted') AND id!=?",
                (task_id or "",))]
            legacy = [json.loads(r[0]) for r in db.execute("SELECT payload FROM workspace_operations WHERE status='running'")]
        reserved = [Path(t["target_root"]) for t in others]
        reserved.extend(Path(t["source_root"]) for t in others)
        reserved.extend(Path(t["target"]) for t in legacy if t.get("target"))
        reserved.extend(Path(t["source"]) for t in legacy if t["action"] not in {"copy", "read-sensitive"})
        if any(within(p, r) or within(r, p) for p in paths for r in reserved):
            raise PermissionError("路径已由其他文件操作占用")

    def inventory(self, scope, path=".", *, follow_links=False):
        from .filesystem_scan import ScopedScan
        source, _ = self._roots(scope)
        policy = WorkspacePaths(source, allowed_roots=(source,))
        raw = Path(path)
        start = raw if raw.is_absolute() else source / raw
        scan = ScopedScan(source, policy, follow_links=follow_links)
        rows = []
        for logical, target, info, _ in scan.walk(start):
            if stat.S_ISREG(info.st_mode):
                row = {"path": logical.relative_to(source).as_posix(), "size": info.st_size,
                       "revision": [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]}
                if logical != target:
                    row['resolved_path'] = target.relative_to(source).as_posix()
                rows.append(row)
        return {**scan.report(), "items": rows, "file_count": len(rows),
                "total_bytes": sum(r["size"] for r in rows), "excluded_internal_entries": scan.excluded_entries}

    def organize(self, request):
        from .data_organization import organize
        return organize(self, request)

    def coverage(self, identity, conversation_id):
        from .data_organization import coverage_groups
        task = self.get(identity, conversation_id)
        rows = self.operation_files(identity, conversation_id, full=True)
        return {'id': identity, 'status': task['status'], 'scan': task.get('scan'),
                'recipe': task.get('recipe'), 'quality': task.get('quality'),
                'conflict_count': len(task['conflicts']), 'conflicts': task['conflicts'][:10],
                'snapshot': rows['snapshot'], 'unmatched': coverage_groups(rows['items'])}

    def operation_files(self, identity, conversation_id, kind="unmatched", offset=0, limit=50, full=False):
        task = self._raw(identity, 'preview')
        self._owner(task, conversation_id)
        if kind not in {"unmatched", "changes", "preserved"} or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("文件清单范围或分页无效")
        snapshot = True
        if kind == "changes":
            rows = [{k: m.get(k) for k in ('action', 'path', 'destination', 'size')}
                    for m in self.repository.items(identity, offset, None if full else limit)]
            total, end = task['file_count'], task['file_count'] if full else offset + limit
            return {'id': identity, 'kind': kind, 'status': task['status'], 'manifest_hash': task['manifest_hash'],
                    'items': rows, 'total': total, 'extension_counts': task['_stats'].get('extension_counts', {}),
                    'snapshot': True, 'unmatched_policy': 'skip', 'included_in_batch': True,
                    'complete_scan': True, 'page_complete': end >= total, 'next_offset': end if end < total else None}
        elif kind == 'preserved':
            rows = [{k: m.get(k) for k in ('action', 'path', 'destination', 'size')} for m in self.repository.items(identity)
                    if m['action'] == 'copy' and m['path'] == m.get('destination')]
        else:
            if not task.get("scan"):
                raise ValueError("该操作没有整理扫描记录")
            with self.store._connect() as db:
                saved = db.execute("SELECT payload FROM data_operation_inventories WHERE id=?", (identity,)).fetchone()
            if saved:
                rows = json.loads(saved[0])
            else:
                # Legacy batches did not freeze exclusions. Report a fresh scan
                # explicitly; never present it as the original preview snapshot.
                snapshot = False
                source, target = self._roots(task["scope"])
                self._check_roots(task, source, target)
                selected = {m['path'] for m in self.repository.items(identity)}
                rows = [r for r in self.inventory(task["scope"])["items"] if r["path"] not in selected]
            rows = [{k: r[k] for k in ("path", "size")} for r in rows]
        types = {}
        for row in rows:
            path = row["path"]
            extension = ".nii.gz" if path.lower().endswith(".nii.gz") else Path(path).suffix.lower() or "(none)"
            types[extension] = types.get(extension, 0) + 1
        end = len(rows) if full else offset + limit
        return {"id": identity, "kind": kind, "status": task["status"], "manifest_hash": task["manifest_hash"],
                "items": rows[offset:end], "total": len(rows), "extension_counts": types,
                "snapshot": snapshot, "unmatched_policy": "skip", "included_in_batch": kind in {"changes", "preserved"},
                "complete_scan": True, "page_complete": end >= len(rows),
                "next_offset": end if end < len(rows) else None}

    def propose(self, request):
        return self._finish_request(self._propose(request), request.execution)

    def _propose(self, request, conflicts=None, *, ready=True, status='pending', identity=None):
        scope = request.model_dump(exclude={"title", "edits", "execution"})
        source, target = self._roots(scope)
        manifest, conflicts = [], list(conflicts or [])
        sources, targets = set(), set()
        for edit in request.edits:
            request_checkpoint()
            src = self._path(source, edit.path)
            dst = self._path(target, edit.destination or edit.path)
            if scope["mode"] == "copy" and edit.action not in {"copy", "write_text"}:
                raise ValueError("复制模式不能移动或删除输入")
            if edit.action in {"copy", "rename"} and not edit.destination:
                raise ValueError("复制和改名需要准确目标路径")
            if edit.action == "delete" and edit.destination:
                raise ValueError("删除不能指定目标路径")
            if edit.action == "copy" and src == dst:
                raise ValueError("复制的源和目标不能相同")
            if edit.action == "write_text":
                if edit.content is None or src.suffix.lower() not in {".json", ".csv", ".tsv", ".txt", ".md", ".yaml", ".yml"} or "\x00" in edit.content:
                    raise ValueError("文本编辑需要有效文本，且仅允许文本格式")
                if src.suffix.lower() == ".json":
                    json.loads(edit.content)
                if edit.destination and scope["mode"] == "inplace" and src != dst:
                    raise ValueError("原地文本调整不能同时改名；请分开提出操作")
            elif edit.content is not None:
                raise ValueError("只有文本写入允许 content")
            if edit.action != "write_text" and not src.is_file():
                raise FileNotFoundError(f"源文件不存在：{edit.path}")
            if src.exists() and not src.is_file():
                raise ValueError("不支持修改目录")
            if dst in targets:
                conflicts.append(f"重复目标：{dst.relative_to(target)}")
            targets.add(dst)
            if scope["mode"] == "inplace" and src in sources:
                conflicts.append(f"重复修改同一源：{edit.path}")
            sources.add(src)
            allowed_text_target = edit.action == "write_text" and (scope["mode"] == "copy" or src == dst)
            if dst.exists() and not (allowed_text_target or (edit.action == "delete" and scope["mode"] == "inplace" and src == dst)):
                conflicts.append(f"目标已存在：{dst.relative_to(target)}")
            if dst.exists() and not dst.is_file():
                raise ValueError("目标不是普通文件")
            row = edit.model_dump()
            row.update(source=str(src), target=str(dst), before=stamp(src), target_before=stamp(dst),
                       size=src.stat().st_size if src.exists() else 0)
            if edit.action == "write_text":
                before = ""
                previous = dst if dst.exists() else src
                if previous.exists():
                    if previous.stat().st_size > 2_000_000:
                        raise ValueError("文本过大，请使用隔离 Python 分析并生成副本")
                    before = previous.read_text(encoding="utf-8")
                    row["before_hash" if previous == src else "target_before_hash"] = digest(previous)
                row["content_hash"] = hashlib.sha256(edit.content.encode()).hexdigest()
                row["size"] = len(edit.content.encode())
                row["diff"] = "".join(list(difflib.unified_diff(before.splitlines(True), edit.content.splitlines(True),
                                                              fromfile=edit.path, tofile=edit.destination or edit.path))[:120])[:12000]
            manifest.append(row)
        return self._record(scope, source, target, request.title, manifest, conflicts, identity=identity, ready=ready, status=status)

    def _record(self, scope, source, target, title, manifest, conflicts, *, identity=None, ready=True, status='pending'):
        request_checkpoint()
        targets = {Path(row['target']) for row in manifest}
        for path in targets:
            if any(parent in targets for parent in path.parents):
                conflicts.append('目标文件同时被用作其他目标的父目录：' + str(path))
        root_stamps = [root_stamp(source), root_stamp(target)]
        canonical = json.dumps({"scope": scope, "source": str(source), "target": str(target),
                                "root_stamps": root_stamps, "manifest": manifest},
                               sort_keys=True, ensure_ascii=False).encode()
        task = {"id": identity or uuid4().hex, "kind": "data", "title": title, "actor": principal.get().name,
                "conversation_id": scope["conversation_id"], "source_root": str(source), "target_root": str(target),
                "scope": scope, "mode": scope["mode"], "manifest": manifest, "manifest_hash": hashlib.sha256(canonical).hexdigest(),
                "root_stamps": root_stamps,
                "status": status, "conflicts": conflicts, "file_count": len(manifest),
                "total_bytes": sum(m["size"] for m in manifest), "completed": 0, "journal": [],
                "created_at": time.time(), "expires_at": time.time() + 86400,
                "manifest_ready": ready, "completed_bytes": 0, "execution_policy": "manual"}
        self._save(task)
        return task

    @staticmethod
    def _output_overwrite(task):
        if '_stats' in task:
            return task['_stats']['overwrite']
        scope = task["scope"]
        output = scope.get("workspace_id") and (scope["mode"] == "copy" or scope["source_scope"] == "output")
        return bool(output and any(m["action"] != "delete" and m.get("target_before") is not None
                                   for m in task["manifest"]))

    def _finish_request(self, task, execution):
        # Old clients/models may still request execution=output. Preparing a
        # proposal never grants execution; only the HTTP run controls can do so.
        return self.public(task)

    def confirm(self, identity, request):
        from .task_management import assert_plan_open
        assert_plan_open(self.store,'data',identity)
        with self.lock, self.run_lock:
            task = self._raw(identity)
            self._owner(task, request.conversation_id)
            if task["manifest_hash"] != request.manifest_hash:
                raise PermissionError("确认的操作清单已变化")
            if task["status"] != "pending":
                return self.public(task)  # Idempotent confirmation cannot execute twice.
            if not task.get("manifest_ready", True) or not task["file_count"]:
                raise PermissionError("脚本尚未运行或没有可执行清单；请先点击运行脚本")
            if task["conflicts"] or task.get("quality", {}).get("blocking_issues") or task["expires_at"] < time.time():
                raise ValueError("操作存在冲突或已过期，请重新生成预览")
            if task["mode"] == "inplace" and task["scope"]["source_scope"] == "dataset":
                from .data_auth import inplace_available
                if not inplace_available():
                    raise PermissionError("当前访问身份不能批准原地修改")
                if not request.allow_inplace:
                    raise PermissionError("请明确授权本批次原地修改")
            if any(m["action"] == "delete" for m in task["manifest"]) and not request.allow_delete:
                raise PermissionError("删除需要单独明确授权")
            if self._output_overwrite(task) and not request.allow_overwrite:
                raise PermissionError("覆盖输出目录中的现有文件需要单独明确确认")
            task["status"] = "validating"
            self._save(task)
            token = current_operation.set(OperationContext(self._cancel_event(identity), lambda _: None))
            try:
                self._validate(task)
                request_checkpoint()
            except OperationCancelled:
                task["status"] = "cancelled"
                self._save(task)
                return self.public(task)
            except Exception:
                task["status"] = "pending"
                self._save(task)
                raise
            finally:
                current_operation.reset(token)
            task["grant"] = {"actor": principal.get().name, "manifest_hash": request.manifest_hash,
                             "expires_at": task["expires_at"], "revoked": False,
                             "actions": sorted({m["action"] for m in task["manifest"]})}
            task["status"] = "running"
            self._save(task)
            actor = principal.get()
            self.futures[identity] = self.pool.submit(self._run_as, actor, identity, False)
            return self.public(task)

    def _validate(self, task):
        source, target = self._roots(task["scope"])
        self._check_roots(task, source, target)
        self._guard(source, target, task_id=task['id'])
        for row in task["manifest"]:
            request_checkpoint()
            src = self._path(source, row["path"])
            dst = self._path(target, row["destination"] or row["path"])
            if stamp(src) != row["before"] or stamp(dst) != row["target_before"]:
                raise ValueError("文件自预览后发生变化，请重新生成预览")
            if row.get("before_hash") and digest(src) != row["before_hash"]:
                raise ValueError("文件内容已变化")
            if row.get("target_before_hash") and digest(dst) != row["target_before_hash"]:
                raise ValueError("输出文件内容已变化")
            if row.get("artifact"):
                artifact = Path(row["artifact"])
                self._artifact_path(artifact)
                if digest(artifact) != row["content_hash"]:
                    raise ValueError("隔离执行结果已变化")

    def _check_roots(self, task, source, target):
        if (str(source) != task["source_root"] or str(target) != task["target_root"]
                or [root_stamp(source), root_stamp(target)] != task["root_stamps"]):
            raise PermissionError("登记目录发生变化")

    def cancel(self, identity, conversation_id):
        # Signal before waiting for the mutation lock. Hashing and copying may
        # hold that lock, but must still observe an authorized stop promptly.
        snapshot = self._raw(identity, 'state')
        self._owner(snapshot, conversation_id)
        if snapshot["status"] in {"pending", "validating", "queued", "analyzing", "running", "rolling_back", "cancel_requested"}:
            self._cancel_event(identity).set()
        with self.lock:
            task = self._raw(identity)
            self._owner(task, conversation_id)
            if task["status"] == "pending":
                task["status"] = "cancelled"
            elif task['status'] == 'queued':
                task['status'] = 'cancelled'
            elif task['status'] == 'analyzing':
                task['status'] = 'cancel_requested'
            elif task['status'] == 'cancel_requested':
                pass
            elif task["status"] == "interrupted" and not task["journal"]:
                task["status"] = "cancelled"
                if task.get("grant"):
                    task["grant"]["revoked"] = True
            elif task["status"] in {"validating", "running", "rolling_back"}:
                if task.get("grant"):
                    task["grant"]["revoked"] = True
            elif task["status"] == "cancelled":
                return self.public(task)
            else:
                raise ValueError("当前任务不能取消")
            self._cancel_event(identity).set()
            self._save(task)
            return self.public(task)

    def undo(self, identity, request):
        with self.lock, self.run_lock:
            task = self._raw(identity)
            self._owner(task, request.conversation_id)
            if request.manifest_hash != task["manifest_hash"] or not self.public(task)["can_undo"]:
                raise ValueError("当前操作不能恢复")
            from .data_auth import inplace_available
            if task["mode"] == "inplace" and task["scope"]["source_scope"] == "dataset" and (not inplace_available() or not request.allow_inplace):
                raise PermissionError("恢复原地修改需要明确授权")
            if self._output_overwrite(task) and not request.allow_overwrite:
                raise PermissionError("恢复被覆盖的输出文件需要明确确认")
            future = self.futures.get(identity)
            if future is not None and not future.done():
                raise ValueError("当前步骤仍在清理，请稍后恢复")
            self._cancel_events[identity] = Event()
            task["status"] = "rolling_back"
            task["undo_actor"] = principal.get().name
            self._save(task)
            self.futures[identity] = self.pool.submit(self._run_as, principal.get(), identity, True)
            return self.public(task)

    def _run_as(self, actor, identity, undo):
        token = principal.set(actor)
        context_token = current_operation.set(OperationContext(self._cancel_event(identity), lambda _: None))
        try:
            request_checkpoint()
            if undo:
                self._undo(identity)
            else:
                self._execute(identity)
        except Exception as exc:
            if not isinstance(exc, (CopyCancelled, OperationCancelled)):
                logger.exception("Data operation %s failed during %s", identity, "undo" if undo else "execution")
            with self.lock:
                task = self._raw(identity, 'state')
                cancelled = isinstance(exc, (CopyCancelled, OperationCancelled))
                task.update(status="cancelled" if cancelled else "failed", error=None if cancelled else str(exc))
                self._save(task)
        finally:
            current_operation.reset(context_token)
            principal.reset(token)

    def _parents(self, target, root):
        missing = []
        current = target.parent
        while current != root and not current.exists():
            missing.append(current)
            current = current.parent
        for path in reversed(missing):
            io.mkdir(path)
        return [str(p) for p in missing]

    def _execute(self, identity):
        with self.lock:
            task = self._raw(identity)
            self._validate(task)
        backup_root = self.directory / identity / "backup"
        backup_root.mkdir(parents=True, exist_ok=True)
        needed = sum((m.get("target_before") or [0, 0, 0])[2] for m in task["manifest"]
                     if m["action"] in {"write_text", "delete"})
        if shutil.disk_usage(backup_root).free < needed + 10 * 1024 * 1024:
            raise ValueError("原版本备份空间不足，未开始修改")
        rows = task['manifest']
        abort = Event()
        # Parallelize only independent copies; dependent renames and mutations
        # retain ordered semantics and their existing recovery protocol.
        independent = all(r['action'] == 'copy' for r in rows) and not ({r['source'] for r in rows} & {r['target'] for r in rows})
        if independent and self.file_workers > 1:
            actor, iterator = principal.get(), iter(enumerate(rows))
            pending, failure = set(), None
            def submit():
                item = next(iterator, None)
                if item is not None:
                    pending.add(self.file_pool.submit(self._file_as, actor, identity, item[0], item[1], backup_root, abort))
            for _ in range(self.file_workers):
                submit()
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    try:
                        future.result()
                    except Exception as exc:
                        if failure is None or isinstance(failure, CopyCancelled):
                            failure = exc
                        abort.set()
                if not abort.is_set():
                    for _ in done:
                        submit()
            if failure:
                raise failure
        else:
            for index, row in enumerate(rows):
                self._execute_file(identity, index, row, backup_root, abort)
        with self.lock:
            task = self._raw(identity, 'state')
            if task['grant']['revoked'] or self._cancel_event(identity).is_set():
                raise CopyCancelled()
            task['status'] = 'succeeded'
            self._save(task)

    def _file_as(self, actor, identity, index, row, backup_root, abort):
        token = principal.set(actor)
        event = self._cancel_event(identity)
        class FileStop(Event):
            def is_set(self):
                return event.is_set() or abort.is_set()
        context_token = current_operation.set(OperationContext(FileStop(), lambda _: None))
        try:
            self._execute_file(identity, index, row, backup_root, abort)
        finally:
            current_operation.reset(context_token)
            principal.reset(token)
            with self.lock:
                self._inflight_bytes.get(identity, {}).pop(index, None)

    def _execute_file(self, identity, index, row, backup_root, abort):
        def checkpoint(copied):
            if abort.is_set() or self._cancel_event(identity).is_set():
                raise CopyCancelled()
            with self.lock:
                self._inflight_bytes.setdefault(identity, {})[index] = copied
        with self.lock:
            task = self._raw(identity, 'state')
            if task["grant"]["revoked"] or task["grant"]["expires_at"] < time.time():
                raise CopyCancelled()
            source, target = self._roots(task["scope"])
            self._check_roots(task, source, target)
            src, dst = self._path(source, row["path"]), self._path(target, row["destination"] or row["path"])
            self._guard(src, dst, task_id=identity)
            if stamp(src) != row["before"] or stamp(dst) != row["target_before"]:
                raise ValueError(f"执行前文件发生变化：{row['path']}")
            entry = {"index": index, "source": str(src), "target": str(dst), "action": row["action"],
                     "state": "prepared", "before": row["before"], "created_dirs": []}
            checkpoint(0)
            entry["created_dirs"] = self._parents(dst, target)
            if row["action"] in {"copy", "write_text"}:
                entry["staging"] = str(dst.with_name(".neuroweave-data-" + uuid4().hex))
            task["journal"].append(entry)
            self._save(task)  # Intent is durable before any live mutation.
        stage = Path(entry["staging"]) if entry.get("staging") else None
        if row["action"] in {"write_text", "delete"} and dst.exists():
            backup = backup_root / str(index)
            with io.observe_copy(lambda _: checkpoint(0)):
                receipt = io.copy(dst, backup, self.workspace.paths)
            if stamp(dst) != row["target_before"]:
                raise ValueError("备份期间目标文件变化，未修改此文件")
            if receipt and receipt['source_stamp'] != row['target_before']:
                raise ValueError('备份读取的目标身份变化，未修改此文件')
            entry.update(backup=str(backup), backup_hash=receipt['sha256'] if receipt else digest(backup),
                         backup_target=True, original_mode=stat.S_IMODE(dst.stat().st_mode))
            with self.lock:
                self._save_item(self._raw(identity, 'state'), entry)
        receipt = None
        if row["action"] == "copy":
            with io.observe_copy(checkpoint):
                receipt = io.copy(src, stage, self.workspace.paths)
            if stamp(src) != row["before"] or (receipt and receipt['source_stamp'] != row['before']):
                raise ValueError("复制期间源文件变化，保留临时文件，未提交")
        elif row["action"] == "write_text":
            if row.get("artifact"):
                artifact = Path(row["artifact"])
                self._artifact_path(artifact)
                if digest(artifact) != row["content_hash"]:
                    raise ValueError("隔离结果变化")
                with io.observe_copy(checkpoint):
                    receipt = io.copy(artifact, stage, self.workspace.paths)
            else:
                with io.directory_fd(stage.parent) as parent:
                    fd = os.open(stage if parent is None else stage.name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(row["content"].encode())
                        stream.flush()
                        os.fsync(stream.fileno())
            if (receipt["sha256"] if receipt else digest(stage)) != row["content_hash"]:
                raise ValueError("生成内容校验失败")
        with self.lock, self.run_lock:
            task = self._raw(identity, 'state')
            task["journal"] = [entry]
            if task["grant"]["revoked"] or task["grant"]["expires_at"] < time.time():
                raise CopyCancelled()
            checkpoint(0)
            self._check_roots(task, *self._roots(task["scope"]))
            self._guard(src, dst, task_id=identity)
            self._path(source, row["path"])
            self._path(target, row["destination"] or row["path"])
            if stamp(src) != row["before"] or stamp(dst) != row["target_before"]:
                raise ValueError("提交前文件变化，未替换")
            if stage:
                if receipt and stamp(stage) != receipt['stamp']:
                    raise ValueError('暂存内容在复制后发生变化，未提交')
                task["journal"][-1].update(staged_stamp=stamp(stage), staged_hash=receipt["sha256"] if receipt else digest(stage))
                self._save(task)
            if row["action"] == "rename":
                io.rename(src, dst)
            elif row["action"] == "delete":
                io.remove(src)
            elif row["action"] == "write_text" and row["target_before"] is not None:
                # The original is backed up; no overwrite happens before the
                # generated version is ready. Parent descriptors reject links.
                with io.directory_fd(dst.parent) as parent:
                    os.replace(stage if parent is None else stage.name, dst if parent is None else dst.name,
                               src_dir_fd=parent, dst_dir_fd=parent)
                if entry.get("original_mode") is not None:
                    os.chmod(dst, entry["original_mode"])
            else:
                io.rename(stage, dst)
            published = stamp(dst)
            staged = entry.get('staged_stamp')
            if staged is not None and published != staged:
                raise ValueError('发布后文件身份变化，请检查恢复记录')
            post_hash = entry['staged_hash'] if staged is not None else digest(dst) if dst.exists() else None
            saved = task["journal"][-1]
            saved.update(state="applied", after=stamp(dst), after_hash=post_hash)
            self._inflight_bytes.get(identity, {}).pop(index, None)
            task["completed"] += 1
            task["completed_bytes"] = task.get("completed_bytes", 0) + row["size"]
            self._save(task)

    def _undo(self, identity):
        task = self._raw(identity)
        source, target = self._roots(task["scope"])
        self._check_roots(task, source, target)
        # Preflight the whole journal before undoing any item. Prepared records
        # are reconciled using file identities/content after an interrupted commit.
        applied = []
        for entry in task["journal"]:
            request_checkpoint()
            row = task["manifest"][entry["index"]]
            src = self._path(source, row["path"])
            dst = self._path(target, row["destination"] or row["path"])
            restore = dst if entry.get("backup_target") else src
            self._guard(src, dst, task_id=identity)
            if entry["state"] == "undone":
                continue
            if entry.get("undo_prepared"):
                restored = stamp(restore)
                if row["action"] == "rename" and restored == entry["undo_stamp"] and not dst.exists():
                    entry["state"] = "undone"
                    continue
                if entry.get("backup") and restored == entry["undo_stamp"] and digest(restore) == entry["backup_hash"]:
                    entry["state"] = "undone"
                    continue
                if not entry.get("backup") and row["action"] != "rename" and not dst.exists():
                    entry["state"] = "undone"
                    continue
            if entry["state"] == "prepared":
                if row["action"] == "rename" and stamp(dst) == row["before"]:
                    if src.exists() and not (stamp(src) == row["before"] and os.path.samefile(src, dst)):
                        raise ValueError("原路径已被其他文件占用")
                    entry.update(state="applied", after=stamp(dst), after_hash=digest(dst))
                elif row["action"] == "delete" and not src.exists():
                    entry.update(state="applied", after=None, after_hash=None)
                elif row["action"] in {"copy", "write_text"} and dst.exists() and stamp(dst) != row["target_before"]:
                    expected = entry.get("staged_hash")
                    if stamp(dst) != entry.get("staged_stamp") or expected is None or digest(dst) != expected:
                        raise ValueError("中断后的文件状态不明确，请人工检查")
                    entry.update(state="applied", after=stamp(dst), after_hash=expected)
                else:
                    if stamp(src) != row["before"] or stamp(dst) != row["target_before"]:
                        raise ValueError("未提交的文件也发生了变化，请人工检查后恢复")
                    continue
            if stamp(dst) != entry["after"] or (dst.exists() and digest(dst) != entry["after_hash"]):
                raise ValueError("操作后文件再次变化，不能覆盖后续改动")
            if row["action"] == "rename" and src.exists() and not (stamp(src) == entry["after"] and os.path.samefile(src, dst)):
                raise ValueError("原路径已被其他文件占用")
            if entry.get("backup") and digest(Path(entry["backup"])) != entry["backup_hash"]:
                raise ValueError("备份校验失败")
            applied.append(entry)
        for entry in reversed(applied):
            request_checkpoint()
            row = task["manifest"][entry["index"]]
            with self.lock, self.run_lock:
                src, dst = self._path(source, row["path"]), self._path(target, row["destination"] or row["path"])
                self._guard(src, dst, task_id=identity)
                if stamp(dst) != entry["after"] or (dst.exists() and digest(dst) != entry["after_hash"]):
                    raise ValueError("恢复期间文件变化，停止恢复")
                if row["action"] == "rename":
                    entry.update(undo_prepared=True, undo_stamp=stamp(dst))
                    self._save_item(task, entry)
                    if src.exists():
                        if stamp(src) != entry["after"] or not os.path.samefile(src, dst):
                            raise ValueError("原路径已被其他文件占用")
                        io.remove(dst)
                    else:
                        io.rename(dst, src)
                elif entry.get("backup"):
                    restore = dst if entry.get("backup_target") else src
                    if entry.get("undo_staging"):
                        previous = Path(entry["undo_staging"])
                        if previous.parent != restore.parent or not previous.name.startswith(".neuroweave-restore-"):
                            raise PermissionError("恢复临时文件路径不匹配")
                        WorkspacePaths(target, allowed_roots=(target,)).resolve(target, str(previous), write=True, internal=True)
                        if previous.exists():
                            if not previous.is_file():
                                raise PermissionError("恢复临时文件已被替换")
                            io.remove(previous)
                    stage = restore.with_name(".neuroweave-restore-" + uuid4().hex)
                    entry["undo_staging"] = str(stage)
                    self._save_item(task, entry)
                    io.copy(Path(entry["backup"]), stage, self.workspace.paths)
                    request_checkpoint()
                    entry.update(undo_prepared=True, undo_stamp=stamp(stage), undo_staging=str(stage))
                    self._save_item(task, entry)
                    if restore.exists():
                        with io.directory_fd(restore.parent) as parent:
                            os.replace(stage if parent is None else stage.name, restore if parent is None else restore.name,
                                       src_dir_fd=parent, dst_dir_fd=parent)
                    else:
                        io.rename(stage, restore)
                    os.chmod(restore, entry["original_mode"])
                elif dst.exists():
                    entry.update(undo_prepared=True, undo_stamp=None)
                    self._save_item(task, entry)
                    io.remove(dst)
                entry["state"] = "undone"
                self._save_item(task, entry)
        created_dirs = {p for entry in task["journal"] for p in entry.get("created_dirs", [])}
        # Only remove private staging files recorded by this task. Check their
        # parent and basename again; never recursively delete a computed path.
        for entry in task["journal"]:
            request_checkpoint()
            for key in ("staging", "undo_staging"):
                if entry.get(key):
                    temporary = Path(entry[key])
                    if (within(temporary, target) and temporary.name.startswith(
                            (".neuroweave-data-", ".neuroweave-restore-"))):
                        WorkspacePaths(target, allowed_roots=(target,)).resolve(target, str(temporary), write=True, internal=True)
                        if temporary.exists():
                            if not temporary.is_file():
                                raise PermissionError("临时文件已被替换，停止清理")
                            io.remove(temporary)
        for value in sorted(created_dirs, key=lambda p: len(Path(p).parts), reverse=True):
            directory = Path(value)
            if within(directory, target) and directory != target:
                try:
                    WorkspacePaths(target, allowed_roots=(target,)).resolve(target, value, write=True)
                    with io.directory_fd(directory.parent) as parent:
                        os.rmdir(directory if parent is None else directory.name, dir_fd=parent)
                except OSError:
                    pass  # Retain directories containing later user files.
        task.update(status="rolled_back", error=None)
        self._save(task)

    def _artifact_path(self, path):
        root = self.directory.resolve()
        if not within(path.resolve(), root):
            raise PermissionError("隔离结果超出内部目录")
        WorkspacePaths(root, allowed_roots=(root,)).resolve(root, str(path), write=True)
        if not path.is_file():
            raise ValueError("隔离结果必须是普通文件")
