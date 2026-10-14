"""Task identity endpoints; plan actions retain the existing manual Run APIs."""

import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from ..data_auth import principal
from ..managed_tasks import ManagedTasks, sync_state
from ..models import ChatRequest
from ..agent_identity import task_closure


def router_for(services):
    router = APIRouter()
    from ..agent_state import AgentTasks

    AgentTasks(services.local_store)
    tasks = ManagedTasks(services.local_store)
    tasks.bootstrap()

    @router.get("/api/task-index")
    def index(
        offset: int = Query(0, ge=0),
        limit: int = Query(30, ge=1, le=100),
        q: str = Query("", max_length=200),
        source: str = "",
        status: str = "",
    ):
        return tasks.index(
            offset=offset, limit=limit, query=q, source=source, status=status
        )

    @router.get("/api/tasks/{identity}")
    def get_task(identity: str):
        try:
            result = tasks.get(identity)
            active = result["current_plan"] or {}
            if task_closure(result['task']) and active.get('kind') == 'workflow':
                return result
            result["plan"] = None
            if not result["deleted"] and active.get("kind") == "workflow":
                # The workflow card owns the graph query/cache. Task polling
                # returns its identity/version, not a second large dataset graph.
                services.workflow_service.get(active["id"])
            elif not result["deleted"] and active.get("kind") == "data":
                result["plan"] = services.data_operations.progress(
                    active["id"], result["entry"]["conversation_id"]
                )
            elif not result["deleted"] and active.get("kind") == "workspace":
                result["plan"] = services.workspace_service.operation(active["id"])
            return result
        except KeyError as exc:
            raise HTTPException(404, "Task not found") from exc

    @router.post("/api/tasks/{identity}/open-conversation")
    def open_conversation(identity: str):
        try:
            row, state = tasks.require(identity)
            if state.get("task_deleted"):
                raise ValueError("任务已删除，历史记录只读")
            cid = row["conversation_id"]
            lost_conversation = False
            with services.local_store.connection() as db:
                if (
                    cid
                    and not db.execute(
                        "SELECT 1 FROM conversations WHERE conversation_id=?", (cid,)
                    ).fetchone()
                ):
                    cid = None
                    lost_conversation = True
            if task_closure(state) and cid:
                # Opening history must not replace the conversation's new goal.
                return {'conversation_id':cid,'task_id':identity}
            if lost_conversation and not task_closure(state):
                # Immutable file/script proposals remain bound to their original
                # conversation. Keep their history; prepare a fresh plan under
                # the same task before offering Run in a restored conversation.
                state = {
                    **state,
                    "active_plan": {},
                    "phase": "needs_replan",
                    "next_step": "原会话已删除，请更新此任务的方案后再运行",
                }
            if cid and any(
                t["conversation_id"] == cid
                for t in services.chat_turns.active()["turns"]
            ):
                raise ValueError("请先停止当前会话请求，等待结束后再切换任务")
            if not cid:
                cid = services.conversation_service.create(
                    state["goal"][:80]
                ).conversation_id
            scope = state.get("scope", {})
            if scope.get("workspace_id"):
                services.workspace_service.get(scope["workspace_id"])
            with services.local_store.transaction() as db:
                current = db.execute(
                    "SELECT payload FROM assistant_tasks WHERE conversation_id=? AND actor=?",
                    (cid, principal.get().name),
                ).fetchone()
                old = json.loads(current[0]) if current else {}
                if (
                    old.get("task_id") not in (None, identity)
                    and (
                        old.get("active_plan")
                        or old.get("deliverable", "answer") != "answer"
                    )
                    and not task_closure(old)
                ):
                    raise ValueError(
                        "这条会话仍在维护另一个目标，请先由用户结束或确认完成"
                    )
                if db.execute(
                    "SELECT 1 FROM chat_turns WHERE conversation_id=? AND status IN ('queued','running')",
                    (cid,),
                ).fetchone():
                    raise ValueError("当前会话正在处理请求，请先停止并等待结束")
                db.execute(
                    "UPDATE conversations SET workspace_id=? WHERE conversation_id=?",
                    (scope.get("workspace_id"), cid),
                )
                if old.get("task_id") != identity:
                    active = state.get("active_plan") or {}
                    value = {
                        **state,
                        "protocol_version": 2,
                        "actor": principal.get().name,
                        "task_source": row["source"],
                        "last_request": {
                            "conversation_id": cid,
                            "workspace_id": scope.get("workspace_id"),
                            "dataset_id": scope.get("dataset_id"),
                            "message": state["goal"],
                            "language": state.get("language", "zh-CN"),
                        },
                        "artifacts": {
                            active["id"]: {
                                "id": active["id"],
                                "kind": active["kind"],
                                "revision": active.get("revision"),
                                "artifact_type": "workflow_plan"
                                if active["kind"] == "workflow"
                                else "script_plan",
                            }
                        }
                        if active
                        else {},
                        "awaiting_results": [active["id"]] if active else [],
                    }
                    db.execute(
                        "INSERT OR REPLACE INTO assistant_tasks VALUES(?,?,?)",
                        (
                            cid,
                            principal.get().name,
                            json.dumps(value, ensure_ascii=False),
                        ),
                    )
                    sync_state(db, value, cid, principal.get().name)
            return {"conversation_id": cid, "task_id": identity}
        except KeyError as exc:
            raise HTTPException(404, "Task not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/api/tasks/{identity}/replan", status_code=202)
    async def replan(identity: str):
        try:
            row, state = tasks.require(identity)
            from ..task_management import assert_task_open
            assert_task_open(state)
            cid = row["conversation_id"]
            if not cid or state.get("task_deleted"):
                raise ValueError("请先在会话中打开当前任务")
            with services.local_store.connection() as db:
                current = db.execute(
                    "SELECT payload FROM assistant_tasks WHERE conversation_id=? AND actor=?",
                    (cid, principal.get().name),
                ).fetchone()
            value = json.loads(current[0]) if current else {}
            if value.get("task_id") != identity:
                raise ValueError("请先切换回此任务对应的会话")
            scope = value["scope"]
            request = ChatRequest(
                message="请根据当前任务目标更新同一份完整方案，保留仍有效的输入选择和参数，不执行任务。",
                task_action="update_plan",
                conversation_id=cid,
                workspace_id=scope.get("workspace_id"),
                dataset_id=scope.get("dataset_id"),
                language=state.get("language", "zh-CN"),
                client_turn_id="task-update-" + uuid4().hex,
            )
            return services.chat_turns.submit(request)
        except KeyError as exc:
            raise HTTPException(404, "Task not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
