"""Datasets HTTP endpoints; dependencies are supplied by the application factory."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..application_services import ApplicationServices
from ..dataset_models import (
    DatasetRecord,
    DatasetRegisterRequest,
    DirectoryListing,
)
from ..workspace_models import DatasetWorkspaceRequest
from .errors import submit_operation


def router_for(services: ApplicationServices) -> APIRouter:
    router = APIRouter()

    @router.get("/api/datasets/roots")
    def dataset_roots() -> dict[str, Any]:
        return {
            "roots": [root.model_dump() for root in services.dataset_service.roots()]
        }

    @router.get("/api/datasets/directories", response_model=DirectoryListing)
    def dataset_directories(root_id: str, relative_path: str = "") -> DirectoryListing:
        try:
            return services.dataset_service.list_directories(root_id, relative_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/datasets/register-jobs", status_code=202)
    def register_dataset_job(request: DatasetRegisterRequest) -> dict[str, Any]:
        return submit_operation(
            services.operation_service,
            "dataset_register",
            f"{request.root_id}:{request.relative_path}",
            lambda: services.dataset_service.register(
                request.root_id, request.relative_path, request.display_name
            ),
        )

    @router.post("/api/datasets/{dataset_id}/inspect-jobs", status_code=202)
    def inspect_dataset_job(dataset_id: str) -> dict[str, Any]:
        return submit_operation(
            services.operation_service,
            "dataset_inspect",
            dataset_id,
            lambda: services.dataset_service.inspect(dataset_id),
        )

    @router.get("/api/datasets")
    def list_datasets() -> dict[str, list[DatasetRecord]]:
        return {"datasets": services.dataset_service.list()}

    @router.patch("/api/datasets/{dataset_id}/workspace", response_model=DatasetRecord)
    def associate_dataset_workspace(dataset_id: str, request: DatasetWorkspaceRequest):
        try:
            if request.workspace_id:
                services.workspace_service.get(request.workspace_id)
            dataset = services.dataset_service.get(dataset_id).model_copy(
                update={"workspace_id": request.workspace_id}
            )
            services.local_store.save_dataset(dataset)
            return dataset
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/api/datasets/register", response_model=DatasetRecord)
    def register_dataset(request: DatasetRegisterRequest) -> DatasetRecord:
        try:
            return services.dataset_service.register(
                request.root_id,
                request.relative_path,
                request.display_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/datasets/{dataset_id}", response_model=DatasetRecord)
    def get_dataset(dataset_id: str) -> DatasetRecord:
        try:
            return services.dataset_service.get(dataset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Dataset not found") from exc

    @router.post("/api/datasets/{dataset_id}/inspect", response_model=DatasetRecord)
    def inspect_dataset(dataset_id: str) -> DatasetRecord:
        try:
            return services.dataset_service.inspect(dataset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Dataset not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
