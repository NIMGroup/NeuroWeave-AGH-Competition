"""Small, authorized summaries of active executions; never starts any work."""
import json
from fastapi import APIRouter, HTTPException

ACTIVE = ("validating", "queued", "analyzing", "running", "rolling_back", "cancel_requested")


def workflow_conversation(store, identity):
    # Workflow models have no conversation field; use persisted reply bindings.
    with store._connect() as db:
        candidates = db.execute("""SELECT DISTINCT conversation_id FROM conversation_messages
            WHERE json_extract(metadata_json,'$.workflow_id')=? ORDER BY created_at DESC""", (identity,)).fetchall()
    for row in candidates:
        try:
            if store.get_conversation(row[0]):
                return row[0]
        except PermissionError:
            continue
    return None


def active_executions(services):
    store = services.local_store
    result = []
    with store._connect() as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        records = []
        for kind, table, key in (("data", "data_operations", "id"), ("workspace", "workspace_operations", "id"),
                                 ("run", "runs", "run_id"), ("check", "operations", "operation_id")):
            if table in tables:
                rows = db.execute(f"SELECT * FROM {table} WHERE status IN ({','.join('?' for _ in ACTIVE)})", ACTIVE).fetchall()
                records.extend((kind, key, dict(row)) for row in rows)
    for kind, key, row in records:
        identity = row[key]
        try:
            if kind == "data":
                service = services.data_operations
                task = service._raw(identity, "state")
                service._owner(task, task["conversation_id"])
                event = service._cancel_event(identity)
                value = dict(conversation_id=task["conversation_id"], title=task["title"], status=task["status"],
                             completed=task.get("completed"), total=task.get("file_count"),
                             cancel_requested=event.is_set())
            elif kind == "workspace":
                task = services.workspace_service.operation(identity)
                value = dict(conversation_id=task["conversation_id"], title=task["action"] + " · " + task["source"],
                             status=task["status"], cancel_requested=task["cancel_requested"])
            elif kind == "run":
                with store._connect() as db:
                    store._authorize_run(db, row)
                task = json.loads(row["payload_json"])
                workflow = store.get_workflow(row["workflow_id"])
                value = dict(conversation_id=workflow_conversation(store, row["workflow_id"]),
                             title=workflow.title if workflow else row["workflow_id"], status=row["status"],
                             progress=task.get("progress"), cancel_requested=task.get("cancellation_requested", False))
            else:
                task = services.operation_service.get(identity)
                # Dataset scans are global; workflow checks may have a conversation.
                entity = task["entity_key"].split(":")[0]
                workflow = store.get_workflow(entity) if task["kind"].startswith('workflow_') else None
                value = dict(conversation_id=workflow_conversation(store, entity) if workflow else None,
                             title=task["message"], status=task["status"],
                             cancel_requested=task["status"] == "cancel_requested",
                             entity_id=entity, operation_kind=task['kind'])
            if value["status"] in ACTIVE:
                result.append(dict(id=identity, kind=kind, can_cancel=True, **value))
        except (PermissionError, KeyError):
            continue
        except HTTPException as exc:
            if exc.status_code not in {403, 404}:
                raise
    return {"activities": result}


def router_for(services):
    router = APIRouter()

    @router.get("/api/execution-activities")
    def active():
        return active_executions(services)

    return router
