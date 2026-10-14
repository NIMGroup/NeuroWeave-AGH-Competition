"""Canonical goals and human closure; no extra model rounds or execution grants."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.agent_runtime import execute, run
from app.agent_state import AgentTasks
from app.agent_team import AgentTeam
from app.chat_service import ChatService
from app.conversation_service import ConversationService
from app.data_models import DataConfirmation
from app.goal_planner import read_routes, saved_route
from app.managed_tasks import ManagedTasks
from app.method_planning import MethodPlan
from app.models import ChatRequest
from app.operation_service import OperationService
from app.task_contract import TaskContract
from app.task_management import TaskActionError, TaskConfirmation, confirm_task, edit_task
from test_agent_team import TeamProvider
from test_conversation_task_plans import task_case, user_edit
from test_data_operations import data as data
from test_declared_input_planning import route_case
from test_goal_planner import workflow_case as workflow_case
from test_managed_task_identity import prepared


def acceptance(request):
    state = request.assistant_task
    return TaskConfirmation(task_id=state['task_id'],expected_revision=state['task_revision'],
        expected_plan=state.get('active_plan'),confirmed=True)


def workflow_goal(case):
    conversations = ConversationService(case.store,ChatService())
    conv = conversations.create('流程目标')
    request,_ = conversations.begin_turn(ChatRequest(message='生成FOD',
        conversation_id=conv.conversation_id,dataset_id=case.dataset.dataset_id))
    ledger = AgentTasks(case.store)
    ledger.begin(request)
    ledger.update_goal(request,{'goal':'生成FOD','deliverable':'workflow',
        'task_contract':{'goal_artifact_types':['fod_image']}})
    request.task_contract = TaskContract.model_validate(request.assistant_task['task_contract'])
    request._input_selection='user'
    request._registered_plan=MethodPlan(status='matched',
        steps=[{'id':'fod','method_id':'mrtrix.dwi2fod'}])
    workflow=asyncio.run(case.service.draft(request,{'recommend_workflow':True}))[0]
    ledger.record(request,'prepare_workflow',{},dict(id=workflow.workflow_id,kind='workflow',
        revision=workflow.revision,status='pending',input_selection='user',draft_ready=True))
    return ledger,request,workflow


@pytest.mark.parametrize('echo',[
    None, {'goal_artifact_types':['tractogram']},
    {'goal_artifact_types':['tractogram','fod_image'],'goal':'different wording'},
    {'goal_artifact_types':['fod_image','tractogram'],'sample_policy':'all'},
])
def test_methods_inherit_multiple_outputs_and_required_stages_without_rewriting(data,echo):
    owner,request,ledger=route_case(data)
    ledger.update_goal(request,{'goal':'生成FOD和纤维','task_contract':{
        'goal_artifact_types':['fod_image','tractogram'],
        'required_output_types':['response_functions']}})
    args={} if echo is None else {'task_contract':echo}
    result=read_routes(owner,ledger,request,args)
    assert result['status']=='matched'
    graph=saved_route(owner,ledger,request,result['routes'][0]['route_id'])
    assert [s['method_id'] for s in graph['steps']]==[
        'mrtrix.dwi2response','mrtrix.dwi2fod','mrtrix.tckgen']
    canonical=request.assistant_task['method_planning']['query']['task_contract']
    assert canonical['goal_artifact_types']==['fod_image','tractogram']
    assert canonical['required_output_types']==['response_functions']
    repeat=read_routes(owner,ledger,request,{})
    assert repeat['summary_reused'] and repeat['routes']==result['routes']
    owner.data_operations._roots.assert_not_called()
    assert owner.chat.requests==[]


def test_methods_cannot_broaden_canonical_generation_permission(data):
    owner,request,ledger=route_case(data,[{'artifact_type':'fod_image','available':False}])
    ledger.update_goal(request,{'goal':'不生成缺失输入','task_contract':{
        'goal_artifact_types':['tractogram'],'allow_generated_intermediates':False}})
    result=read_routes(owner,ledger,request,{'task_contract':{
        'goal_artifact_types':['tractogram'],'allow_generated_intermediates':True}})
    assert result['status']!='matched' and not result['routes']


def test_model_completed_does_not_permit_new_goal_or_clear(data):
    owner,request,ledger=task_case(data)
    ledger.save(request,{**request.assistant_task,'phase':'completed'})
    old_id=request.assistant_task['task_id']
    with pytest.raises(TaskActionError,match='尚未'):
        ledger.update_goal(request,{'relation':'new','goal':'另一项需求','deliverable':'workflow'})
    with pytest.raises(ValueError,match='先确认'):
        edit_task(ledger.store,request.conversation_id,user_edit(request),clear=True)
    assert ledger.load(request)['task_id']==old_id
    assert not ledger.load(request).get('user_completion')
    ledger.update_goal(request,{'goal':'临时解释目标','deliverable':'answer'})
    with pytest.raises(TaskActionError,match='尚未'):
        ledger.update_goal(request,{'relation':'new','goal':'另一项需求','deliverable':'workflow'})


def test_human_acceptance_is_idempotent_and_late_writes_cannot_erase_it(data):
    owner,request,ledger=task_case(data)
    old=copy.deepcopy(request.assistant_task)
    confirmation=acceptance(request)
    accepted=confirm_task(ledger.store,request.conversation_id,confirmation)
    assert accepted['phase']=='user_confirmed'
    assert confirm_task(ledger.store,request.conversation_id,confirmation)==accepted
    with pytest.raises(ValueError,match='旧请求'):
        ledger.save(request,{**old,'phase':'preparing'})
    with pytest.raises(TaskActionError,match='已由用户'):
        edit_task(ledger.store,request.conversation_id,user_edit(request))
    ledger.begin(request)
    ledger.finish(request,{'status':'answered','summary':'解释已完成的目标'})
    assert request.assistant_task['phase']=='user_confirmed'
    assert json.loads(ledger.context(request))['user_completion']==accepted['user_completion']
    assert 'closed_plan_snapshot' not in json.loads(ledger.context(request))


@pytest.mark.parametrize('status',['queued','running'])
def test_busy_chat_cannot_be_confirmed(data,status):
    owner,request,ledger=task_case(data)
    with ledger.store.transaction() as db:
        db.execute('INSERT INTO chat_turns VALUES(?,?,?)',('turn',request.conversation_id,status))
    with pytest.raises(ValueError,match='先停止'):
        confirm_task(ledger.store,request.conversation_id,acceptance(request))
    assert not ledger.load(request).get('user_completion')


def test_workflow_confirmation_freezes_exact_revision_and_blocks_execution(workflow_case):
    case=workflow_case
    ledger,request,workflow=workflow_goal(case)
    operation=OperationService(case.store)
    confirmation=acceptance(request)
    confirmation.expected_plan={**confirmation.expected_plan,'revision':workflow.revision-1}
    with pytest.raises(ValueError,match='版本'):
        confirm_task(case.store,request.conversation_id,confirmation)
    confirmation=acceptance(request)
    with case.store.transaction() as db:
        db.execute('INSERT INTO operations VALUES(?,?,?,?,?,NULL,NULL,?,?)',
            ('check','workflow_dry_run',workflow.workflow_id+':'+str(workflow.revision),
             'running','核查中','now','now'))
    with pytest.raises(ValueError,match='检查'):
        confirm_task(case.store,request.conversation_id,confirmation)
    with case.store.transaction() as db:
        db.execute("UPDATE operations SET status='succeeded'")
    confirm_task(case.store,request.conversation_id,confirmation)
    frozen=ManagedTasks(case.store).get(request.assistant_task['task_id'])
    assert frozen['entry']['status']=='user_confirmed'
    assert frozen['plan']['revision']==workflow.revision
    snapshot=copy.deepcopy(frozen['plan'])
    workflow.revision+=1
    workflow.title='forbidden overwrite'
    with pytest.raises(TaskActionError):
        case.store.save_workflow(workflow,event_type='changed')
    action=Mock()
    try:
        with pytest.raises(TaskActionError):
            operation.submit('workflow_dry_run',workflow.workflow_id+':1',action)
        with pytest.raises(TaskActionError):
            case.store.save_run(SimpleNamespace(status='queued',workflow_id=workflow.workflow_id))
        with pytest.raises(TaskActionError):
            case.store.save_execution_draft(workflow.workflow_id,workflow.revision-1,0,Mock())
        from app.data_auth import Principal, principal
        token=principal.set(Principal('administrator',True))
        try:
            with pytest.raises(TaskActionError):
                case.store.save_run(SimpleNamespace(status='queued',workflow_id=workflow.workflow_id))
        finally:
            principal.reset(token)
        action.assert_not_called()
        assert not case.store.list_runs()
        assert ManagedTasks(case.store).get(request.assistant_task['task_id'])['plan']==snapshot
    finally:
        operation._executor.shutdown(wait=True)


def test_confirmation_then_new_goal_uses_new_identity_and_keeps_closed_goal(data):
    ledger,request,ops=prepared(data)
    identity=request.assistant_task['task_id']
    confirm_task(ledger.store,request.conversation_id,acceptance(request))
    ledger.begin(request)
    with pytest.raises(TaskActionError):
        ledger.update_goal(request,{'goal':'修改已确认目标'})
    ledger.update_goal(request,{'relation':'new','goal':'下一项整理','deliverable':'files',
        'decisions':[{'quote':request.message,'reference':'current'}]})
    assert request.assistant_task['task_id']!=identity and 'active_plan' not in request.assistant_task
    assert not request.assistant_task.get('user_completion')
    registry=ManagedTasks(ledger.store)
    assert registry.closed_in_conversation(request.conversation_id)[0]['task_id']==identity
    assert registry.get(identity)['current_plan']['id']==ops[-1]['id']
    assert registry.get(identity)['entry']['status']=='user_confirmed'
    with pytest.raises(TaskActionError):
        data.service.confirm(ops[-1]['id'],DataConfirmation(confirmed=True,
            conversation_id=request.conversation_id,manifest_hash=ops[-1]['manifest_hash']))
    assert list(data.output.iterdir())==[] and not data.service.futures


def test_scope_change_does_not_silently_start_a_second_goal(data):
    owner,request,ledger=task_case(data)
    identity=request.assistant_task['task_id']
    request.dataset_id=None
    response=asyncio.run(run(owner,request))
    assert '尚未归档' in response.content
    assert response.assistant_task['task_id']==identity
    request.dataset_id='ds_test'
    assert ledger.load(request)['task_id']==identity and owner.chat.requests==[]


def test_missing_goal_returns_owner_action_without_repeated_methods_queries(data):
    owner,request,ledger=task_case(data)
    ledger.save(request,{k:v for k,v in request.assistant_task.items() if k!='task_contract'})
    owner.chat=TeamProvider(methods=[[('read_method_routes',{})]])
    team=AgentTeam(owner,request)
    report=asyncio.run(team.delegate({'agent':'methods','question':'查询路线'}))
    assert report['action_owner']=='coordinator'
    assert report['error_code']=='planning_goal_missing'
    assert 'update_task_state' in report['next_action']
    assert len(owner.chat.requests)==1
    assignment=json.loads(owner.chat.requests[0][1][1]['content'])
    assert assignment['task_goal']==request.assistant_task['goal']
    assert assignment['user_requirements']==request.assistant_task['user_requirements']


def test_user_owned_error_ends_this_turn_and_skips_later_proposals(data):
    owner,request,ledger=task_case(data)
    owner.chat=TeamProvider(coordinator=[[
        ('update_task_state',{'relation':'new','goal':'另一需求','deliverable':'files'}),
        ('propose_data_changes',{'mode':'copy','edits':[{'action':'copy','path':'a.txt','destination':'b.txt'}]})]])
    identity=request.assistant_task['task_id']
    result=asyncio.run(run(owner,request))
    assert result.assistant_task['task_id']==identity
    assert result.assistant_task['phase']=='needs_input'
    assert '确认完成并结束此目标' in result.content
    assert len(owner.chat.requests)==1
    assert data.service.list(request.conversation_id)==[] and list(data.output.iterdir())==[]


def test_archived_goal_can_be_discussed_without_reentering_plan_preparation(data):
    owner,request,ledger=task_case(data)
    state=request.assistant_task
    ledger.save(request,{**state,'method_planning':{'task_id':state['task_id'],
        'task_revision':state['task_revision'],'state':{'summary':{
            'status':'matched','routes':[{'route_id':'old-route'}]}}}})
    confirm_task(ledger.store,request.conversation_id,acceptance(request))
    owner.chat=TeamProvider(coordinator=['这是已确认完成的目标说明。'])
    result=asyncio.run(run(owner,request))
    assert result.assistant_task['phase']=='user_confirmed' and result.assistant_task['user_completion']
    assert result.content=='这是已确认完成的目标说明。' and len(owner.chat.requests)==1
    tools=owner.chat.requests[0][2]
    assert 'update_task_state' in tools and 'prepare_workflow' not in tools and 'delegate_agent' not in tools


def test_completion_api_and_opening_old_goal_preserve_the_current_goal(workflow_case):
    import httpx
    from fastapi import FastAPI
    from app.api.conversations import router_for as conversation_router
    from app.api.managed_tasks import router_for as tasks_router
    case=workflow_case
    ledger,request,workflow=workflow_goal(case)
    identity=request.assistant_task['task_id']
    submit=Mock()
    services=SimpleNamespace(local_store=case.store,workflow_service=case.service,
        conversation_service=ConversationService(case.store,ChatService()),
        chat_turns=SimpleNamespace(active=lambda:dict(turns=[]),submit=submit))
    app=FastAPI()
    app.include_router(conversation_router(services))
    app.include_router(tasks_router(services))
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            url='/api/conversations/'+request.conversation_id
            result=await client.post(url+'/task/confirm-completion',json=acceptance(request).model_dump())
            assert result.status_code==200 and result.json()['user_completion']
            assert (await client.post('/api/tasks/'+identity+'/replan')).status_code==409
            submit.assert_not_called()
            ledger.begin(request)
            ledger.update_goal(request,{'relation':'new','goal':'下一目标','deliverable':'workflow',
                'decisions':[dict(quote=request.message,reference='current')]})
            current=request.assistant_task['task_id']
            opened=await client.post('/api/tasks/'+identity+'/open-conversation')
            assert opened.status_code==200 and opened.json()['conversation_id']==request.conversation_id
            pointer=(await client.get(url+'/task')).json()
            assert pointer['task']['task_id']==current
            assert [t['task_id'] for t in pointer['closed_tasks']]==[identity]
            frozen=(await client.get('/api/tasks/'+identity)).json()
            assert frozen['plan']['revision']==workflow.revision
            assert frozen['task']['user_completion'] and 'closed_plan_snapshot' not in frozen['task']
            assert case.store.list_runs()==[]
    asyncio.run(check())
