"""Operations HTTP endpoints; dependencies are supplied by the application factory."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..application_services import ApplicationServices


def router_for(services: ApplicationServices) -> APIRouter:
    router = APIRouter()

    @router.get("/api/operations/{operation_id}")
    def get_operation(operation_id: str) -> dict[str, Any]:
        try:
            return services.operation_service.get(operation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Operation not found") from exc

    @router.post("/api/operations/{operation_id}/cancel")
    def cancel_operation(operation_id: str) -> dict[str, Any]:
        try:
            return services.operation_service.cancel(operation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Operation not found") from exc

    return router
