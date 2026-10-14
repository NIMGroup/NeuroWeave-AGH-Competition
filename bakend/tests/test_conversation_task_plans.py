"""Task edits and complete graph delivery, independently of model phrasing."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.agent_state import AgentTasks
from app.capability_registry import CapabilityRegistry
from app.goal_planner import GoalPlanner, RouteQuery, read_routes, saved_route
from app.method_planning import MethodPlan, validate_plan
from app.models import ChatRequest
from app.task_contract import TaskContract
from app.task_management import TaskEdit, TaskConfirmation, edit_task, confirm_task
from test_agent_team import TeamProvider, setup
from test_data_operations import data as data
from test_goal_planner import workflow_case as workflow_case
from test_workflow_service import SKILLS_DIR


def contract():
    return TaskContract(goal_artifact_types=['tractogram'],required_output_types=['fod_image'],plan_scope='goal_from_inputs')


def test_user_selected_inputs_do_not_replace_required_intermediate():
    registry=CapabilityRegistry(SKILLS_DIR)
    state,result=GoalPlanner(registry).search(RouteQuery(input_selection='user',task_contract=contract().model_dump()),{})
    assert result['status']=='matched'
    steps=state['routes'][0]['steps']
    assert [s['method_id'] for s in steps]==['mrtrix.dwi2fod','mrtrix.tckgen']
    assert steps[-1]['inputs']['fod']['step_id']==steps[0]['id']
    assert 'mrtrix.dwi2response' not in [s['method_id'] for s in steps]
    assert 'fod' not in [s['slot_id'] for s in state['routes'][0]['bindings']]


def test_unconnected_parts_cannot_pass_full_goal_validation():
    registry=CapabilityRegistry(SKILLS_DIR)
    isolated=MethodPlan(status='matched',steps=[{'id':'fod','method_id':'mrtrix.dwi2fod'},
                                              {'id':'track','method_id':'mrtrix.tckgen'}])
    with pytest.raises(ValueError,match='不通向目标'):
        validate_plan(isolated,contract(),registry)
    with pytest.raises(ValueError,match='必需阶段'):
        validate_plan(MethodPlan(status='matched',steps=[{'id':'track','method_id':'mrtrix.tckgen'}]),contract(),registry)


def task_case(data):
    owner,request=setup(data,TeamProvider(),'从响应函数生成FOD后追踪')
    ledger=AgentTasks(data.service.store)
    ledger.begin(request)
    ledger.update_goal(request,{'goal':request.message,'deliverable':'workflow','success_criteria':['完整连接'],
        'task_contract':contract().model_dump()})
    with data.service.store.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS chat_turns(id TEXT PRIMARY KEY,conversation_id TEXT,status TEXT)')
    return owner,request,ledger


def user_edit(request):
    t=request.assistant_task
    return TaskEdit(task_id=t['task_id'],expected_revision=t['task_revision'],user_requirements='新增质量检查',
                    goal='追踪并检查质量',success_criteria=['生成纤维并提供质量报告'])


def test_task_edit_keeps_identity_facts_and_plan_but_invalidates_old_route(data):
    owner,request,ledger=task_case(data)
    ledger.save(request,{**request.assistant_task,'active_plan':{'id':'wf_old','kind':'workflow','task_revision':request.assistant_task['task_revision']},
                         'method_planning':{'query':{'stale':True}},'user_requirements':'原始要求'})
    old_id=request.assistant_task['task_id']
    edit=user_edit(request)
    changed=edit_task(data.service.store,request.conversation_id,edit)
    assert changed['task_id']==old_id and changed['task_revision']==edit.expected_revision+1
    assert changed['active_plan']['id']=='wf_old' and changed['phase']=='needs_replan'
    assert 'method_planning' not in ledger.load(request)
    with pytest.raises(ValueError,match='已更新'):
        edit_task(data.service.store,request.conversation_id,edit)
    ledger.begin(request)
    assert request.assistant_task['task_id']==old_id and request.assistant_task['user_requirements']=='新增质量检查'


def test_running_request_rejects_edit_and_delete(data):
    owner,request,ledger=task_case(data)
    edit=user_edit(request)
    with data.service.store.transaction() as db:
        db.execute('INSERT INTO chat_turns VALUES(?,?,?)',('turn',request.conversation_id,'running'))
    for clear in (False,True):
        with pytest.raises(ValueError,match='先停止'):
            edit_task(data.service.store,request.conversation_id,edit,clear=clear)
    assert ledger.load(request)['task_revision']==edit.expected_revision


def test_late_state_write_cannot_overwrite_user_edit(data):
    owner,request,ledger=task_case(data)
    old=dict(request.assistant_task)
    edit_task(data.service.store,request.conversation_id,user_edit(request))
    with pytest.raises(ValueError,match='旧请求结果'):
        ledger.save(request,{**old,'goal':'late old goal'})
    assert ledger.load(request)['goal']=='追踪并检查质量'


def test_plan_update_keeps_user_edited_fields_authoritative(data):
    owner,request,ledger=task_case(data)
    edit_task(data.service.store,request.conversation_id,user_edit(request))
    request.task_action='update_plan'
    ledger.begin(request)
    ledger.update_goal(request,{'goal':'model paraphrase','scope_notes':'model assumption',
        'success_criteria':['model criterion'],'deliverable':'workflow','task_contract':contract().model_dump()})
    assert request.assistant_task['goal']=='追踪并检查质量'
    assert request.assistant_task['success_criteria']==['生成纤维并提供质量报告']
    assert request.assistant_task['user_requirements']=='新增质量检查'


def test_explanation_preserves_completed_task_and_current_plan(data):
    owner,request,ledger=task_case(data)
    ledger.save(request,{**request.assistant_task,'phase':'completed','previous_phase':'completed',
        'active_plan':{'id':'workflow','kind':'workflow','task_revision':request.assistant_task['task_revision']}})
    ledger.finish(request,{'status':'answered','summary':'这是参数含义。'})
    assert request.assistant_task['phase']=='completed'
    assert request.assistant_task['active_plan']['id']=='workflow'


def test_previous_workflow_run_cannot_complete_new_plan_version(data,monkeypatch):
    from app.agent_runtime import refresh_artifacts
    owner,request,ledger=task_case(data)
    ledger.save(request,{**request.assistant_task,'artifacts':{
        'workflow':{'id':'workflow','kind':'workflow'},
        'run':{'id':'run','kind':'workflow_run','workflow_id':'workflow'}}})
    owner.workflow_service=SimpleNamespace(get=lambda identity:SimpleNamespace(revision=3))
    monkeypatch.setattr(owner.service.store,'list_runs',lambda **kwargs:[SimpleNamespace(
        run_id='run',workflow_revision=2,status='succeeded',state_revision=1,error_message='',artifacts=[])])
    with pytest.raises(ValueError,match='旧流程版本'):
        asyncio.run(refresh_artifacts(owner,ledger,request,['run']))


def test_delete_task_preserves_files_and_conversation_then_new_task_gets_new_identity(data):
    owner,request,ledger=task_case(data)
    file=data.output/'keep.txt'
    file.write_text('keep')
    identity=request.assistant_task['task_id']
    with pytest.raises(ValueError, match='先确认'):
        edit_task(data.service.store,request.conversation_id,user_edit(request),clear=True)
    confirm_task(data.service.store,request.conversation_id,TaskConfirmation(task_id=identity,
        expected_revision=request.assistant_task['task_revision'],confirmed=True))
    result=edit_task(data.service.store,request.conversation_id,user_edit(request),clear=True)
    assert result['task_deleted'] and file.read_text()=='keep'
    assert data.service.store.get_conversation(request.conversation_id)
    ledger.begin(request)
    assert request.assistant_task['task_id']!=identity


def test_partial_query_inherits_full_goal_and_previous_revision_cannot_be_reused(data):
    owner,request,ledger=task_case(data)
    owner.workflow_service=SimpleNamespace(registry=CapabilityRegistry(SKILLS_DIR))
    partial=read_routes(owner,ledger,request,{'task_contract':{'goal_artifact_types':['fod_image']}})
    assert partial['routes'][0]['method_ids']==['mrtrix.dwi2fod','mrtrix.tckgen']
    routes=read_routes(owner,ledger,request,{'task_contract':{'goal_artifact_types':['tractogram']}})
    assert routes['summary_reused'] and routes['routes']==partial['routes']
    identity=routes['routes'][0]['route_id']
    ledger.update_goal(request,{'goal':'同一任务增加一个质量要求'})
    with pytest.raises(ValueError,match='旧路线'):
        saved_route(owner,ledger,request,identity)


def test_route_reuses_user_parameter_choices_and_explicit_changes_win(data):
    owner,request,ledger=task_case(data)
    owner.workflow_service=SimpleNamespace(registry=CapabilityRegistry(SKILLS_DIR))
    ledger.save(request,{**request.assistant_task,'active_plan':{'id':'current','kind':'workflow'}})
    request._active_plan_snapshot=SimpleNamespace(workflow_id='current',steps=[SimpleNamespace(
        selected_method_id='mrtrix.dwi2fod',selected_parameters={'algorithm':'msmt_csd','threads':7})])
    args={'task_contract':{'goal_artifact_types':['tractogram']}}
    first=read_routes(owner,ledger,request,args)
    selected=first['routes'][0]['parameter_choices']
    fod=next(p for p in selected if p['method_id']=='mrtrix.dwi2fod')
    assert fod['parameters']['algorithm']=='msmt_csd' and fod['parameters']['threads']==7
    second=read_routes(owner,ledger,request,{**args,'parameters':{'mrtrix.dwi2fod':{'algorithm':'csd'}}})
    fod=next(p for p in second['routes'][0]['parameter_choices'] if p['method_id']=='mrtrix.dwi2fod')
    assert fod['parameters']['algorithm']=='csd' and fod['parameters']['threads']==7


def test_replanning_updates_same_workflow_preserves_user_input_and_parameter(workflow_case):
    case=workflow_case
    req=ChatRequest(message='生成FOD',dataset_id=case.dataset.dataset_id,task_contract=TaskContract(goal_artifact_types=['fod_image']))
    req._input_selection='user'
    req._registered_plan=MethodPlan(status='matched',steps=[{'id':'fod','method_id':'mrtrix.dwi2fod','parameters':{'threads':7}}])
    original=asyncio.run(case.service.draft(req,{'recommend_workflow':True}))[0]
    selected=case.service.bind_input(original.workflow_id,'fod','dwi',case.dataset.root_id,
        case.dataset.relative_path+'/{sample}/dwi/image.mif',original.revision,'template')
    original_steps=json.dumps(selected.model_dump(),sort_keys=True)
    req.task_contract=contract()
    req.message='生成FOD再追踪'
    req._registered_plan=MethodPlan(status='matched',steps=[{'id':'fod','method_id':'mrtrix.dwi2fod'},
        {'id':'track','method_id':'mrtrix.tckgen','inputs':{'fod':{'step_id':'fod','output_slot_id':'wm_fod'}}}])
    updated=asyncio.run(case.service.draft(req,{'recommend_workflow':True},existing=selected))[0]
    assert updated.workflow_id==original.workflow_id and updated.revision==selected.revision+1
    assert updated.steps[0].selected_parameters['threads']==7
    assert updated.steps[0].input_bindings['dwi'].confirmed_by_user
    assert updated.steps[1].input_bindings['fod'].upstream_step_id=='fod'
    assert updated.dry_run_report is None and updated.confirmed_at is None
    assert json.dumps(selected.model_dump(),sort_keys=True)==original_steps
    assert len(case.store.list_workflows())==1
