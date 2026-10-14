"""Server filesystem operations with persisted, single-use user confirmations.

Proposals do not mutate disk. Confirmation is deliberately separate from model
tools. Trash metadata and audit records live in SQLite, never in a Git checkpoint.
"""
from __future__ import annotations

import getpass
import json
import os
import stat
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .workspace_models import Workspace, WorkspaceCreate, WorkspaceUpdate, FileOperationRequest, DirectoryCreationRequest
from .workspace_paths import WorkspacePaths, within
from . import workspace_io as secure_io
from .filesystem_reader import FilesystemReader, fingerprint
from .dataset_files import protect_datasets
from .operation_context import OperationContext, OperationCancelled, current_operation, checkpoint


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkspaceService(FilesystemReader):
    def __init__(self, store, default_root: Path):
        self.store = store
        self.paths = WorkspacePaths(default_root)
        if not self.paths.default.exists() and not os.getenv("WORKSPACE_DEFAULT_ROOT"):
            self.paths.resolve(self.paths.default, ".", write=True).mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.run_lock = threading.RLock()
        self._cancel_events = {}
        self.protected_paths = lambda: [Path(store.database_path)]
        with store._lock, store._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workspace_operations (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, status TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workspace_trash (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workspace_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workspace_tool_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, turn_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_workspace_tool_turn ON workspace_tool_events(turn_id, id);
                CREATE TABLE IF NOT EXISTS data_tool_event_owners (id TEXT PRIMARY KEY, actor TEXT);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(conversations)")}
            if "workspace_id" not in columns:
                db.execute("ALTER TABLE conversations ADD COLUMN workspace_id TEXT")

    def get(self, workspace_id: str, *, active: bool = True) -> Workspace:
        with self.store._connect() as db:
            row = db.execute("SELECT payload FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
        if not row:
            raise KeyError("工作区不存在")
        from .data_auth import principal
        principal.get().check('output', workspace_id)
        result = Workspace.model_validate_json(row[0])
        if active and result.status != "active":
            raise ValueError("工作区已归档或移除，请先恢复工作区记录")
        return self._with_access(result)

    def list(self) -> list[Workspace]:
        from .data_auth import principal
        with self.store._connect() as db:
            rows = db.execute("SELECT payload FROM workspaces ORDER BY id DESC").fetchall()
        return [self._with_access(Workspace.model_validate_json(row[0])) for row in rows
                if json.loads(row[0])["status"] != "deleted"
                and ("*" in principal.get().outputs or json.loads(row[0])["id"] in principal.get().outputs)]

    def validate_output_directory(self, root: Path):
        root = self.paths.browse_root(str(root))
        self.paths.resolve(root, ".", write=True)
        protect_datasets(self.store, root)
        if not root.is_dir() or not os.access(root, os.R_OK | os.W_OK | os.X_OK):
            raise PermissionError("输出目录需要后端账户的读取、写入和进入权限；请改选目录或由管理员授权")
        return root

    def _with_access(self, record):
        try:
            self.validate_output_directory(Path(record.root_path))
            return record.model_copy(update={"manageable": True, "permission_reason": "可管理；文件操作仍需确认"})
        except (OSError, ValueError) as exc:
            return record.model_copy(update={"manageable": False, "permission_reason": str(exc)})

    def _save(self, record: Workspace):
        with self.store._lock, self.store._connect() as db:
            db.execute("INSERT INTO workspaces VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                       (record.id, record.model_dump_json()))
        return record

    def create(self, request: WorkspaceCreate) -> Workspace:
        from .data_auth import principal
        if "*" not in principal.get().outputs:
            raise PermissionError("新增输出目录需要管理员授权")
        root = self.validate_output_directory(Path(request.root_path))
        return self._with_access(self._save(Workspace(id=f"ws_{uuid4().hex}", name=request.name.strip(), root_path=str(root),
                                    description=request.description, created_at=now(), updated_at=now())))

    def update(self, workspace_id: str, request: WorkspaceUpdate):
        record = self.get(workspace_id, active=False)
        changes = request.model_dump(exclude_none=True)
        return self._save(record.model_copy(update={**changes, "updated_at": now()}))

    def remove(self, workspace_id: str):
        record = self.get(workspace_id, active=False)
        self._save(record.model_copy(update={"status": "deleted", "updated_at": now()}))
        return {"removed": True, "disk_preserved": True, "root_path": record.root_path}

    def bind_conversation(self, conversation_id: str, workspace_id: str | None):
        from .data_auth import check_conversation
        check_conversation(self.store, conversation_id)
        if workspace_id:
            self.get(workspace_id)
        with self.store._lock, self.store._connect() as db:
            if db.execute("UPDATE conversations SET workspace_id=?, updated_at=? WHERE conversation_id=?",
                          (workspace_id, now(), conversation_id)).rowcount != 1:
                raise KeyError("对话不存在")

    def root(self, workspace_id: str | None = None, root_path: str | None = None):
        if workspace_id:
            stored = Path(self.get(workspace_id).root_path)
            resolved = self.paths.browse_root(str(stored))
            if stored != resolved:
                raise PermissionError("工作区根目录被重定向，请重新检查并登记")
            return resolved
        root = self.paths.browse_root(root_path)
        from .data_auth import principal
        if "*" not in principal.get().outputs and not any(within(root, Path(w.root_path)) for w in self.list()):
            raise PermissionError("未获授权浏览这个服务器目录")
        return root

    def _protect(self, *paths: Path, operation_id=None):
        self._protect_workspace_reservations(*paths, operation_id=operation_id)
        for candidate in paths:
            protect_datasets(self.store, candidate)
            for protected in self.protected_paths():
                protected = Path(protected).resolve()
                if within(candidate, protected) or within(protected, candidate):
                    raise PermissionError(f"路径正被后台任务或平台存储使用，不能修改：{candidate}")

    def _protect_workspace_reservations(self, *paths, operation_id=None):
        with self.store._connect() as db:
            records = db.execute("SELECT payload FROM workspace_operations WHERE status='running' AND id!=?",
                                 (operation_id or "",)).fetchall()
        reserved = [Path(value) for row in records for value in
                    (json.loads(row[0]).get("source"), json.loads(row[0]).get("target")) if value]
        if any(within(path, other) or within(other, path) for path in paths for other in reserved):
            raise PermissionError("路径已由其他文件操作占用")

    def guard_dataset_registration(self, directory: Path):
        # Called under the same short reservation lock as confirm(). A folder
        # cannot become a protected dataset halfway through an approved copy.
        with self.store._connect() as db:
            operations = [json.loads(row[0]) for row in db.execute("SELECT payload FROM workspace_operations WHERE status='running'")]
        for operation in operations:
            for value in (operation.get('source'), operation.get('target')):
                if value and (within(Path(value).resolve(), directory) or within(directory, Path(value).resolve())):
                    raise ValueError("目录正在执行文件操作，请完成后再登记数据集")
        with self.store._connect() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='data_operations'").fetchone():
                tasks = [json.loads(r[0]) for r in db.execute("SELECT payload FROM data_operations WHERE status IN ('validating','running','rolling_back','cancel_requested','interrupted')")]
                for task in tasks:
                    if any(within(directory, Path(value)) or within(Path(value), directory)
                           for value in (task["source_root"], task["target_root"])):
                        raise ValueError("目录正在进行数据操作，请完成后再登记数据集")
        for run in self.store.incomplete_runs():
            output = Path(run.output_directory).resolve()
            if within(output, directory) or within(directory, output):
                raise ValueError("目录正在接收运行输出，请任务完成后再登记为输入数据集")

    def _trash_root(self, workspace_id):
        root = self.root(workspace_id)
        configured = Path(self.paths.trash_name)
        target = configured / workspace_id if configured.is_absolute() else root / configured
        allowed = next((p for p in self.paths.roots if within(target.resolve(), p)), None)
        if allowed is None:
            raise PermissionError("回收站不在允许访问的根目录内")
        return self.paths.resolve(allowed, str(target), write=True, internal=True)

    def _operation_root(self, action, request):
        if request.workspace_id:
            if getattr(request, "root_path", None):
                raise ValueError("已选择输出目录时不能另外指定服务器根目录")
            return self.validate_output_directory(self.root(request.workspace_id))
        if action != "mkdir" or not isinstance(request, DirectoryCreationRequest) or not request.root_path:
            raise ValueError("请先选择输出目录；选择阶段仅允许新建文件夹")
        if request.destination or request.overwrite or request.permanent:
            raise ValueError("选择输出目录时仅允许新建一个不存在的子目录")
        if not Path(request.root_path).is_absolute():
            raise ValueError("父目录必须是服务器绝对路径")
        # The parent may contain datasets elsewhere: protect the new child,
        # without granting management rights over this entire browsing root.
        root = self.root(root_path=request.root_path)
        self.paths.resolve(root, request.root_path, write=True)
        if not root.is_dir() or not os.access(root, os.R_OK | os.W_OK | os.X_OK):
            raise PermissionError("父目录需要后端账户的读取、写入和进入权限")
        return root

    def _operation_paths(self, action, request, operation_id=None):
        root = self._operation_root(action, request)
        source = self.paths.resolve(root, request.path, write=True, sensitive=action == "read-sensitive")
        target = self.paths.resolve(root, request.destination, write=True) if request.destination else None
        if action == "rename":
            if not request.destination or Path(request.destination).name != request.destination or request.destination in {".", ".."}:
                raise ValueError("重命名只接受新名称，不能包含目录")
            target = self.paths.resolve(root, str(source.parent / request.destination), write=True)
        if action == "delete" and not request.permanent:
            target = self._trash_root(request.workspace_id) / uuid4().hex / source.name
        if source == root or target == root:
            raise PermissionError("不能修改工作区根目录本身")
        if action in {"copy", "move", "rename"} and target is None:
            raise ValueError("必须指定准确的目标路径（含文件或文件夹名称）")
        if target and (within(target, source) or within(source, target)):
            raise ValueError("源路径和目标路径不能相同或互相包含")
        if action == "mkdir":
            if not request.workspace_id and source.parent != root:
                raise ValueError("请在当前浏览目录下新建一个直接子目录")
            if source.exists():
                raise ValueError("目标已存在")
            if not source.parent.is_dir():
                raise ValueError("请先创建父目录")
        elif not source.exists():
            raise FileNotFoundError("源路径不存在")
        if action not in {"read-sensitive", "copy"}:
            self._protect(source, operation_id=operation_id)
        if target:
            self._protect(target, operation_id=operation_id)
            if action != "delete" and not target.parent.is_dir():
                raise ValueError("目标父目录不存在")
            if target.exists() and not request.overwrite:
                raise ValueError("目标已存在；覆盖必须另行明确确认")
        return source, target

    def propose(self, action: str, request: FileOperationRequest):
        from .data_auth import principal
        if action not in {"mkdir", "copy", "move", "rename", "delete", "read-sensitive"}:
            raise ValueError("不支持的文件操作")
        if request.permanent and action != "delete":
            raise ValueError("permanent 仅适用于删除")
        if request.conversation_id:
            conversation = self.store.get_conversation(request.conversation_id)
            if not conversation or (request.workspace_id and conversation.workspace_id != request.workspace_id):
                raise ValueError("操作工作区与当前对话不匹配")
        source, target = self._operation_paths(action, request)
        operation = {"id": uuid4().hex, "actor": principal.get().name, "workspace_id": request.workspace_id, "action": action,
                     "source": str(source), "target": str(target) if target else None,
                     "conversation_id": request.conversation_id, "request": request.model_dump(),
                     "source_fingerprint": fingerprint(source), "target_fingerprint": fingerprint(target) if target else None,
                     "root_fingerprint": fingerprint(self._operation_root(action, request))[:2],
                     "created_at": now(), "expires_at": time.time() + 600, "status": "pending", "confirmed": False}
        with self.store._lock, self.store._connect() as db:
            db.execute("INSERT INTO workspace_operations VALUES(?,?,?,?,?)",
                       (operation["id"], request.workspace_id or "", "pending", json.dumps(operation), now()))
        self._audit(operation, "awaiting_confirmation")
        return operation

    def operation(self, operation_id):
        with self.store._connect() as db:
            row = db.execute("SELECT payload,status FROM workspace_operations WHERE id=?", (operation_id,)).fetchone()
        if not row:
            raise KeyError("操作不存在")
        operation = {**json.loads(row[0]), "status": row[1]}
        event = self._cancel_events.get(operation_id)
        operation["can_cancel"] = operation["status"] in {"pending", "running"}
        operation["cancel_requested"] = operation["status"] == "running" and bool(event and event.is_set())
        from .data_auth import principal
        actor = principal.get()
        if actor.authenticated and not actor.administrator and operation.get("actor", "local") != actor.name:
            raise PermissionError("文件操作不属于当前账户")
        if operation.get("workspace_id"):
            actor.check("output", operation["workspace_id"])
        if operation.get("conversation_id"):
            self.store.get_conversation(operation["conversation_id"])
        return operation

    def _audit(self, operation, result, error=None):
        entry = {key: operation.get(key) for key in ("id", "workspace_id", "conversation_id", "action", "source", "target", "confirmed")}
        from .data_auth import principal
        entry.update(actor=principal.get().name, backend_account=getpass.getuser(), time=now(), result=result, error=error)
        with self.store._lock, self.store._connect() as db:
            db.execute("INSERT INTO workspace_audit(payload) VALUES(?)", (json.dumps(entry),))

    def _trash(self, workspace_id, source, target=None, conversation_id=None):
        trash_id = uuid4().hex
        target = target or self._trash_root(workspace_id) / trash_id / source.name
        secure_io.mkdir(target.parent, parents=True, exist_ok=False)
        record = {"id": trash_id, "workspace_id": workspace_id, "original_path": str(source),
                  "trash_path": str(target), "deleted_at": now(), "conversation_id": conversation_id, "status": "prepared"}
        # Persist before moving: an interrupted process leaves a recoverable record.
        with self.store._lock, self.store._connect() as db:
            db.execute("INSERT INTO workspace_trash VALUES(?,?,?)", (trash_id, workspace_id, json.dumps(record)))
        try:
            secure_io.rename(source, target)
        except OSError:
            # Refuse cross-filesystem moves rather than risking a partial deletion.
            raise ValueError("移动至回收站失败；回收站需与源文件在同一文件系统，源文件保留")
        record["status"] = "deleted"
        with self.store._lock, self.store._connect() as db:
            db.execute("UPDATE workspace_trash SET payload=? WHERE id=?", (json.dumps(record), trash_id))
        return record

    def _validate_tree(self, path: Path):
        budget = 100000
        stack = [path]
        while stack:
            checkpoint()
            entry = stack.pop()
            budget -= 1
            if budget < 0:
                raise ValueError("目录超过 100000 项，请分批操作")
            if entry.is_symlink() or self.paths.sensitive(entry) or self.paths.special(entry):
                raise PermissionError("目录含符号链接、敏感或特殊内容，拒绝操作")
            info = entry.lstat()
            if stat.S_ISDIR(info.st_mode):
                with os.scandir(entry) as items:
                    stack.extend(Path(item.path) for item in items)
            elif not stat.S_ISREG(info.st_mode):
                raise PermissionError("目录含特殊文件，拒绝操作")

    def confirm(self, operation_id, conversation_id=None):
        from .task_management import assert_plan_open
        assert_plan_open(self.store,'workspace',operation_id)
        with self.lock:
            operation = self.operation(operation_id)
            if operation["conversation_id"] != conversation_id:
                raise PermissionError("确认会话与操作会话不匹配")
            if conversation_id and operation.get("workspace_id"):
                conversation = self.store.get_conversation(conversation_id)
                if not conversation or conversation.workspace_id != operation["workspace_id"]:
                    raise PermissionError("输出目录选择已变化，请重新准备操作")
            if operation["status"] != "pending" or operation["expires_at"] < time.time():
                raise ValueError("操作已执行、过期或正在执行；请重新检查路径")
            # Reserve the paths atomically against new Runner submissions, but
            # never hold the Runner lock during a large copy or directory walk.
            with self.run_lock:
                self._protect_workspace_reservations(Path(operation["source"]))
                self._protect(Path(operation["source"])) if operation["action"] not in {"copy", "read-sensitive"} else None
                if operation.get("target"):
                    self._protect(Path(operation["target"]))
                with self.store._lock, self.store._connect() as db:
                    assert_plan_open(self.store,'workspace',operation_id)
                    if db.execute("UPDATE workspace_operations SET status='running' WHERE id=? AND status='pending'", (operation_id,)).rowcount != 1:
                        raise ValueError("此操作已被确认")
            event = self._cancel_events.setdefault(operation_id, threading.Event())
            operation["confirmed"] = True
            self._audit(operation, "started")
        token = current_operation.set(OperationContext(event, lambda _: None))
        try:
            checkpoint()
            result = self._execute(operation)
            operation.update(status="succeeded", result=result)
        except OperationCancelled:
            operation.update(status="cancelled", error=None)
            self._audit(operation, "cancelled")
        except Exception as exc:
            operation.update(status="failed", error=str(exc))
            self._audit(operation, "failed", str(exc))
        else:
            self._audit(operation, "succeeded")
        finally:
            current_operation.reset(token)
        operation.update(can_cancel=False, cancel_requested=False)
        with self.store._lock, self.store._connect() as db:
            persisted = operation
            if operation["action"] == "read-sensitive" and operation.get("result"):
                # A sensitive preview is returned once to the confirming UI,
                # never retained in history, audit, or operation JSON.
                persisted = {**operation, "result": {k: v for k, v in operation["result"].items() if k != "text"}}
            db.execute("UPDATE workspace_operations SET status=?,payload=? WHERE id=?",
                       (operation["status"], json.dumps(persisted), operation_id))
        return operation

    def cancel(self, operation_id, conversation_id=None):
        operation = self.operation(operation_id)
        if operation["conversation_id"] != conversation_id:
            raise PermissionError("停止会话与操作会话不匹配")
        event = self._cancel_events.setdefault(operation_id, threading.Event())
        if operation["status"] == "running":
            event.set()  # Never wait for a copy, hash or tree walk to release a lock.
        elif operation["status"] == "pending":
            with self.store._lock, self.store._connect() as db:
                changed = db.execute("UPDATE workspace_operations SET status='cancelled' WHERE id=? AND status='pending'",
                                     (operation_id,)).rowcount
            event.set()
            if changed:
                self._audit(operation, "cancelled")
        return self.operation(operation_id)

    def _execute(self, operation):
        action = operation["action"]
        request_type = DirectoryCreationRequest if action == "mkdir" else FileOperationRequest
        request = request_type.model_validate(operation["request"])
        root = self._operation_root(action, request)
        if fingerprint(root)[:2] != operation["root_fingerprint"]:
            raise ValueError("工作区目录已被替换，请重新确认")
        if action == "restore":
            return self._restore(operation)
        source, target = self._operation_paths(action, request, operation_id=operation["id"])
        # Delete's generated destination is frozen at proposal time.
        if action == "delete" and not request.permanent:
            target = Path(operation["target"])
            self.paths.resolve(self._trash_root(request.workspace_id), str(target), write=True, internal=True)
        if fingerprint(source) != operation["source_fingerprint"] or (fingerprint(target) if target else None) != operation["target_fingerprint"]:
            raise ValueError("源或目标自预览后已变化，请重新确认")
        if action == "read-sensitive":
            return self.read_text(str(source), request.workspace_id, sensitive=True)
        if action == "mkdir":
            secure_io.mkdir(source)
        else:
            self._validate_tree(source)
            backup = None
            staging = None
            if action == "copy":
                staging = target.with_name(".neuroweave-copy-" + uuid4().hex)
                operation["staging_path"] = str(staging)
                with self.store._lock, self.store._connect() as db:
                    db.execute("UPDATE workspace_operations SET payload=? WHERE id=?", (json.dumps(operation), operation["id"]))
                secure_io.copy(source, staging, self.paths)
                self._validate_tree(staging)
                # A failed or interrupted copy keeps its staging path in the
                # audit operation. The visible target remains untouched until
                # the full copy is ready for a no-replace atomic rename.
                if fingerprint(target) != operation["target_fingerprint"]:
                    raise ValueError("复制过程中目标发生变化，已保留临时副本，未替换目标")
            checkpoint()  # Stop before the backup/publish transaction, never in its middle.
            if target and target.exists():
                if not request.overwrite or operation["target_fingerprint"] is None or fingerprint(target) != operation["target_fingerprint"]:
                    raise ValueError("目标发生变化，未授权覆盖此文件")
                self._validate_tree(target)
                backup = self._trash(request.workspace_id, target, conversation_id=request.conversation_id)
            if action == "copy":
                secure_io.rename(staging, target)
            elif action in {"move", "rename"}:
                secure_io.rename(source, target)
            elif action == "delete":
                if request.permanent:
                    secure_io.remove(source)
                else:
                    return self._trash(request.workspace_id, source, target, request.conversation_id)
            return {"source": str(source), "target": str(target) if target else None, "overwritten_backup": backup}
        return {"path": str(source)}

    def trash(self, workspace_id):
        self.get(workspace_id, active=False)
        with self.store._connect() as db:
            rows = db.execute("SELECT payload FROM workspace_trash WHERE workspace_id=? ORDER BY rowid DESC LIMIT 500", (workspace_id,)).fetchall()
        return [json.loads(row[0]) for row in rows if json.loads(row[0])["status"] != "restored"]

    def propose_restore(self, trash_id, conversation_id=None, destination=None):
        with self.store._connect() as db:
            row = db.execute("SELECT payload FROM workspace_trash WHERE id=?", (trash_id,)).fetchone()
        if not row:
            raise KeyError("回收站记录不存在")
        record = json.loads(row[0])
        workspace_id = record["workspace_id"]
        source = self.paths.resolve(self._trash_root(workspace_id), record["trash_path"], write=True, internal=True)
        target = self.paths.resolve(self.root(workspace_id), destination or record["original_path"], write=True)
        self._protect(target)
        if not source.exists() or target.exists() or not target.parent.is_dir():
            raise ValueError("回收站内容不存在、原位置被占用或父目录不存在；请选择新的目标路径")
        request = FileOperationRequest(workspace_id=workspace_id, path=str(target), conversation_id=conversation_id)
        operation = {"id": uuid4().hex, "workspace_id": workspace_id, "action": "restore", "source": str(source),
                     "target": str(target), "trash_id": trash_id, "conversation_id": conversation_id,
                     "request": request.model_dump(), "source_fingerprint": fingerprint(source), "target_fingerprint": None,
                     "root_fingerprint": fingerprint(self.root(workspace_id))[:2], "created_at": now(),
                     "expires_at": time.time() + 600, "status": "pending", "confirmed": False}
        with self.store._lock, self.store._connect() as db:
            db.execute("INSERT INTO workspace_operations VALUES(?,?,?,?,?)", (operation["id"], workspace_id, "pending", json.dumps(operation), now()))
        self._audit(operation, "awaiting_confirmation")
        return operation

    def _restore(self, operation):
        workspace_id = operation["workspace_id"]
        source = self.paths.resolve(self._trash_root(workspace_id), operation["source"], write=True, internal=True)
        target = self.paths.resolve(self.root(workspace_id), operation["target"], write=True)
        self._protect(target, operation_id=operation["id"])
        if fingerprint(source) != operation["source_fingerprint"] or target.exists():
            raise ValueError("恢复路径已变化，请重新确认")
        checkpoint()
        secure_io.rename(source, target)
        with self.store._lock, self.store._connect() as db:
            row = db.execute("SELECT payload FROM workspace_trash WHERE id=?", (operation["trash_id"],)).fetchone()
            record = json.loads(row[0])
            record.update(status="restored", restored_at=now(), restored_path=str(target))
            db.execute("UPDATE workspace_trash SET payload=? WHERE id=?", (json.dumps(record), record["id"]))
        return {"restored_path": str(target)}

    def event(self, turn_id, payload):
        from .data_auth import principal
        payload = {**payload, "time": now()}
        with self.store._lock, self.store._connect() as db:
            db.execute("INSERT OR IGNORE INTO data_tool_event_owners VALUES(?,?)", (turn_id, principal.get().name))
            db.execute("INSERT INTO workspace_tool_events(turn_id,payload) VALUES(?,?)", (turn_id, json.dumps(payload)))
        return payload

    def events(self, turn_id):
        from .data_auth import principal
        with self.store._connect() as db:
            actor = principal.get()
            if actor.authenticated and not actor.administrator:
                owner = db.execute("SELECT actor FROM data_tool_event_owners WHERE id=?", (turn_id,)).fetchone()
                if not owner or owner[0] != actor.name:
                    # A newly accepted turn may not have produced events yet.
                    row = db.execute("SELECT conversation_id FROM chat_turns WHERE id=?", (turn_id,)).fetchone() if db.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='chat_turns'").fetchone() else None
                    if not row:
                        raise PermissionError("工具记录不属于当前账户")
                    self.store.get_conversation(row[0])
            return [json.loads(row[0]) for row in db.execute("SELECT payload FROM workspace_tool_events WHERE turn_id=? ORDER BY id LIMIT 100", (turn_id,))]
