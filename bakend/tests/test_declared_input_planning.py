"""User assertions shape the graph; unknown file choices still belong to users."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.agent_runtime import execute, refresh_artifacts
from app.agent_state import AgentTasks
from app.capability_registry import CapabilityRegistry
from app.goal_planner import read_routes, saved_route
from app.data_auth import Principal, principal
from app.task_contract import TaskContract, declared_input_scope
from test_agent_team import TeamProvider, setup
from test_data_operations import data as data
from test_goal_planner import workflow_case as workflow_case
from test_workflow_service import SKILLS_DIR


def route_case(data, declarations=(), goal='tractogram', workflow=None, dataset_id=None):
    message='已有响应函数，没有 FOD；现在有 FOD；没有纤维；没有 bval。'
    owner, request = setup(data, TeamProvider(), message)
    owner.workflow_service = workflow or SimpleNamespace(registry=CapabilityRegistry(SKILLS_DIR))
    if workflow:
        request.dataset_id = dataset_id
        principal.set(Principal('alice', True, (dataset_id,), (data.ws.id,)))
        # Both services represent the same conversation even when the fixtures
        # use separate stores; the AGH fork enforces its recorded ownership.
        workflow.store.create_conversation(data.service.store.get_conversation(request.conversation_id))
        owner.service = SimpleNamespace(store=workflow.store)
        owner.skill_catalog = None
        workflow.chat_service.plan_registered_methods = AsyncMock(
            side_effect=AssertionError('Known graph must not trigger model planning'))
    owner.data_operations._roots = Mock(side_effect=AssertionError('Planning must not scan data'))
    ledger = AgentTasks(data.service.store)
    ledger.begin(request)
    ledger.update_goal(request, {'goal':message, 'deliverable':'workflow',
        'task_contract':{'goal_artifact_types':[goal]},
        'declared_inputs':[{'reference':'current', 'quote':message, **v} for v in declarations]})
    return owner, request, ledger


def route_args(goal='tractogram', **kwargs):
    return {'task_contract':{'goal_artifact_types':[goal]}, **kwargs}


def test_missing_required_input_is_generated_once_and_known_responses_stay_external(data):
    owner, request, ledger = route_case(data, [
        {'artifact_type':'fod_image', 'available':False},
        {'artifact_type':'response_functions', 'available':True}])
    args = route_args(parameters={'mrtrix.dwi2fod':{'algorithm':'msmt_csd'}})
    result = read_routes(owner, ledger, request, args)
    assert result['status'] == 'matched'
    route = saved_route(owner, ledger, request, result['routes'][0]['route_id'])
    assert [s['method_id'] for s in route['steps']] == ['mrtrix.dwi2fod', 'mrtrix.tckgen']
    assert route['steps'][-1]['inputs']['fod']['step_id'] == route['steps'][0]['id']
    assert {'wm_response','gm_response','csf_response'} <= {b['slot_id'] for b in route['bindings']}
    assert 'fod' not in {b['slot_id'] for b in route['bindings']}
    assert not any(b.get('path') for b in route['bindings'])
    repeat = read_routes(owner, ledger, request, args)
    assert repeat['summary_reused'] and repeat['routes'] == result['routes']
    assert not ledger.load(request)['task_contract'].get('forbidden_existing_inputs')
    owner.data_operations._roots.assert_not_called()
    assert owner.chat.requests == []


def test_missing_dependency_of_another_goal_uses_same_boundary(data):
    owner, request, ledger = route_case(data, [
        {'artifact_type':'tractogram', 'available':False},
        {'artifact_type':'fod_image', 'available':True}], goal='streamline_weights')
    result = read_routes(owner, ledger, request, route_args('streamline_weights'))
    assert result['status'] == 'matched'
    route = saved_route(owner, ledger, request, result['routes'][0]['route_id'])
    assert route['steps'][0]['method_id'] == 'mrtrix.tckgen'
    assert route['steps'][-1]['inputs']['in_tracks']['step_id'] == route['steps'][0]['id']
    assert len(route['steps']) == 2 and 'in_tracks' not in {b['slot_id'] for b in route['bindings']}
    owner.data_operations._roots.assert_not_called()


@pytest.mark.parametrize('available', [None, True])
def test_unknown_or_available_inputs_remain_selectable_without_file_queries(data, available):
    declarations = [] if available is None else [{'artifact_type':'fod_image', 'available':available}]
    owner, request, ledger = route_case(data, declarations)
    kwargs = {'preferred_methods':['mrtrix.dwi2fod','mrtrix.tckgen']} if available else {}
    result = read_routes(owner, ledger, request, route_args(**kwargs))
    assert result['routes'][0]['method_ids'] == ['mrtrix.tckgen']
    assert any(s['slot_id']=='fod' for s in result['routes'][0]['inputs_to_select'])
    owner.data_operations._roots.assert_not_called()


def test_unregistered_declaration_does_not_block_user_input_selection(data):
    owner, request, ledger = route_case(data, [{'artifact_type':'old_unregistered_label','available':False}])
    result = read_routes(owner,ledger,request,route_args())
    assert result['status'] == 'matched' and result['routes'][0]['method_ids'] == ['mrtrix.tckgen']
    assert saved_route(owner,ledger,request,result['routes'][0]['route_id'])
    owner.data_operations._roots.assert_not_called()


def test_correction_retracts_derived_exclusion_and_invalidates_previous_route(data):
    owner, request, ledger = route_case(data, [{'artifact_type':'fod_image','available':False}])
    first = read_routes(owner, ledger, request, route_args())
    old = first['routes'][0]['route_id']
    ledger.facts.declare(request, {'reference':f'message:{request._user_sequence}',
        'quote':'现在有 FOD', 'artifact_type':'fod_image', 'available':True})
    with pytest.raises(ValueError, match='已变'):
        saved_route(owner, ledger, request, old)
    result = read_routes(owner, ledger, request, route_args())
    assert not result['summary_reused'] and result['routes'][0]['method_ids'] == ['mrtrix.tckgen']
    # A new quotation of the same assertion reuses the existing summary.
    ledger.facts.declare(request, {'reference':f'message:{request._user_sequence}',
        'quote':request.message, 'artifact_type':'fod_image', 'available':True})
    assert read_routes(owner, ledger, request, route_args())['summary_reused']


def test_role_and_path_exclusions_do_not_expand_to_unrelated_inputs():
    from app.workflow_models import InputSlotSpec
    contract = TaskContract(goal_artifact_types=['fod_image'])
    effective = declared_input_scope(contract, [{'summary':{
        'artifact_type':'response_functions','slot_id':'wm_response','available':False}}])
    wm = InputSlotSpec(id='wm_response',type='response_functions',label='white matter')
    gm = InputSlotSpec(id='gm_response',type='response_functions',label='gray matter')
    assert not effective.allows_slot(wm) and effective.allows_slot(gm)
    path_only = declared_input_scope(contract, [{'summary':{
        'artifact_type':'response_functions','path':'missing.txt','available':False}}])
    assert path_only.allows_slot(wm) and path_only.allows_slot(wm,'other.txt')
    assert not path_only.allows_slot(wm,'missing.txt')
    assert contract.forbidden_existing_inputs == []


def test_declaration_edit_invalidates_readiness_but_unchanged_assertions_do_not(data):
    from app.agent_delivery import matched_routes
    owner, request, ledger = route_case(data, [{'artifact_type':'fod_image','available':False}])
    result = read_routes(owner, ledger, request, route_args())
    revision = request.assistant_task['task_revision']
    identity = request.assistant_task['task_id']
    assert matched_routes(request.assistant_task)
    change = {'goal':request.message, 'declared_inputs':[{'reference':'current',
        'quote':'现在有 FOD', 'artifact_type':'fod_image','available':True}]}
    ledger.update_goal(request, change)
    assert request.assistant_task['task_id'] == identity
    assert request.assistant_task['task_revision'] == revision + 1
    assert not matched_routes(request.assistant_task)
    with pytest.raises(ValueError, match='旧路线'):
        saved_route(owner,ledger,request,result['routes'][0]['route_id'])
    assert read_routes(owner,ledger,request,route_args())['routes'][0]['method_ids'] == ['mrtrix.tckgen']
    ledger.update_goal(request, change)
    assert request.assistant_task['task_revision'] == revision + 1
    assert read_routes(owner,ledger,request,route_args())['summary_reused']


def test_impossible_dependency_reports_gap_without_a_partial_matched_route(data):
    owner, request, ledger = route_case(data, [{'artifact_type':'b_values','slot_id':'bvals','available':False}], goal='fod_image')
    result = read_routes(owner, ledger, request, route_args('fod_image',
        parameters={'mrtrix.dwi2fod':{'gradient_source':'fsl'}}))
    assert result['status'] != 'matched' and not result['routes']
    assert any(g['slot_id']=='bvals' and '生成步骤' in g['reason'] for g in result['gaps'])
    assert read_routes(owner, ledger, request, route_args('fod_image',
        parameters={'mrtrix.dwi2fod':{'gradient_source':'fsl'}}))['summary_reused']
    owner.data_operations._roots.assert_not_called()


def test_user_prohibition_on_generation_is_not_overridden(data):
    owner, request, ledger = route_case(data, [{'artifact_type':'fod_image','available':False}])
    ledger.save(request,{**request.assistant_task,'task_contract':{
        **request.assistant_task['task_contract'],'allow_generated_intermediates':False}})
    result = read_routes(owner, ledger, request, route_args())
    assert result['status'] != 'matched' and not result['routes']
    assert any('不允许补充生成步骤' in v['reason'] for v in result['excluded'])


def test_direct_partial_graph_is_rejected_before_saving_a_workflow(workflow_case, data):
    case = workflow_case
    owner, request, ledger = route_case(data, [{'artifact_type':'fod_image','available':False}],
        workflow=case.service, dataset_id=case.dataset.dataset_id)
    before = case.store.list_workflows()
    with pytest.raises(ValueError, match='fod'):
        asyncio.run(execute(owner,ledger,request,'prepare_workflow',{
            'goal':'纤维追踪','steps':[{'id':'track','method_id':'mrtrix.tckgen'}]},[]))
    assert case.store.list_workflows() == before and not case.store.list_runs()
    case.service.chat_service.plan_registered_methods.assert_not_awaited()


def test_complete_route_is_saved_with_unselected_external_inputs_and_no_execution(workflow_case, data):
    case = workflow_case
    owner, request, ledger = route_case(data, [{'artifact_type':'fod_image','available':False}],
        workflow=case.service, dataset_id=case.dataset.dataset_id)
    result = read_routes(owner, ledger, request, route_args())
    draft = asyncio.run(execute(owner,ledger,request,'prepare_workflow',{
        'goal':'从响应函数生成FOD后追踪','route_id':result['routes'][0]['route_id'],
        'task_contract':{'goal':'different wording','goal_artifact_types':['fod_image']}},[]))
    workflow = case.service.get(draft['id'])
    assert draft['draft_ready'] and not draft['executed'] and not draft['inputs_ready']
    assert len(workflow.steps) == 2 and not workflow.can_confirm
    assert workflow.steps[-1].input_bindings['fod'].source == 'upstream_output'
    assert all(b.source=='upstream_output' for s in workflow.steps for b in s.input_bindings.values())
    assert not case.store.list_runs()
    case.service.chat_service.plan_registered_methods.assert_not_awaited()
    owner.data_operations._roots.assert_not_called()


def test_delivery_rechecks_current_declarations_against_an_older_graph(workflow_case, data):
    case = workflow_case
    owner, request, ledger = route_case(data, workflow=case.service, dataset_id=case.dataset.dataset_id)
    draft = asyncio.run(execute(owner,ledger,request,'prepare_workflow',{
        'goal':'纤维追踪','steps':[{'id':'track','method_id':'mrtrix.tckgen'}]},[]))
    ledger.record(request,'prepare_workflow',{},draft)
    ledger.facts.declare(request,{'reference':f'message:{request._user_sequence}',
        'quote':'没有 FOD','artifact_type':'fod_image','available':False})
    with pytest.raises(ValueError, match='fod'):
        asyncio.run(refresh_artifacts(owner,ledger,request,[draft['id']]))
    assert not case.store.list_runs()
