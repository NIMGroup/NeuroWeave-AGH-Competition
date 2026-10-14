"""Conversational purpose must not be reduced to missing-input repair or keyword routing."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
import test_workflow_service as fixtures
from app.chat_service import ChatService
from app.models import ChatRequest
from app.response_policy import ResponsePolicy
from app.task_contract import PlanNotMatched
from app.workspace_tools import WRITE_ACTIONS, WorkspaceTools


def intent(action, relation="continue"):
    return {
        "decision": "task" if action in {"plan", "revise"} else "chat",
        "candidate_capability_ids": ["dwi.tractography"],
        "confidence": 0.95,
        "coverage": "complete",
        "source": "model",
        "response_policy": {"action": action, "relation": relation},
        "task_contract": {"goal": "全脑纤维", "goal_artifact_types": ["tractogram"]},
    }


def chat_model():
    chat = ChatService()
    chat.base_url, chat.model = "http://model.invalid", "test"
    return chat


@pytest.mark.parametrize(
    "action", ["answer", "explore", "inspect", "clarify", "plan", "revise"]
)
def test_valid_semantics_are_authoritative_despite_keyword_hints(action):
    chat = chat_model()
    chat.complete = AsyncMock(return_value=json.dumps(intent(action)))
    result = asyncio.run(
        chat.normalize_task_intent(
            ChatRequest(message="纤维追踪之后呢？"),
            [{"capability_id": "dwi.tractography"}],
            ["dwi.tractography"],
            [{"id": "tractogram"}],
        )
    )
    assert result["response_policy"]["action"] == action
    assert (result["decision"] == "task") == (action in {"plan", "revise"})
    if action not in {"plan", "revise"}:
        assert result["candidate_capability_ids"] == []


def test_inconsistent_or_unknown_semantic_actions_retry_without_promoting_fallback():
    chat = chat_model()
    bad = intent("explore")
    bad["decision"] = "task"
    chat.complete = AsyncMock(return_value=json.dumps(bad))
    result = asyncio.run(
        chat.normalize_task_intent(
            ChatRequest(message="解释追踪"),
            [{"capability_id": "dwi.tractography"}],
            ["dwi.tractography"],
            [{"id": "tractogram"}],
        )
    )
    assert result["source"] == "heuristic_after_model_error"
    assert result["confidence"] < 0.5
    assert chat.complete.await_count == 2


@pytest.fixture
def service():
    fixture = fixtures.WorkflowServiceTests()
    fixture.setUp()
    try:
        yield fixture.service
    finally:
        fixture.tearDown()


def test_discussion_never_enters_capability_tree_or_creates_workflow(service):
    for action in ["answer", "explore", "inspect", "clarify"]:
        req = ChatRequest(
            message="我没有脑部分区，还有哪些选择？", interaction_mode="task"
        )
        service.chat_service.normalize_task_intent = AsyncMock(
            return_value=intent(action)
        )
        with patch.object(
            service.router,
            "route_capabilities_hierarchically",
            side_effect=AssertionError("No tree for discussion"),
        ):
            decision = asyncio.run(service.assess_task(req, conversation=True))
        assert not decision["recommend_workflow"] and not decision["recommend_skills"]
        assert req.response_policy.action == action


def test_normal_chat_can_discuss_a_requested_plan_without_creating_it(service):
    service.chat_service.normalize_task_intent = AsyncMock(return_value=intent("plan"))
    with patch.object(
        service.router,
        "route_capabilities_hierarchically",
        side_effect=AssertionError("No graph in normal chat"),
    ):
        request = ChatRequest(message="给我流程", interaction_mode="chat")
        result = asyncio.run(service.assess_task(request, conversation=True))
    assert not result["recommend_workflow"]
    assert request.response_policy.action == "explore"


def test_switch_and_independent_do_not_inject_previous_workflow():
    req = ChatRequest(
        message="换个目标",
        current_workflow_context={"marker": "old-tractography-state"},
    )
    for relation in ["independent", "switch", "continue"]:
        req.response_policy = ResponsePolicy(action="plan", relation=relation)
        prompt = ChatService.system_message("plan", req)["content"]
        assert ("old-tractography-state" in prompt) == (relation == "continue")


@pytest.mark.parametrize("action", ["answer", "explore", "clarify", "inspect"])
def test_discussion_cannot_write_even_if_provider_calls_unadvertised_tool(action):
    req = ChatRequest(
        message="说明如何整理文件",
        interaction_mode="task",
        workspace_id="output",
        response_policy=ResponsePolicy(action=action),
    )
    tools = WorkspaceTools(Mock(), Mock())
    names = {item["function"]["name"] for item in tools.available_tools(req)}
    assert not names.intersection(WRITE_ACTIONS)
    with pytest.raises(PermissionError):
        tools.execute("delete_output_path", {"path": "result.mif"}, req)
    tools.service.propose.assert_not_called()


def test_explicit_file_management_still_proposes_confirmation():
    req = ChatRequest(
        message="重命名输出",
        interaction_mode="task",
        workspace_id="output",
        response_policy=ResponsePolicy(action="operate", needs_files=True),
    )
    tools = WorkspaceTools(Mock(), Mock())
    tools.execute(
        "rename_output_path", {"path": "result.mif", "destination": "new.mif"}, req
    )
    tools.service.propose.assert_called_once()
    tools.service.confirm.assert_not_called()


def test_exploration_budget_preserves_evidence_without_triggering_graph_retry():
    req = ChatRequest(
        message="比较两种算法",
        interaction_mode="task",
        response_policy=ResponsePolicy(action="explore", needs_catalog=True),
    )
    tools = WorkspaceTools(Mock(), Mock())
    assert (
        tools.handoff(
            req, [{"result": {"items": [{"id": "mrtrix.tckgen"}]}}], "query_budget"
        )
        is None
    )
    assert not req.planning_handoff
    assert "mrtrix.tckgen" in req.workspace_context
    assert "complete_scan" in req.workspace_context


def test_query_failure_can_still_lead_to_an_honest_conversational_answer():
    req = ChatRequest(
        message="这两种方法有何区别",
        interaction_mode="task",
        response_policy=ResponsePolicy(action="explore", needs_catalog=True),
    )
    chat = SimpleNamespace(
        mode="model",
        workspace_completion=AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "1",
                            "function": {
                                "name": "read_skill_method",
                                "arguments": '{"method_id":"unavailable"}',
                            },
                        }
                    ],
                },
                {"role": "assistant", "content": "unverified answer"},
            ]
        ),
    )
    tools = WorkspaceTools(Mock(), chat)
    tools.skill_catalog = Mock()
    tools.execute = Mock(side_effect=ValueError("Method not found in this query"))
    assert asyncio.run(tools.reply(req)) is None
    assert "lookup_failed" in req.workspace_context
    assert not req.planning_handoff


def test_blocked_plan_keeps_validation_failure_and_explains_options():
    chat = chat_model()
    chat.complete = AsyncMock(
        return_value=json.dumps(
            {
                "explanation": "软件能力和当前可连接的接口是两个层次。",
                "alternatives": [
                    {
                        "title": "先完成分割",
                        "method_ids": ["freesurfer.recon_all"],
                        "explanation": "已有命令可以处理T1。",
                        "limitation": "仍需定义分区和空间转换的输出接口。",
                    }
                ],
                "question": "先讨论路线还是调整处理目标？",
            }
        )
    )
    error = PlanNotMatched(
        "未生成流程：缺少类型接口",
        {"unregistered_goal_outputs": ["parcellation_image"]},
    )
    answer = asyncio.run(
        chat.explain_planning_block(
            ChatRequest(message="帮我生成"), error, [{"id": "freesurfer.recon_all"}]
        )
    )
    assert answer.startswith(str(error)) and "先完成分割" in answer
    assert "freesurfer.recon_all" in answer


def test_blocked_plan_rejects_invented_registered_methods_and_retains_honest_fallback():
    chat = chat_model()
    chat.complete = AsyncMock(
        return_value=json.dumps(
            {
                "explanation": "advice",
                "alternatives": [
                    {
                        "title": "fiction",
                        "method_ids": ["fake.method"],
                        "explanation": "try",
                        "limitation": "unknown",
                    }
                ],
            }
        )
    )
    error = PlanNotMatched("No validated graph", {})
    result = asyncio.run(
        chat.explain_planning_block(ChatRequest(message="help"), error, [])
    )
    assert result == str(error)
    assert chat.complete.await_count == 2
    assert len(error.diagnostics["model_errors"]) == 2


def test_general_discussion_is_not_blocked_by_unregistered_artifact_types():
    chat = chat_model()
    payload = intent("explore")
    payload["task_contract"] = {
        "goal_artifact_types": ["not_registered_scientific_concept"]
    }
    chat.complete = AsyncMock(return_value=json.dumps(payload))
    result = asyncio.run(
        chat.normalize_task_intent(ChatRequest(message="讨论一种新方向"), [], [], [])
    )
    assert result["decision"] == "chat"
    assert result["task_contract"]["goal_artifact_types"] == []
    assert result["source"] == "model"
    assert chat.complete.await_count == 1
