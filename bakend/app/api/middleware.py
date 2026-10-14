"""Publication gate and client contract check, shared by every resource router."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..application_services import ApplicationServices
from ..settings import API_CONTRACT


def install_maintenance(app: FastAPI, services: ApplicationServices) -> None:
    @app.middleware("http")
    async def release_maintenance(request: Request, call_next):
        client_contract = request.headers.get("x-neuroweave-contract")
        if client_contract and client_contract != API_CONTRACT:
            return JSONResponse(
                {
                    "detail": {
                        "code": "client_version_mismatch",
                        "message": "Frontend/API version mismatch; refresh the page before continuing.",
                    }
                },
                status_code=409,
            )

        if request.method in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        } and request.url.path.startswith(
            ("/api/fs/", "/api/workspaces", "/api/output-directories", "/api/library")
        ):
            origin = request.headers.get("origin")
            if (
                origin
                and origin not in services.settings.origins
                and origin != str(request.base_url).rstrip("/")
            ):
                return JSONResponse(
                    {"detail": "工作区写请求来自未授权的网页来源"}, status_code=403
                )
        # Deployment keeps reads/health available while rejecting new mutations.
        marker = services.settings.database_path.parent / "release-maintenance.lock"
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and marker.exists():
            return JSONResponse(
                {"detail": "系统正在发布维护，请稍后重试"},
                status_code=503,
                headers={"Retry-After": "30"},
            )
        response = await call_next(request)
        response.headers["X-NeuroWeave-Contract"] = API_CONTRACT
        return response
