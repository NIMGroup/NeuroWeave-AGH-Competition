"""Stop real temporary-file work at safe boundaries without cancelling other jobs."""
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
import time

import pytest
from app import workspace_io
from app.data_auth import Principal, principal
from app.data_models import DataPlanRequest, DataEdit
from app.execution_activity import active_executions
from app.operation_context import OperationContext, OperationCancelled, checkpoint, current_operation
from app.workspace_models import FileOperationRequest
from app.workflow_models import Workflow
from app.run_models import WorkflowRun
from test_data_operations import data, approval, finished
from test_workspaces import setup
from test_module_boundaries import application


def submit(pool, action, *args):
    return pool.submit(copy_context().run, action, *args)


def wait_for_stop(entered):
    entered.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        checkpoint()
        Event().wait(.01)
    pytest.fail("Cancellation was not observed")


def services(data):
    return SimpleNamespace(local_store=data.service.store, data_operations=data.service,
                           workspace_service=data.workspace)


def test_stop_during_validation_does_not_wait_for_the_validation_lock(data, monkeypatch):
    (data.source / "a.txt").write_text("old")
    task = data.service.propose(DataPlanRequest(**data.scope, mode="inplace",
        edits=[DataEdit(action="write_text", path="a.txt", content="new")]))
    entered = Event()
    monkeypatch.setattr(data.service, "_validate", lambda _: wait_for_stop(entered))
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = submit(pool, data.service.confirm, task["id"], approval(task, inplace=True))
        assert entered.wait(3)
        activity = active_executions(services(data))["activities"]
        assert activity[0]["status"] == "validating" and activity[0]["can_cancel"]
        assert "manifest" not in activity[0] and "script" not in activity[0]
        stopped = submit(pool, data.service.cancel, task["id"], task["conversation_id"]).result(3)
        assert stopped["status"] == "cancelled"
        assert running.result(3)["status"] == "cancelled"
    assert (data.source / "a.txt").read_text() == "old"
    assert not active_executions(services(data))["activities"]


@pytest.mark.parametrize("workers", [1, 2])
def test_stop_execution_hash_including_parallel_worker(data, monkeypatch, workers):
    data.service.file_workers = workers
    (data.source / "a.txt").write_text("old")
    task = data.service.propose(DataPlanRequest(**data.scope,
        edits=[DataEdit(action="copy", path="a.txt", destination="b.txt")]))
    original = workspace_io.copy
    entered = Event()
    def hashing(*args, **kwargs):
        wait_for_stop(entered)
        return original(*args, **kwargs)
    monkeypatch.setattr(workspace_io, "copy", hashing)
    data.service.confirm(task["id"], approval(task))
    assert entered.wait(3)
    data.service.cancel(task["id"], task["conversation_id"])
    done = finished(data, task)
    assert done["status"] == "cancelled" and done["can_undo"]
    assert not (data.output / "b.txt").exists()
    assert (data.source / "a.txt").read_text() == "old"
    monkeypatch.setattr(workspace_io, "copy", original)
    data.service.undo(task["id"], approval(task))
    assert finished(data, task)["status"] == "rolled_back"


def test_stop_restore_retains_staging_record_and_can_resume(data, monkeypatch):
    (data.source / "a.txt").write_text("old")
    task = data.service.propose(DataPlanRequest(**data.scope, mode="inplace",
        edits=[DataEdit(action="write_text", path="a.txt", content="new")]))
    data.service.confirm(task["id"], approval(task, inplace=True))
    assert finished(data, task)["status"] == "succeeded"
    original = workspace_io.copy
    entered = Event()
    def copying(source, target, *args, **kwargs):
        target.write_text("partial")
        wait_for_stop(entered)
        return original(source, target, *args, **kwargs)
    monkeypatch.setattr(workspace_io, "copy", copying)
    data.service.undo(task["id"], approval(task, inplace=True))
    assert entered.wait(3)
    data.service.cancel(task["id"], task["conversation_id"])
    done = finished(data, task)
    assert done["status"] == "cancelled" and done["can_undo"]
    assert (data.source / "a.txt").read_text() == "new"
    journal = data.service._raw(task["id"])["journal"]
    assert journal[0]["undo_staging"]
    monkeypatch.setattr(workspace_io, "copy", original)
    data.service.undo(task["id"], approval(task, inplace=True))
    assert finished(data, task)["status"] == "rolled_back"
    assert (data.source / "a.txt").read_text() == "old"
    assert not list(data.source.glob(".neuroweave-restore-*"))


def test_workspace_copy_stop_is_independent_and_retains_unpublished_staging(setup, monkeypatch):
    fs, ws, root = setup
    (root / "a.txt").write_text("old")
    task = fs.propose("copy", FileOperationRequest(workspace_id=ws.id, path="a.txt", destination="b.txt"))
    entered = Event()
    original = workspace_io.copy
    def copying(source, target, *args, **kwargs):
        target.write_text("partial")
        wait_for_stop(entered)
        return original(source, target, *args, **kwargs)
    monkeypatch.setattr(workspace_io, "copy", copying)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = submit(pool, fs.confirm, task["id"])
        assert entered.wait(3)
        active = active_executions(SimpleNamespace(local_store=fs.store, workspace_service=fs))["activities"]
        assert active[0]["kind"] == "workspace"
        assert fs.operation(task["id"])["status"] == "running"
        with pytest.raises(PermissionError):
            fs.propose("rename", FileOperationRequest(workspace_id=ws.id, path="a.txt", destination="c.txt"))
        with pytest.raises(PermissionError):
            fs.cancel(task["id"], "wrong-conversation")
        assert not fs._cancel_events[task["id"]].is_set()
        fs.cancel(task["id"])
        done = running.result(3)
    assert done["status"] == "cancelled" and not done["can_cancel"]
    assert not (root / "b.txt").exists()
    assert (root / "a.txt").read_text() == "old"
    assert fs.operation(task["id"])["staging_path"]


