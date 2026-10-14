"""User assertions drive plans; only explicit preflight reads scientific content."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.agent_team import AgentTeam
from app.capability_registry import CapabilityRegistry
from app.goal_planner import read_routes
from app.execution.base import RunnerContext
from app.execution.check_cache import checked_file, content_cache, _items
from app.execution.gradient_policy import gradient_arguments
from app.execution.recipe_compiler import RecipeCompiler
from app.workflow_models import InputBinding, WorkflowStep
from threading import Event
from test_agent_team import TeamProvider, setup
from test_data_operations import data as data
from test_goal_planner import workflow_case as workflow_case
from test_workflow_service import SKILLS_DIR
from test_data_sequences import nifti


def test_real_workflow_defers_content_and_dry_run_reuses_cached_checks(
    workflow_case, monkeypatch
):
    from app.models import ChatRequest
    from app.method_planning import MethodPlan
    from app.task_contract import TaskContract
    from app.workflows.assistant_bindings import AssistantInput
    from app.execution.runner_registry import RunnerRegistry
    from app.execution import scientific_checks as checks

    case = workflow_case
    for sample in ("sub-01", "sub-02"):
        folder = case.dataset_folder / sample / "dwi"
        (folder / "image.mif").write_text(
            "mrtrix image\ndim: 2,2,2,3\ndw_scheme: 0,0,0,0\ndw_scheme: 1,0,0,1000\ndw_scheme: 0,1,0,2000\nEND\n"
        )
        nifti(folder / (sample + "_mask.nii.gz"), 1)
        for role in ("wm", "gm", "csf"):
            (folder / (role + "_response.txt")).write_text("1\n1\n1\n")
    request = ChatRequest(
        message="用已有响应函数计算FOD",
        dataset_id=case.dataset.dataset_id,
        interaction_mode="task",
        task_contract=TaskContract(goal_artifact_types=["fod_image"]),
    )
    request._registered_plan = MethodPlan(
        status="matched",
        steps=[
            {
                "id": "fod",
                "method_id": "mrtrix.dwi2fod",
                "parameters": {"algorithm": "msmt_csd"},
            }
        ],
    )
    request._workflow_input_bindings = [
        AssistantInput(
            method_id="mrtrix.dwi2fod", slot_id=slot, path="sub-01/dwi/" + name
        )
        for slot, name in [
            ("dwi", "image.mif"),
            ("mask", "sub-01_mask.nii.gz"),
            ("wm_response", "wm_response.txt"),
            ("gm_response", "gm_response.txt"),
            ("csf_response", "csf_response.txt"),
        ]
    ]
    real_header = checks.image_header
    header = Mock(wraps=real_header)
    monkeypatch.setattr(checks, "image_header", header)
    workflow, _ = asyncio.run(
        case.service.draft(request, {"recommend_workflow": True, "capability_ids": []})
    )
    header.assert_not_called()
    assert workflow.sample_scope is None and not workflow.can_confirm
    from app.execution.output_preview import workflow_output_preview
    from app.run_models import RunExecutionOptions

    preview = workflow_output_preview(
        workflow, RecipeCompiler(case.service.registry), RunExecutionOptions()
    )
    assert (
        preview["input_validation"] == "pending_dry_run"
        and preview["sample_count"] == 2
    )
    runners = RunnerRegistry()
    runner = runners.get("mrtrix.dwi2fod")
    monkeypatch.setattr(runner, "runtime_status", lambda: {"available": True})
    monkeypatch.setenv("LOCALFLOW_RUN_INPUT_ROOTS", str(case.dataset_folder.parent))
    case.service.catalog.runner_registry = runners
    workflow = case.service.lock_chain(workflow.workflow_id, workflow.revision)
    workflow = case.service.review_parameters(workflow.workflow_id, workflow.revision)
    parsed = Mock(wraps=checks.numeric_table)
    monkeypatch.setattr(checks, "numeric_table", parsed)
    workflow = case.service.dry_run(workflow.workflow_id, workflow.revision)
    assert workflow.dry_run_report.valid and workflow.can_confirm
    assert len(workflow.sample_scope.eligible_sample_ids) == 2
    assert workflow.input_check_cache
    assert all(
        row["details"]["source"] == "embedded"
        for row in workflow.dry_run_report.quality_checks
        if row["code"] == "gradient_source_resolved"
    )
    parsed.assert_called()
    first_count = parsed.call_count
    _items.clear()
    workflow = case.service.dry_run(workflow.workflow_id, workflow.revision)
    assert workflow.dry_run_report.valid
    assert parsed.call_count == first_count
    # Parameter edits invalidate the pass, but retain immutable content reads.
    workflow.dry_run_report = None
    workflow.steps[0].selected_parameters["threads"] = 8
    case.store.save_workflow(workflow, event_type="test_parameter_change")
    _items.clear()
    workflow = case.service.dry_run(workflow.workflow_id, workflow.revision)
    assert workflow.dry_run_report.valid and parsed.call_count == first_count
    (case.dataset_folder / "sub-02/dwi/wm_response.txt").write_text("1\n1\n")
    workflow = case.service.dry_run(workflow.workflow_id, workflow.revision)
    assert not workflow.dry_run_report.valid and not workflow.can_confirm
    assert parsed.call_count == first_count + 1
    assert not case.store.list_runs(workflow_id=workflow.workflow_id)


def test_declared_roles_produce_route_without_file_queries_and_survive_restart(data):
    message = "所有样本已有预处理DWI、掩膜以及白质、灰质、脑脊液响应函数，计算FOD"
    owner, request = setup(data, TeamProvider(), message)
    owner.workflow_service = SimpleNamespace(registry=CapabilityRegistry(SKILLS_DIR))
    team = AgentTeam(owner, request)
    inputs = [
        ("preprocessed_dwi", ""),
        ("brain_mask", ""),
        *[
            ("response_functions", s)
            for s in ("wm_response", "gm_response", "csf_response")
        ],
    ]
    team.ledger.update_goal(
        request,
        {
            "goal": message,
            "deliverable": "workflow",
            "declared_inputs": [
                {
                    "artifact_type": t,
                    "slot_id": s,
                    "quote": message,
                    "reference": "current",
                }
                for t, s in inputs
            ],
        },
    )
    owner.data_operations._roots = Mock(
        side_effect=AssertionError("Planning must not inspect files")
    )
    declared = asyncio.run(
        team.query(
            "data",
            "query_data_files",
            {
                "source_scope": "dataset",
                "required_patterns": {"wm_response": "**/wm_response.txt"},
            },
        )
    )
    assert declared["validation"] == "pending_dry_run" and declared["fact_ids"]
    args = {
        "task_contract": {"goal": message, "goal_artifact_types": ["fod_image"]},
        "preferred_methods": ["mrtrix.dwi2fod"],
        "parameters": {"mrtrix.dwi2fod": {"algorithm": "msmt_csd"}},
    }
    result = read_routes(owner, team.ledger, request, args)
    assert result["status"] == "matched"
    assert result["routes"][0]["method_ids"] == ["mrtrix.dwi2fod"]
    assert result['routes'][0]['fact_ids'] == []
    assert result['routes'][0]['input_selection'] == 'user'
    assert result['routes'][0]['user_input_count'] == 5
    restored = AgentTeam(owner, request)
    assert len(restored.facts.declarations(request)) == 5
    again = read_routes(owner, restored.ledger, request, args)
    assert again["summary_reused"]
    owner.data_operations._roots.assert_not_called()


def test_assertions_require_real_quotes_and_do_not_prove_file_observations(data):
    owner, request = setup(data, TeamProvider(), "已有响应函数")
    team = AgentTeam(owner, request)
    team.ledger.update_goal(
        request,
        {
            "goal": request.message,
            "declared_inputs": [
                {
                    "artifact_type": "response_functions",
                    "quote": "模型编造的原话",
                    "reference": "current",
                }
            ],
        },
    )
    assert not team.facts.declarations(request)
    assert request.assistant_task["state_warnings"]
    team.ledger.update_goal(
        request,
        {
            "goal": request.message,
            "declared_inputs": [
                {
                    "artifact_type": "response_functions",
                    "quote": request.message,
                    "reference": "current",
                }
            ],
        },
    )
    row = team.facts.declarations(request)[0]
    assert row["summary"]["provenance"] == "user_declared"
    assert row["summary"]["validation"] == "pending_dry_run"
    assert "complete_scan" not in row["summary"]


def test_workflow_planning_cannot_call_content_readers(data):
    owner, request = setup(data, TeamProvider())
    team = AgentTeam(owner, request)
    team.ledger.update_goal(request, {"goal": "处理图像", "deliverable": "workflow"})
    for name in (
        "inspect_data_sequences",
        "inspect_data_metadata",
        "read_dataset_text",
    ):
        with pytest.raises(ValueError, match="dry-run"):
            asyncio.run(team.query("data", name, {}))


def test_portable_cache_reuses_report_after_process_cache_clear_and_invalidates_changes(
    tmp_path,
):
    path = tmp_path / "input"
    path.write_text("old")
    calls = []

    def read():
        calls.append(path.read_text())
        return {"value": calls[-1]}

    workflow = SimpleNamespace(dry_run_report=None)
    context = SimpleNamespace(workflow=workflow, input_checks={})
    with content_cache(context):
        assert checked_file(path, "reader-v1", read) == {"value": "old"}
    assert len(calls) == 1
    workflow.dry_run_report = SimpleNamespace(input_checks=context.input_checks)
    _items.clear()
    fresh = SimpleNamespace(workflow=workflow, input_checks={})
    with content_cache(fresh):
        assert checked_file(path, "reader-v1", read) == {"value": "old"}
    assert len(calls) == 1
    path.write_text("new content")
    with content_cache(fresh):
        assert checked_file(path, "reader-v1", read) == {"value": "new content"}
        checked_file(path, "reader-v2", read)
    assert len(calls) == 3


def gradient_context(tmp_path, method_id, image, source="auto", pair=True):
    registry = CapabilityRegistry(SKILLS_DIR)
    method = RecipeCompiler._method_option(registry.method_for("method", method_id))
    image_slot = "dwi" if any(s.id == "dwi" for s in method.input_slots) else "input"
    bindings = {
        image_slot: InputBinding(
            slot_id=image_slot,
            artifact_type="preprocessed_dwi",
            source="existing_path",
            absolute_path=str(image),
        )
    }
    if pair:
        for suffix, role in (("bval", "bvals"), ("bvec", "bvecs")):
            slot = (
                role
                if any(s.id == role for s in method.input_slots)
                else "fslgrad_" + role
            )
            path = tmp_path / ("wrong." + suffix)
            path.write_text("0 1000" if suffix == "bval" else "0 1\n0 0\n0 0")
            bindings[slot] = InputBinding(
                slot_id=slot,
                artifact_type="gradient_table",
                source="existing_path",
                absolute_path=str(path),
            )
    step = WorkflowStep(
        id="s",
        order=1,
        title="s",
        description="",
        capability="dwi.fod",
        status="confirmed",
        selected_parameters={"gradient_source": source},
        input_bindings=bindings,
    )
    return RunnerContext(
        run_id="test",
        workflow=SimpleNamespace(dry_run_report=None),
        step=step,
        method=method,
        output_directory=tmp_path / "output",
        cancel_event=Event(),
        log=lambda *_: None,
        is_dry_run=True,
    )


@pytest.mark.parametrize(
    "method_id",
    [
        "mrtrix.dwi2fod",
        "mrtrix.dwi2response",
        "mrtrix.command.dwi2tensor",
        "mrtrix.command.dwi2adc",
        "mrtrix.command.dwi2mask",
        "mrtrix.command.dwiextract",
    ],
)
def test_mif_embedded_gradients_win_over_unrelated_external_pair(tmp_path, method_id):
    image = tmp_path / "image.mif"
    image.write_text(
        "mrtrix image\ndim: 2,2,2,3\ndw_scheme: 0,0,0,0\ndw_scheme: 1,0,0,1000\ndw_scheme: 0,1,0,2000\nEND\n"
    )
    context = gradient_context(tmp_path, method_id, image)
    args, source, profile = gradient_arguments(context)
    assert args == [] and source == "embedded" and profile["volume_count"] == 3
    context.step.selected_parameters["gradient_source"] = "fsl"
    with pytest.raises(ValueError, match="体积数"):
        gradient_arguments(context)


def test_mif_without_embedded_gradients_is_not_accepted_by_extension(tmp_path):
    image = tmp_path / "image.mif"
    image.write_text("mrtrix image\ndim: 2,2,2,3\nEND\n")
    context = gradient_context(tmp_path, "mrtrix.dwi2fod", image, pair=False)
    with pytest.raises(ValueError, match="缺少"):
        gradient_arguments(context)


def test_nifti_external_gradients_are_checked_during_preflight(tmp_path):
    image = tmp_path / "image.nii"
    nifti(image, 2)
    context = gradient_context(tmp_path, "mrtrix.command.dwi2tensor", image)
    args, source, profile = gradient_arguments(context)
    assert args[0] == "-fslgrad" and source == "fsl" and profile["count"] == 2
    nifti(image, 3)
    with pytest.raises(ValueError, match="体积数"):
        gradient_arguments(context)
