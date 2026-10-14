"""One durable task identity across conversations, plans and execution records."""

import json
from datetime import datetime, timezone

from .agent_identity import public_understanding, task_closure, closed_task_phase
from .data_auth import check_conversation, principal

ACTIVE = {
    "queued",
    "running",
    "validating",
    "analyzing",
    "cancel_requested",
    "rolling_back",
}


def iso(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    return value or datetime.now(timezone.utc).isoformat()


def ensure_schema(store):
    if getattr(store, "_managed_tasks_ready", False):
        return
    with store.transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS managed_tasks(
            task_id TEXT,actor TEXT,conversation_id TEXT,source TEXT,payload TEXT,updated_at TEXT,
            PRIMARY KEY(task_id,actor))""")
        db.execute("""CREATE TABLE IF NOT EXISTS managed_task_plans(
            kind TEXT,plan_id TEXT,actor TEXT,task_id TEXT,PRIMARY KEY(kind,plan_id,actor))""")
        db.execute("""CREATE TABLE IF NOT EXISTS managed_task_versions(
            task_id TEXT,actor TEXT,version INTEGER,kind TEXT,plan_id TEXT,revision TEXT,payload TEXT,created_at TEXT,
            PRIMARY KEY(task_id,actor,version),UNIQUE(task_id,actor,kind,plan_id,revision))""")
        db.execute(
            "CREATE INDEX IF NOT EXISTS managed_tasks_recency ON managed_tasks(actor,updated_at)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS managed_task_plan_owner ON managed_task_plans(task_id,actor)"
        )
    store._managed_tasks_ready = True


def plan_kind(fact):
    kind = fact.get("kind")
    if kind in {"workflow", "data", "workspace"}:
        return kind
    return (
        "workspace"
        if fact.get("artifact_type") in {"file_plan", "file_changes"}
        else None
    )


def bind_plan(db, task_id, actor, kind, identity, revision=None, summary=None):
    if not kind or not identity:
        return
    prior = db.execute(
        "SELECT task_id FROM managed_task_plans WHERE kind=? AND plan_id=? AND actor=?",
        (kind, identity, actor),
    ).fetchone()
    if prior and prior[0] != task_id:
        # Only replace a provisional standalone workflow entry. An existing
        # conversation task cannot silently acquire another task's plan.
        old = db.execute(
            "SELECT conversation_id FROM managed_tasks WHERE task_id=? AND actor=?",
            (prior[0], actor),
        ).fetchone()
        if old and old[0]:
            raise ValueError("该方案已属于另一个任务，请继续原任务或创建独立方案")
        db.execute(
            "DELETE FROM managed_tasks WHERE task_id=? AND actor=?", (prior[0], actor)
        )
        for row in db.execute(
            "SELECT kind,plan_id,revision,payload,created_at FROM managed_task_versions WHERE task_id=? AND actor=?",
            (prior[0], actor),
        ).fetchall():
            number = db.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM managed_task_versions WHERE task_id=? AND actor=?",
                (task_id, actor),
            ).fetchone()[0]
            db.execute(
                "INSERT OR IGNORE INTO managed_task_versions VALUES(?,?,?,?,?,?,?,?)",
                (task_id, actor, number, *row),
            )
        db.execute(
            "DELETE FROM managed_task_versions WHERE task_id=? AND actor=?",
            (prior[0], actor),
        )
    db.execute(
        "INSERT OR REPLACE INTO managed_task_plans VALUES(?,?,?,?)",
        (kind, identity, actor, task_id),
    )
    revision = str(revision or "1")
    if db.execute(
        "SELECT 1 FROM managed_task_versions WHERE task_id=? AND actor=? AND kind=? AND plan_id=? AND revision=?",
        (task_id, actor, kind, identity, revision),
    ).fetchone():
        return
    number = db.execute(
        "SELECT COALESCE(MAX(version),0)+1 FROM managed_task_versions WHERE task_id=? AND actor=?",
        (task_id, actor),
    ).fetchone()[0]
    details = {
        k: v
        for k, v in (summary or {}).items()
        if k in {"title", "status", "task_revision", "method_ids", "goal", "phase"}
    }
    db.execute(
        "INSERT INTO managed_task_versions VALUES(?,?,?,?,?,?,?,?)",
        (
            task_id,
            actor,
            number,
            kind,
            identity,
            revision,
            json.dumps(details, ensure_ascii=False),
            iso(None),
        ),
    )


def sync_state(db, state, conversation_id, actor):
    identity = state.get("task_id")
    if not identity or (
        state.get("deliverable", "answer") == "answer" and not state.get("active_plan")
    ):
        return
    previous = db.execute(
        "SELECT payload,source FROM managed_tasks WHERE task_id=? AND actor=?",
        (identity, actor),
    ).fetchone()
    prior = json.loads(previous[0]) if previous else {}
    value = {
        **prior,
        **public_understanding(state),
        "scope": state.get("scope", prior.get("scope", {})),
        "language": state.get("last_request", {}).get(
            "language", prior.get("language", "zh-CN")
        ),
    }
    value["task_revision"] = state.get("task_revision", 1)
    if task_closure(state):
        value['closed_plan_snapshot'] = state.get('closed_plan_snapshot', prior.get('closed_plan_snapshot'))
    active = value.get("active_plan")
    if not active and state.get("awaiting_results"):
        pid = state["awaiting_results"][0]
        fact = state.get("artifacts", {}).get(pid, {})
        active = {
            "id": pid,
            "kind": plan_kind(fact),
            "revision": fact.get("revision"),
            "task_revision": value["task_revision"],
        }
        value["active_plan"] = active
    if active:
        kind = active.get("kind") or plan_kind(
            state.get("artifacts", {}).get(active["id"], {})
        )
        active["kind"] = kind or "workspace"
    db.execute(
        "INSERT OR REPLACE INTO managed_tasks VALUES(?,?,?,?,?,?)",
        (
            identity,
            actor,
            conversation_id,
            previous[1] if previous else state.get("task_source", "conversation"),
            json.dumps(value, ensure_ascii=False),
            iso(state.get("updated_at")),
        ),
    )
    for pid, fact in state.get("artifacts", {}).items():
        kind = plan_kind(fact)
        if kind:
            bind_plan(
                db,
                identity,
                actor,
                kind,
                pid,
                fact.get("revision", fact.get("version")),
                fact,
            )
    if active:
        bind_plan(
            db,
            identity,
            actor,
            active["kind"],
            active["id"],
            active.get("revision"),
            value,
        )


def sync_workflow(db, workflow):
    actor = principal.get().name
    existing = db.execute(
        "SELECT task_id,actor FROM managed_task_plans WHERE kind=? AND plan_id=? AND actor=?",
        ("workflow", workflow.workflow_id, actor),
    ).fetchone()
    identity = workflow.managed_task_id or (
        existing[0] if existing else workflow.workflow_id
    )
    workflow.managed_task_id = identity
    row = db.execute(
        "SELECT payload FROM managed_tasks WHERE task_id=? AND actor=?",
        (identity, actor),
    ).fetchone()
    if row:
        from .task_management import assert_task_open
        assert_task_open(json.loads(row[0]))
    if not row:
        value = {
            "task_id": identity,
            "task_revision": 1,
            "goal": workflow.original_request or workflow.title,
            "deliverable": "workflow",
            "phase": workflow.status,
            "scope": {
                "dataset_id": workflow.dataset_id,
                "workspace_id": workflow.workspace_id,
            },
            "goal_source": "pipeline"
            if workflow.planner_source == "pipeline-library"
            else "workflow",
            "active_plan": {
                "kind": "workflow",
                "id": workflow.workflow_id,
                "revision": workflow.revision,
                "task_revision": 1,
            },
        }
        db.execute(
            "INSERT INTO managed_tasks VALUES(?,?,?,?,?,?)",
            (
                identity,
                actor,
                None,
                "pipeline"
                if workflow.planner_source == "pipeline-library"
                else "conversation",
                json.dumps(value, ensure_ascii=False),
                workflow.updated_at,
            ),
        )
    else:
        value = json.loads(row[0])
        active = value.get("active_plan") or {}
        if (
            active.get("kind") == "workflow"
            and active.get("id") == workflow.workflow_id
            and active.get("revision") != workflow.revision
        ):
            value["scope"] = {
                "dataset_id": workflow.dataset_id,
                "workspace_id": workflow.workspace_id,
            }
            active["revision"] = workflow.revision
            if active.get("task_revision", 1) == value.get("task_revision", 1):
                value["phase"] = (
                    "awaiting_inputs"
                    if any(s.missing_required_inputs for s in workflow.steps)
                    else "awaiting_run"
                )
            if value.get("goal_source") in {"workflow", "pipeline"}:
                value["goal"] = workflow.original_request or workflow.title
            db.execute(
                "UPDATE managed_tasks SET payload=?,updated_at=? WHERE task_id=? AND actor=?",
                (
                    json.dumps(value, ensure_ascii=False),
                    workflow.updated_at,
                    identity,
                    actor,
                ),
            )
            current = (
                db.execute(
                    "SELECT conversation_id,payload FROM assistant_tasks WHERE actor=? AND json_extract(payload,'$.task_id')=?",
                    (actor, identity),
                ).fetchone()
                if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='assistant_tasks'"
                ).fetchone()
                else None
            )
            if current:
                state = json.loads(current[1])
                if state.get("active_plan", {}).get("id") == workflow.workflow_id:
                    state.update(
                        scope=value["scope"], phase=value["phase"], active_plan=active
                    )
                    state["last_request"] = {
                        **state.get("last_request", {}),
                        **value["scope"],
                    }
                    db.execute(
                        "UPDATE assistant_tasks SET payload=? WHERE conversation_id=? AND actor=?",
                        (json.dumps(state, ensure_ascii=False), current[0], actor),
                    )
    bind_plan(
        db,
        identity,
        actor,
        "workflow",
        workflow.workflow_id,
        workflow.revision,
        {
            "title": workflow.title,
            "status": workflow.status,
            "method_ids": [s.selected_method_id for s in workflow.steps],
        },
    )


class ManagedTasks:
    def __init__(self, store):
        self.store = store
        ensure_schema(store)

    def bootstrap(self):
        """Adopt old task bindings once; never rewrite plans or execute them."""
        with self.store.transaction() as db:
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='assistant_tasks'"
            ).fetchone():
                for row in db.execute(
                    "SELECT conversation_id,actor,payload FROM assistant_tasks"
                ).fetchall():
                    state = json.loads(row[2])
                    if (
                        state.get("task_id")
                        and not db.execute(
                            "SELECT 1 FROM managed_tasks WHERE task_id=? AND actor=?",
                            (state["task_id"], row[1]),
                        ).fetchone()
                    ):
                        sync_state(db, state, row[0], row[1])
            # Legacy workflows lacked creator metadata. Preserve their previous
            # scope-based visibility, while newly created tasks retain ownership.
            for row in db.execute(
                "SELECT workflow_id,payload_json FROM workflows WHERE workflow_id NOT IN (SELECT plan_id FROM managed_task_plans WHERE kind='workflow')"
            ).fetchall():
                p = json.loads(row[1])
                identity = p.get("managed_task_id") or row[0]
                state = {
                    "task_id": identity,
                    "task_revision": 1,
                    "goal": p.get("original_request") or p["title"],
                    "deliverable": "workflow",
                    "phase": p["status"],
                    "goal_source": "workflow",
                    "scope": {
                        "dataset_id": p.get("dataset_id"),
                        "workspace_id": p.get("workspace_id"),
                    },
                    "active_plan": {
                        "id": row[0],
                        "kind": "workflow",
                        "revision": p["revision"],
                        "task_revision": 1,
                    },
                }
                db.execute(
                    "INSERT OR IGNORE INTO managed_tasks VALUES(?,?,?,?,?,?)",
                    (
                        identity,
                        "",
                        None,
                        "pipeline"
                        if p.get("planner_source") == "pipeline-library"
                        else "conversation",
                        json.dumps(state, ensure_ascii=False),
                        p["updated_at"],
                    ),
                )
                bind_plan(
                    db,
                    identity,
                    "",
                    "workflow",
                    row[0],
                    p["revision"],
                    {"title": p["title"], "status": p["status"]},
                )

    def _allowed(self, row):
        actor = principal.get()
        if row["actor"] not in ("", actor.name):
            return False
        state = json.loads(row["payload"])
        try:
            with self.store.connection() as db:
                exists = db.execute(
                    "SELECT 1 FROM conversations WHERE conversation_id=?",
                    (row["conversation_id"],),
                ).fetchone()
            if exists:
                check_conversation(self.store, row["conversation_id"])
            for key, scope in [("dataset_id", "dataset"), ("workspace_id", "output")]:
                identity = state.get("scope", {}).get(key)
                if identity:
                    actor.check(scope, identity)
        except PermissionError:
            return False
        return True

    def require(self, identity):
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT * FROM managed_tasks WHERE task_id=? ORDER BY actor DESC",
                (identity,),
            ).fetchall()
        row = next((r for r in rows if self._allowed(r)), None)
        if row is None:
            raise KeyError(identity)
        return dict(row), json.loads(row["payload"])

    def view(self, row, state, *, include_details=True):
        active = state.get("active_plan") or {}
        kind = active.get("kind")
        pid = active.get("id")
        latest = None
        plan = {}
        runs = 0
        with self.store.connection() as db:
            runs = db.execute(
                "SELECT COUNT(*) FROM runs WHERE workflow_id IN (SELECT plan_id FROM managed_task_plans WHERE task_id=? AND actor=? AND kind='workflow')",
                (row["task_id"], row["actor"]),
            ).fetchone()[0]
            has_data = db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='data_operations'"
            ).fetchone()
            has_workspace = db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='workspace_operations'"
            ).fetchone()
            if has_data:
                runs += db.execute(
                    """SELECT COUNT(*) FROM data_operations WHERE id IN
                       (SELECT plan_id FROM managed_task_plans WHERE task_id=? AND actor=? AND kind='data')
                       AND (json_extract(payload,'$._script_grant.confirmed_at') IS NOT NULL
                         OR COALESCE(json_extract(payload,'$._journal_count'),0)>0
                         OR status IN ('validating','queued','analyzing','running','rolling_back','cancel_requested'))""",
                    (row["task_id"], row["actor"]),
                ).fetchone()[0]
            if has_workspace:
                runs += db.execute(
                    """SELECT COUNT(*) FROM workspace_operations WHERE id IN
                       (SELECT plan_id FROM managed_task_plans WHERE task_id=? AND actor=? AND kind='workspace')
                       AND (json_extract(payload,'$.confirmed')=1 OR status IN ('running','succeeded','failed'))""",
                    (row["task_id"], row["actor"]),
                ).fetchone()[0]
            if kind == "workflow":
                found = db.execute(
                    "SELECT payload_json FROM workflows WHERE workflow_id=?", (pid,)
                ).fetchone()
                plan = json.loads(found[0]) if found else {}
                run = db.execute(
                    "SELECT status,workflow_revision,updated_at FROM runs WHERE workflow_id=? ORDER BY created_at DESC,run_id DESC LIMIT 1",
                    (pid,),
                ).fetchone()
                if run and run["workflow_revision"] == plan.get("revision"):
                    latest = dict(run)
            elif kind == "data" and has_data:
                found = db.execute(
                    "SELECT payload FROM data_operations WHERE id=?", (pid,)
                ).fetchone()
                plan = json.loads(found[0]) if found else {}
                if plan.get("actor") != principal.get().name:
                    plan = {}
            elif kind == "workspace" and has_workspace:
                found = db.execute(
                    "SELECT payload,status FROM workspace_operations WHERE id=?", (pid,)
                ).fetchone()
                plan = {**json.loads(found[0]), "status": found[1]} if found else {}
                if plan.get("actor") != principal.get().name:
                    plan = {}
        phase = state.get("phase", "preparing")
        stale = active.get("task_revision", 1) != state.get("task_revision", 1)
        physical = latest.get("status") if latest else plan.get("status")
        if state.get("task_deleted"):
            status = "deleted"
        elif task_closure(state):
            status = closed_task_phase(state)
        elif stale:
            status = "needs_replan"
        elif physical in ACTIVE | {"failed", "cancelled", "interrupted", "rolled_back"}:
            status = physical
        elif phase == "completed":
            status = "succeeded"
        elif state.get("goal_source") in {"pipeline", "workflow"}:
            status = physical or phase
        elif phase in {
            "preparing",
            "verifying",
            "needs_replan",
            "blocked",
            "needs_input",
        }:
            status = phase
        elif physical in {"succeeded", "analyzed"}:
            status = "verifying"
        else:
            status = phase
        updated = max(
            row["updated_at"],
            iso(plan.get("updated_at", row["updated_at"])),
            (latest or {}).get("updated_at", row["updated_at"]),
        )
        with self.store.connection() as db:
            versions = (
                [
                    dict(r)
                    for r in db.execute(
                        "SELECT version,kind,plan_id,revision,payload,created_at FROM managed_task_versions WHERE task_id=? AND actor=? ORDER BY version DESC LIMIT 40",
                        (row["task_id"], row["actor"]),
                    )
                ]
                if include_details
                else []
            )
            links = (
                [
                    {"kind": r[0], "id": r[1]}
                    for r in db.execute(
                        "SELECT kind,plan_id FROM managed_task_plans WHERE task_id=? AND actor=?",
                        (row["task_id"], row["actor"]),
                    )
                ]
                if include_details
                else []
            )
        for version in versions:
            version.update(json.loads(version.pop("payload")))
        entry = {
            "task_id": row["task_id"],
            "workflow_id": pid if kind == "workflow" else None,
            "plan_id": pid,
            "plan_kind": kind,
            "title": state.get("goal") or plan.get("title") or "任务",
            "status": status,
            "task_revision": state.get("task_revision", 1),
            "updated_at": updated,
            "source": row["source"],
            "conversation_id": row["conversation_id"],
            "run_count": runs,
            "latest_run_status": (latest or {}).get("status") if not stale or task_closure(state) else None,
            "dataset_label": plan.get("dataset", {}).get("display_name")
            if plan.get("dataset")
            else state.get("scope", {}).get("dataset_id"),
            "workspace_path": plan.get("workspace_root_path", plan.get("target_root")),
        }
        closed = bool(task_closure(state))
        return {
            "entry": entry,
            "task": public_understanding(state),
            "current_plan": {
                **active,
                "revision": active.get("revision") if closed else plan.get("revision", active.get("revision")),
            }
            if active
            else None,
            "versions": versions,
            "plan_links": links,
            "deleted": bool(state.get("task_deleted")),
            "plan": state.get('closed_plan_snapshot') if closed else None,
        }

    def closed_in_conversation(self, conversation_id):
        with self.store.connection() as db:
            rows = db.execute('SELECT * FROM managed_tasks WHERE conversation_id=? AND actor=? ORDER BY updated_at',
                (conversation_id,principal.get().name)).fetchall()
        return [public_understanding(json.loads(r['payload'])) for r in rows
            if self._allowed(r) and task_closure(json.loads(r['payload']))]

    def get(self, identity):
        return self.view(*self.require(identity))

    def index(self, *, offset=0, limit=30, query="", source="", status="", conversation_id=None):
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT * FROM managed_tasks WHERE actor IN (?,'') ORDER BY actor DESC,updated_at DESC",
                (principal.get().name,),
            ).fetchall()
        items = []
        seen = set()
        for row in rows:
            if conversation_id is not None and row['conversation_id'] != conversation_id:
                continue
            if not self._allowed(row):
                continue
            if row["task_id"] in seen:
                continue
            seen.add(row["task_id"])
            state = json.loads(row["payload"])
            if state.get("task_deleted"):
                continue
            if source and source != row["source"]:
                continue
            if query.casefold() not in state.get("goal", "").casefold():
                continue
            entry = self.view(dict(row), state, include_details=False)["entry"]
            classified = (
                "active"
                if entry["status"] in ACTIVE
                else (
                    "succeeded"
                    if entry["status"] in {"succeeded", "user_confirmed"}
                    else "failed"
                    if entry["status"] in {"failed", "blocked", "interrupted"}
                    else "cancelled"
                    if entry["status"] in {"cancelled","user_ended"}
                    else "draft"
                )
            )
            if status and status != classified:
                continue
            items.append(entry)
        items.sort(key=lambda x: (x["updated_at"], x["task_id"]), reverse=True)
        return {
            "items": items[offset : offset + limit],
            "has_more": len(items) > offset + limit,
        }
