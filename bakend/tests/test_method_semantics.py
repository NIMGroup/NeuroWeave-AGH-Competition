"""Scientific port identities must survive collection, retrieval and planning."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.capability_registry import CapabilityRegistry
from app.chat_service import ChatService
from app.execution.runners.freesurfer import FreeSurferReconAllRunner
from app.method_planning import MethodContractIndex, MethodPlan, validate_plan
from app.method_registry import MethodRegistry
from app.models import ChatRequest
from app.response_policy import ResponsePolicy, response_guidance
from app.task_contract import TaskContract
from app.workflows.planning import WorkflowPlanner
from app.workspace_tools import WorkspaceTools

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def registry():
    return CapabilityRegistry(ROOT / "skills")


def test_real_anatomical_producer_found_outside_wrong_capability_branch(registry):
    index = MethodContractIndex(registry)
    goal = TaskContract(
        goal="从 T1w 得到解剖脑区分区", goal_artifact_types=["parcellation_image"]
    )
    ids, trace = index.retrieve(goal, ["skill.kmeans"])
    assert "freesurfer.recon_all" in ids
    assert not trace["missing_goal_types"]
    assert "reviewed.nilearn-tool.kmeans" not in index.producers["parcellation_image"]
    assert "reviewed.fsl-tool.fast" not in index.producers["parcellation_image"]


def test_anatomical_labels_can_feed_real_lut_conversion(registry):
    plan = MethodPlan.model_validate(
        {
            "status": "matched",
            "steps": [
                {
                    "id": "segment",
                    "method_id": "freesurfer.recon_all",
                    "parameters": {"processing_stage": "all"},
                },
                {
                    "id": "remap",
                    "method_id": "mrtrix.command.labelconvert",
                    "inputs": {
                        "path_in": {
                            "step_id": "segment",
                            "output_slot_id": "parcellation",
                        }
                    },
                },
            ],
        }
    )
    goal = TaskContract(goal_artifact_types=["parcellation_image"])
    validate_plan(plan, goal, registry)
    plan.steps[0].parameters["processing_stage"] = "autorecon1"
    with pytest.raises(ValueError):
        validate_plan(plan, goal, registry)


def test_generic_or_tissue_file_cannot_masquerade_as_anatomical_atlas(registry):
    for mid, output in [
        ("reviewed.fsl-tool.fast", "file_tissue_seg_nii_gz"),
        ("reviewed.nilearn-tool.kmeans", "file_parcel_labels_nii_gz"),
    ]:
        plan = MethodPlan.model_validate(
            {
                "status": "matched",
                "steps": [
                    {"id": "segment", "method_id": mid},
                    {
                        "id": "remap",
                        "method_id": "mrtrix.command.labelconvert",
                        "inputs": {
                            "path_in": {"step_id": "segment", "output_slot_id": output}
                        },
                    },
                ],
            }
        )
        with pytest.raises(ValueError):
            validate_plan(
                plan, TaskContract(goal_artifact_types=["parcellation_image"]), registry
            )


def test_reviewed_public_types_and_runtime_spec_have_one_source():
    store = MethodRegistry(ROOT)
    store.load()
    specs = store.reviewed_specs()
    for mid, method in store.methods.items():
        if method.execution["kind"] != "reviewed":
            continue
        for port in method.output_slots:
            if port.id == "results":
                continue
            assert (
                specs[mid]["artifact_types"][
                    port.path_template.removeprefix("results/")
                ]
                == port.type
            )
    assert (
        specs["reviewed.dwi-skill.dwibiascorrect"]["artifact_types"]["dwi.bvec"]
        == "b_vectors"
    )
    assert (
        specs["reviewed.fsl-tool.fast"]["artifact_types"]["tissue_seg.nii.gz"]
        == "label_image"
    )


@pytest.mark.parametrize(
    "stage,expected",
    [
        ("autorecon1", {"subject", "t1w", "brain_t1w"}),
        ("autorecon2", {"subject"}),
        ("autorecon3", {"subject", "parcellation", "aseg", "aseg_stats"}),
        ("stats", {"subject", "aseg_stats"}),
        ("all", {"subject", "t1w", "brain_t1w", "parcellation", "aseg", "aseg_stats"}),
    ],
)
def test_freesurfer_registers_only_current_stage_files(tmp_path, stage, expected):
    store = MethodRegistry(ROOT)
    method = store.method("freesurfer.recon_all")
    root = tmp_path / "sub01"
    root.mkdir()
    # Include stale later-stage files too: they must not leak into stage outputs.
    for port in method.output_slots:
        if port.id == "subject":
            continue
        p = root / port.path_template.removeprefix("{subject}/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"nonempty fixture")
    context = SimpleNamespace(
        method=method,
        output_directory=tmp_path,
        step=SimpleNamespace(id="fs"),
        file_sha256=lambda _: "fixture",
        log=Mock(),
    )
    runner = FreeSurferReconAllRunner()
    # Stage anatomy validation is exercised separately in test_freesurfer_outputs.
    runner.validate_outputs = Mock(return_value=["stage checks passed"])
    result = runner.collect_result(context, root, stage)
    artifacts = {a.metadata["output_slot_id"]: a for a in result.artifacts}
    assert set(artifacts) == expected
    if "parcellation" in expected:
        assert artifacts["parcellation"].artifact_type == "parcellation_image"
        assert not artifacts["parcellation"].metadata["spatial_alignment_verified"]
        (root / "mri/aparc+aseg.mgz").write_bytes(b"")
        with pytest.raises(RuntimeError):
            runner.collect_result(context, root, stage)
    # Old immutable snapshots do not acquire newly introduced file requirements.
    context.method = method.model_copy(
        update={"output_slots": [method.output_slots[0]]}
    )
    assert len(runner.collect_result(context, root, stage).artifacts) == 1


def test_advice_keeps_initial_high_rank_candidate_after_semantic_expansion(registry):
    index = MethodContractIndex(registry)
    index.refresh()
    others = [m for m in index.methods if m != "freesurfer.recon_all"]
    diagnostics = {
        "searches": [
            {"returned_methods": [*others[:4], "freesurfer.recon_all", *others[4:31]]},
            {"returned_methods": [*others[31:70], "freesurfer.recon_all"]},
        ],
        "semantic_scan": {"candidate_ids": others[80:150]},
    }
    service = SimpleNamespace(method_index=index)
    result = WorkflowPlanner.planning_advice_context(service, diagnostics)
    assert len(result) <= 20
    fs = next(r for r in result if r["id"] == "freesurfer.recon_all")
    assert any(s["type"] == "parcellation_image" for s in fs["outputs"])


@pytest.mark.parametrize("action", ["answer", "explore"])
def test_optional_invitation_is_not_a_request_to_plan_or_execute(action):
    policy = ResponsePolicy(action=action, offer_workflow=True)
    assert not policy.plans
    guidance = response_guidance(policy)
    assert '完整请求' in guidance and '本轮目标' in guidance
    assert '不声称已创建或能自动执行流程' in guidance
    for plain in [
        ResponsePolicy(action=action),
        ResponsePolicy(action="inspect", offer_workflow=True),
        ResponsePolicy(action="plan", offer_workflow=True),
        ResponsePolicy(action="clarify", offer_workflow=True),
    ]:
        assert '可选的规划邀请' not in response_guidance(plain)


def test_intent_preserves_semantic_invitation_without_promoting_task():
    chat = ChatService()
    chat.base_url = "http://model.invalid"
    chat.model = "mock"
    chat.complete = AsyncMock(
        return_value=json.dumps(
            {
                "decision": "chat",
                "candidate_capability_ids": [],
                "confidence": 0.9,
                "coverage": "complete",
                "response_policy": {"action": "explore", "offer_workflow": True},
            }
        )
    )
    result = asyncio.run(
        chat.normalize_task_intent(
            ChatRequest(message="我缺少脑区分区，有哪些办法？"), [], [], []
        )
    )
    assert result["decision"] == "chat" and result["response_policy"]["offer_workflow"]
    assert "offer_workflow" in chat.complete.call_args.args[0][0]["content"]


def test_file_and_catalog_answer_receives_same_invitation_guidance():
    response = AsyncMock(
        side_effect=[
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "1",
                        "function": {
                            "name": "read_skill_method",
                            "arguments": '{"method_id":"freesurfer.recon_all"}',
                        },
                    }
                ],
            },
            {"role": "assistant", "content": "已查询分区方法。可按此方案请求规划。"},
        ]
    )
    tools = WorkspaceTools(
        Mock(), SimpleNamespace(mode="model", workspace_completion=response)
    )
    tools.skill_catalog = Mock()
    tools.execute = Mock(return_value={"id": "freesurfer.recon_all"})
    request = ChatRequest(
        message="我有哪些分区选择？",
        interaction_mode="chat",
        response_policy=ResponsePolicy(
            action="explore", needs_catalog=True, offer_workflow=True
        ),
    )
    assert asyncio.run(tools.reply(request)).startswith("已查询")
    prompt = response.call_args.args[0][0]["content"]
    assert '可选的规划邀请' in prompt and 'task mode' in prompt
