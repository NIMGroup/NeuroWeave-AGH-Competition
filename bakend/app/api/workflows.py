"""Workflows HTTP endpoints; dependencies are supplied by the application factory."""

from __future__ import annotations

from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query

from ..application_services import ApplicationServices
from ..models import ChatRequest
from ..task_contract import PlanNotMatched
from ..workflow_models import (
    MethodSelectionRequest,
    Workflow,
    WorkflowDatasetRequest,
    WorkflowDraftRequest,
    WorkflowMutationRequest,
)
from ..workflow_service import RevisionConflict
from .errors import revision_http_error, submit_operation


def router_for(services: ApplicationServices) -> APIRouter:
    router = APIRouter()

    @router.post('/api/workflows/{workflow_id}/replan-jobs', status_code=202)
    def replan_job(workflow_id: str, request: WorkflowMutationRequest) -> dict[str, Any]:
        import asyncio
        from ..operation_context import cancellable
        async def action():
            workflow, _ = await cancellable(services.workflow_service.replan(workflow_id, request.expected_revision))
            return workflow
        return submit_operation(services.operation_service, 'workflow_replan',
            f'{workflow_id}:{request.expected_revision}', lambda: asyncio.run(action()))

    @router.post('/api/workflows/{workflow_id}/complete-dependencies-jobs', status_code=202)
    def dependencies_job(workflow_id: str, request: WorkflowMutationRequest) -> dict[str, Any]:
        import asyncio
        from ..operation_context import cancellable
        async def action():
            return await cancellable(services.workflow_service.complete_dependencies(workflow_id, request.expected_revision))
        return submit_operation(services.operation_service, 'workflow_complete_dependencies',
            f'{workflow_id}:{request.expected_revision}', lambda: asyncio.run(action()))

    @router.post("/api/workflows/draft", response_model=Workflow)
    async def draft_workflow(request: WorkflowDraftRequest) -> Workflow:
        try:
            workflow, _content = await services.workflow_service.draft(
                services.chat_coordinator.resolve_context(
                    ChatRequest(**request.model_dump())
                )
            )
            return workflow
        except PlanNotMatched as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "status": "candidate_mismatch",
                    "message": str(exc),
                    "diagnostics": exc.diagnostics,
                },
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"模型服务请求失败（HTTP {exc.response.status_code}）",
            ) from exc
        except (
            httpx.RequestError,
            RuntimeError,
            KeyError,
            IndexError,
            TypeError,
        ) as exc:
            raise HTTPException(status_code=502, detail=f"无法生成流程：{exc}") from exc

    @router.get("/api/workflow-index")
    def workflow_index(
        offset: int = Query(0, ge=0),
        limit: int = Query(30, ge=1, le=100),
        q: str = Query("", max_length=200),
        source: Literal["", "pipeline", "conversation"] = "",
        status: Literal["", "draft", "active", "succeeded", "failed", "cancelled"] = "",
    ):
        return services.local_store.workflow_index(
            offset=offset, limit=limit, query=q, source=source, status=status
        )

    @router.get("/api/workflows/{workflow_id}", response_model=Workflow)
    def get_workflow(workflow_id: str) -> Workflow:
        try:
            return services.workflow_service.get(workflow_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Workflow not found") from exc

    @router.get("/api/workflows")
    def list_workflows(limit: int = 50) -> dict[str, list[Workflow]]:
        return {"workflows": services.workflow_service.list(limit)}

    @router.get("/api/workflows/{workflow_id}/events")
    def workflow_events(workflow_id: str) -> dict[str, Any]:
        try:
            return {"events": services.workflow_service.events(workflow_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Workflow not found") from exc

    @router.patch(
        "/api/workflows/{workflow_id}/steps/{step_id}/method", response_model=Workflow
    )
    def select_workflow_method(
        workflow_id: str,
        step_id: str,
        request: MethodSelectionRequest,
    ) -> Workflow:
        try:
            return services.workflow_service.select_method(
                workflow_id,
                step_id,
                request.method_id,
                request.expected_revision,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Workflow not found") from exc
        except RevisionConflict as exc:
            raise revision_http_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/workflows/{workflow_id}/lock", response_model=Workflow)
    def lock_workflow(workflow_id: str, request: WorkflowMutationRequest) -> Workflow:
        try:
            return services.workflow_service.lock_chain(
                workflow_id, request.expected_revision
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Workflow not found") from exc
        except RevisionConflict as exc:
            raise revision_http_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.patch("/api/workflows/{workflow_id}/dataset", response_model=Workflow)
    def change_workflow_dataset(
        workflow_id: str, request: WorkflowDatasetRequest
    ) -> Workflow:
        try:
            return services.workflow_service.change_dataset(
                workflow_id,
                request.dataset_id,
                request.expected_revision,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Workflow or dataset not found"
            ) from exc
        except RevisionConflict as exc:
            raise revision_http_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/workflows/{workflow_id}/unlock", response_model=Workflow)
    def unlock_workflow(workflow_id: str, request: WorkflowMutationRequest) -> Workflow:
        try:
            return services.workflow_service.unlock_chain(
                workflow_id, request.expected_revision
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Workflow not found") from exc
        except RevisionConflict as exc:
            raise revision_http_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post(
        "/api/workflows/{workflow_id}/steps/{step_id}/remove", response_model=Workflow
    )
    def remove_workflow_step(
        workflow_id: str, step_id: str, request: WorkflowMutationRequest
    ) -> Workflow:
        try:
            return services.workflow_service.remove_step(
                workflow_id, step_id, request.expected_revision
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Workflow or step not found"
            ) from exc
        except RevisionConflict as exc:
            raise revision_http_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/workflows/{workflow_id}/replan", response_model=Workflow)
    async def replan_workflow(
        workflow_id: str, request: WorkflowMutationRequest
    ) -> Workflow:
        try:
            workflow, _content = await services.workflow_service.replan(
                workflow_id,
                request.expected_revision,
            )
            return workflow
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Workflow not found") from exc
        except RevisionConflict as exc:
            raise revision_http_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post(
        "/api/workflows/{workflow_id}/complete-dependencies", response_model=Workflow
    )
    async def complete_workflow_dependencies(
        workflow_id: str,
        request: WorkflowMutationRequest,
    ) -> Workflow:
        try:
            return await services.workflow_service.complete_dependencies(
                workflow_id,
                request.expected_revision,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Workflow not found") from exc
        except RevisionConflict as exc:
            raise revision_http_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
