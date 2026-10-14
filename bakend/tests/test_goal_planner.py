"""Goal routes, durable progress and coverage policy; no scientific execution."""
import asyncio
import json
from threading import Event
from pathlib import Path

import pytest

from app.capability_registry import CapabilityRegistry
from app.goal_planner import GoalPlanner, RouteQuery
from app.method_planning import MethodPlan, validate_plan
from app.operation_context import OperationCancelled, OperationContext, current_operation
from app.sample_scope import restrict_sample_bindings
import test_workflow_service as fixtures
import test_task_contract_planning as contract_fixtures


@pytest.fixture
def registry():
    return CapabilityRegistry(fixtures.SKILLS_DIR)


def query(**overrides):
    return RouteQuery.model_validate({
        'task_contract': {'goal': '生成纤维', 'goal_artifact_types': ['tractogram'],
            'input_policy': 'only_listed', 'plan_scope': 'goal_from_inputs',
            'allowed_existing_inputs': [{'artifact_type': t} for t in ['preprocessed_dwi', 'gradient_table', 'brain_mask']]},
        'existing_inputs': [{'artifact_type': t, 'path': 'sub-01/dwi/' + p, 'fact_id': 'fact:files'}
            for t, p in [('preprocessed_dwi', 'dwi.nii.gz'), ('brain_mask', 'mask.nii.gz'),
                         ('gradient_table', 'dwi.bval'), ('gradient_table', 'dwi.bvec')]],
        **overrides})


def test_backward_chain_uses_declared_dependencies_and_feasible_parameters(registry):
    planner = GoalPlanner(registry)
    state, result = planner.search(query(), {'fact:files': 'version-1'})
    assert result['status'] == 'matched'
    route = state['routes'][0]
    assert [s['method_id'] for s in route['steps']] == ['mrtrix.dwi2response', 'mrtrix.dwi2fod', 'mrtrix.tckgen']
    assert route['steps'][-1]['parameters']['act_enabled'] is False
    validate_plan(MethodPlan(status='matched', steps=route['steps']), query().task_contract, registry)
    # Full contracts and execution graphs stay out of the compact model result.
    assert 'steps' not in result['routes'][0] and len(json.dumps(result)) < 5000
    assert {b['slot_id'] for b in route['bindings']} == {'dwi', 'mask'}
    assert result['routes'][0]['sample_coverage']=='pending_dry_run'


def test_route_contract_cannot_select_unrelated_legacy_planning_mode():
    from app.goal_planner import ROUTE_TOOL
    schema=ROUTE_TOOL['function']['parameters']
    assert 'task_contract' not in schema['properties'] and '$defs' not in schema
    assert 'task_contract' not in schema.get('required', [])
    assert {'preferred_methods','parameters'} <= schema['properties'].keys()
    value=query().model_dump()
    value['task_contract']['plan_scope']='requested_steps'
    with pytest.raises(ValueError,match='goal_from_inputs'):
        RouteQuery.model_validate(value)


def test_identical_facts_reuse_summary_and_changes_invalidate_routes(registry):
    planner = GoalPlanner(registry)
    state, result = planner.search(query(), {'fact:files': 'v1'})
    same, repeat = planner.search(query(), {'fact:files': 'v1'}, json.loads(json.dumps(state)))
    assert repeat['summary_reused'] and same['expansions'] == state['expansions']
    rephrased=query()
    rephrased.task_contract.goal='同一目标换一种说法'
    rephrased.task_contract.retrieval_queries=['另一个同义关键词']
    again,repeat=planner.search(rephrased,{'fact:files':'v1'},state)
    assert repeat['summary_reused'] and again['expansions']==state['expansions']
    changed, _ = planner.search(query(), {'fact:files': 'v2'}, state)
    assert changed['key'] != state['key']


