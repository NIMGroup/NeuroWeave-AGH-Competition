"""Incremental run storage; old inline JSON records remain readable during migration."""
from __future__ import annotations

import json
from typing import Any

from .run_models import WorkflowRun


class RunStoreMixin:
    def _authorize_run(self, connection, row):
        from .data_auth import principal, check_run_plan
        if not principal.get().authenticated or principal.get().administrator:
            return
        if row["storage_version"]:
            saved = connection.execute("SELECT payload_json FROM run_plans WHERE run_id=?", (row["run_id"],)).fetchone()
            plan = json.loads(saved[0]) if saved else {}
        else:
            plan = json.loads(row["payload_json"]).get("plan_snapshot", {})
        check_run_plan(self, plan)

    def save_run(self, run: WorkflowRun, *, event_type=None, event_message="", level="info",
                 step_id=None, event_payload=None) -> None:
        with self._lock, self._connect() as connection:
            if run.status in {'queued','running'}:
                from .task_management import assert_plan_open
                assert_plan_open(self,'workflow',run.workflow_id)
            previous = connection.execute(
                "SELECT storage_version,state_revision FROM runs WHERE run_id=?", (run.run_id,)
            ).fetchone()
            fresh = previous is None or previous["storage_version"] == 0
            revision = (previous["state_revision"] if previous else 0) + 1
            header = run.model_dump(mode="json", exclude={"plan_snapshot", "steps", "artifacts"})
            artifact_ids = {a.artifact_id for step in run.steps for a in step.artifacts}
            artifact_positions = {a.artifact_id: position for position, a in enumerate(run.artifacts)}
            header["artifacts"] = [a.model_dump(mode="json") for a in run.artifacts if a.artifact_id not in artifact_ids]
            connection.execute(
                """INSERT INTO runs(run_id,workflow_id,workflow_revision,status,payload_json,created_at,updated_at,storage_version,state_revision)
                   VALUES(?,?,?,?,?,?,?,1,?) ON CONFLICT(run_id) DO UPDATE SET
                   status=excluded.status,payload_json=excluded.payload_json,updated_at=excluded.updated_at,
                   storage_version=1,state_revision=excluded.state_revision""",
                (run.run_id, run.workflow_id, run.workflow_revision, run.status,
                 json.dumps(header, ensure_ascii=False), run.created_at, run.updated_at, revision))
            if fresh:
                connection.execute("INSERT OR IGNORE INTO run_plans(run_id,payload_json) VALUES(?,?)",
                                   (run.run_id, json.dumps(run.plan_snapshot, ensure_ascii=False)))
            # Log/progress callbacks name a step; lifecycle transitions flush all dirty steps.
            for position, step in enumerate(run.steps):
                if not fresh and step_id and step.step_id != step_id:
                    continue
                connection.execute(
                    """INSERT INTO run_steps(run_id,step_id,position,payload_json,state_revision) VALUES(?,?,?,?,?)
                       ON CONFLICT(run_id,step_id) DO UPDATE SET payload_json=excluded.payload_json,
                       state_revision=excluded.state_revision WHERE run_steps.payload_json != excluded.payload_json""",
                    (run.run_id, step.step_id, position, step.model_dump_json(), revision))
                if not fresh and step_id:
                    connection.executemany("INSERT OR IGNORE INTO run_artifact_order VALUES(?,?,?)",
                        [(run.run_id, artifact.artifact_id, artifact_positions[artifact.artifact_id])
                         for artifact in step.artifacts if artifact.artifact_id in artifact_positions])
            if fresh or not step_id:
                # Parallel samples finish out of step order. Preserve the audit
                # artifact order separately without retransmitting it on every poll.
                connection.executemany("INSERT OR IGNORE INTO run_artifact_order VALUES(?,?,?)",
                    [(run.run_id, identifier, position) for identifier, position in artifact_positions.items()])
            if event_type:
                connection.execute(
                    """INSERT INTO run_events(run_id,event_type,level,message,progress,step_id,payload_json,created_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (run.run_id,event_type,level,event_message,run.progress,step_id,
                     json.dumps(event_payload or {}, ensure_ascii=False),run.updated_at))

    def _hydrate_run(self, connection, row, *, include_plan=True):
        payload = json.loads(row["payload_json"])
        payload["state_revision"] = row["state_revision"]
        if row["storage_version"]:
            payload["steps"] = [json.loads(item["payload_json"]) for item in connection.execute(
                "SELECT payload_json FROM run_steps WHERE run_id=? ORDER BY position", (row["run_id"],))]
            payload["artifacts"] = payload.get("artifacts", []) + [
                artifact for step in payload["steps"] for artifact in step.get("artifacts", [])]
            order = dict(connection.execute("SELECT artifact_id,position FROM run_artifact_order WHERE run_id=?",
                                             (row["run_id"],)))
            payload["artifacts"].sort(key=lambda artifact: order.get(artifact["artifact_id"],len(order)))
            if include_plan:
                plan = connection.execute("SELECT payload_json FROM run_plans WHERE run_id=?", (row["run_id"],)).fetchone()
                payload["plan_snapshot"] = json.loads(plan["payload_json"]) if plan else {}
        if not include_plan:
            payload.pop("plan_snapshot", None)
        return WorkflowRun.model_validate(payload)

    def get_run(self, run_id):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row:
                self._authorize_run(connection, row)
            return self._hydrate_run(connection, row) if row else None

    def list_runs(self, *, workflow_id=None, limit=50, summary=False, offset=0):
        with self._connect() as connection:
            condition = "WHERE workflow_id=?" if workflow_id else ""
            args = ([workflow_id] if workflow_id else []) + [max(1, min(limit, 200)),max(0,offset)]
            rows = connection.execute(f"SELECT * FROM runs {condition} ORDER BY created_at DESC,run_id DESC LIMIT ? OFFSET ?", args).fetchall()
            permitted = []
            for row in rows:
                try:
                    self._authorize_run(connection, row)
                except PermissionError:
                    continue
                permitted.append(row)
            rows = permitted
            if summary:
                return [dict(json.loads(row["payload_json"]), plan_snapshot={}, steps=[], artifacts=[],
                             state_revision=row["state_revision"]) for row in rows]
            return [self._hydrate_run(connection, row) for row in rows]

    def active_run_for_workflow(self, workflow_id):
        with self._connect() as connection:
            row = connection.execute("""SELECT * FROM runs WHERE workflow_id=?
                AND status IN ('queued','running','cancel_requested') ORDER BY updated_at DESC LIMIT 1""", (workflow_id,)).fetchone()
            if row:
                self._authorize_run(connection, row)
            return self._hydrate_run(connection, row) if row else None

    def historical_runs(self, dataset_id: str, *, offset=0, limit=10):
        """Page by immutable original dataset, including legacy run snapshots."""
        limit = max(1, min(limit, 20))
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT r.* FROM runs r LEFT JOIN run_plans p ON p.run_id=r.run_id
                WHERE r.status IN ('succeeded','failed','cancelled')
                  AND CASE WHEN r.storage_version=1
                      THEN json_extract(p.payload_json,'$.dataset_id')
                      ELSE json_extract(r.payload_json,'$.plan_snapshot.dataset_id') END = ?
                ORDER BY r.created_at DESC,r.run_id DESC LIMIT ? OFFSET ?
            """, (dataset_id, limit + 1, max(0, offset))).fetchall()
            permitted = []
            for row in rows[:limit]:
                try:
                    self._authorize_run(connection, row)
                except PermissionError:
                    continue
                permitted.append(self._hydrate_run(connection, row))
            return permitted, len(rows) > limit

    def incomplete_runs(self):
        with self._connect() as connection:
            return [self._hydrate_run(connection, row) for row in connection.execute(
                "SELECT * FROM runs WHERE status IN ('queued','running','cancel_requested')").fetchall()]

    def monitor_run(self, run_id: str, after_revision: int = -1) -> dict[str, Any]:
        with self._connect() as connection:
            # A single read transaction keeps the header and changed steps at one revision.
            connection.execute("BEGIN")
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            self._authorize_run(connection, row)
            if not row["storage_version"]:
                run = self._hydrate_run(connection, row, include_plan=False)
                return {"run": run.model_dump(mode="json", exclude={"steps", "plan_snapshot"}),
                        "steps": [s.model_dump(mode="json") for s in run.steps], "reset": True}
            reset = after_revision < 0 or after_revision > row["state_revision"]
            header = json.loads(row["payload_json"])
            header["state_revision"] = row["state_revision"]
            steps = [json.loads(item["payload_json"]) for item in connection.execute(
                "SELECT payload_json FROM run_steps WHERE run_id=? AND state_revision>? ORDER BY position",
                (run_id, -1 if reset else after_revision))]
            return {"run": header, "steps": steps, "reset": reset}

    def run_event_page(self, run_id, *, after_event_id=0, before_event_id=None, tail=False, limit=200):
        limit = max(1, min(limit, 1000))
        backwards = tail or before_event_id is not None
        condition = "event_id<?" if before_event_id is not None else "event_id>?"
        cursor = before_event_id if before_event_id is not None else max(0, after_event_id)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            self._authorize_run(connection, row)
            rows = connection.execute(
                f"""SELECT * FROM run_events WHERE run_id=? AND {condition}
                    ORDER BY event_id {'DESC' if backwards else 'ASC'} LIMIT ?""",
                (run_id,cursor,limit+1)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        if backwards:
            rows.reverse()
        events = [dict(event_id=r["event_id"],event_type=r["event_type"],level=r["level"],
                       message=r["message"],progress=r["progress"],step_id=r["step_id"],
                       payload=json.loads(r["payload_json"]),created_at=r["created_at"]) for r in rows]
        return {"events":events,"next_event_id":events[-1]["event_id"] if events else after_event_id,
                "has_more":more and not backwards,"has_older":more and backwards,
                "before_event_id":events[0]["event_id"] if events else before_event_id}

    def run_events(self, run_id, *, after_event_id=0):
        return self.run_event_page(run_id, after_event_id=after_event_id, limit=1000)["events"]
