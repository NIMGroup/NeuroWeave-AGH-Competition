"""Conversations HTTP endpoints; dependencies are supplied by the application factory."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query, Response

from ..application_services import ApplicationServices
from ..task_management import TaskEdit, TaskConfirmation, GoalRunActive, edit_task, confirm_task, clear_result_waiting
from ..conversation_models import (
    ConversationPage,
    ConversationRecord,
    ConversationSnapshot,
    ConversationUpdateRequest,
)


def router_for(services: ApplicationServices) -> APIRouter:
    router = APIRouter()

    @router.get('/api/conversations/{conversation_id}/task')
    def current_task(conversation_id: str):
        """Read the current task during planning without loading message graphs."""
        from ..data_auth import principal
        from ..agent_identity import public_understanding
        try:
            if not services.local_store.get_conversation(conversation_id):
                raise KeyError(conversation_id)
            with services.local_store.connection() as db:
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_tasks'").fetchone()
                row = db.execute(
                    'SELECT payload FROM assistant_tasks WHERE conversation_id=? AND actor=?',
                    (conversation_id, principal.get().name),
                ).fetchone() if exists else None
            from ..managed_tasks import ManagedTasks
            history = ManagedTasks(services.local_store).closed_in_conversation(conversation_id)
            return {'task': public_understanding(json.loads(row[0])) if row else None,
                    'closed_tasks':history}
        except KeyError as exc:
            raise HTTPException(404, 'Conversation not found') from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc

    @router.patch('/api/conversations/{conversation_id}/task')
    def update_task(conversation_id: str, request: TaskEdit):
        try:
            if any(t['conversation_id'] == conversation_id for t in services.chat_turns.active()['turns']):
                raise ValueError('请先停止当前请求，等待停止完成后再编辑任务')
            return edit_task(services.local_store, conversation_id, request)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post('/api/conversations/{conversation_id}/task/clear')
    def clear_task(conversation_id: str, request: TaskEdit):
        try:
            if any(t['conversation_id'] == conversation_id for t in services.chat_turns.active()['turns']):
                raise ValueError('请先停止当前请求，等待停止完成后再删除任务')
            return edit_task(services.local_store, conversation_id, request, clear=True)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post('/api/conversations/{conversation_id}/task/confirm-completion')
    def confirm_completion(conversation_id: str, request: TaskConfirmation):
        try:
            if any(t['conversation_id'] == conversation_id for t in services.chat_turns.active()['turns']):
                raise ValueError('请先停止当前请求，等待停止完成后再确认任务')
            return confirm_task(services.local_store, conversation_id, request)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post('/api/conversations/{conversation_id}/task/end')
    def end_task(conversation_id: str, request: TaskConfirmation):
        try:
            if any(t['conversation_id'] == conversation_id for t in services.chat_turns.active()['turns']):
                raise ValueError('请先停止当前请求，等待停止完成后再归档')
            return confirm_task(services.local_store, conversation_id, request, outcome='ended')
        except GoalRunActive as exc:
            # Only this manual End action may stop a genuinely paused job.
            try:
                stopping = [services.run_service.stop_paused_for_goal_end(wid,
                    before_cancel=lambda:clear_result_waiting(services.local_store,conversation_id,request))
                    for wid in exc.workflow_ids]
            except ValueError as changed:
                raise HTTPException(409,str(changed)) from changed
            if all(stopping):
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=202,content={
                    'status':'stopping','message':'正在停止已暂停的执行，停止确认后将归档目标'})
            raise HTTPException(409,str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc

    @router.post("/api/conversations", response_model=ConversationRecord)
    def create_conversation() -> ConversationRecord:
        return services.conversation_service.create()

    @router.get("/api/conversations", response_model=ConversationPage)
    def list_conversations(
        limit: int = Query(default=20, ge=1, le=50),
        q: str = Query(default="", max_length=1000),
        archived: bool = False,
        cursor: str = Query(default="", max_length=1024),
    ) -> ConversationPage:
        try:
            return services.conversation_service.list_page(
                limit,
                query=q,
                archived=archived,
                cursor=cursor,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.delete("/api/conversations/{conversation_id}", status_code=204)
    def delete_conversation(conversation_id: str) -> Response:
        try:
            services.conversation_service.delete(conversation_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Conversation not found"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(status_code=204)

    @router.patch(
        "/api/conversations/{conversation_id}", response_model=ConversationRecord
    )
    def update_conversation(
        conversation_id: str,
        request: ConversationUpdateRequest,
    ) -> ConversationRecord:
        try:
            if "workspace_id" in request.model_fields_set:
                services.workspace_service.bind_conversation(
                    conversation_id, request.workspace_id
                )
                if request.title is None and request.archived is None:
                    return services.local_store.get_conversation(conversation_id)
            return services.conversation_service.update(
                conversation_id,
                title=request.title,
                archived=request.archived,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Conversation not found"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get(
        "/api/conversations/{conversation_id}", response_model=ConversationSnapshot
    )
    def get_conversation(
        conversation_id: str,
        limit: int = Query(40, ge=1, le=100),
        before_sequence: int | None = Query(None, ge=1),
    ) -> ConversationSnapshot:
        try:
            return services.conversation_service.snapshot(
                conversation_id, limit=limit, before_sequence=before_sequence
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Conversation not found"
            ) from exc

    @router.post(
        "/api/conversations/{conversation_id}/compact",
        response_model=ConversationSnapshot,
    )
    async def compact_conversation(conversation_id: str) -> ConversationSnapshot:
        try:
            await services.conversation_service.compact_if_needed(
                conversation_id, force=True
            )
            return services.conversation_service.snapshot(conversation_id, limit=40)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Conversation not found"
            ) from exc

    return router
