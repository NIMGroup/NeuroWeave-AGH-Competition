"""End-to-end data batches and authorization boundaries, using real temp files."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.chat_service import ChatService
from app.conversation_service import ConversationService
from app.data_auth import DataAuth, Principal, password_hash, principal
from app.data_models import DataConfirmation, DataEdit, DataPlanRequest, OrganizeRequest, PythonRequest, DataScriptConfirmation
from app.data_operations import DataOperations
from app.data_routes import data_router
from app.data_sandbox import DataSandbox
from app.dataset_models import DatasetInspection, DatasetRecord
from app.local_store import LocalStore
from app.models import ChatRequest
from app.workspace_models import WorkspaceCreate
from app.workspace_service import WorkspaceService
from app.workspace_tools import WorkspaceTools


@pytest.fixture
def data(tmp_path, monkeypatch):
    source, output = tmp_path / "data", tmp_path / "output"
    source.mkdir(); output.mkdir()
    monkeypatch.setenv("WORKSPACE_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setenv("WORKSPACE_DEFAULT_ROOT", str(output))
    monkeypatch.delenv("NEUROWEAVE_DATA_USERS", raising=False)
    monkeypatch.delenv("NEUROWEAVE_DATA_PYTHON_IMAGE", raising=False)
    token = principal.set(Principal("alice", True))
    store = LocalStore(tmp_path / "state" / "db.sqlite")
    workspace = WorkspaceService(store, output)
    ws = workspace.create(WorkspaceCreate(name="output", root_path=str(output)))
    conv = ConversationService(store, ChatService()).create("data task")
    workspace.bind_conversation(conv.conversation_id, ws.id)
    store.save_dataset(DatasetRecord(dataset_id="ds_test", root_id="test", relative_path="data",
                                    display_name="test", absolute_path=str(source), inspection=DatasetInspection(scanned_at="now"),
                                    created_at="now", updated_at="now"))
    datasets = SimpleNamespace(reader=lambda identity: SimpleNamespace(root=lambda: source))
    operations = DataOperations(workspace, datasets)
    scope = dict(conversation_id=conv.conversation_id, dataset_id="ds_test", workspace_id=ws.id, source_scope="dataset")
    value = SimpleNamespace(service=operations, source=source, output=output, scope=scope, workspace=workspace, ws=ws)
    principal.set(Principal("alice", True, ("ds_test",), (ws.id,)))
    yield value
    principal.reset(token)
    value.service.shutdown()


def approval(task, *, inplace=False, deletion=False):
    return DataConfirmation(confirmed=True, conversation_id=task["conversation_id"], manifest_hash=task["manifest_hash"],
                            allow_inplace=inplace, allow_delete=deletion)


def finished(data, task):
    data.service.futures[task["id"]].result(timeout=60)
    return data.service.get(task["id"], task["conversation_id"])


def script_approval(task):
    return DataScriptConfirmation(confirmed=True, conversation_id=task['conversation_id'], script_plan_hash=task['script_plan_hash'])


def run_script(data, task):
    data.service.start_python(task['id'], script_approval(task))
    return finished(data, task)


def test_full_scan_copy_preserves_families_and_numbers_duplicate_acquisitions(data):
    for i in range(25):
        subject = data.source / f"subject-{i:02}"
        subject.mkdir()
        for stem in ("long_T1w_801", "long_T1w_802", "long_dwi_901"):
            for suffix in (".nii.gz", ".json"):
                (subject / (stem + suffix)).write_bytes((stem + suffix).encode())
        for suffix in (".bval", ".bvec"):
            (subject / ("long_dwi_901" + suffix)).write_bytes(b"gradient")
    task = data.service.organize(OrganizeRequest(**data.scope, mode="copy",
        rules=[{"pattern": "*T1w*.nii.gz", "name": "T1"}, {"pattern": "*dwi*.nii.gz", "name": "dwi"}]))
    assert task["file_count"] == 200 and task["scan"]["complete_scan"]
    assert task["preview_truncated"] and task["can_confirm"]
    assert list(data.output.iterdir()) == []
    data.service.confirm(task["id"], approval(task))
    done = finished(data, task)
    assert done["status"] == "succeeded", done
    assert done["completed"] == 200
    for i in range(25):
        assert (data.source / f"subject-{i:02}" / "long_T1w_801.nii.gz").exists()
        assert (data.output / f"subject-{i:02}" / "T1_run-01.json").read_bytes() == b"long_T1w_801.json"
        assert (data.output / f"subject-{i:02}" / "dwi.bvec").read_bytes() == b"gradient"
    assert data.service.confirm(task["id"], approval(task))["status"] == "succeeded"
    data.service.undo(task["id"], approval(task))
    assert finished(data, task)["status"] == "rolled_back"
    assert not list(data.output.rglob("*.nii.gz"))


def test_inplace_requires_authenticated_actor_exact_grant_and_keeps_legacy_dataset_readonly(data):
    (data.source / "old.nii.gz").write_bytes(b"image")
    task = data.service.propose(DataPlanRequest(**data.scope, mode="inplace",
        edits=[DataEdit(action="rename", path="old.nii.gz", destination="T1.nii.gz")]))
    with pytest.raises(PermissionError):
        data.service.confirm(task["id"], approval(task))
    handle = principal.set(Principal("alice", False))
    try:
        with pytest.raises(PermissionError):
            data.service.confirm(task["id"], approval(task, inplace=True))
    finally:
        principal.reset(handle)
    bad = approval(task, inplace=True).model_copy(update={"manifest_hash": "wrong"})
    with pytest.raises(PermissionError):
        data.service.confirm(task["id"], bad)
    data.service.confirm(task["id"], approval(task, inplace=True))
    assert finished(data, task)["status"] == "succeeded"
    assert (data.source / "T1.nii.gz").read_bytes() == b"image"
    data.service.undo(task["id"], approval(task, inplace=True))
    assert finished(data, task)["status"] == "rolled_back"
    assert (data.source / "old.nii.gz").read_bytes() == b"image"


def test_content_version_restore_and_changed_output_prevents_undo(data):
    original = '{"age": 20}\n'
    (data.source / "meta.json").write_text(original)
    task = data.service.propose(DataPlanRequest(**data.scope, mode="inplace",
        edits=[DataEdit(action="write_text", path="meta.json", content='{"age": 21}\n')]))
    assert "-{\"age\": 20}" in task["items"][0]["diff"]
    data.service.confirm(task["id"], approval(task, inplace=True))
    assert finished(data, task)["status"] == "succeeded"
    data.service.undo(task["id"], approval(task, inplace=True))
    assert finished(data, task)["status"] == "rolled_back"
    assert (data.source / "meta.json").read_text() == original
    task = data.service.propose(DataPlanRequest(**data.scope, mode="inplace",
        edits=[DataEdit(action="write_text", path="meta.json", content='{"age": 22}')]))
    data.service.confirm(task["id"], approval(task, inplace=True))
    finished(data, task)
    (data.source / "meta.json").write_text('{"age": 99}')
    data.service.undo(task["id"], approval(task, inplace=True))
    assert finished(data, task)["status"] == "failed"
    assert (data.source / "meta.json").read_text() == '{"age": 99}'


def test_conflicts_stale_preview_scope_injection_and_busy_paths(data):
    (data.source / "a.txt").write_text("a")
    (data.output / "a.txt").write_text("existing")
    task = data.service.propose(DataPlanRequest(**data.scope, edits=[DataEdit(action="copy", path="a.txt", destination="a.txt")]))
    assert task["conflicts"] and not task["can_confirm"]
    with pytest.raises(ValueError):
        data.service.confirm(task["id"], approval(task))
    for path in ("../outside", str(data.source / "a.txt"), ".env"):
        with pytest.raises(PermissionError):
            data.service.propose(DataPlanRequest(**data.scope, mode="inplace",
                edits=[DataEdit(action="write_text", path=path, content="x")]))
    task = data.service.propose(DataPlanRequest(**data.scope, mode="inplace",
        edits=[DataEdit(action="rename", path="a.txt", destination="b.txt")]))
    (data.source / "a.txt").write_text("changed")
    with pytest.raises(ValueError):
        data.service.confirm(task["id"], approval(task, inplace=True))
    task = data.service.propose(DataPlanRequest(**data.scope, mode="inplace",
        edits=[DataEdit(action="rename", path="a.txt", destination="b.txt")]))
    data.service.protected_paths = lambda: [data.source]
    with pytest.raises(PermissionError):
        data.service.confirm(task["id"], approval(task, inplace=True))
    assert not (data.source / "b.txt").exists()


def test_partial_failure_journal_restores_only_applied_items(data, monkeypatch):
    from app import workspace_io
    (data.source / "a.txt").write_text("a")
    (data.source / "b.txt").write_text("b")
    task = data.service.propose(DataPlanRequest(**data.scope, mode="inplace", edits=[
        DataEdit(action="rename", path="a.txt", destination="aa.txt"), DataEdit(action="rename", path="b.txt", destination="bb.txt")]))
    rename = workspace_io.rename
    def fail_second(src, dst):
        if src.name == "b.txt":
            raise OSError("disk fault")
        rename(src, dst)
    monkeypatch.setattr(workspace_io, "rename", fail_second)
    data.service.confirm(task["id"], approval(task, inplace=True))
    assert finished(data, task)["status"] == "failed"
    assert (data.source / "aa.txt").exists() and (data.source / "b.txt").exists()
    monkeypatch.setattr(workspace_io, "rename", rename)
    data.service.undo(task["id"], approval(task, inplace=True))
    assert finished(data, task)["status"] == "rolled_back"
    assert (data.source / "a.txt").read_text() == "a"
    assert (data.source / "b.txt").read_text() == "b"


def test_delete_separate_grant_cancel_and_owner_checks(data):
    (data.source / "a.txt").write_text("a")
    task = data.service.propose(DataPlanRequest(**data.scope, mode="inplace", edits=[DataEdit(action="delete", path="a.txt")]))
    with pytest.raises(PermissionError):
        data.service.confirm(task["id"], approval(task, inplace=True))
    other = principal.set(Principal("bob", True))
    try:
        with pytest.raises(PermissionError):
            data.service.confirm(task["id"], approval(task, inplace=True, deletion=True))
    finally:
        principal.reset(other)
    data.service.confirm(task["id"], approval(task, inplace=True, deletion=True))
    assert finished(data, task)["status"] == "succeeded"
    assert not (data.source / "a.txt").exists()
    data.service.undo(task["id"], approval(task, inplace=True, deletion=True))
    assert finished(data, task)["status"] == "rolled_back"
    assert (data.source / "a.txt").read_text() == "a"
    task = data.service.propose(DataPlanRequest(**data.scope, mode="inplace", edits=[DataEdit(action="delete", path="a.txt")]))
    assert data.service.cancel(task["id"], task["conversation_id"])["status"] == "cancelled"
    assert data.service.confirm(task["id"], approval(task, inplace=True, deletion=True))["status"] == "cancelled"
    assert (data.source / "a.txt").exists()


def test_python_artifacts_need_preview_and_restore_and_input_stability(data):
    (data.source / "table.csv").write_text("id,age\n1,20\n")
    def generated(source, directory, code):
        directory.mkdir()
        artifacts = directory / "artifacts"; artifacts.mkdir()
        (artifacts / "table.csv").write_text("id,age\n1,21\n")
        return artifacts
    data.service.sandbox.run = generated
    data.service.sandbox.image = 'sha256:' + 'a' * 64
    request = PythonRequest(**data.scope, mode="inplace", code="# Transform table\nprint('prepared')")
    task = data.service.python(request)
    assert task['status'] == 'pending' and task['can_run_script'] and task['id'] not in data.service.futures
    task = run_script(data, task)
    assert (data.source / "table.csv").read_text() == "id,age\n1,20\n"
    data.service.confirm(task["id"], approval(task, inplace=True))
    assert finished(data, task)["status"] == "succeeded"
    assert (data.source / "table.csv").read_text() == "id,age\n1,21\n"
    data.service.undo(task["id"], approval(task, inplace=True))
    assert finished(data, task)["status"] == "rolled_back"
    assert (data.source / "table.csv").read_text() == "id,age\n1,20\n"
    def changed(source, directory, code):
        result = generated(source, directory, code)
        (data.source / "table.csv").write_text("id,age\n1,99\n")
        return result
    data.service.sandbox.run = changed
    failed = run_script(data, data.service.python(request))
    assert failed['status'] == 'failed' and '输入清单变化' in failed['error']


def test_sessions_acl_and_http_only_confirmation(data, monkeypatch):
    monkeypatch.setenv("NEUROWEAVE_DATA_USERS", json.dumps({"alice": {
        "password_hash": password_hash("test-password"), "datasets": ["ds_test"], "outputs": [data.ws.id]}}))
    auth = DataAuth(data.service.store)
    app = FastAPI()
    auth.install(app)
    app.include_router(auth.router())
    app.include_router(data_router(data.service))
    with TestClient(app) as client:
        assert client.get("/api/data-operations", params={"conversation_id": data.scope["conversation_id"]}).status_code == 401
        assert client.post("/api/auth/login", json={"username": "alice", "password": "bad"}).status_code == 403
        assert client.post("/api/auth/login", json={"username": "alice", "password": "test-password"}).status_code == 200
        assert client.get("/api/auth/session").json()["inplace_available"]
        assert client.post("/api/data-operations/preview", headers={"origin": "http://attacker"}, json={}).status_code == 403
        assert client.post("/api/data-operations/preview", json={**data.scope, "dataset_id": "ds_unauthorized",
            "mode": "inplace", "edits": [{"action": "write_text", "path": "a.txt", "content": "x"}]}).status_code == 403
        complete_text = "".join(f"row-{i}\n" for i in range(200))
        proposal = client.post("/api/data-operations/preview", json={**data.scope,
            "mode": "inplace", "edits": [{"action": "write_text", "path": "a.txt", "content": complete_text}]}).json()
        assert proposal["items"][0]["has_artifact"]
        response = client.get("/api/data-operations/" + proposal["id"] + "/artifacts/0",
                              params={"conversation_id": data.scope["conversation_id"]})
        assert response.status_code == 200 and response.content.decode() == complete_text
        assert not (data.source / "a.txt").exists()
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/data-operations", params={"conversation_id": data.scope["conversation_id"]}).status_code == 401


def test_assistant_proposes_without_confirmation_tool_or_scientific_planner(data):
    (data.source / "long_T1w.nii.gz").write_bytes(b"image")
    class Model:
        mode = "model"
        async def workspace_completion(self, messages, tools, required=False):
            names = {t["function"]["name"] for t in tools}
            assert not any("confirm" in name or "authorize" in name for name in names)
            if required:
                return {"role": "assistant", "tool_calls": [{"id": "one", "type": "function", "function": {
                    "name": "propose_data_organization", "arguments": json.dumps({"mode": "copy", "rules": [{"pattern": "*T1w*.nii.gz", "name": "T1"}]})}}]}
            return {"role": "assistant", "content": "done"}
    tools = WorkspaceTools(data.workspace, Model(), data.service.datasets)
    tools.data_operations = data.service
    request = ChatRequest(message="按序列改名放到输出目录", interaction_mode="task", **{k: v for k, v in data.scope.items() if k != "source_scope"})
    answer = asyncio.run(tools.reply(request))
    assert '尚未执行' in answer and '点击运行' in answer and not request.planning_handoff
    assert not list(data.output.iterdir())
    events = data.workspace.events(request.client_turn_id)
    assert any(e.get("data_operation", {}).get("kind") == "data" for e in events)
    names = {t["function"]["name"] for t in tools.available_tools(request.model_copy(update={"interaction_mode": "chat"}))}
    assert "propose_data_changes" not in names and "inventory_data_files" in names


def test_missing_sandbox_never_executes_host_python(tmp_path, monkeypatch):
    monkeypatch.delenv("NEUROWEAVE_DATA_PYTHON_IMAGE", raising=False)
    from unittest.mock import patch
    with patch("app.data_sandbox.subprocess.run") as run:
        with pytest.raises(ValueError):
            DataSandbox().run(tmp_path, tmp_path / "sandbox", "raise RuntimeError()")
        run.assert_not_called()


def test_sandbox_archive_rejects_escape_links_and_oversized_members(tmp_path):
    import io
    import tarfile
    from app.data_sandbox import extract_outputs
    directory = tmp_path / "artifacts"; directory.mkdir()
    archive = tmp_path / "output.tar"
    for name, kind, size in [("../outside", tarfile.REGTYPE, 1), ("/outside", tarfile.REGTYPE, 1),
                            ("nested/link", tarfile.SYMTYPE, 0), ("nested/pipe", tarfile.FIFOTYPE, 0),
                            ("C:outside", tarfile.REGTYPE, 1), ("huge", tarfile.REGTYPE, 513 * 1024 * 1024)]:
        member = tarfile.TarInfo(name); member.type = kind; member.size = size
        if size > 1024:
            archive.write_bytes(member.tobuf() + b"\0" * 1024)
        else:
            with tarfile.open(archive, "w") as output:
                output.addfile(member, io.BytesIO(b"x" * size) if kind == tarfile.REGTYPE else None)
        with pytest.raises((PermissionError, ValueError)):
            extract_outputs(archive, directory)
    assert not list(directory.iterdir())
    with tarfile.open(archive, "w") as output:
        member = tarfile.TarInfo("nested/table.csv"); member.size = 4
        output.addfile(member, io.BytesIO(b"test"))
    extract_outputs(archive, directory)
    assert (directory / "nested/table.csv").read_bytes() == b"test"


def test_root_replacement_and_readonly_analysis_without_output(data):
    (data.source / "a.txt").write_text("a")
    task = data.service.propose(DataPlanRequest(**data.scope, mode="inplace",
        edits=[DataEdit(action="rename", path="a.txt", destination="b.txt")]))
    moved = data.source.with_name("old-data")
    data.source.rename(moved)
    data.source.mkdir()
    (moved / "a.txt").rename(data.source / "a.txt")
    with pytest.raises(PermissionError, match="登记目录"):
        data.service.confirm(task["id"], approval(task, inplace=True))
    def analyze(source, directory, code):
        directory.mkdir()
        (directory / "execution.log").write_text("rows=25\n")
        artifacts = directory / "artifacts"; artifacts.mkdir()
        return artifacts
    data.service.sandbox.run = analyze
    data.service.sandbox.image = 'sha256:' + 'a' * 64
    result = data.service.python(PythonRequest(**{**data.scope, "workspace_id": None},
                                               mode="analysis", code="print('rows=25')"))
    result = run_script(data, result)
    assert result["status"] == "analyzed" and result["execution_log"] == "rows=25\n"
    assert not result["can_confirm"] and not result["can_undo"]
    assert (data.source / "a.txt").read_text() == "a"


def test_revoke_during_staging_and_conflicting_batch_reservation(data, monkeypatch):
    from threading import Event
    from app import workspace_io
    (data.source / "a.txt").write_text("a")
    first = data.service.propose(DataPlanRequest(**data.scope,
        edits=[DataEdit(action="copy", path="a.txt", destination="nested/a.txt")]))
    second = data.service.propose(DataPlanRequest(**data.scope, mode="inplace",
        edits=[DataEdit(action="rename", path="a.txt", destination="b.txt")]))
    started, release = Event(), Event()
    copy = workspace_io.copy
    def blocked(src, dst, policy, *args):
        copy(src, dst, policy, *args)
        if dst.name.startswith(".neuroweave-data-"):
            started.set()
            assert release.wait(10)
    monkeypatch.setattr(workspace_io, "copy", blocked)
    data.service.confirm(first["id"], approval(first))
    try:
        assert started.wait(10)
        with pytest.raises(PermissionError, match="占用"):
            data.service.confirm(second["id"], approval(second, inplace=True))
        data.service.cancel(first["id"], first["conversation_id"])
    finally:
        release.set()
    assert finished(data, first)["status"] == "cancelled"
    assert not (data.output / "nested/a.txt").exists()
    data.service.undo(first["id"], approval(first))
    assert finished(data, first)["status"] == "rolled_back"
    assert not list(data.output.iterdir())


def test_restart_after_publish_before_journal_commit_and_repeated_undo(data, monkeypatch):
    (data.source / "meta.json").write_text('{"age":20}')
    task = data.service.propose(DataPlanRequest(**data.scope, mode="inplace",
        edits=[DataEdit(action="write_text", path="meta.json", content='{"age":21}')]))
    save = data.service._save
    fault = {"commit": True, "undo": False}
    def crash(record):
        if fault["commit"] and record["journal"] and record["journal"][-1]["state"] == "applied":
            fault["commit"] = False
            raise OSError("crash after publish")
        if fault["undo"] and record["journal"] and record["journal"][-1]["state"] == "undone":
            fault["undo"] = False
            raise OSError("crash after restore")
        save(record)
    monkeypatch.setattr(data.service, "_save", crash)
    data.service.confirm(task["id"], approval(task, inplace=True))
    assert finished(data, task)["status"] == "failed"
    assert (data.source / "meta.json").read_text() == '{"age":21}'
    # An unclean service restart retains the intent and freezes the task.
    record = data.service._raw(task["id"])
    record["status"] = "running"; save(record)
    data.service.shutdown()
    data.service = DataOperations(data.workspace, data.service.datasets)
    assert data.service.get(task["id"], task["conversation_id"])["status"] == "interrupted"
    fault["undo"] = True
    save = data.service._save
    monkeypatch.setattr(data.service, "_save", crash)
    data.service.undo(task["id"], approval(task, inplace=True))
    assert finished(data, task)["status"] == "failed"
    assert (data.source / "meta.json").read_text() == '{"age":20}'
    data.service.undo(task["id"], approval(task, inplace=True))
    assert finished(data, task)["status"] == "rolled_back"
    assert (data.source / "meta.json").read_text() == '{"age":20}'


def test_scope_guards_for_conversations_tool_events_background_checks_and_runs(data):
    from app.operation_service import OperationService
    from app.run_models import WorkflowRun
    store = data.service.store
    conversation = data.scope["conversation_id"]
    store.update_conversation(conversation, archived=True, updated_at="later")
    data.workspace.event("test-turn", {"tool": "inventory", "status": "succeeded"})
    checks = OperationService(store)
    operation = checks.submit("scan", "ds_test", lambda: {"private": True})
    with store.transaction() as db:
        db.execute("INSERT INTO workflows (workflow_id,dataset_id,status,revision,payload_json,created_at,updated_at) "
                   "VALUES (?,?,'confirmed',1,?,'now','now')",
                   ("wf_private", "ds_test", json.dumps({**data.scope, "title": "Private task"})))
    run = WorkflowRun(run_id="run_private", workflow_id="wf_private", workflow_revision=1,
        output_directory=str(data.output), created_at="now", updated_at="now",
        plan_snapshot={**data.scope})
    store.save_run(run)
    other = principal.set(Principal("bob", True, ("ds_test",), (data.ws.id,)))
    try:
        for call in (lambda: store.delete_archived_conversation(conversation),
                     lambda: store.conversation_messages(conversation),
                     lambda: data.workspace.events("test-turn"),
                     lambda: checks.get(operation["operation_id"]),
                     lambda: checks.cancel(operation["operation_id"]),
                     lambda: store.get_run("run_private"),
                     lambda: store.monitor_run("run_private"),
                     lambda: store.run_event_page("run_private")):
            with pytest.raises(PermissionError):
                call()
        assert store.list_runs() == []
        assert store.list_conversations() == []
        assert store.workflow_index()["items"] == []
    finally:
        principal.reset(other)
        checks.shutdown(wait=True)
    assert store.get_conversation(conversation)
    assert store.get_run("run_private").run_id == "run_private"


def test_general_answer_does_not_create_plan_or_read_files(data):
    from unittest.mock import Mock
    from app.chat_coordinator import ChatCoordinator
    class Model:
        mode = "model"
        provider_name = "test"
        model = "test-model"
        async def workspace_completion(self, *args, **kwargs):
            return {"role": "assistant", "tool_calls": [{"id": "answer", "type": "function", "function": {
                "name": "answer_without_action", "arguments": json.dumps({"answer": "可以先讨论目标，再选择工具。"})}}]}
    model = Model()
    tools = WorkspaceTools(data.workspace, model, data.service.datasets)
    tools.data_operations = data.service
    conversation_service = ConversationService(data.service.store, ChatService())
    skills = SimpleNamespace(find=lambda name: None, infer=lambda text: [])
    workflows = Mock()
    coordinator = ChatCoordinator(conversation_service, data.workspace, data.service.datasets, tools, skills, workflows, model)
    # No dataset and no output selection: general questions still use the assistant.
    request = ChatRequest(message="你能帮我做什么", interaction_mode="task")
    from app.chat_turns import ChatTurns
    turns = ChatTurns(data.service.store, conversation_service, coordinator.reply)
    async def complete():
        accepted = turns.submit(request)
        return await turns.wait(accepted["turn_id"])
    response = asyncio.run(complete())
    assert response.workflow is None and response.content == "可以先讨论目标，再选择工具。"
    workflows.handle_chat.assert_not_called()
    workflows.assess_task.assert_not_called()


def test_tool_failures_cannot_implicitly_trigger_scientific_planning(data):
    tools = WorkspaceTools(data.workspace, SimpleNamespace(), data.service.datasets)
    tools.data_operations = data.service
    request = ChatRequest(message="整理数据", interaction_mode="task")
    answer = tools.handoff(request, [{"result": {"error": "container unavailable"}}], "lookup_failed")
    assert "container unavailable" in answer and not request.planning_handoff


def test_restart_before_first_write_can_release_empty_reservation(data):
    (data.source / "a.txt").write_text("a")
    task = data.service.propose(DataPlanRequest(**data.scope, mode="inplace",
        edits=[DataEdit(action="rename", path="a.txt", destination="b.txt")]))
    record = data.service._raw(task["id"])
    record["status"] = "running"
    data.service._save(record)
    data.service.shutdown()
    data.service = DataOperations(data.workspace, data.service.datasets)
    interrupted = data.service.get(task["id"], task["conversation_id"])
    assert interrupted["status"] == "interrupted" and not interrupted["can_undo"]
    missing = data.source.with_name("temporarily-unmounted")
    data.source.rename(missing)
    assert data.service.cancel(task["id"], task["conversation_id"])["status"] == "cancelled"
    missing.rename(data.source)
    data.service._guard(data.source)
    assert (data.source / "a.txt").read_text() == "a" and not (data.source / "b.txt").exists()


def test_configured_container_keeps_input_readonly_and_exports_tmpfs(tmp_path):
    import os
    image = os.getenv("NEUROWEAVE_DATA_PYTHON_TEST_IMAGE")
    if not image:
        pytest.skip("Set NEUROWEAVE_DATA_PYTHON_TEST_IMAGE to an existing local image for Docker integration")
    source = tmp_path / "input"; source.mkdir(); source.chmod(0o755)
    (source / "table.csv").write_text("age\n20\n")
    (source / "table.csv").chmod(0o644)
    sandbox = DataSandbox(); sandbox.image = image
    artifacts = sandbox.run(source, tmp_path / "sandbox", """