def test_budget_continues_saved_frontier_instead_of_restarting(registry):
    planner = GoalPlanner(registry)
    small = query(expansion_budget=1)
    state, result = planner.search(small, {})
    assert result['status'] == 'search_incomplete' and result['search']['resumable']
    resumed, result = planner.search(query(), {}, json.loads(json.dumps(state)))
    assert result['status'] == 'matched' and resumed['expansions'] > state['expansions']
    assert resumed['key'] == state['key']


def test_explicit_input_control_is_not_relaxed_by_planner(registry):
    planner = GoalPlanner(registry)
    request = query(parameters={'mrtrix.tckgen': {'act_enabled': True}})
    state, _ = planner.search(request, {})
    assert not any(any(s['method_id']=='mrtrix.tckgen' and s.get('parameters', {}).get('act_enabled') is False
        for s in r['steps']) for r in state['routes'])


def test_same_type_response_ports_are_not_interchanged(registry):
    planner = GoalPlanner(registry)
    request = query(parameters={'mrtrix.dwi2fod': {'algorithm': 'msmt_csd'}})
    state, result = planner.search(request, {})
    assert result['status'] == 'matched'
    fod = next(s for s in state['routes'][0]['steps'] if s['method_id']=='mrtrix.dwi2fod')
    for slot in ('wm_response', 'gm_response', 'csf_response'):
        assert fod['inputs'][slot]['output_slot_id'] == slot
    assert len({fod['inputs'][s]['step_id'] for s in ('wm_response','gm_response','csf_response')}) == 1


def test_generic_image_ports_do_not_create_false_anatomical_routes(registry):
    planner=GoalPlanner(registry)
    state,result=planner.search(query(max_routes=3),{})
    assert result['status']=='matched'
    assert not any(any('5tt' in s['method_id'] for s in route['steps']) for route in state['routes'])
    assert any('语义' in excluded['reason'] for excluded in result['excluded'])


def test_multiple_goals_reuse_already_planned_output(registry):
    request=query()
    request.task_contract.goal_artifact_types.append('fod_image')
    state,result=GoalPlanner(registry).search(request,{})
    assert result['status']=='matched'
    assert [s['method_id'] for s in state['routes'][0]['steps']].count('mrtrix.dwi2fod')==1


def test_explicit_input_scope_need_not_be_duplicated_in_two_fields(registry):
    value=query().model_dump()
    value['task_contract']['allowed_existing_inputs']=[]
    value['task_contract']['forbidden_existing_inputs']=[{'artifact_type':'five_tt_image'}]
    request=RouteQuery.model_validate(value)
    assert {a.artifact_type for a in request.task_contract.allowed_existing_inputs}=={i.artifact_type for i in request.existing_inputs}
    assert not request.task_contract.allows_external('five_tt_image')
    assert GoalPlanner(registry).search(request,{})[1]['status']=='matched'


def test_missing_starting_evidence_returns_one_specific_gap(registry):
    value=query().model_dump()
    value['task_contract']['allowed_existing_inputs']=[]
    value['existing_inputs']=[]
    state,result=GoalPlanner(registry).search(RouteQuery.model_validate(value),{})
    assert result['status']=='needs_facts' and result['search']['expanded']==0
    assert len(result['gaps'])==1


def test_generic_goal_does_not_enumerate_unrelated_file_producers(registry):
    request=query()
    request.task_contract.goal_artifact_types=['generated_file']
    _,result=GoalPlanner(registry).search(request,{})
    assert result['status']=='needs_facts' and result['search']['expanded']==0
    assert '具体操作' in result['gaps'][0]['reason']


