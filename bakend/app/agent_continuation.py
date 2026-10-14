"""Resume the same goal from backend results, without inventing a user message."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from .data_auth import Principal, principal
from .models import ChatRequest
from .agent_identity import task_closure

logger = logging.getLogger(__name__)
ACTIVE = {"validating", "queued", "analyzing", "running", "cancel_requested", "rolling_back"}
TERMINAL = {"succeeded", "analyzed", "failed", "interrupted", "cancelled", "rolled_back"}


class AgentContinuation:
    def __init__(self, services):
        self.services = services
        self.task = None

    def start(self):
        self.task = asyncio.create_task(self.watch())

    async def shutdown(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    def actor(self, name):
        auth = self.services.data_auth
        if not auth or not auth.users:
            return Principal() if name == "local" else None
        user = auth.users.get(name)
        return Principal(name, True, tuple(user.get("datasets", [])), tuple(user.get("outputs", []))) if user else None

    async def scan(self):
        store = self.services.local_store
        with store.connection() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_tasks'").fetchone():
                return
            rows = db.execute("SELECT conversation_id,actor,payload FROM assistant_tasks").fetchall()
        for row in rows:
            state = json.loads(row["payload"])
            if state.get("protocol_version") != 2 or not state.get("awaiting_results") or task_closure(state):
                continue
            conversation_id = row["conversation_id"]
            with store.connection() as db:
                busy = db.execute("SELECT 1 FROM chat_turns WHERE conversation_id=? AND status IN ('queued','running')",
                                  (conversation_id,)).fetchone()
                newest = db.execute("SELECT MAX(sequence) FROM conversation_messages WHERE conversation_id=? AND role='user'",
                                    (conversation_id,)).fetchone()[0]
            # A user's newer turn owns the goal. Delayed execution must not compete.
            if busy or newest != state.get("source_sequence"):
                continue
            actor = self.actor(row["actor"])
            if actor is None:
                continue
            token = principal.set(actor)
            try:
                for identity in state["awaiting_results"]:
                    fact = state.get("artifacts", {}).get(identity, {})
                    kind = fact.get("kind")
                    if kind == "data":
                        current = await asyncio.to_thread(self.services.data_operations.progress, identity, conversation_id)
                        version = current.get("manifest_hash") if current.get("manifest_ready", True) else current.get("script_plan_hash")
                        transitioned = current.get("status") == "pending" and fact.get("artifact_type") == "script_plan" and current.get("manifest_ready", True) and version != fact.get("version")
                    elif kind == "workflow":
                        runs = store.list_runs(workflow_id=identity)
                        if not runs:
                            continue
                        run = runs[0]
                        current = {"id": run.run_id, "kind": "workflow_run", "workflow_id": identity,
                                   "status": run.status, "state_revision": run.state_revision, "error": run.error_message}
                        version, transitioned = run.state_revision, False
                    else:
                        current = self.services.workspace_service.operation(identity)
                        version, transitioned = current.get("expires_at"), False
                    if current["status"] in ACTIVE:
                        self.set_phase(row, state, "executing")
                        continue
                    if current["status"] not in TERMINAL and not transitioned:
                        continue
                    event = {"task_id": state["task_id"], "source_id": identity, "id": current["id"],
                             "status": current["status"], "version": version, "phase": current.get("phase")}
                    digest = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()[:32]
                    request = ChatRequest.model_validate(state["last_request"])
                    request.client_turn_id = "result-" + digest
                    request._execution_feedback = event
                    started = self.services.chat_turns.submit_feedback(request, newest)
                    if started:
                        self.set_phase(row, state, "verifying")
                    else:
                        with store.connection() as db:
                            feedback = db.execute("SELECT status,message FROM chat_turns WHERE id=?", (request.client_turn_id,)).fetchone()
                        if feedback and feedback["status"] in {"failed", "interrupted", "cancelled"}:
                            self.set_phase(row, state, "blocked",
                                "执行结果核验尚未完成：" + feedback["message"] + "。可发送继续核验；不会自动重跑任务。")
                    break  # One continuation per conversation at a time.
            except (ValueError, OSError, KeyError, RuntimeError):
                logger.exception("Assistant result continuation unavailable for %s", conversation_id)
            finally:
                principal.reset(token)

    def set_phase(self, row, state, phase, message=None):
        if state.get("phase") == phase:
            return
        # Do not replace a concurrently updated goal or draft.
        change = {"phase": phase}
        if message:
            change.update(next_step=message, awaiting_results=[])
        updated = json.dumps({**state, **change}, ensure_ascii=False)
        with self.services.local_store.transaction() as db:
            changed=db.execute("UPDATE assistant_tasks SET payload=? WHERE conversation_id=? AND actor=? AND payload=?",
                       (updated, row["conversation_id"], row["actor"], row["payload"]))
            if changed.rowcount:
                from .managed_tasks import sync_state
                sync_state(db,json.loads(updated),row['conversation_id'],row['actor'])

    async def watch(self):
        while True:
            try:
                await self.scan()
            except Exception:
                logger.exception("Assistant result watcher failed")
            await asyncio.sleep(2)
