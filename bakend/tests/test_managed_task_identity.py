"""Task identity is independent of a plan's physical ID or current UI surface."""

import asyncio

import pytest

from app.agent_state import AgentTasks
from app.data_auth import Principal, principal
from app.data_models import DataPlanRequest
from app.managed_tasks import ManagedTasks
from app.models import ChatRequest
from app.method_planning import MethodPlan
from app.task_contract import TaskContract
from app.task_management import TaskEdit, TaskConfirmation, edit_task, confirm_task
from test_agent_team import TeamProvider, setup
from test_data_operations import data as data
from test_goal_planner import workflow_case as workflow_case


def prepared(data):
    (data.source / "a.txt").write_text("original")
    owner, request = setup(data, TeamProvider(), "复制文件")
    ledger = AgentTasks(data.service.store)
    ledger.begin(request)
    ledger.update_goal(
        request,
        {"goal": "复制文件", "deliverable": "files", "success_criteria": ["生成副本"]},
    )
    operations = []
    for name in ("first.txt", "second.txt"):
        op = data.service.propose(
            DataPlanRequest(
                **data.scope,
                mode="copy",
                edits=[{"action": "copy", "path": "a.txt", "destination": name}],
            )
        )
        ledger.record(request, "propose_data_changes", {}, op)
        operations.append(op)
    return ledger, request, operations


def test_revised_file_plans_share_one_management_and_conversation_entity(data):
    ledger, request, ops = prepared(data)
    registry = ManagedTasks(data.service.store)
    items = registry.index()["items"]
    assert len(items) == 1 and items[0]["task_id"] == request.assistant_task["task_id"]
    assert items[0]["plan_id"] == ops[-1]["id"] and items[0]["plan_kind"] == "data"
    assert items[0]["workflow_id"] is None and items[0]["run_count"] == 0
    view = registry.get(items[0]["task_id"])
    assert view["current_plan"]["id"] == ops[-1]["id"] and len(view["versions"]) == 2
    assert {link["id"] for link in view["plan_links"]} == {o["id"] for o in ops}
    assert list(data.output.iterdir()) == []


def test_task_edit_and_clear_update_management_without_deleting_plan_records(data):
    ledger, request, ops = prepared(data)
    identity = request.assistant_task["task_id"]
    with data.service.store.transaction() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS chat_turns(id TEXT,conversation_id TEXT,status TEXT)"
        )
    edit = TaskEdit(
        task_id=identity,
        expected_revision=request.assistant_task["task_revision"],
        goal="新的目标",
        user_requirements="新的要求",
    )
    changed = edit_task(data.service.store, request.conversation_id, edit)
    registry = ManagedTasks(data.service.store)
    assert registry.index()["items"][0]["title"] == "新的目标"
    assert registry.get(identity)["entry"]["status"] == "needs_replan"
    edit.expected_revision = changed["task_revision"]
    ledger.begin(request)
    ledger.record(request,"propose_data_changes",{},ops[-1])
    confirm_task(data.service.store,request.conversation_id,TaskConfirmation(task_id=identity,
        expected_revision=edit.expected_revision,expected_plan=request.assistant_task['active_plan'],confirmed=True))
    edit_task(data.service.store, request.conversation_id, edit, clear=True)
    assert registry.index()["items"] == []
    assert registry.get(identity)["deleted"]
    assert data.service.get(ops[0]["id"], request.conversation_id)["id"] == ops[0]["id"]


def test_task_entities_and_version_refs_do_not_cross_actors(data):
    ledger, request, ops = prepared(data)
    token = principal.set(Principal("other", True, ("*",), ("*",)))
    try:
        registry = ManagedTasks(data.service.store)
        assert registry.index()["items"] == []
        with pytest.raises(KeyError):
            registry.get(request.assistant_task["task_id"])
    finally:
        principal.reset(token)


def test_bootstrap_is_idempotent_and_file_versions_survive_reopening(data):
    ledger, request, ops = prepared(data)
    first = ManagedTasks(data.service.store)
    first.bootstrap()
    second = ManagedTasks(data.service.store)
    second.bootstrap()
    assert first.get(request.assistant_task["task_id"]) == second.get(
        request.assistant_task["task_id"]
    )
    assert len(second.index()["items"]) == 1


