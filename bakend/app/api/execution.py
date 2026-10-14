"""Execution HTTP endpoints; dependencies are supplied by the application factory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..application_services import ApplicationServices
from ..execution import RunConflict
from ..run_models import (
    ExecutionDraftRequest,
    OutputPreviewRequest,
    RunCreateRequest,
    RunRetryRequest,
    WorkflowRun,
)
from ..workflow_models import (
    Workflow,
    WorkflowConfirmRequest,
    WorkflowMutationRequest,
)
from ..workflow_service import RevisionConflict
from .errors import revision_http_error, submit_operation


def router_for(services: ApplicationServices) -> APIRouter:
    router = APIRouter()

    @router.post('/api/workflows/{workflow_id}/output-preview')
    def output_preview(workflow_id: str, request: OutputPreviewRequest) -> dict[str, Any]:
        from ..execution.output_preview import workflow_output_preview
        try:
            workflow = services.workflow_service.get(workflow_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='Workflow not found') from exc
        if workflow.revision != request.expected_revision:
            raise revision_http_error(RevisionConflict(workflow))
        return workflow_output_preview(workflow, services.run_service.recipe_compiler,
            request.execution, offset=request.offset, limit=request.limit)

    @router.get('/api/workflows/{workflow_id}/checks/latest')
    def latest_check(workflow_id: str) -> dict[str, Any]:
        try:
            services.workflow_service.get(workflow_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='Workflow not found') from exc
        return {'operation': services.operation_service.latest_workflow_check(workflow_id)}

    @router.post("/api/workflows/{workflow_id}/dry-run-jobs", status_code=202)
    def dry_run_job(
        workflow_id: str, request: WorkflowMutationRequest
    ) -> dict[str, Any]:
        return submit_operation(
            services.operation_service,
            "workflow_dry_run",
            f"{workflow_id}:{request.expected_revision}",
            lambda: services.workflow_service.dry_run(
                workflow_id, request.expected_revision
            ),
        )

    @router.post("/api/workflows/{workflow_id}/dry-run", response_model=Workflow)
    def dry_run_workflow(
        workflow_id: str, request: WorkflowMutationRequest
    ) -> Workflow:
        try:
            return services.workflow_service.dry_run(
                workflow_id, request.expected_revision
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Workflow not found") from exc
        except RevisionConflict as exc:
            raise revision_http_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/workflows/{workflow_id}/confirm", response_model=Workflow)
    def confirm_workflow(workflow_id: str, request: WorkflowConfirmRequest) -> Workflow:
        try:
            return services.workflow_service.confirm(
                workflow_id, request.expected_revision, request.acknowledge_quality
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Workflow not found") from exc
        except RevisionConflict as exc:
            raise revision_http_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/api/workflows/{workflow_id}/execution-draft")
    def get_execution_draft(workflow_id: str) -> dict[str, Any]:
        if services.local_store.get_workflow(workflow_id) is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return services.local_store.execution_draft(workflow_id)

    @router.put("/api/workflows/{workflow_id}/execution-draft")
    def put_execution_draft(
        workflow_id: str, request: ExecutionDraftRequest
    ) -> dict[str, Any]:
        try:
            return services.local_store.save_execution_draft(
                workflow_id,
                request.expected_revision,
                request.expected_draft_revision,
                request.execution,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Workflow not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/workflows/{workflow_id}/runs", response_model=WorkflowRun)
    def start_workflow_run(
        workflow_id: str,
        request: RunCreateRequest,
    ) -> WorkflowRun:
        try:
            # New submissions must have an explicit output selection. Existing runs
            # and retries keep the immutable output snapshot saved with their run.
            with (
                services.run_service.mutation_lock,
                services.workflow_service.mutation_lock,
            ):
                workflow = services.workflow_service.get(workflow_id)
                if not workflow.workspace_id or not workflow.workspace_root_path:
                    raise ValueError("请先选择服务器输出目录，再提交运行")
                selected = services.workspace_service.get(workflow.workspace_id)
                if (
                    Path(selected.root_path).resolve()
                    != Path(workflow.workspace_root_path).resolve()
                ):
                    raise ValueError("输出目录已变化，请重新选择并检查流程")
                services.workspace_service.validate_output_directory(
                    Path(selected.root_path)
                )
                return services.run_service.create(
                    workflow_id, request.expected_revision, request.execution
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Workflow not found") from exc
        except (RunConflict, ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/api/runs")
    def list_runs(
        workflow_id: str | None = None,
        limit: int = 50,
        summary: bool = False,
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        return {
            "runs": services.local_store.list_runs(
                workflow_id=workflow_id, limit=limit, summary=summary, offset=offset
            )
        }

    @router.get("/api/execution/backends")
    def execution_backends() -> dict[str, Any]:
        return services.run_service.execution_backends()

    @router.get("/api/runs/{run_id}", response_model=WorkflowRun)
    def get_run(run_id: str) -> WorkflowRun:
        try:
            return services.run_service.get(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc

    @router.get("/api/runs/{run_id}/events")
    def run_events(
        run_id: str,
        after_event_id: int = 0,
        before_event_id: int | None = Query(None, ge=1),
        tail: bool = False,
        limit: int = Query(1000, ge=1, le=1000),
    ) -> dict[str, Any]:
        try:
            return services.local_store.run_event_page(
                run_id,
                after_event_id=after_event_id,
                before_event_id=before_event_id,
                tail=tail,
                limit=limit,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc

    @router.get("/api/runs/{run_id}/monitor")
    def monitor_run(run_id: str, after_revision: int = -1) -> dict[str, Any]:
        try:
            return services.local_store.monitor_run(run_id, after_revision)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc

    @router.post("/api/runs/{run_id}/cancel", response_model=WorkflowRun)
    def cancel_run(run_id: str) -> WorkflowRun:
        try:
            return services.run_service.cancel(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except RunConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/runs/{run_id}/retry", response_model=WorkflowRun)
    def retry_run(run_id: str, request: RunRetryRequest) -> WorkflowRun:
        try:
            return services.run_service.retry(
                run_id, request.step_ids, request.sample_ids
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except (RunConflict, ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/runs/{run_id}/recover-freesurfer")
    def recover_freesurfer_run(
        run_id: str,
        request: RunCreateRequest,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        try:
            return services.run_service.recover_freesurfer(
                run_id, request.expected_revision, dry_run=dry_run
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except RunConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/runs/{run_id}/resume-monitoring", response_model=WorkflowRun)
    def resume_slurm_monitoring(run_id: str) -> WorkflowRun:
        try:
            return services.run_service.resume_slurm_monitoring(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except RunConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
