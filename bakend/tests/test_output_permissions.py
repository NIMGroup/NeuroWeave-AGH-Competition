"""Selected-output defaults and explicit overwrite/delete/in-place boundaries."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.data_auth import DataAuth, Principal, principal
from app.data_models import DataPlanRequest, DataEdit, PythonRequest, OrganizeRequest
from app.data_routes import data_router
from app.models import ChatRequest
from app.response_policy import ResponsePolicy
from app.workspace_tools import WorkspaceTools
from test_data_operations import data, approval, finished, run_script


def output_scope(data, **updates):
    return dict(data.scope, source_scope="output", mode="inplace", execution="output", **updates)


def test_current_local_access_can_approve_exact_dataset_changes_without_accounts(data):
    handle = principal.set(Principal())
    try:
        app = FastAPI()
        auth = DataAuth(data.service.store)
        auth.install(app); app.include_router(auth.router()); app.include_router(data_router(data.service))
        client = TestClient(app)
        session = client.get("/api/auth/session").json()
        assert not session["required"] and not session["authenticated"] and session["inplace_available"]
        (data.source / "old.nii.gz").write_bytes(b"original")
        task = data.service.propose(DataPlanRequest(**data.scope, mode="inplace", execution="output",
            edits=[DataEdit(action="rename", path="old.nii.gz", destination="T1.nii.gz")]))
        assert task["status"] == "pending" and task["requires_inplace_confirmation"]
        assert (data.source / "old.nii.gz").exists()
        with pytest.raises(PermissionError):
            data.service.confirm(task["id"], approval(task))
        with pytest.raises(PermissionError):
            data.service.confirm(task["id"], approval(task, inplace=True).model_copy(update={"manifest_hash": "wrong"}))
        data.service.confirm(task["id"], approval(task, inplace=True))
        assert finished(data, task)["status"] == "succeeded"
        data.service.undo(task["id"], approval(task, inplace=True))
        assert finished(data, task)["status"] == "rolled_back"
        assert (data.source / "old.nii.gz").read_bytes() == b"original"
    finally:
        principal.reset(handle)


def test_new_output_files_can_run_and_restore_without_inplace_or_overwrite_grants(data):
    task = data.service.propose(DataPlanRequest(**output_scope(data),
        edits=[DataEdit(action="write_text", path="nested/result.json", content='{"valid":true}')]))
    assert task['status'] == 'pending' and task['execution_policy'] == 'manual' and not task['requires_inplace_confirmation']
    assert task['id'] not in data.service.futures and not list(data.output.iterdir())
    assert not task["requires_overwrite_confirmation"]
    data.service.confirm(task['id'], approval(task))
    done = finished(data, task)
    assert done["status"] == "succeeded" and done["total_bytes"] == 14
    assert json.loads((data.output / "nested/result.json").read_text()) == {"valid": True}
    data.service.undo(task["id"], approval(task))
    assert finished(data, task)["status"] == "rolled_back"
    assert not (data.output / "nested").exists()


def test_output_overwrite_is_pending_until_explicit_confirmation_and_restores_original(data):
    (data.output / "table.csv").write_text("old")
    task = data.service.propose(DataPlanRequest(**output_scope(data),
        edits=[DataEdit(action="write_text", path="table.csv", content="new")]))
    assert task["status"] == "pending" and task["requires_overwrite_confirmation"] and not task["conflicts"]
    assert (data.output / "table.csv").read_text() == "old" and task["id"] not in data.service.futures
    with pytest.raises(PermissionError):
        data.service.confirm(task["id"], approval(task, inplace=True))
    approved = approval(task).model_copy(update={"allow_overwrite": True})
    data.service.confirm(task["id"], approved)
    assert finished(data, task)["status"] == "succeeded"
    with pytest.raises(PermissionError):
        data.service.undo(task["id"], approval(task))
    data.service.undo(task["id"], approved)
    assert finished(data, task)["status"] == "rolled_back"
    assert (data.output / "table.csv").read_text() == "old"


def test_generated_output_overwrite_backs_up_and_restores_output_instead_of_input(data):
    (data.source / "table.csv").write_text("source")
    (data.output / "table.csv").write_text("old output")
    def generated(source, directory, code):
        artifacts = directory / "artifacts"; artifacts.mkdir(parents=True)
        (artifacts / "table.csv").write_text("generated")
        return artifacts
    data.service.sandbox.run = generated
    data.service.sandbox.image = 'sha256:' + 'a' * 64
    task = data.service.python(PythonRequest(**data.scope, mode="copy", execution="output", code="synthetic"))
    task = run_script(data, task)
    assert task["status"] == "pending" and task["requires_overwrite_confirmation"] and not task["conflicts"]
    approved = approval(task).model_copy(update={"allow_overwrite": True})
    data.service.confirm(task["id"], approved)
    assert finished(data, task)["status"] == "succeeded"
    assert (data.source / "table.csv").read_text() == "source"
    assert (data.output / "table.csv").read_text() == "generated"
    data.service.undo(task["id"], approved)
    assert finished(data, task)["status"] == "rolled_back"
    assert (data.output / "table.csv").read_text() == "old output"
    assert (data.source / "table.csv").read_text() == "source"


def test_organizer_new_outputs_run_but_preview_request_still_does_not_write(data):
    (data.source / "sample_T1w.nii.gz").write_bytes(b"image")
    rules = [{"pattern": "*T1w.nii.gz", "name": "T1"}]
    preview = data.service.organize(OrganizeRequest(**data.scope, rules=rules))
    assert preview["status"] == "pending" and not (data.output / "T1.nii.gz").exists()
    task = data.service.organize(OrganizeRequest(**data.scope, rules=rules, execution="output"))
    assert task['status'] == 'pending' and not (data.output / 'T1.nii.gz').exists()
    data.service.confirm(task['id'], approval(task))
    assert finished(data, task)["status"] == "succeeded"
    assert task["scan"]["unmatched_snapshot"]
    assert (data.source / "sample_T1w.nii.gz").read_bytes() == b"image"
    assert (data.output / "T1.nii.gz").read_bytes() == b"image"


def test_delete_scope_changes_traversal_and_active_paths_cannot_use_output_default(data):
    (data.output / "a.txt").write_text("a")
    task = data.service.propose(DataPlanRequest(**output_scope(data), edits=[DataEdit(action="delete", path="a.txt")]))
    assert task["status"] == "pending" and task["requires_delete_confirmation"]
    with pytest.raises(PermissionError):
        data.service.confirm(task["id"], approval(task))
    for path in ("../escape.txt", str(data.source / "escape.txt"), ".env"):
        with pytest.raises(PermissionError):
            data.service.propose(DataPlanRequest(**output_scope(data), edits=[DataEdit(action="write_text", path=path, content="x")]))
    data.workspace.bind_conversation(data.scope["conversation_id"], None)
    with pytest.raises(PermissionError):
        data.service.propose(DataPlanRequest(**output_scope(data), edits=[DataEdit(action="write_text", path="new.txt", content="x")]))
    data.workspace.bind_conversation(data.scope["conversation_id"], data.ws.id)
    data.service.protected_paths = lambda: [data.output]
    pending = data.service.propose(DataPlanRequest(**output_scope(data), edits=[DataEdit(action='write_text', path='new.txt', content='x')]))
    with pytest.raises(PermissionError):
        data.service.confirm(pending['id'], approval(pending))
    assert (data.output / "a.txt").read_text() == "a"


def test_output_tools_route_files_to_recoverable_batches_and_keep_overwrite_delete_preview(data):
    (data.output / "a.txt").write_text("a")
    tools = WorkspaceTools(data.workspace, SimpleNamespace())
    tools.data_operations = data.service
    request = ChatRequest(conversation_id=data.scope["conversation_id"], workspace_id=data.ws.id,
                          message="copy", interaction_mode="task", response_policy=ResponsePolicy(action="operate"))
    copied = tools.execute("copy_output_path", {"path": "a.txt", "destination": "b.txt"}, request)
    assert copied['kind'] == 'data' and copied['execution_policy'] == 'manual' and copied['status'] == 'pending'
    assert not (data.output / 'b.txt').exists()
    data.service.confirm(copied['id'], approval(copied))
    assert finished(data, copied)["status"] == "succeeded"
    moved = tools.execute("move_output_path", {"path": "b.txt", "destination": "c.txt"}, request)
    assert moved['status'] == 'pending' and (data.output / 'b.txt').exists()
    data.service.confirm(moved['id'], approval(moved))
    assert finished(data, moved)["status"] == "succeeded"
    data.service.undo(moved["id"], approval(moved))
    assert finished(data, moved)["status"] == "rolled_back" and (data.output / "b.txt").exists()
    data.service.undo(copied["id"], approval(copied))
    assert finished(data, copied)["status"] == "rolled_back" and not (data.output / "b.txt").exists()
    pending = tools.execute("copy_output_path", {"path": "a.txt", "destination": "b.txt", "preview_only": True}, request)
    assert pending["status"] == "pending" and not (data.output / "b.txt").exists()
    overwritten = tools.execute("copy_output_path", {"path": "a.txt", "destination": "a2.txt", "overwrite": True}, request)
    assert overwritten["status"] == "pending" and not (data.output / "a2.txt").exists()
    deleted = tools.execute("delete_output_path", {"path": "a.txt"}, request)
    assert deleted["status"] == "pending" and (data.output / "a.txt").exists()
    request.response_policy = ResponsePolicy(action="inspect")
    with pytest.raises(PermissionError):
        tools.execute("propose_data_changes", {"source_scope": "output", "mode": "inplace", "execution": "output",
            "edits": [{"action": "write_text", "path": "new.txt", "content": "new"}]}, request)


def test_new_file_publish_never_overwrites_a_target_created_after_last_check(data, monkeypatch):
    original_save = data.service._save
    introduced = []
    def racing_save(task):
        original_save(task)
        if task.get("journal") and task["journal"][-1].get("staged_stamp") and not introduced:
            (data.output / "race.txt").write_text("external")
            introduced.append(True)
    monkeypatch.setattr(data.service, "_save", racing_save)
    task = data.service.propose(DataPlanRequest(**output_scope(data),
        edits=[DataEdit(action="write_text", path="race.txt", content="new")]))
    data.service.confirm(task['id'], approval(task))
    assert finished(data, task)["status"] == "failed"
    assert (data.output / "race.txt").read_text() == "external"


@pytest.mark.parametrize("ending", ["answer_without_action", "continue_workflow_planning"])
def test_assistant_reports_submitted_writes_without_claiming_no_action_or_planning(data, ending):
    class Model:
        mode = "model"
        calls = 0

        async def workspace_completion(self, *args, **kwargs):
            self.calls += 1
            name, arguments = ("copy_output_path", {"path": "a.txt", "destination": "b.txt"}) if self.calls == 1 else (
                ending, {"answer": "复制已提交。"} if ending == "answer_without_action" else {})
            return {"role": "assistant", "tool_calls": [{"id": str(self.calls), "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)}}]}

    (data.output / "a.txt").write_text("a")
    tools = WorkspaceTools(data.workspace, Model())
    tools.data_operations = data.service
    request = ChatRequest(conversation_id=data.scope["conversation_id"], workspace_id=data.ws.id,
                          message="复制文件", interaction_mode="task", response_policy=ResponsePolicy(action="operate"))
    answer = asyncio.run(tools.reply(request))
    assert '尚未执行' in answer and '点击运行' in answer and not request.planning_handoff
    events = data.workspace.events(request.client_turn_id)
    event = next(e for e in events if e.get("data_operation"))
    assert 'operation' not in event and event['data_operation']['status'] == 'pending'
    assert event['data_operation']['id'] not in data.service.futures and not (data.output / 'b.txt').exists()


def test_mixed_completed_output_and_pending_overwrite_reports_both(data):
    (data.output / "existing.txt").write_text("old")
    new = data.service.propose(DataPlanRequest(**output_scope(data),
        edits=[DataEdit(action="write_text", path="new.txt", content="new")]))
    data.service.confirm(new['id'], approval(new))
    new = finished(data, new)
    overwrite = data.service.propose(DataPlanRequest(**output_scope(data),
        edits=[DataEdit(action="write_text", path="existing.txt", content="changed")]))
    observations = [{"tool": "propose_data_changes", "result": task} for task in [new, overwrite]]
    answer = WorkspaceTools.pending_summary(observations)
    assert '已有操作提交执行' in answer and '仍需点击运行' in answer and '尚未修改文件' not in answer
    assert (data.output / "new.txt").read_text() == "new" and (data.output / "existing.txt").read_text() == "old"


def test_directory_copy_cannot_remove_a_target_created_after_last_fingerprint(data, monkeypatch):
    from app import workspace_service
    from app.workspace_models import FileOperationRequest

    source, target = data.output / "source", data.output / "target"
    source.mkdir(); (source / "a.txt").write_text("source")
    operation = data.workspace.propose("copy", FileOperationRequest(workspace_id=data.ws.id,
        conversation_id=data.scope["conversation_id"], path="source", destination="target"))
    original = workspace_service.fingerprint
    checks = []
    def racing_fingerprint(path):
        result = original(path)
        if path == target:
            checks.append(True)
            if len(checks) == 2:
                target.mkdir(); (target / "external.txt").write_text("external")
        return result
    monkeypatch.setattr(workspace_service, "fingerprint", racing_fingerprint)
    result = data.workspace.confirm(operation["id"], data.scope["conversation_id"])
    assert result["status"] == "failed"
    assert (target / "external.txt").read_text() == "external"
    assert (source / "a.txt").read_text() == "source"
    assert not data.workspace.trash(data.ws.id)


def test_output_directory_rebinding_invalidates_pending_file_confirmation(data):
    from app.workspace_models import FileOperationRequest
    operation = data.workspace.propose("mkdir", FileOperationRequest(workspace_id=data.ws.id,
        conversation_id=data.scope["conversation_id"], path="new-directory"))
    data.workspace.bind_conversation(data.scope["conversation_id"], None)
    with pytest.raises(PermissionError):
        data.workspace.confirm(operation["id"], data.scope["conversation_id"])
    assert not (data.output / "new-directory").exists()