def test_workspace_operation_status_and_execution_belong_to_the_same_task(data):
    from app.workspace_models import FileOperationRequest

    ledger, request, ops = prepared(data)
    identity = request.assistant_task["task_id"]
    operation = data.workspace.propose(
        "mkdir",
        FileOperationRequest(
            workspace_id=data.ws.id,
            path="new-folder",
            conversation_id=request.conversation_id,
        ),
    )
    ledger.record(request, "propose_directory", {}, operation)
    ledger.save(request, {**request.assistant_task, "phase": "awaiting_run"})
    registry = ManagedTasks(data.service.store)
    before = registry.get(identity)
    assert (
        before["current_plan"]["kind"] == "workspace"
        and before["entry"]["run_count"] == 0
    )
    assert not (data.output / "new-folder").exists()
    # Simulate the explicit Run action in this isolated test workspace.
    data.workspace.confirm(operation["id"], request.conversation_id)
    after = registry.get(identity)
    assert (
        after["entry"]["task_id"] == identity
        and after["entry"]["status"] == "verifying"
    )
    assert after["entry"]["run_count"] == 1 and len(registry.index()["items"]) == 1
    assert len(after["plan_links"]) == 3


def test_workflow_configuration_revision_keeps_entity_identity(workflow_case):
    case = workflow_case
    req = ChatRequest(
        message="计算FOD",
        dataset_id=case.dataset.dataset_id,
        task_contract=TaskContract(goal_artifact_types=["fod_image"]),
    )
    req._input_selection = "user"
    req._registered_plan = MethodPlan(
        status="matched", steps=[{"id": "fod", "method_id": "mrtrix.dwi2fod"}]
    )
    wf = asyncio.run(case.service.draft(req, {"recommend_workflow": True}))[0]
    identity = wf.managed_task_id
    wf = case.service.lock_chain(wf.workflow_id, wf.revision)
    wf = case.service.update_parameters(
        wf.workflow_id, "fod", {"threads": 7}, wf.revision
    )
    registry = ManagedTasks(case.store)
    assert registry.index()["items"][0]["task_id"] == identity
    assert registry.get(identity)["current_plan"]["revision"] == wf.revision
    assert len(registry.get(identity)["versions"]) >= 3


def test_native_scope_change_keeps_conversation_task_identity(workflow_case):
    case = workflow_case
    from app.chat_service import ChatService
    from app.conversation_service import ConversationService

    conv = ConversationService(case.store, ChatService()).create("计算FOD")
    req = ChatRequest(
        message="计算FOD",
        conversation_id=conv.conversation_id,
        dataset_id=case.dataset.dataset_id,
        task_contract=TaskContract(goal_artifact_types=["fod_image"]),
    )
    ledger = AgentTasks(case.store)
    ledger.begin(req)
    ledger.update_goal(req, {"goal": "计算FOD", "deliverable": "workflow"})
    identity = req.assistant_task["task_id"]
    req._input_selection = "user"
    req._registered_plan = MethodPlan(
        status="matched", steps=[{"id": "fod", "method_id": "mrtrix.dwi2fod"}]
    )
    wf = asyncio.run(case.service.draft(req, {"recommend_workflow": True}))[0]
    ledger.record(
        req,
        "prepare_workflow",
        {},
        {
            "id": wf.workflow_id,
            "kind": "workflow",
            "status": "pending",
            "revision": wf.revision,
            "input_selection": "user",
            "draft_ready": True,
        },
    )
    wf.revision += 1
    wf.dataset_id = None
    case.store.save_workflow(wf, event_type="workflow_dataset_changed")
    req.dataset_id = None
    ledger.begin(req)
    assert req.assistant_task["task_id"] == identity
    assert ManagedTasks(case.store).index()["items"][0]["task_id"] == identity


def test_duplicate_legacy_placeholder_is_hidden_by_owned_identity(data):
    ledger, request, ops = prepared(data)
    identity = request.assistant_task["task_id"]
    with data.service.store.transaction() as db:
        row = db.execute(
            "SELECT * FROM managed_tasks WHERE task_id=?", (identity,)
        ).fetchone()
        db.execute(
            "INSERT INTO managed_tasks VALUES(?,?,?,?,?,?)",
            (identity, "", None, row["source"], row["payload"], row["updated_at"]),
        )
    assert len(ManagedTasks(data.service.store).index()["items"]) == 1


