"""FastAPI factory: transport registration and process lifetime, no business logic."""

from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import (
    catalog,
    chat,
    conversations,
    datasets,
    execution,
    health,
    operations,
    managed_tasks,
    workflow_inputs,
    workflows,
)
from .api.middleware import install_maintenance
from .application_services import ApplicationServices
from .bootstrap import build_services
from .catalog_locale import CatalogLocaleMiddleware
from .errors import ServiceError
from .library_routes import library_router
from .settings import Settings
from .workspace_routes import workspace_router
from .data_routes import data_router
from .execution_activity import router_for as execution_activity_router


def create_app(services: ApplicationServices | None = None) -> FastAPI:
    services = services or build_services(Settings.from_environment())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from .agent_continuation import AgentContinuation
        continuation = AgentContinuation(services)
        if os.getenv('NEUROWEAVE_HACKATHON') != 'true':
            continuation.start()
        yield
        await continuation.shutdown()
        await services.chat_turns.shutdown()
        if services.data_operations:
            services.data_operations.shutdown()
        services.operation_service.shutdown()
        services.run_service.shutdown()

    app = FastAPI(
        title="NeuroWeave API",
        version="0.1.0",
        description="Local dialogue and skill-discovery API.",
        lifespan=lifespan,
    )
    app.state.services = services
    app.add_middleware(CatalogLocaleMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(services.settings.origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    install_maintenance(app, services)
    app.include_router(execution_activity_router(services))
    if services.data_auth:
        services.data_auth.install(app, services.settings.origins)
        app.include_router(services.data_auth.router())
    if services.data_operations:
        app.include_router(data_router(services.data_operations))

    @app.exception_handler(ServiceError)
    async def application_error(request: Request, error: ServiceError):
        return JSONResponse(
            status_code=error.status_code, content={"detail": error.detail}
        )

    @app.exception_handler(PermissionError)
    async def permission_error(request: Request, error: PermissionError):
        return JSONResponse(status_code=403, content={"detail": str(error)})

    app.include_router(
        workspace_router(services.workspace_service, services.dataset_files)
    )
    app.include_router(
        library_router(
            services.pipeline_library,
            services.workflow_service,
            services.workspace_service,
            services.runner_registry,
            services.chat_service,
        )
    )
    for group in (
        health,
        catalog,
        datasets,
        operations,
        conversations,
        chat,
        workflows,
        workflow_inputs,
        execution,
        managed_tasks,
    ):
        app.include_router(group.router_for(services))
    return app