from pathlib import Path
import socket
try:
    Path('/input/table.csv').write_text('changed')
except OSError:
    pass
else:
    raise AssertionError('Input mount must be readonly')
connection = socket.socket()
connection.settimeout(0.5)
try:
    connection.connect(('1.1.1.1', 53))
except OSError:
    pass
else:
    raise AssertionError('Network must be disabled')
finally:
    connection.close()
Path('/output/nested').mkdir()
Path('/output/nested/result.csv').write_text('age\\n21\\n')
print('rows=1')
""")
    assert (artifacts / "nested/result.csv").read_text() == "age\n21\n"
    assert (source / "table.csv").read_text() == "age\n20\n"


def test_followup_about_pending_batch_is_answered_and_unmatched_inventory_is_frozen(data):
    (data.source / "sample_T1w.nii.gz").write_bytes(b"image")
    (data.source / "fieldmap.nii.gz").write_bytes(b"field")
    (data.source / "fieldmap.json").write_text("{}")
    task = data.service.organize(OrganizeRequest(**data.scope, rules=[{"pattern": "*T1w.nii.gz", "name": "T1"}]))
    # Files added after preview must not be silently folded into its snapshot.
    (data.source / "later.txt").write_text("later")
    frozen = data.service.operation_files(task["id"], task["conversation_id"], limit=1)
    assert frozen["snapshot"] and frozen["total"] == 2 and not frozen["included_in_batch"]
    assert frozen["extension_counts"] == {".nii.gz": 1, ".json": 1}
    assert not frozen["page_complete"] and frozen["next_offset"] == 1
    class Model:
        mode = "model"
        rounds = 0
        async def workspace_completion(self, messages, tools, required=False):
            self.rounds += 1
            if self.rounds <= 2:
                name, args = ("get_data_operation", {"id": task["id"]}) if self.rounds == 1 else (
                    "list_data_operation_files", {"id": task["id"], "limit": 50})
                return {"role": "assistant", "tool_calls": [{"id": str(self.rounds), "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)}}]}
            result = json.loads(messages[-1]["content"])
            assert result["total"] == 2 and result["snapshot"]
            return {"role": "assistant", "content": "不会复制。未匹配 2 个文件：fieldmap.nii.gz 和 fieldmap.json。"}
    request = ChatRequest(**{k: data.scope[k] for k in ("conversation_id", "workspace_id", "dataset_id")},
                          message="未匹配的文件会复制过去吗，有哪些", interaction_mode="task")
    tools = WorkspaceTools(data.workspace, Model()); tools.data_operations = data.service
    answer = asyncio.run(tools.reply(request))
    assert "不会复制" in answer and "fieldmap.json" in answer and "请检查批量预览" not in answer
    events = data.workspace.events(request.client_turn_id)
    assert [e for e in events if e["tool"] == "get_data_operation"][-1]["status"] == "succeeded"
    assert data.service.get(task["id"], task["conversation_id"])["status"] == "pending"
    assert list(data.output.iterdir()) == []


def test_legacy_unmatched_scan_is_explicitly_current_and_http_download_is_owner_scoped(data):
    (data.source / "sample_T1w.nii.gz").write_bytes(b"image")
    (data.source / "scout.json").write_text("{}")
    task = data.service.organize(OrganizeRequest(**data.scope, rules=[{"pattern": "*T1w.nii.gz", "name": "T1"}]))
    with data.service.store.transaction() as db:
        db.execute("DELETE FROM data_operation_inventories WHERE id=?", (task["id"],))
    (data.source / "later.txt").write_text("new")
    app = FastAPI(); app.include_router(data_router(data.service))
    with TestClient(app) as client:
        path = "/api/data-operations/" + task["id"] + "/files"
        params = {"conversation_id": task["conversation_id"], "download": True}
        result = client.get(path, params=params)
        assert result.status_code == 200
        body = result.json()
        assert not body["snapshot"] and body["total"] == 2 and body["page_complete"]
        assert {r["path"] for r in body["items"]} == {"later.txt", "scout.json"}
        assert client.get(path, params={**params, "conversation_id": "wrong"}).status_code == 403
        assert client.get(path, params={**params, "limit": 201}).status_code == 422


@pytest.mark.skipif(__import__("os").name == "nt", reason="Linux anchored filesystem fallback")
def test_nfs_exclusive_publish_and_interrupted_hardlink_rename_can_be_restored(data, monkeypatch):
    import ctypes, errno, os
    from app import workspace_io
    class Unsupported:
        def __call__(self, *args):
            ctypes.set_errno(errno.EINVAL)
            return -1
    monkeypatch.setattr(workspace_io.ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(renameat2=Unsupported()))
    (data.source / "copy.txt").write_text("copy")
    copied = data.service.propose(DataPlanRequest(**data.scope, edits=[
        DataEdit(action="copy", path="copy.txt", destination="new.txt")]))
    data.service.confirm(copied["id"], approval(copied))
    assert finished(data, copied)["status"] == "succeeded"
    assert (data.output / "new.txt").read_text() == "copy"
    (data.output / "occupied.txt").write_text("keep")
    with pytest.raises(FileExistsError):
        workspace_io.rename(data.source / "copy.txt", data.output / "occupied.txt")
    assert (data.source / "copy.txt").exists() and (data.output / "occupied.txt").read_text() == "keep"
    task = data.service.propose(DataPlanRequest(**data.scope, mode="inplace", edits=[
        DataEdit(action="rename", path="copy.txt", destination="renamed.txt")]))
    unlink = workspace_io.os.unlink
    def interrupted(name, *args, **kwargs):
        if str(name) == "copy.txt":
            raise OSError("interrupted after exclusive link")
        return unlink(name, *args, **kwargs)
    monkeypatch.setattr(workspace_io.os, "unlink", interrupted)
    data.service.confirm(task["id"], approval(task, inplace=True))
    assert finished(data, task)["status"] == "failed"
    assert os.path.samefile(data.source / "copy.txt", data.source / "renamed.txt")
    monkeypatch.setattr(workspace_io.os, "unlink", unlink)
    data.service.undo(task["id"], approval(task, inplace=True))
    assert finished(data, task)["status"] == "rolled_back"
    assert (data.source / "copy.txt").read_text() == "copy" and not (data.source / "renamed.txt").exists()
    again = data.service.propose(DataPlanRequest(**data.scope, mode="inplace", edits=[
        DataEdit(action="rename", path="copy.txt", destination="renamed.txt")]))
    data.service.confirm(again["id"], approval(again, inplace=True))
    assert finished(data, again)["status"] == "succeeded"
    def interrupted_undo(name, *args, **kwargs):
        if str(name) == "renamed.txt":
            raise OSError("interrupted reverse rename after link")
        return unlink(name, *args, **kwargs)
    monkeypatch.setattr(workspace_io.os, "unlink", interrupted_undo)
    data.service.undo(again["id"], approval(again, inplace=True))
    assert finished(data, again)["status"] == "failed"
    assert os.path.samefile(data.source / "copy.txt", data.source / "renamed.txt")
    monkeypatch.setattr(workspace_io.os, "unlink", unlink)
    data.service.undo(again["id"], approval(again, inplace=True))
    assert finished(data, again)["status"] == "rolled_back"
    assert (data.source / "copy.txt").read_text() == "copy" and not (data.source / "renamed.txt").exists()


def test_length_limited_tool_call_is_not_executed_and_retry_answer_is_complete(data):
    class Model:
        mode = "model"
        rounds = 0
        async def workspace_completion(self, messages, tools, required=False):
            self.rounds += 1
            if self.rounds == 1:
                return {"role": "assistant", "finish_reason": "length", "tool_calls": [{
                    "id": "partial", "type": "function", "function": {
                        "name": "propose_data_changes", "arguments": '{"edits":[{"action":"delete"'}}]}
            assert "长度上限" in messages[0]["content"]
            return {"role": "assistant", "tool_calls": [{"id": "answer", "type": "function",
                "function": {"name": "answer_without_action", "arguments": '{"answer":"完整的简短回答。"}'}}]}
    tools = WorkspaceTools(data.workspace, Model()); tools.data_operations = data.service
    request = ChatRequest(conversation_id=data.scope["conversation_id"], message="解释一下", interaction_mode="task")
    answer = asyncio.run(tools.reply(request))
    assert "完整的简短回答" in answer
    assert data.service.list(request.conversation_id) == []


@pytest.mark.skipif(not __import__("os").getenv("NEUROWEAVE_DATA_NFS_TEST_ROOT"), reason="Set a private NFS validation root")
def test_real_nfs_input_output_with_local_database_can_copy_and_restore(data, monkeypatch):
    import os, tempfile
    from app.workspace_paths import WorkspacePaths
    shared = Path(os.environ["NEUROWEAVE_DATA_NFS_TEST_ROOT"]).resolve()
    assert shared.is_dir()
    with tempfile.TemporaryDirectory(prefix="nw-files-", dir=shared) as name:
        root = Path(name); source = root / "input"; output = root / "output"
        source.mkdir(); output.mkdir()
        (source / "sample.nii.gz").write_bytes(b"synthetic imaging bytes")
        token = principal.set(Principal("alice", True))
        try:
            data.workspace.paths = WorkspacePaths(output, allowed_roots=(root,))
            ws = data.workspace.create(WorkspaceCreate(name="NFS validation", root_path=str(output)))
            data.workspace.bind_conversation(data.scope["conversation_id"], ws.id)
            data.service.datasets = SimpleNamespace(reader=lambda identity: SimpleNamespace(root=lambda: source))
            scope = {**data.scope, "workspace_id": ws.id}
            task = data.service.propose(DataPlanRequest(**scope, edits=[
                DataEdit(action="copy", path="sample.nii.gz", destination="dwi.nii.gz")]))
            data.service.confirm(task["id"], approval(task))
            assert finished(data, task)["status"] == "succeeded"
            assert (output / "dwi.nii.gz").read_bytes() == (source / "sample.nii.gz").read_bytes()
            data.service.undo(task["id"], approval(task))
            assert finished(data, task)["status"] == "rolled_back" and not list(output.iterdir())
            assert (source / "sample.nii.gz").read_bytes() == b"synthetic imaging bytes"
        finally:
            principal.reset(token)

def test_expired_preview_does_not_offer_execution_or_modify_files(data):
    (data.source / "a.txt").write_text("a")
    task = data.service.propose(DataPlanRequest(**data.scope, edits=[
        DataEdit(action="copy", path="a.txt", destination="b.txt")]))
    stored = data.service._raw(task["id"]); stored["expires_at"] = 0
    data.service._save(stored)
    current = data.service.get(task["id"], task["conversation_id"])
    assert current["preview_expired"] and not current["can_confirm"]
    with pytest.raises(ValueError, match="过期"):
        data.service.confirm(task["id"], approval(task))
    assert list(data.output.iterdir()) == []

def test_assistant_transport_retains_finish_reason_and_has_separate_output_budget(monkeypatch):
    import httpx
    import app.chat_service as module
    monkeypatch.setenv("LOCALFLOW_LLM_BASE_URL", "https://synthetic.example")
    monkeypatch.setenv("LOCALFLOW_LLM_MODEL", "synthetic")
    monkeypatch.setenv("LOCALFLOW_LLM_MAX_TOKENS", "800")
    monkeypatch.setenv("LOCALFLOW_LLM_THINKING", "disabled")
    monkeypatch.delenv("LOCALFLOW_ASSISTANT_MAX_TOKENS", raising=False)
    def handle(request):
        assert json.loads(request.content)["max_tokens"] == 2400
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": "partial"}}]})
    client = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handle)))
    result = asyncio.run(ChatService().workspace_completion([], []))
    assert result["finish_reason"] == "length"
