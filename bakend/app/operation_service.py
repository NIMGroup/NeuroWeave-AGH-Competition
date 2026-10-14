"""Bounded, persistent background scans and dry-runs. Never executes workflow jobs."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event, RLock
from uuid import uuid4
from contextvars import copy_context

from .operation_context import OperationCancelled, OperationContext, checkpoint, current_operation
from .data_auth import principal


class OperationService:
    def __init__(self, store):
        self.store = store
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="neuroweave-check")
        self._events = {}
        with store._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS data_operation_owners (id TEXT PRIMARY KEY, actor TEXT)")
            connection.execute("""CREATE TABLE IF NOT EXISTS operations(
                operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, entity_key TEXT NOT NULL,
                status TEXT NOT NULL, message TEXT NOT NULL, result_json TEXT, error_json TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            connection.execute("""UPDATE operations SET status='failed',message='后端重启，请重新发起检查'
                WHERE status IN ('queued','running','cancel_requested')""")

    def get(self, operation_id):
        with self.store._connect() as connection:
            row = connection.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None:
            raise KeyError(operation_id)
        actor = principal.get()
        if actor.authenticated and not actor.administrator:
            with self.store._connect() as connection:
                owner = connection.execute("SELECT actor FROM data_operation_owners WHERE id=?", (operation_id,)).fetchone()
            if not owner or owner[0] != actor.name:
                raise PermissionError("后台检查不属于当前账户")
        value = dict(row)
        value["result"] = json.loads(value.pop("result_json")) if value["result_json"] else None
        value.pop("result_json", None)
        value["error"] = json.loads(value.pop("error_json")) if value["error_json"] else None
        value.pop("error_json", None)
        return value

    def submit(self, kind, entity_key, action):
        with self._lock, self.store._lock, self.store._connect() as connection:
            if kind.startswith('workflow_'):
                from .task_management import assert_plan_open
                assert_plan_open(self.store,'workflow',entity_key.rsplit(':',1)[0])
            active = connection.execute("SELECT * FROM operations WHERE status IN ('queued','running','cancel_requested')").fetchall()
            own_ids = {r[0] for r in connection.execute(
                "SELECT id FROM data_operation_owners WHERE actor=?", (principal.get().name,))}
            same = next((row for row in active if row["kind"] == kind and row["entity_key"] == entity_key
                         and row["operation_id"] in own_ids), None)
            if same:
                return dict(operation_id=same["operation_id"],status=same["status"],message=same["message"])
            if len(active) >= 16:
                raise ValueError("后台检查队列已满，请稍后重试")
            identifier = "op_" + uuid4().hex[:16]
            now = datetime.now(timezone.utc).isoformat()
            connection.execute("INSERT INTO operations VALUES(?,?,?,?,?,NULL,NULL,?,?)",
                               (identifier,kind,entity_key,"queued","等待后台检查",now,now))
            connection.execute("INSERT INTO data_operation_owners VALUES(?,?)", (identifier, principal.get().name))
            # Register the cancellation token before exposing the committed job.
            event = Event()
            self._events[identifier] = event
        self._executor.submit(copy_context().run, self._run, identifier, event, action)
        return dict(operation_id=identifier,status="queued",message="等待后台检查")

    def latest_workflow_check(self, workflow_id):
        """Compact, authorized status; results remain behind the single-job read."""
        kinds = ('workflow_dry_run', 'workflow_replan', 'workflow_complete_dependencies')
        actor = principal.get()
        with self.store._connect() as db:
            row = db.execute('''SELECT o.* FROM operations o
                LEFT JOIN data_operation_owners a ON a.id=o.operation_id
                WHERE o.kind IN (?,?,?) AND substr(o.entity_key,1,instr(o.entity_key,':')-1)=?
                  AND (? OR a.actor=?)
                ORDER BY CASE WHEN o.status IN ('queued','running','cancel_requested') THEN 0 ELSE 1 END,
                         o.created_at DESC LIMIT 1''',
                (*kinds, workflow_id, not actor.authenticated or actor.administrator, actor.name)).fetchone()
        if row is None:
            return None
        result = {k: row[k] for k in ('operation_id','kind','status','message','created_at','updated_at')}
        result['workflow_id'] = workflow_id
        result['expected_revision'] = int(row['entity_key'].rsplit(':',1)[1])
        error = json.loads(row['error_json']) if row['error_json'] else None
        if error:
            result['error'] = {'message': error.get('message','')}
        return result

    def _run(self, identifier, event, action):
        last_report = 0.0
        def report(message):
            nonlocal last_report
            now = time.monotonic()
            if now - last_report >= 0.5:
                with self.store._connect() as connection:
                    connection.execute("UPDATE operations SET message=?,updated_at=? WHERE operation_id=?",
                                       (message,datetime.now(timezone.utc).isoformat(),identifier))
                last_report = now
        token = current_operation.set(OperationContext(event, report))
        try:
            checkpoint()
            with self.store._connect() as connection:
                connection.execute("UPDATE operations SET status='running' WHERE operation_id=? AND status='queued'", (identifier,))
            value = action()
            result = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
            # Mutating services check cancellation immediately before their commit.
            # After that commit, report success even if a late cancel arrives.
            self._finish(identifier,"succeeded","检查完成",result=result)
        except OperationCancelled as exc:
            self._finish(identifier,"cancelled",str(exc))
        except Exception as exc:
            error = {"message":str(exc)}
            if hasattr(exc, "current"):
                error["current"] = exc.current.model_dump(mode="json")
            self._finish(identifier,"failed",str(exc),error=error)
        finally:
            current_operation.reset(token)
            self._events.pop(identifier,None)

    def _finish(self, identifier, status, message, *, result=None, error=None):
        with self.store._connect() as connection:
            connection.execute("""UPDATE operations SET status=?,message=?,result_json=?,error_json=?,updated_at=?
                WHERE operation_id=?""", (status,message,json.dumps(result,ensure_ascii=False),
                json.dumps(error,ensure_ascii=False),datetime.now(timezone.utc).isoformat(),identifier))

    def cancel(self, identifier):
        with self._lock:
            record = self.get(identifier)
            if record["status"] in {"queued","running","cancel_requested"}:
                event = self._events.get(identifier)
                if event:
                    event.set()
                with self.store._connect() as connection:
                    connection.execute("""UPDATE operations SET status='cancel_requested',
                        message='正在取消，等待当前检查退出' WHERE operation_id=? AND status IN ('queued','running')""",(identifier,))
        return self.get(identifier)

    def shutdown(self, *, wait=False):
        for event in list(self._events.values()):
            event.set()
        self._executor.shutdown(wait=wait,cancel_futures=True)
