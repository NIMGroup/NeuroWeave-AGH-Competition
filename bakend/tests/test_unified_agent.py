"""Cross-capability orchestration using real plans/files, never auto execution."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.agent_state import AgentTasks
from app.agent_runtime import available_tools
from app.agent_continuation import AgentContinuation
from app.chat_service import ChatService
from app.chat_turns import ChatTurns
from app.conversation_service import ConversationService
from app.models import ChatRequest
from app.workspace_tools import WorkspaceTools
from test_data_operations import data, approval, finished, run_script


class Provider:
    mode, model, provider_name = "model", "test", "test"

    def __init__(self, replies):
        self.replies, self.rounds = iter(replies), []

    async def workspace_completion(self, messages, tools, **kwargs):
        self.rounds.append((messages.copy(), {t["function"]["name"] for t in tools}, kwargs))
        reply = next(self.replies)
        if callable(reply):
            reply = reply(messages)
        if isinstance(reply, str):
            return {"role": "assistant", "content": reply}
        return {"role": "assistant", "reasoning_content": "strategy may change",
                "tool_calls": [{"id": f"call-{len(self.rounds)}-{i}", "type": "function",
                               "function": {"name": name, "arguments": json.dumps(args)}}
                              for i, (name, args) in enumerate(reply)]}

    async def json_response(self, *args, **kwargs):
        raise AssertionError("No upfront classifier or separate proposal judge")


def fixture(data, replies, message="修改当前文件"):
    provider = Provider(replies)
    owner = WorkspaceTools(data.workspace, provider, data.service.datasets)
    owner.data_operations = data.service
    owner.workflow_service = SimpleNamespace()
    request = ChatRequest(conversation_id=data.scope["conversation_id"], workspace_id=data.ws.id,
                          dataset_id="ds_test", message=message, interaction_mode="task",
                          client_turn_id="unified-test-turn-0001")
    request, _ = ConversationService(data.service.store, ChatService()).begin_turn(request)
    return owner, request, provider


def latest_id(messages):
    for message in reversed(messages):
        if message["role"] == "tool":
            result = json.loads(message["content"])
            if result.get("kind") == "data":
                return result["id"]
    raise AssertionError("No operation")


def ready(messages):
    return [("finish_task", {"status": "awaiting_run", "summary": "方案就绪，等待运行。", "artifact_ids": [latest_id(messages)]})]


def goal(deliverable="file_changes"):
    return ("update_task_state", {"goal": "按用户要求处理当前数据", "deliverable": deliverable})


def test_wrong_strategy_and_tool_error_are_recoverable_without_reclassifying(data):
    (data.output / "a.txt").write_text("original")
    owner, request, _ = fixture(data, [
        [goal(), ("propose_data_changes", {"source_scope": "output", "mode": "copy",
          "edits": [{"action": "rename", "path": "a.txt", "destination": "b.txt"}]})],
        [("propose_data_changes", {"source_scope": "output", "mode": "inplace",
          "edits": [{"action": "rename", "path": "a.txt", "destination": "b.txt"}]})],
        ready,
    ])
    response = asyncio.run(owner.reply(request))
    assert response.assistant_task["phase"] == "awaiting_run"
    events = data.workspace.events(request.client_turn_id)
    assert any(v["status"] == "failed" for v in events)
    assert not (data.output / "b.txt").exists()
    assert not any(v["tool"] == "understand_task" for v in events)


def test_no_initial_tools_required_for_questions_and_planning_always_available(data):
    owner, request, provider = fixture(data, ["可以先讨论表格整理规则。"])
    assert asyncio.run(owner.reply(request)).content.startswith("可以")
    assert provider.rounds[0][2]["required"] is False
    assert "prepare_workflow" in provider.rounds[0][1]
    assert "run_data_python" in provider.rounds[0][1]
    assert "prepare_current_step" not in provider.rounds[0][1]


def test_analysis_is_not_file_change_completion(data, monkeypatch):
    monkeypatch.setenv("NEUROWEAVE_DATA_PYTHON_IMAGE", "synthetic:readonly")
    data.service.sandbox.image = "synthetic:readonly"
    owner, request, _ = fixture(data, [
        [goal(), ("run_data_python", {"source_scope": "output", "mode": "analysis", "code": "print('map')"} )],
        ready,
    ])
    response = asyncio.run(owner.reply(request))
    ledger = AgentTasks(data.service.store)
    request.assistant_task = ledger.load(request)
    identity = next(iter(request.assistant_task["artifacts"]))
    request.assistant_task["artifacts"][identity].update(status="analyzed", artifact_type="report", current_state_verified=True)
    with pytest.raises(ValueError, match="缺少符合原目标"):
        ledger.finish(request, {"status": "completed", "summary": "改名完成", "artifact_ids": [identity]})
    assert response.assistant_task["phase"] == "awaiting_run"
    assert data.service.list(request.conversation_id)[0]["status"] == "pending"


def test_user_decisions_need_exact_user_quote_not_assistant_text(data):
    owner, request, _ = fixture(data, ["好的"], message="排除 backups，只处理 csv")
    ledger = AgentTasks(data.service.store)
    ledger.begin(request)
    ledger.update_goal(request, {"goal": "整理表格", "deliverable": "files",
                                "decisions": [{"quote": "排除 backups", "reference": "current"}]})
    ledger.update_goal(request, {"goal": "整理表格", "decisions": [{"quote": "所有文件都删除", "reference": "current"}]})
    assert ledger.load(request)['state_warnings']
    assert ledger.load(request)["decisions"][0]["quote"] == "排除 backups"


def test_actual_workflow_planner_is_callable_in_the_same_loop(data):
    owner, request, provider = fixture(data, [
        [goal("workflow"), ("prepare_workflow", {"goal": "从已有输入生成统计结果", "task_contract": {"goal_artifact_types": ["report"]},
            "steps":[{"id":"inspect","method_id":"dataset.inspect"}], "resolved_need_ids":["unknown_optional_id"]})],
        [("finish_task", {"status": "awaiting_run", "summary": "流程已准备", "artifact_ids": ["wf_test"], "resolved_need_ids":["another_unknown_id"]})],
    ])
    workflow = Mock(workflow_id="wf_test", revision=1, title="统计", steps=[], warnings=[])
    # Response validation expects a real Workflow.
    from app.workflow_models import Workflow
    workflow = Workflow(workflow_id="wf_test", template_id="test", title="统计", summary="", original_request=request.message,
                        status="chain_ready", created_at="now", updated_at="now", steps=[])
    owner.workflow_service = SimpleNamespace(registry=SimpleNamespace(artifact_types=lambda: [SimpleNamespace(id="report")]),
        draft=AsyncMock(return_value=(workflow, "准备完成")), get=Mock(return_value=workflow))
    response = asyncio.run(owner.reply(request))
    assert response.type == "workflow" and response.workflow.workflow_id == "wf_test"
    assert owner.workflow_service.draft.await_count == 1 and len(provider.rounds) == 2


def test_runtime_feedback_resumes_same_goal_without_new_user_message(data):
    (data.output / "a.txt").write_text("original")
    owner, request, _ = fixture(data, [
        [goal(), ("propose_data_changes", {"source_scope": "output", "mode": "inplace",
          "edits": [{"action": "rename", "path": "a.txt", "destination": "b.txt"}]})], ready,
    ])
    response = asyncio.run(owner.reply(request))
    operation = data.service.list(request.conversation_id)[0]
    data.service.confirm(operation["id"], approval(operation))
    done = finished(data, operation)
    assert done["status"] == "succeeded"
    conversations = ConversationService(data.service.store, ChatService())
    asyncio.run(conversations.finish_turn(request.conversation_id, response))
    owner.chat = Provider([
        [("finish_task", {"status": "completed", "summary": "文件修改已核验。", "artifact_ids": [operation["id"]]})],
    ])
    async def handler(req):
        prepared, _ = conversations.begin_turn(req)
        answer = await owner.reply(prepared)
        await conversations.finish_turn(req.conversation_id, answer, req.client_turn_id)
        return answer
    turns = ChatTurns(data.service.store, conversations, handler)
    services = SimpleNamespace(local_store=data.service.store, data_operations=data.service, workspace_service=data.workspace,
        chat_turns=turns, data_auth=SimpleNamespace(users={"alice": {"datasets": ["ds_test"], "outputs": [data.ws.id]}}))
    monitor = AgentContinuation(services)
    async def run():
        await monitor.scan()
        await asyncio.gather(*list(turns.tasks.values()))
        await monitor.scan()
    asyncio.run(run())
    rows = data.service.store.conversation_messages(request.conversation_id)
    assert len([m for m in rows if m.role == "user"]) == 1
    assert len([m for m in rows if m.role == "assistant"]) == 2
    assert AgentTasks(data.service.store).load(request)["phase"] == "completed"
    with data.service.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM chat_turns WHERE id LIKE 'result-%'").fetchone()[0] == 1


def test_chat_mode_exposes_only_reads_and_no_planner(data):
    owner, request, _ = fixture(data, ["解释"])
    request.interaction_mode = "chat"
    names = {t["function"]["name"] for t in available_tools(owner, request)}
    assert not names & {"run_data_python", "propose_data_changes", "prepare_workflow", "delete_output_path"}


def test_main_loop_must_review_a_plan_before_it_can_run(data):
    (data.output / "a.txt").write_text("original")
    def verify_then_finish(messages):
        task = data.service.get(latest_id(messages), data.scope["conversation_id"])
        assert task["quality"]["proposal_review"]["decision"] == "pending"
        assert not task["can_confirm"]
        with pytest.raises(ValueError):
            data.service.confirm(task["id"], approval(task))
        return ready(messages)
    owner, request, _ = fixture(data, [
        [goal(), ("propose_data_changes", {"source_scope": "output", "mode": "inplace",
          "edits": [{"action": "rename", "path": "a.txt", "destination": "b.txt"}]})], verify_then_finish,
    ])
    asyncio.run(owner.reply(request))
    task = data.service.list(request.conversation_id)[0]
    assert task["can_confirm"] and task["quality"]["proposal_review"]["source"] == "unified_agent"
    assert (data.output / "a.txt").exists() and not (data.output / "b.txt").exists()


def test_script_generated_manifest_needs_new_review_and_separate_run(data):
    (data.output / "a.txt").write_text("original")
    data.service.sandbox.image = "synthetic:readonly"
    def generate(source, directory, code):
        directory.mkdir()
        result = directory / "artifacts"
        result.mkdir()
        (result / "changes.json").write_text(json.dumps({"edits": [{"action": "rename", "path": "a.txt", "destination": "b.txt"}]}))
        return result
    data.service.sandbox.run = generate
    owner, request, _ = fixture(data, [
        [goal(), ("run_data_python", {"source_scope": "output", "mode": "inplace", "result_kind": "changes",
                                    "change_actions": ["rename"], "code": "# Generate full changes.json\nprint('prepared')"})], ready,
    ])
    asyncio.run(owner.reply(request))
    script = data.service.list(request.conversation_id)[0]
    assert script["can_run_script"]
    manifest = run_script(data, script)
    assert manifest["status"] == "pending" and manifest["manifest_ready"]
    assert manifest["quality"]["proposal_review"]["decision"] == "pending" and not manifest["can_confirm"]
    with pytest.raises(ValueError):
        data.service.confirm(manifest["id"], approval(manifest))
    assert (data.output / "a.txt").exists() and not (data.output / "b.txt").exists()
    from test_agent_team import TeamProvider, done
    owner.chat = TeamProvider(coordinator=[
        [("update_task_state", {"goal":"完成原文件改名", "deliverable":"file_changes", "success_criteria":["改名清单经过核验并等待手动运行"]}),
         ("delegate_agent", {"agent":"runtime", "question":"核查本次脚本生成的实际清单版本"})],
        [("finish_task", {"status": "awaiting_run", "summary": "清单生成并核验，请点击运行应用改动。", "artifact_ids": [manifest["id"]]})]],
        runtime=[[('get_data_operation', {'id':manifest['id']})], done])
    request._execution_feedback = {"source_id": manifest["id"], "status": "pending", "phase": "files"}
    response = asyncio.run(owner.reply(request))
    assert response.assistant_task["phase"] == "awaiting_run"
    assert data.service.get(manifest["id"], request.conversation_id)["can_confirm"]
    assert (data.output / "a.txt").exists()


def test_delayed_execution_does_not_compete_with_new_user_input(data):
    (data.output / "a.txt").write_text("original")
    owner, request, _ = fixture(data, [
        [goal(), ("propose_data_changes", {"source_scope": "output", "mode": "inplace",
          "edits": [{"action": "rename", "path": "a.txt", "destination": "b.txt"}]})], ready,
    ])
    asyncio.run(owner.reply(request))
    task = data.service.list(request.conversation_id)[0]
    data.service.confirm(task["id"], approval(task)); finished(data, task)
    conversations = ConversationService(data.service.store, ChatService())
    conversations.begin_turn(request.model_copy(update={"client_turn_id": "a-new-user-turn-0002", "message": "先解释另一件事"}))
    turns = ChatTurns(data.service.store, conversations, AsyncMock())
    services = SimpleNamespace(local_store=data.service.store, data_operations=data.service, workspace_service=data.workspace,
        chat_turns=turns, data_auth=SimpleNamespace(users={"alice": {"datasets": ["ds_test"], "outputs": [data.ws.id]}}))
    asyncio.run(AgentContinuation(services).scan())
    assert not turns.tasks


def test_revising_the_same_goal_retires_only_the_unrun_previous_version(data):
    (data.output / "a.txt").write_text("original")
    owner, request, _ = fixture(data, [
        [goal(), ("propose_data_changes", {"source_scope": "output", "mode": "inplace",
          "edits": [{"action": "rename", "path": "a.txt", "destination": "b.txt"}]})], ready,
    ])
    asyncio.run(owner.reply(request))
    old = data.service.list(request.conversation_id)[0]
    owner.chat = Provider([
        [("propose_data_changes", {"source_scope": "output", "mode": "inplace",
          "edits": [{"action": "rename", "path": "a.txt", "destination": "c.txt"}]})], ready,
    ])
    request2, _ = ConversationService(data.service.store, ChatService()).begin_turn(
        request.model_copy(update={"client_turn_id": "new-revision-turn-0002", "message": "目标名称改成 c.txt"}))
    response = asyncio.run(owner.reply(request2))
    tasks = data.service.list(request.conversation_id)
    new = next(v for v in tasks if v["id"] != old["id"])
    retired = data.service.get(old["id"], request.conversation_id)
    assert response.assistant_task["phase"] == "awaiting_run" and new["can_confirm"]
    assert not retired["can_confirm"] and retired["quality"]["proposal_review"]["superseded_by"] == new["id"]
    assert not (data.output / "c.txt").exists()


def test_model_failure_does_not_leave_task_forever_preparing(data):
    def fail(messages):
        raise RuntimeError("provider unavailable")
    owner, request, _ = fixture(data, [fail])
    conversations = ConversationService(data.service.store, ChatService())
    turns = ChatTurns(data.service.store, conversations, owner.reply)
    async def run():
        submitted = turns.submit(request)
        result = await turns.wait(submitted["turn_id"])
        assert 'provider unavailable' in result.content
    asyncio.run(run())
    state = AgentTasks(data.service.store).load(request)
    assert state["phase"] == "blocked" and not state.get("awaiting_results")
    assert "provider unavailable" in state["next_step"]
