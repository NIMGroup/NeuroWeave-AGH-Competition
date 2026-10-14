"""Regression for a second-turn prerequisite question that stopped at tool budget."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from app.capability_registry import CapabilityRegistry
from app.chat_service import ChatService
from app.conversation_service import ConversationService
from app.local_store import LocalStore
from app.models import ChatRequest
from app.skill_catalog import SkillCatalog
from app.skill_loader import SkillLoader
from app.task_contract import TaskContract
from app.workflow_models import InputSlotSpec, MethodOption
from app.workspace_tools import WorkspaceTools

ROOT = Path(__file__).resolve().parents[2]


def call(name, args, index):
    return {
        "id": str(index),
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


@pytest.mark.parametrize("batch", [1, 20])
def test_query_budget_hands_off_with_bounded_evidence_and_no_duplicate_reads(batch):
    class Model:
        mode = "model"

        async def workspace_completion(self, *args, **kwargs):
            return {
                "role": "assistant",
                "tool_calls": [
                    call("list_skill_methods", {"q": "connectome"}, i)
                    for i in range(batch)
                ],
            }

    tools = WorkspaceTools(Mock(), Model())
    tools.skill_catalog = Mock()
    tools.execute = Mock(
        return_value={"items": [{"method_id": "mrtrix.tck2connectome"}]}
    )
    req = ChatRequest(
        message="如果我当前没有脑部分区，应该怎么做", interaction_mode="task"
    )
    assert asyncio.run(tools.reply(req)) is None
    assert req.planning_handoff
    tools.execute.assert_called_once()
    data = json.loads(req.workspace_context.split("：\n")[-1])
    assert data["stop_reason"] == "no_progress"
    assert data["complete_scan"] is False
    assert (
        data["observations"][0]["result"]["items"][0]["method_id"]
        == "mrtrix.tck2connectome"
    )


def test_handoff_preserves_pending_write_confirmation_and_never_plans_it():
    tools = WorkspaceTools(Mock(), Mock())
    request = ChatRequest(message="移动文件", interaction_mode="task")
    answer = tools.handoff(
        request,
        [
            {
                "result": {
                    "status": "pending",
                    "action": "move",
                    "source": "/output/a",
                    "target": "/output/b",
                }
            }
        ],
        "query_budget",
    )
    assert "尚未执行" in answer
    assert not request.planning_handoff
    assert not request.workspace_context


def test_oversized_observations_remain_valid_json_and_chat_does_not_request_planning():
    tools = WorkspaceTools(Mock(), Mock())
    request = ChatRequest(message="列出文件", interaction_mode="chat")
    tools.handoff(request, [{"result": {"items": ["x" * 20000]}}], "query_budget")
    assert not request.planning_handoff
    assert json.loads(request.workspace_context.split("：\n")[-1])["truncated"]


def test_malformed_tool_arguments_are_recorded_without_crashing():
    class Model:
        mode = "model"

        async def workspace_completion(self, *args, **kwargs):
            return {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "bad",
                        "type": "function",
                        "function": {"name": "list_skill_methods", "arguments": "{bad"},
                    }
                ],
            }

    tools = WorkspaceTools(Mock(), Model())
    tools.skill_catalog = Mock()
    request = ChatRequest(message="说明方法")
    assert asyncio.run(tools.reply(request)) is None
    assert (
        "error"
        in json.loads(request.workspace_context.split("：\n")[-1])["observations"][0][
            "result"
        ]
    )


def test_bilingual_synonyms_recall_real_method_without_requiring_both_languages():
    catalog = SkillCatalog(
        SkillLoader(ROOT / "skills"), CapabilityRegistry(ROOT / "skills"), Mock()
    )
    result = catalog.list_methods("dwi-skill", q="connectome 连接组", limit=50)
    assert "mrtrix.tck2connectome" in {m["method_id"] for m in result["items"]}
    assert result["match_mode"] == "ranked_any_term"
    assert result["scope"] == "dwi-skill"
    assert catalog.list_methods(q="definitely_nonexistent_xyz")["total"] == 0
    # Selected Skill is a scope, not proof of global absence. Broader recall is explicit.
    assert (
        "Omit skill_id"
        in catalog.list_methods("dwi-skill", q="definitely_nonexistent_xyz")["hint"]
    )
    assert any(
        m["method_id"] == "freesurfer.recon_all"
        for m in catalog.list_methods(q="recon-all 脑分区", limit=50)["items"]
    )


def test_followup_context_uses_persisted_selection_and_cannot_cross_datasets(tmp_path):
    store = LocalStore(tmp_path / "test.db")
    service = ConversationService(store, Mock())
    conversation = service.create("structural connectome")
    store.append_conversation_message(
        conversation.conversation_id,
        role="assistant",
        content="review plan",
        estimated_tokens=3,
        metadata={"workflow_id": "wf"},
        created_at=service._now(),
    )
    method = MethodOption(
        id="selected",
        definition_id="mrtrix.tck2connectome",
        skill_id="dwi-skill",
        label="connectome",
        description="",
        runner_id="mrtrix.tck2connectome",
        input_slots=[
            InputSlotSpec(
                id="parcellation",
                type="parcellation_image",
                label="brain parcellation",
                required=True,
            )
        ],
    )
    wf = SimpleNamespace(
        workflow_id="wf",
        revision=3,
        dataset_id="ds",
        status="methods_locked",
        original_request="结构连接矩阵",
        task_contract=TaskContract(goal="connectome"),
        excluded_capabilities=[],
        steps=[
            SimpleNamespace(
                id="matrix",
                method_options=[method],
                selected_method_id="selected",
                selected_parameters={},
                input_bindings={},
                missing_required_inputs=["parcellation"],
            )
        ],
    )
    with patch.object(store, "get_workflow", return_value=wf):
        req, _ = service.begin_turn(
            ChatRequest(
                conversation_id=conversation.conversation_id,
                dataset_id="ds",
                message="如果我没有脑部分区怎么办",
                interaction_mode="task",
                current_workflow_context={"untrusted": True},
                planning_handoff=True,
            )
        )
        assert not req.planning_handoff
        context = req.current_workflow_context
        assert context["revision"] == 3 and context["workflow_id"] == "wf"
        assert context["steps"][0]["inputs"][0]["required"]
        assert context["steps"][0]["inputs"][0]["binding_source"] is None
        assert "untrusted" not in context
        assert (
            service._workflow_context(conversation.conversation_id, "different-dataset")
            == {}
        )
        assert "current_workflow_context" not in req.model_dump()
        assert (
            "mrtrix.tck2connectome"
            in ChatService.system_message("intent", req)["content"]
        )