def test_unknown_required_feature_is_deferred_to_dry_run(registry):
    planner = GoalPlanner(registry)
    planner.index.methods = {'mrtrix.tckgen': planner.index.methods['mrtrix.tckgen'].model_copy(deep=True)}
    method = planner.index.methods['mrtrix.tckgen']
    method.input_slots[0].requires_features = {'normalized': True}
    planner.index.producers = {'tractogram': {'mrtrix.tckgen'}}
    request = query(existing_inputs=[{'artifact_type':'fod_image', 'path':'fod.mif','fact_id':'fact:fod'}])
    from app.task_contract import ArtifactConstraint
    request.task_contract.allowed_existing_inputs.append(ArtifactConstraint(artifact_type='fod_image'))
    state, result = planner.search(request, {})
    assert result['status']=='matched' and state['routes']
    assert any('normalized' in note and 'dry-run' in note for note in result['input_notes'])


def test_search_cancellation_propagates(registry):
    operation = OperationContext(Event(), lambda _: None)
    operation.cancel_event.set()
    token = current_operation.set(operation)
    try:
        with pytest.raises(OperationCancelled):
            GoalPlanner(registry).search(query(), {})
    finally:
        current_operation.reset(token)


def test_coordinator_restores_route_instead_of_rewriting_known_graph():
    from app.agent_runtime import PLANNING_TOOL
    from app.agent_team import coordinator_tools
    original=json.dumps(PLANNING_TOOL)
    task={'task_id':'current','method_planning':{'task_id':'current','state':{'summary':{
        'status':'matched','routes':[{'route_id':'route:known'}]}}}}
    schema=coordinator_tools([PLANNING_TOOL],task)[0]['function']['parameters']
    assert schema['required']==['goal','route_id'] and schema['properties']['route_id']['enum']==['route:known']
    assert 'steps' not in schema['properties'] and '$defs' not in schema
    assert json.dumps(PLANNING_TOOL)==original
    task['task_id']='new-task'
    assert 'steps' in coordinator_tools([PLANNING_TOOL],task)[0]['function']['parameters']['properties']


@pytest.fixture
def workflow_case():
    case = contract_fixtures.TaskContractPlanningTests()
    case.setUp()
    try:
        yield case
    finally:
        case.tearDown()


def test_eligible_subset_is_saved_and_only_required_inputs_define_scope(workflow_case):
    case = workflow_case
    request = case.direct_request()
    for step in request._registered_plan.steps:
        if step.method_id in {'mrtrix.dwi2response','mrtrix.dwi2fod'}:
            step.parameters['gradient_source']='fsl'
    (case.dataset_folder/'sub-02/dwi/sub-02_dwi.bval').unlink()
    workflow, _ = asyncio.run(case.service.draft(request, {'recommend_workflow':True,'capability_ids':[]}))
    assert workflow.sample_scope is None
    assert all(b.deferred_resolution for s in workflow.steps for b in s.input_bindings.values() if b.source=='existing_path')
    from app.workflows.preflight_inputs import resolve_planned_inputs
    resolve_planned_inputs(workflow,case.dataset_service)
    case.service.state.refresh(workflow)
    assert workflow.sample_scope.total == 2
    assert len(workflow.sample_scope.eligible_sample_ids) == 1
    assert len(workflow.sample_scope.skipped_samples) == 1
    assert any('b' in reason for reason in workflow.sample_scope.skipped_samples[0].reasons)
    assert all(not s.missing_required_inputs for s in workflow.steps)
    restricted = restrict_sample_bindings(workflow, workflow.sample_scope)
    for step in restricted.steps:
        for binding in step.input_bindings.values():
            if binding.binding_mode=='per_sample':
                assert all(m.sample_id in workflow.sample_scope.eligible_sample_ids for m in binding.sample_matches)
    assert case.store.list_runs(workflow_id=workflow.workflow_id) == []
    case.service.chat_service.plan_registered_methods.assert_not_awaited()


