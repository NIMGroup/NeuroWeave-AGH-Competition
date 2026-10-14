"""Draft delivery and explicit input selection across planning and execution gates."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.agent_team import AgentTeam
from app.capability_registry import CapabilityRegistry
from app.goal_planner import GoalPlanner, RouteQuery, read_routes
from app.method_planning import MethodPlan
from app.models import ChatRequest
from app.task_contract import TaskContract
from app.workflows.assistant_bindings import AssistantInput
from test_agent_team import TeamProvider, setup
from test_data_operations import data as data
from test_data_sequences import nifti
from test_goal_planner import workflow_case as workflow_case
from test_workflow_service import SKILLS_DIR


def user_draft(case, suggestions=True):
    request = ChatRequest(message="使用已有三种响应函数计算 FOD", dataset_id=case.dataset.dataset_id,
                          task_contract=TaskContract(goal_artifact_types=["fod_image"]))
    request._input_selection = "user"
    request._registered_plan = MethodPlan(status="matched", steps=[
        {"id":"fod", "method_id":"mrtrix.dwi2fod", "parameters":{"algorithm":"msmt_csd"}}])
    if suggestions:
        request._workflow_input_bindings = [
            AssistantInput(method_id="mrtrix.dwi2fod", slot_id="dwi", path="sub-01/dwi/image.mif")]
    return asyncio.run(case.service.draft(request, {"recommend_workflow":True,"capability_ids":[]}))[0]


def scientific_inputs(case):
    for sample in ("sub-01","sub-02"):
        folder = case.dataset_folder/sample/"dwi"
        (folder/"image.mif").write_text(
            "mrtrix image\ndim: 2,2,2,3\ndw_scheme: 0,0,0,0\ndw_scheme: 1,0,0,1000\ndw_scheme: 0,1,0,2000\nEND\n")
        nifti(folder/"mask.nii.gz", 1)
        for tissue in ("wm","gm","csf"):
            (folder/(tissue+"_response.txt")).write_text("1\n1\n1\n")


def select_rules(case, workflow):
    for slot, name in [("dwi","image.mif"),("mask","mask.nii.gz"),
                       ("wm_response","wm_response.txt"),("gm_response","gm_response.txt"),("csf_response","csf_response.txt")]:
        path = (Path(case.dataset.relative_path)/"{sample}"/"dwi"/name).as_posix()
        workflow = case.service.bind_input(workflow.workflow_id,"fod",slot,case.dataset.root_id,path,
                                          workflow.revision,"template")
    return workflow


def test_wrong_classification_does_not_block_draft_or_trigger_data_queries(data):
    message = "所有样本已有白质灰质以及脑脊液响应函数，计算每个样本 FOD"
    owner, request = setup(data, TeamProvider(), message)
    owner.workflow_service = SimpleNamespace(registry=CapabilityRegistry(SKILLS_DIR))
    team = AgentTeam(owner, request)
    team.ledger.update_goal(request, {"goal":message,"deliverable":"workflow","success_criteria":["生成流程草案"],
        "declared_inputs":[{"artifact_type":"response_function","slot_id":role,"reference":"current","quote":message}
                           for role in ("wm_response","gm_response","csf_response")]})
    owner.data_operations._roots = Mock(side_effect=AssertionError("Draft planning must not inspect data"))
    args={"task_contract":{"goal_artifact_types":["fod_image"]},
          "existing_inputs":[{"artifact_type":"model_invented_type","path":"unobserved.mif"}]}
    result=read_routes(owner,team.ledger,request,args)
    route=result["routes"][0]
    assert result["status"]=="matched" and route["method_ids"]==["mrtrix.dwi2fod"]
    assert route["input_selection"]=="user" and route["existing_input_count"]==0
    assert {s["slot_id"] for s in route["inputs_to_select"]} >= {"dwi","wm_response","gm_response","csf_response"}
    assert route["parameter_choices"][0]["parameters"]["algorithm"]=="msmt_csd"
    repeat=read_routes(owner,team.ledger,request,args)
    assert repeat["summary_reused"] and repeat["routes"][0]["route_id"]==route["route_id"]
    report=asyncio.run(team.delegate({"agent":"data","question":"检查全部响应函数数量和内容"}))
    assert report["planning_complete"] and report["assessment_kind"]=="user_input_selection"
    owner.data_operations._roots.assert_not_called()


def test_user_selection_retains_explicit_method_dependencies():
    registry=CapabilityRegistry(SKILLS_DIR)
    query=RouteQuery(input_selection="user", task_contract={"goal_artifact_types":["tractogram"]},
                     preferred_methods=["mrtrix.dwi2fod","mrtrix.tckgen"])
    state,result=GoalPlanner(registry).search(query,{})
    assert result["status"]=="matched"
    route=state["routes"][0]
    assert [s["method_id"] for s in route["steps"]]==["mrtrix.dwi2fod","mrtrix.tckgen"]
    assert route["steps"][-1]["inputs"]["fod"]["step_id"]==route["steps"][0]["id"]
    assert "mrtrix.dwi2response" not in [s["method_id"] for s in route["steps"]]


def test_draft_suggestions_are_not_bindings_and_preview_does_not_check_files(workflow_case, monkeypatch):
    case=workflow_case
    scientific_inputs(case)
    workflow=user_draft(case)
    step=workflow.steps[0]
    assert workflow.status=="awaiting_input_binding" and workflow.can_lock and not workflow.can_dry_run
    assert not step.input_bindings and step.input_suggestions["dwi"]["validation"]=="unselected"
    from app.execution.output_preview import workflow_output_preview
    from app.execution.recipe_compiler import RecipeCompiler
    from app.run_models import RunExecutionOptions
    output=workflow_output_preview(workflow,RecipeCompiler(case.service.registry),RunExecutionOptions())
    assert output['input_validation']=='pending_selection' and output['sample_count']==2
    original=case.dataset_service._expand_sample_match
    case.dataset_service._expand_sample_match=Mock(side_effect=AssertionError("Only dry-run expands actual matches"))
    path=(Path(case.dataset.relative_path)/"{sample}/dwi/image.mif").as_posix()
    preview=case.service.preview_input_mapping(workflow.workflow_id,"fod","dwi",case.dataset.root_id,path,"template")
    assert preview.can_apply and preview.validation=="pending_dry_run"
    assert preview.coverage.total==2 and preview.coverage.matched==0
    assert {m.status for m in preview.matches}=={"pending"}
    selected=case.service.bind_input(workflow.workflow_id,"fod","dwi",case.dataset.root_id,path,workflow.revision,"template")
    assert selected.steps[0].input_bindings["dwi"].confirmed_by_user
    assert selected.steps[0].input_bindings["dwi"].deferred_resolution
    case.dataset_service._expand_sample_match.assert_not_called()
    case.dataset_service._expand_sample_match=original
    assert not case.store.list_runs(workflow_id=workflow.workflow_id)


def test_dry_run_uses_user_rules_eligible_subset_and_cached_contents(workflow_case,monkeypatch):
    from app.execution import scientific_checks as checks
    from app.execution.check_cache import _items
    from app.execution.runner_registry import RunnerRegistry
    case=workflow_case
    scientific_inputs(case)
    (case.dataset_folder/"sub-02/dwi/csf_response.txt").unlink()
    runners=RunnerRegistry()
    monkeypatch.setattr(runners.get("mrtrix.dwi2fod"),"runtime_status",lambda:{"available":True})
    monkeypatch.setenv("LOCALFLOW_RUN_INPUT_ROOTS",str(case.dataset_folder.parent))
    case.service.catalog.runner_registry=runners
    parsed=Mock(wraps=checks.numeric_table)
    monkeypatch.setattr(checks,"numeric_table",parsed)
    workflow=select_rules(case,user_draft(case))
    parsed.assert_not_called()
    workflow=case.service.lock_chain(workflow.workflow_id,workflow.revision)
    workflow=case.service.review_parameters(workflow.workflow_id,workflow.revision)
    workflow=case.service.dry_run(workflow.workflow_id,workflow.revision)
    assert workflow.dry_run_report.valid and workflow.can_confirm
    assert len(workflow.sample_scope.eligible_sample_ids)==1
    assert len(workflow.sample_scope.skipped_samples)==1
    excluded = workflow.sample_scope.skipped_samples[0]
    assert any(m.sample_id == excluded.sample_id and m.status == 'missing'
               for m in workflow.steps[0].input_bindings['csf_response'].sample_matches)
    count=parsed.call_count
    assert count>0
    _items.clear()
    workflow=case.service.dry_run(workflow.workflow_id,workflow.revision)
    assert workflow.dry_run_report.valid and parsed.call_count==count
    assert all(b.confirmed_by_user for b in workflow.steps[0].input_bindings.values())
    assert not case.store.list_runs(workflow_id=workflow.workflow_id)


def test_saved_rules_require_explicit_selection_and_clear_invalidates_review(workflow_case):
    case=workflow_case
    scientific_inputs(case)
    first=select_rules(case,user_draft(case))
    second=user_draft(case,False)
    saved=case.service.bindings.saved_input_rules(second.workflow_id,"fod","dwi")
    assert saved["rules"] and saved["rules"][0]["binding_mode"]=="template"
    assert not second.steps[0].input_bindings
    rule=saved["rules"][0]
    second=case.service.bind_input(second.workflow_id,"fod","dwi",rule["root_id"],rule["relative_path"],
                                   second.revision,rule["binding_mode"])
    assert second.steps[0].input_bindings["dwi"].confirmed_by_user
    second=case.service.bindings.clear_input(second.workflow_id,"fod","dwi",second.revision)
    assert "dwi" in second.steps[0].missing_required_inputs and not second.can_dry_run
    assert first.steps[0].input_bindings["dwi"].path_template=="{sample}/dwi/image.mif"


@pytest.mark.parametrize("rule",["../escape.mif","{unknown}/dwi/image.mif","{sample.__class__}/image.mif","{sample!r}/image.mif"])
def test_input_rules_reject_unsafe_or_unknown_variables(workflow_case,rule):
    case=workflow_case
    workflow=user_draft(case,False)
    path=(Path(case.dataset.relative_path)/rule).as_posix()
    with pytest.raises(ValueError):
        case.service.preview_input_mapping(workflow.workflow_id,"fod","dwi",case.dataset.root_id,path,"template")


def test_backend_delivers_missing_binding_as_awaiting_inputs(data):
    owner,request=setup(data,TeamProvider())
    team=AgentTeam(owner,request)
    team.ledger.update_goal(request,{"goal":"计算FOD","deliverable":"workflow"})
    team.ledger.record(request,"prepare_workflow",{},{"id":"wf-draft","kind":"workflow","status":"pending",
        "revision":1,"input_selection":"user","draft_ready":True,"missing_inputs":[{"slots":["dwi"]}],"inputs_ready":False})
    result=team.ledger.finish(request,{"status":"awaiting_run","artifact_ids":["wf-draft"],"summary":"方法结构已保存"})
    assert request.assistant_task["phase"]=="awaiting_inputs"
    assert "选择输入" in result and request.assistant_task["awaiting_results"]==["wf-draft"]


def test_unselected_companion_gradients_are_not_adopted_by_dry_run(workflow_case,monkeypatch):
    from app.execution.runner_registry import RunnerRegistry
    case=workflow_case
    scientific_inputs(case)
    for sample in ("sub-01","sub-02"):
        folder=case.dataset_folder/sample/"dwi"
        (folder/"image.mif").write_text("mrtrix image\ndim: 2,2,2,3\nEND\n")
        (folder/"image.bval").write_text("0 1000 2000\n")
        (folder/"image.bvec").write_text("0 1 0\n0 0 1\n0 0 0\n")
    runners=RunnerRegistry()
    monkeypatch.setattr(runners.get("mrtrix.dwi2fod"),"runtime_status",lambda:{"available":True})
    monkeypatch.setenv("LOCALFLOW_RUN_INPUT_ROOTS",str(case.dataset_folder.parent))
    case.service.catalog.runner_registry=runners
    workflow=select_rules(case,user_draft(case))
    workflow=case.service.lock_chain(workflow.workflow_id,workflow.revision)
    workflow=case.service.review_parameters(workflow.workflow_id,workflow.revision)
    workflow=case.service.dry_run(workflow.workflow_id,workflow.revision)
    assert not workflow.dry_run_report.valid and not workflow.can_confirm
    assert not {"bvals","bvecs","grad"} & workflow.steps[0].input_bindings.keys()
    assert not case.store.list_runs(workflow_id=workflow.workflow_id)


def test_unconfirmed_model_path_cannot_satisfy_a_required_input(workflow_case):
    from app.workflow_models import InputBinding
    case=workflow_case
    workflow=user_draft(case,False)
    workflow.steps[0].input_bindings["dwi"]=InputBinding(slot_id="dwi",artifact_type="preprocessed_dwi",
        source="existing_path",deferred_resolution=True,path_template="sub-01/dwi/image.mif")
    case.service.state.refresh(workflow)
    assert "dwi" in workflow.steps[0].missing_required_inputs
    assert "dwi" not in workflow.steps[0].input_bindings
    assert not workflow.can_dry_run and not workflow.can_confirm


def test_shared_files_do_not_invent_a_batch_when_no_sample_rules_were_selected(workflow_case):
    from app.workflows.preflight_inputs import resolve_planned_inputs
    case=workflow_case
    scientific_inputs(case)
    workflow=user_draft(case,False)
    for slot,name in [('dwi','image.mif'),('mask','mask.nii.gz'),('wm_response','wm_response.txt'),
                      ('gm_response','gm_response.txt'),('csf_response','csf_response.txt')]:
        path=(Path(case.dataset.relative_path)/'sub-01/dwi'/name).as_posix()
        workflow=case.service.bind_input(workflow.workflow_id,'fod',slot,case.dataset.root_id,path,workflow.revision,'single')
    resolve_planned_inputs(workflow,case.dataset_service)
    case.service.state.refresh(workflow)
    assert workflow.sample_scope is None
    assert all(b.binding_mode=='single' and b.confirmed_by_user for b in workflow.steps[0].input_bindings.values())