def test_task_http_index_card_and_replan_use_one_identity(data):
    import httpx
    from fastapi import FastAPI
    from types import SimpleNamespace
    from unittest.mock import Mock
    from app.api.managed_tasks import router_for
    from app.api.conversations import router_for as conversations_router
    from app.chat_service import ChatService
    from app.conversation_service import ConversationService

    ledger, request, ops = prepared(data)
    with data.service.store.transaction() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS chat_turns(id TEXT,conversation_id TEXT,status TEXT)"
        )
    submit = Mock(return_value={"turn_id": "background", "status": "queued"})
    services = SimpleNamespace(
        local_store=data.service.store,
        data_operations=data.service,
        workspace_service=data.workspace,
        conversation_service=ConversationService(data.service.store, ChatService()),
        chat_turns=SimpleNamespace(active=lambda: {"turns": []}, submit=submit),
        workflow_service=SimpleNamespace(),
    )
    app = FastAPI()
    app.include_router(router_for(services))
    app.include_router(conversations_router(services))
    identity = request.assistant_task["task_id"]

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            listing = (await client.get("/api/task-index")).json()
            view = (await client.get("/api/tasks/" + identity)).json()
            assert (
                listing["items"][0]["task_id"] == view["entry"]["task_id"] == identity
            )
            assert view["plan"]["id"] == ops[-1]["id"]
            pointer = (
                await client.get(
                    "/api/conversations/" + request.conversation_id + "/task"
                )
            ).json()
            assert pointer["task"]["task_id"] == identity
            assert pointer["task"]["active_plan"]["id"] == ops[-1]["id"]
            assert "artifacts" not in pointer["task"] and "messages" not in pointer
            submit.assert_not_called()
            assert list(data.output.iterdir()) == []
            empty = services.conversation_service.create("空会话")
            assert (
                await client.get(
                    "/api/conversations/" + empty.conversation_id + "/task"
                )
            ).json() == {"task": None, "closed_tasks": []}
            token = principal.set(Principal())
            try:
                assert (
                    await client.get("/api/conversations/missing/task")
                ).status_code == 404
            finally:
                principal.reset(token)
            token = principal.set(
                Principal("other", True, ("unrelated",), ("unrelated",))
            )
            try:
                assert (
                    await client.get(
                        "/api/conversations/" + request.conversation_id + "/task"
                    )
                ).status_code == 403
            finally:
                principal.reset(token)
            edit = await client.patch(
                "/api/conversations/" + request.conversation_id + "/task",
                json={
                    "task_id": identity,
                    "expected_revision": request.assistant_task["task_revision"],
                    "goal": "调整目标",
                    "user_requirements": "调整要求",
                },
            )
            assert edit.status_code == 200
            pointer = (
                await client.get(
                    "/api/conversations/" + request.conversation_id + "/task"
                )
            ).json()
            assert (
                pointer["task"]["goal"] == "调整目标"
                and pointer["task"]["task_id"] == identity
            )
            updated = (await client.get("/api/tasks/" + identity)).json()
            assert (
                updated["entry"]["title"] == "调整目标"
                and updated["entry"]["status"] == "needs_replan"
            )
            response = await client.post("/api/tasks/" + identity + "/replan")
            assert response.status_code == 202
            submitted = submit.call_args[0][0]
            assert (
                submitted.conversation_id == request.conversation_id
                and submitted.task_action == "update_plan"
            )
            assert submitted.workspace_id == data.ws.id
            opened = await client.post("/api/tasks/" + identity + "/open-conversation")
            assert opened.status_code == 200 and opened.json()["task_id"] == identity
            # Deleting messages must not change the task identity or rebind an
            # immutable operation's previous conversation/approval scope.
            services.conversation_service.update(request.conversation_id, archived=True)
            services.conversation_service.delete(request.conversation_id)
            restored = await client.post(
                "/api/tasks/" + identity + "/open-conversation"
            )
            assert (
                restored.status_code == 200 and restored.json()["task_id"] == identity
            )
            assert restored.json()["conversation_id"] != request.conversation_id
            view = (await client.get("/api/tasks/" + identity)).json()
            assert (
                view["current_plan"] is None
                and view["entry"]["status"] == "needs_replan"
            )
            assert (
                len(view["versions"]) >= 2
                and len((await client.get("/api/task-index")).json()["items"]) == 1
            )

    asyncio.run(run())