def test_disjoint_required_inputs_never_create_a_runnable_plan(workflow_case):
    case = workflow_case
    request = case.direct_request()
    for step in request._registered_plan.steps:
        if step.method_id in {'mrtrix.dwi2response','mrtrix.dwi2fod'}:
            step.parameters['gradient_source']='fsl'
    # Each representative comes from a different valid subject, but the joint
    # set is empty. Per-port positive counts must not be mistaken for readiness.
    (case.dataset_folder/'sub-02/dwi/sub-02_dwi.bval').unlink()
    (case.dataset_folder/'sub-01/dwi/sub-01_dwi.bvec').unlink()
    for binding in request._workflow_input_bindings:
        if binding.slot_id=='bvecs':
            binding.path='sub-02/dwi/sub-02_dwi.bvec'
    workflow,_=asyncio.run(case.service.draft(request, {'recommend_workflow':True,'capability_ids':[]}))
    from app.workflows.preflight_inputs import resolve_planned_inputs
    resolve_planned_inputs(workflow,case.dataset_service)
    case.service.state.refresh(workflow)
    assert not workflow.sample_scope.eligible_sample_ids
    assert not workflow.can_confirm
    assert not case.store.list_runs(workflow_id=workflow.workflow_id)


def test_saved_route_prepares_real_workflow_and_survives_team_restart(workflow_case, monkeypatch):
    from app.agent_team import AgentTeam
    from app.agent_runtime import execute
    from app.chat_service import ChatService
    from app.conversation_service import ConversationService
    from app.data_operations import DataOperations
    from app.dataset_files import DatasetFiles
    from app.models import ChatRequest
    from app.workspace_models import WorkspaceCreate
    from app.workspace_service import WorkspaceService
    from app.workspace_tools import WorkspaceTools
    case = workflow_case
    output = Path(case.temporary.name)/'outputs'
    output.mkdir()
    monkeypatch.setenv('WORKSPACE_ALLOWED_ROOTS', case.temporary.name)
    monkeypatch.setenv('WORKSPACE_DEFAULT_ROOT', str(output))
    workspace = WorkspaceService(case.store, output)
    ws = workspace.create(WorkspaceCreate(name='output',root_path=str(output)))
    conversations = ConversationService(case.store, ChatService())
    conv = conversations.create('goal route')
    workspace.bind_conversation(conv.conversation_id,ws.id)
    dataset_files = DatasetFiles(case.dataset_service)
    owner = WorkspaceTools(workspace, case.service.chat_service, dataset_files)
    owner.workflow_service = case.service
    owner.data_operations = DataOperations(workspace, dataset_files)
    req = ChatRequest(message='所有样本生成纤维',conversation_id=conv.conversation_id,
        workspace_id=ws.id,dataset_id=case.dataset.dataset_id,client_turn_id='route-test-000001')
    req,_ = conversations.begin_turn(req)
    team = AgentTeam(owner,req)
    team.ledger.update_goal(req,{'goal':req.message,'deliverable':'workflow','success_criteria':['对具备输入的样本生成纤维']})
    q = query()
    q.parameters={'mrtrix.dwi2response':{'gradient_source':'fsl'},'mrtrix.dwi2fod':{'gradient_source':'fsl'}}
    paths={'preprocessed_dwi':'sub-01/dwi/sub-01_dwi.nii.gz','brain_mask':'sub-01/dwi/sub-01_mask.nii.gz'}
    paths.update({'bval':'sub-01/dwi/sub-01_dwi.bval','bvec':'sub-01/dwi/sub-01_dwi.bvec'})
    async def run():
        await team.query('data','query_data_files',{'source_scope':'dataset','selection':{'path':'.'},
            'patterns':list(paths.values()),'limit':20})
        for item in q.existing_inputs:
            path=paths.get(item.artifact_type) or paths['bval' if item.path.endswith('.bval') else 'bvec']
            item.path=path
            item.fact_id=''  # Backend resolves the actual reference from saved path evidence.
        note=case.dataset_folder/'note.txt'
        note.write_text('unrelated evidence')
        unrelated=await team.query('data','inspect_dataset_path',{'path':'note.txt'})
        q.existing_inputs[0].fact_id=unrelated['fact_id']  # Correct a valid but imprecise data reference locally.
        q.existing_inputs[0].features={'not_proven':True}
        # Confirmed missing input on subject 2 narrows execution, not planning.
        (case.dataset_folder/'sub-02/dwi/sub-02_dwi.bval').unlink()
        routes=await team.query('methods','read_method_routes',q.model_dump())
        assert routes['status']=='matched'
        assert routes['routes'][0]['input_selection']=='user'
        assert routes['routes'][0]['fact_ids']==[]
        assert routes['routes'][0]['inputs_to_select']
        restored=AgentTeam(owner,req)
        assert json.loads(restored.ledger.context(req))['method_routes']['routes']==routes['routes']
        from app.goal_planner import saved_route
        from app.task_contract import TaskContract
        assert saved_route(owner,restored.ledger,req,routes['routes'][0]['route_id'],
            TaskContract.model_validate(req.assistant_task['method_planning']['query']['task_contract']))
        result=await execute(owner,restored.ledger,req,'prepare_workflow',{
            'goal':req.message,'route_id':routes['routes'][0]['route_id']},[])
        assert not result['inputs_ready'] and result['executed'] is False
        assert result['draft_ready'] and result['input_selection']=='user'
        assert result['execution_configuration']['execution_scope'] == 'scientific_runner'
        assert not result['execution_configuration']['execution_verified']
        assert result['sample_scope'] is None
        assert result['input_validation']=='pending_dry_run'
        # No extra model graph generation and no automatic execution.
        assert case.store.list_runs(workflow_id=result['id'])==[]
        return restored, result
    try:
        restored,result=asyncio.run(run())
        restored.ledger.record(req,'prepare_workflow',{},result)
        original_scope=req.assistant_task['artifacts'][result['id']]['sample_scope']
        expanded={'total':1000,'eligible_sample_ids':[str(i) for i in range(600)],
            'skipped_samples':[{'sample_id':str(i),'label':str(i),'reasons':['missing input']} for i in range(600,1000)]}
        req.assistant_task['artifacts'][result['id']]['sample_scope']=expanded
        projected=json.loads(restored.ledger.context(req))['artifacts'][result['id']]['sample_scope']
        assert projected['eligible_count']==600 and projected['skipped_count']==400
        assert len(projected['eligible_sample_ids'])==len(projected['skipped_samples'])==8
        assert req.assistant_task['artifacts'][result['id']]['sample_scope']==expanded
        req.assistant_task['artifacts'][result['id']]['sample_scope']=original_scope
        restored.ledger.finish(req,{'status':'awaiting_inputs','summary':'结构方案已生成，用户选择输入后预检样本范围','artifact_ids':[result['id']]})
        assert req.assistant_task['phase']=='awaiting_inputs'
        # A same-named output file cannot supply evidence for a dataset input.
        output_file=output/paths['preprocessed_dwi']
        output_file.parent.mkdir(parents=True)
        output_file.write_bytes(b'other scope')
        observed=asyncio.run(restored.query('data','query_data_files',{'source_scope':'output',
            'selection':{'path':'.'},'patterns':[paths['preprocessed_dwi']],'limit':20}))
        wrong=q.model_copy(deep=True)
        wrong.existing_inputs[0].fact_id=observed['fact_id']
        from app.goal_planner import read_routes
        repeat=read_routes(owner,restored.ledger,req,wrong.model_dump())
        assert repeat['summary_reused'] and repeat['routes'][0]['fact_ids']==[]
        assert not req._prepared_workflow.steps[-1].input_bindings.get('dwi')
        # A draft is independent of unselected source files. Dry-run validates
        # the files only after an explicit user binding.
        (case.dataset_folder/paths['preprocessed_dwi']).write_bytes(b'changed')
        from app.goal_planner import saved_route
        assert saved_route(owner,restored.ledger,req,result.get('route_id',routes_id(req)),q.task_contract)
    finally:
        owner.data_operations.shutdown()


def routes_id(request):
    return request.assistant_task['method_planning']['state']['routes'][0]['id']