def test_pending_workspace_stop_blocks_run_and_wrong_actor_cannot_stop(data):
    (data.output / "a.txt").write_text("old")
    task = data.workspace.propose("rename", FileOperationRequest(workspace_id=data.ws.id,
        conversation_id=data.scope["conversation_id"], path="a.txt", destination="b.txt"))
    token = principal.set(Principal("bob", True, (), ()))
    try:
        with pytest.raises(PermissionError):
            data.workspace.cancel(task["id"], task["conversation_id"])
    finally:
        principal.reset(token)
    assert not data.workspace._cancel_events.get(task["id"])
    assert data.workspace.cancel(task["id"], task["conversation_id"])["status"] == "cancelled"
    with pytest.raises(ValueError):
        data.workspace.confirm(task["id"], task["conversation_id"])
    assert (data.output / "a.txt").read_text() == "old"


def test_recursive_delete_checks_stop_between_entries(tmp_path, monkeypatch):
    root = tmp_path / "delete-me"
    root.mkdir()
    for i in range(3):
        (root / f"{i}.txt").write_text("original")
    event = Event()
    original = workspace_io.os.unlink
    removed = []
    def unlink(*args, **kwargs):
        original(*args, **kwargs)
        removed.append(args[0])
        event.set()
    monkeypatch.setattr(workspace_io.os, "unlink", unlink)
    token = current_operation.set(OperationContext(event, lambda _: None))
    try:
        with pytest.raises(OperationCancelled):
            workspace_io.remove(root)
    finally:
        current_operation.reset(token)
    assert len(removed) == 1 and root.is_dir() and len(list(root.iterdir())) == 2


def test_activity_route_is_readonly_and_background_check_is_stoppable(application):
    _, client, service = application
    before = client.get("/api/execution-activities").json()
    assert before == {"activities": []}
    entered = Event()
    check = service.operation_service.submit("dataset_inspect", "synthetic", lambda: wait_for_stop(entered))
    assert entered.wait(3)
    activity = client.get("/api/execution-activities").json()["activities"]
    assert len(activity) == 1 and activity[0]["kind"] == "check" and activity[0]["conversation_id"] is None
    service.operation_service.cancel(check["operation_id"])
    deadline = time.monotonic() + 3
    while service.operation_service.get(check["operation_id"])["status"] != "cancelled":
        assert time.monotonic() < deadline
        Event().wait(.01)
    assert client.get("/api/execution-activities").json() == {"activities": []}


def test_scientific_run_discovery_retains_conversation_and_cancel_acknowledgement(application):
    _, client, service = application
    conversation = service.conversation_service.create("Run fixture")
    workflow = Workflow(workflow_id="wf_stop", template_id="fixture", title="Scientific run",
        summary="", original_request="fixture", status="awaiting_method_choice", steps=[],
        created_at="now", updated_at="now")
    service.local_store.save_workflow(workflow, event_type="workflow_created")
    service.local_store.append_conversation_message(conversation.conversation_id, role="assistant",
        content="Fixture plan", estimated_tokens=1, metadata={"workflow_id": workflow.workflow_id}, created_at="now")
    run = WorkflowRun(run_id="run_stop", workflow_id=workflow.workflow_id, workflow_revision=1,
        status="running", progress=25, output_directory="/synthetic/output", cancellation_requested=True,
        created_at="now", updated_at="now")
    service.local_store.save_run(run)
    value = client.get("/api/execution-activities").json()["activities"]
    assert value == [dict(id="run_stop", kind="run", conversation_id=conversation.conversation_id,
        title="Scientific run", status="running", progress=25, can_cancel=True, cancel_requested=True)]
    assert service.local_store.get_run("run_stop").status == "running"


def test_activity_discovery_does_not_expose_other_actor_data_or_checks(data):
    (data.source / "a.txt").write_text("old")
    task = data.service.propose(DataPlanRequest(**data.scope,
        edits=[DataEdit(action="copy", path="a.txt", destination="b.txt")]))
    raw = data.service._raw(task["id"])
    raw["status"] = "validating"
    data.service._save(raw)
    token = principal.set(Principal("bob", True, ("ds_test",), (data.ws.id,)))
    try:
        assert active_executions(services(data))["activities"] == []
        with pytest.raises(PermissionError):
            data.service.cancel(task["id"], task["conversation_id"])
        assert not data.service._cancel_event(task["id"]).is_set()
    finally:
        principal.reset(token)


def test_script_preflight_can_be_stopped_before_sandbox_execution(data, monkeypatch):
    from app.data_models import PythonRequest
    from test_data_operations import script_approval
    (data.source / "a.txt").write_text("old")
    data.service.sandbox.image = "sha256:" + "a" * 64
    task = data.service.python(PythonRequest(**data.scope, code="print('fixture')", mode="analysis"))
    entered = Event()
    monkeypatch.setattr(data.workspace, "_validate_tree", lambda _: wait_for_stop(entered))
    data.service.start_python(task["id"], script_approval(task))
    assert entered.wait(3)
    data.service.cancel(task["id"], task["conversation_id"])
    assert finished(data, task)["status"] == "cancelled"
    assert (data.source / "a.txt").read_text() == "old"
