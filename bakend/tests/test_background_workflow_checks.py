"""Checks remain discoverable/cancellable independently of the originating view."""

import asyncio
import time
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI

from app.api.execution import router_for as execution_router
from app.api.operations import router_for as operation_router
from app.api.workflows import router_for as workflow_router
from app.data_auth import Principal, principal
from app.local_store import LocalStore
from app.operation_context import checkpoint
from app.operation_service import OperationService


@pytest.fixture
def service(tmp_path):
    result = OperationService(LocalStore(tmp_path / "state.db"))
    yield result
    result.shutdown(wait=True)


def await_status(service, identity, status):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        row = service.get(identity)
        if row["status"] == status:
            return row
        time.sleep(0.01)
    raise AssertionError(service.get(identity))


def test_latest_is_compact_owner_scoped_and_matches_exact_workflow_id(service):
    alice = Principal("alice", True, ("input",), ("output",))
    token = principal.set(alice)
    try:
        job = service.submit(
            "workflow_dry_run", "wf_one:4", lambda: {"bulky_result": "x" * 10000}
        )
        await_status(service, job["operation_id"], "succeeded")
        view = service.latest_workflow_check("wf_one")
        assert (
            view["operation_id"] == job["operation_id"]
            and view["expected_revision"] == 4
        )
        assert "result" not in view and "bulky_result" not in str(view)
        assert service.latest_workflow_check("wfXone") is None  # '_' is not a wildcard.
        principal.set(Principal("bob", True, ("input",), ("output",)))
        assert service.latest_workflow_check("wf_one") is None
    finally:
        principal.reset(token)


def test_active_work_is_restored_before_an_older_completed_record(service):
    old = service.submit("workflow_dry_run", "wf:1", lambda: {})
    await_status(service, old["operation_id"], "succeeded")

    def action():
        while True:
            checkpoint()
            time.sleep(0.01)

    current = service.submit("workflow_complete_dependencies", "wf:2", action)
    await_status(service, current["operation_id"], "running")
    assert (
        service.latest_workflow_check("wf")["operation_id"] == current["operation_id"]
    )
    service.cancel(current["operation_id"])
    assert await_status(service, current["operation_id"], "cancelled")


def test_dry_run_http_returns_immediately_and_can_restore_stop_without_execution(
    service,
):
    started = Event()

    def dry_run(*_):
        started.set()
        while True:
            checkpoint("只读检查输入")
            time.sleep(0.01)

    workflow = Mock(
        get=Mock(return_value=SimpleNamespace()), dry_run=Mock(side_effect=dry_run)
    )
    services = SimpleNamespace(
        operation_service=service, workflow_service=workflow, run_service=Mock()
    )
    app = FastAPI()
    app.include_router(execution_router(services))
    app.include_router(operation_router(services))

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/workflows/wf/dry-run-jobs", json={"expected_revision": 1}
            )
            assert response.status_code == 202
            identity = response.json()["operation_id"]
            assert await asyncio.to_thread(started.wait, 2)
            assert (await client.get("/api/workflows/wf/checks/latest")).json()[
                "operation"
            ]["operation_id"] == identity
            again = await client.post(
                "/api/workflows/wf/dry-run-jobs", json={"expected_revision": 1}
            )
            assert (
                again.json()["operation_id"] == identity
            )  # Navigation/retry cannot duplicate a check.
            await client.post("/api/operations/" + identity + "/cancel")
            await asyncio.to_thread(await_status, service, identity, "cancelled")
            assert (await client.get("/api/workflows/wf/checks/latest")).json()[
                "operation"
            ]["status"] == "cancelled"

    asyncio.run(exercise())
    services.run_service.create.assert_not_called()
    assert workflow.dry_run.call_count == 1


@pytest.mark.parametrize('action', ['replan', 'complete-dependencies'])
def test_planning_transport_is_background_and_explicit_stop_cancels_it(service, action):
    started = Event()
    stopped = Event()

    async def replan(*_):
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            stopped.set()

    services = SimpleNamespace(
        operation_service=service, workflow_service=Mock(**{action.replace('-', '_'): replan})
    )
    app = FastAPI()
    app.include_router(workflow_router(services))
    app.include_router(operation_router(services))

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/workflows/wf/{action}-jobs", json={"expected_revision": 1}
            )
            assert response.status_code == 202
            identity = response.json()["operation_id"]
            assert await asyncio.to_thread(started.wait, 2)
            await client.post("/api/operations/" + identity + "/cancel")
            await asyncio.to_thread(await_status, service, identity, "cancelled")
            assert stopped.is_set()

    asyncio.run(exercise())
