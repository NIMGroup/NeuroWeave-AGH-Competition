"""Human goals select catalog ports; aliases never gate planning or execute files."""
import asyncio
import copy

import pytest

from app.agent_runtime import PLANNING_TOOL
from app.agent_state import STATE_TOOLS
from app.agent_team import AgentTeam, specialist_tools
from app.goal_grounding import ground_contract, output_port
from app.goal_planner import ROUTE_TOOL, read_routes, saved_route
from app.method_planning import MethodPlan, validate_plan
from app.skill_catalog import SKILL_TOOLS
from app.task_management import TaskActionError
from test_compact_candidates import catalog as catalog
from test_data_operations import data as data
from test_declared_input_planning import route_case
from test_goal_planner import registry as registry
from test_goal_planner import workflow_case as workflow_case
from test_agent_team import TeamProvider


def target(method, port):
    return {'method_id':method,'output_slot_id':port}


def natural_goal(data, goal='对数据进行处理', stages=()):
    owner, request, ledger = route_case(data)
    ledger.update_goal(request, {'goal':goal,'task_contract':{'required_stages':list(stages)}})
    assert request.assistant_task['task_contract']['goal_artifact_types'] == []
    return owner,request,ledger


@pytest.mark.parametrize('goal, method, port, artifact', [
    ('做纤维追踪，得到streamlines', 'mrtrix.tckgen','tractogram','tractogram'),
    ('生成脑掩膜', 'mrtrix.command.dwi2mask','output','brain_mask'),
    ('做受试者聚类', 'reviewed.subject-subtyping.cluster','file_metrics_json','generated_file'),
])
def test_natural_goal_routes_without_guessing_any_type(data, goal, method, port, artifact):
    owner,request,ledger=natural_goal(data,goal)
    identity=request.assistant_task['task_id']
    revision=request.assistant_task['task_revision']
    args={'targets':[target(method,port)]}
    result=read_routes(owner,ledger,request,args)
    assert result['status']=='matched'
    assert request.assistant_task['task_contract']['goal_artifact_types']==[artifact]
    assert request.assistant_task['goal']==goal
    assert request.assistant_task['task_id']==identity and request.assistant_task['task_revision']==revision
    graph=saved_route(owner,ledger,request,result['routes'][0]['route_id'])
    assert graph['steps'][-1]['method_id']==method
    validate_plan(MethodPlan(status='matched',steps=graph['steps']),
                  ground_contract(owner.workflow_service.registry,request.assistant_task['task_contract']),
                  owner.workflow_service.registry)
    repeat=read_routes(owner,ledger,request,{})
    assert repeat['summary_reused'] and repeat['routes']==result['routes']
    owner.data_operations._roots.assert_not_called()
    assert owner.chat.requests==[]


def test_old_unregistered_goal_labels_repaired_by_real_port_without_alias_table(data):
    owner,request,ledger=route_case(data,goal='streamlines')
    ledger.update_goal(request,{'goal':'纤维追踪',
        'task_contract':{'goal_artifact_types':['streamlines','tractography']}})
    result=read_routes(owner,ledger,request,{'targets':[target('mrtrix.tckgen','tractogram')]})
    assert result['status']=='matched'
    assert request.assistant_task['task_contract']['goal_artifact_types']==['tractogram']


def test_required_natural_stages_cannot_be_dropped_and_existing_inputs_are_reused(data):
    owner,request,ledger=natural_goal(data,'重新估计响应、重建FOD并追踪纤维',['重新估计响应函数','生成FOD'])
    args={'targets':[target('mrtrix.tckgen','tractogram')],
          'stage_outputs':[dict(stage_index=0,**target('mrtrix.dwi2response','wm_response')),
                           dict(stage_index=1,**target('mrtrix.dwi2fod','wm_fod'))]}
    before=copy.deepcopy(request.assistant_task)
    with pytest.raises(TaskActionError) as exc:
        read_routes(owner,ledger,request,{**args,'stage_outputs':args['stage_outputs'][:1]})
    assert exc.value.action_owner=='methods' and exc.value.recoverable is False
    assert request.assistant_task==before
    result=read_routes(owner,ledger,request,args)
    assert result['routes'][0]['method_ids']==['mrtrix.dwi2response','mrtrix.dwi2fod','mrtrix.tckgen']
    assert request.assistant_task['task_contract']['required_stages']==['重新估计响应函数','生成FOD']
    assert request.assistant_task['task_contract']['required_output_types']==['response_functions','fod_image']
    ledger.update_goal(request,{'goal':'只生成脑掩膜'})
    assert request.assistant_task['task_contract']['goal_output_refs']==[]
    assert request.assistant_task['task_contract']['required_output_refs']==[]


def test_unknown_port_has_precise_nonretryable_owner_feedback(data):
    owner,request,ledger=natural_goal(data)
    with pytest.raises(TaskActionError) as exc:
        read_routes(owner,ledger,request,{'targets':[target('mrtrix.tckgen','streamlines')]})
    assert exc.value.error_code=='goal_output_unknown' and exc.value.action_owner=='methods'
    assert exc.value.recoverable is False and 'tractogram' in str(exc.value)
    assert not request.assistant_task.get('method_planning')


def test_natural_draft_preserves_declared_scope_until_user_changes_it(workflow_case,data):
    from app.agent_runtime import execute
    case=workflow_case
    owner,request,ledger=route_case(data,[{'artifact_type':'fod_image','available':False}],
        workflow=case.service,dataset_id=case.dataset.dataset_id)
    ledger.update_goal(request,{'goal':'完成纤维追踪，从响应生成缺失的FOD'})
    result=read_routes(owner,ledger,request,{'targets':[target('mrtrix.tckgen','tractogram')]})
    route_id=result['routes'][0]['route_id']
    assert result['routes'][0]['method_ids']==['mrtrix.dwi2fod','mrtrix.tckgen']
    before=copy.deepcopy(request.assistant_task['task_contract'])
    revision=request.assistant_task['task_revision']
    prepared=asyncio.run(execute(owner,ledger,request,'prepare_workflow',{'goal':request.message,'route_id':route_id,
        'task':{'goal':request.assistant_task['goal'],'scope_notes':'重复的简短流程说明'}},[]))
    assert prepared['draft_ready'] and prepared['executed'] is False
    assert request.assistant_task['task_contract']==before
    assert request.assistant_task['task_revision']==revision
    assert not case.store.list_runs(workflow_id=prepared['id'])
    ledger.update_goal(request,{'goal':request.assistant_task['goal'],
        'declared_inputs':[{'artifact_type':'fod_image','available':True,'quote':'现在有 FOD','reference':'current'}]})
    again=read_routes(owner,ledger,request,{})
    assert again['routes'][0]['method_ids']==['mrtrix.tckgen']
    owner.data_operations._roots.assert_not_called()


def test_output_selection_rejects_another_method_with_same_generic_type(registry):
    ref=target('reviewed.subject-subtyping.cluster','file_metrics_json')
    contract=ground_contract(registry,{'goal':'聚类','plan_scope':'goal_from_inputs'},[ref])
    other=MethodPlan(status='matched',steps=[{'id':'unrelated','method_id':'reviewed.mne-eeg-tool.connectivity'}])
    with pytest.raises(ValueError,match='选定的真实输出'):
        validate_plan(other,contract,registry)


def test_candidate_refs_are_real_and_pipeline_refs_never_masquerade_as_methods(catalog):
    for kind in ('method','pipeline'):
        result=catalog.list_methods(q='纤维追踪',kind=kind,limit=4)
        assert result['items'] and len(result['items'])<=4
        for row in result['items']:
            assert row['catalog_ref']=={'entry_kind':kind,'definition_id':row['definition_id']}
            assert row['output_refs']
            for ref in row['output_refs']:
                output_port(catalog.registry,ref)
                if kind=='pipeline':
                    assert ref['method_id']!=row['definition_id']


def test_natural_query_is_allowed_bounded_and_cached_without_file_access(data,catalog):
    owner,request,ledger=natural_goal(data,'做纤维追踪')
    owner.skill_catalog=catalog
    team=AgentTeam(owner,request)
    result=asyncio.run(team.query('methods','list_skill_methods',{'q':'纤维追踪','limit':12}))
    assert len(result.get('items',result.get('sample_items',[])))<=4
    again=asyncio.run(team.query('methods','list_skill_methods',{'q':'纤维追踪','limit':12}))
    assert again['reused']
    owner.data_operations._roots.assert_not_called()


def test_model_intent_has_no_internal_type_fields_and_route_refs_have_valid_schema():
    for tool in (STATE_TOOLS[0],PLANNING_TOOL):
        contract=tool['function']['parameters']['$defs']['TaskContract']['properties']
        assert 'required_stages' in contract
        assert not {'goal_artifact_types','required_output_types','goal_output_refs'} & contract.keys()
    schema=ROUTE_TOOL['function']['parameters']
    assert '$defs' not in schema
    assert set(schema['properties']['targets']['items']['properties'])=={'method_id','output_slot_id'}
    tools=specialist_tools([*SKILL_TOOLS,ROUTE_TOOL],{'deliverable':'workflow','phase':'preparing'},'methods')
    listing=next(t for t in tools if t['function']['name']=='list_skill_methods')
    assert 'q' in listing['function']['parameters']['properties']
    assert 'output_type' not in listing['function']['parameters']['properties']


def test_matched_route_submission_has_no_redundant_goal_edit_fields():
    from app.agent_team import coordinator_tools
    task={'task_id':'task','task_revision':2,'method_planning':{'task_id':'task','task_revision':2,
        'state':{'summary':{'status':'matched','routes':[{'route_id':'route:one'}]}}}}
    schema=coordinator_tools([PLANNING_TOOL],task)[0]['function']['parameters']
    assert 'task' not in schema['properties']
    task['task_revision']=3
    schema=coordinator_tools([PLANNING_TOOL],task)[0]['function']['parameters']
    assert 'steps' in schema['properties'] and 'enum' not in schema['properties']['route_id']


def test_methods_stop_optional_calls_in_same_batch_after_route_matches(data,catalog,monkeypatch):
    owner,request,ledger=natural_goal(data,'做纤维追踪')
    ledger.update_goal(request,{'goal':request.assistant_task['goal'],'success_criteria':['生成待选择输入的真实流程草案']})
    owner.skill_catalog=catalog
    owner.chat=TeamProvider(methods=[[('read_method_routes',{'targets':[target('mrtrix.tckgen','tractogram')]}),
                                    ('list_skill_methods',{'q':'更多等价方法','limit':4})]])
    monkeypatch.setattr(catalog,'list_methods',lambda **_:pytest.fail('Matched route must end optional discovery'))
    report=asyncio.run(AgentTeam(owner,request).delegate({'agent':'methods','question':'为目标生成真实路线'}))
    assert report['planning_complete'] and report['route_ids']
    assert len(owner.chat.requests)==1
