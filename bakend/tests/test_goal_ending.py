"""User ending is not success, and suspended computation stops before archiving."""
import asyncio
import copy
import json
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI

from app.agent_identity import task_closure
from app.agent_runtime import run
from app.api.conversations import router_for
from app.chat_service import ChatService
from app.conversation_service import ConversationService
from app.execution.run_service import RunService
from app.managed_tasks import ManagedTasks
from app.run_models import WorkflowRun, RunStep, RunExecutionOptions
from app.task_management import TaskActionError, confirm_task
from test_agent_team import TeamProvider
from test_data_operations import data as data
from test_goal_lifecycle import acceptance, workflow_goal
from test_goal_planner import workflow_case as workflow_case
from test_conversation_task_plans import task_case, user_edit
from app.task_management import edit_task


@pytest.mark.parametrize('phase',['awaiting_inputs','awaiting_run','blocked','needs_replan'])
def test_unfinished_goal_can_end_without_success_or_execution(data,phase):
    owner,request,ledger=task_case(data)
    ledger.save(request,{**request.assistant_task,'phase':phase})
    old=copy.deepcopy(request.assistant_task)
    ended=confirm_task(ledger.store,request.conversation_id,acceptance(request),outcome='ended')
    assert ended['phase']=='user_ended' and ended['user_closure']['outcome']=='ended'
    assert ended['user_closure']['previous_phase']==phase
    assert not ended.get('user_completion')
    assert confirm_task(ledger.store,request.conversation_id,acceptance(request))==ended
    with pytest.raises(ValueError,match='旧请求'):
        ledger.save(request,old)
    with pytest.raises(TaskActionError):
        edit_task(ledger.store,request.conversation_id,user_edit(request))
    assert ManagedTasks(ledger.store).get(old['task_id'])['entry']['status']=='user_ended'
    assert not ManagedTasks(ledger.store).index(status='succeeded')['items']
    assert len(ManagedTasks(ledger.store).index(status='cancelled')['items'])==1
    owner.chat=TeamProvider(coordinator=['此目标已主动结束，原需求未完成。'])
    response=asyncio.run(run(owner,request))
    assert response.assistant_task['phase']=='user_ended' and '尚未' not in response.content
    assert 'prepare_workflow' not in owner.chat.requests[0][2]
    ledger.update_goal(request,{'relation':'new','goal':'下一项','deliverable':'files',
        'decisions':[{'quote':request.message,'reference':'current'}]})
    assert request.assistant_task['task_id']!=old['task_id'] and not task_closure(request.assistant_task)
    assert list(data.output.iterdir())==[] and not data.service.futures


@pytest.mark.parametrize('status',['awaiting_method_choice','confirmed'])
def test_unrun_workflow_archives_the_exact_current_graph(workflow_case,status):
    case=workflow_case
    ledger,request,workflow=workflow_goal(case)
    workflow.status=status
    case.store.save_workflow(workflow,event_type='test_status')
    ledger.begin(request)
    before=workflow.model_dump(mode='json')
    ended=confirm_task(case.store,request.conversation_id,acceptance(request),outcome='ended')
    view=ManagedTasks(case.store).get(ended['task_id'])
    assert view['plan']==before and view['entry']['status']=='user_ended'
    assert case.store.list_runs()==[] and case.store.get_workflow(workflow.workflow_id).status==status
    workflow.title='later overwrite'
    with pytest.raises(TaskActionError):
        case.store.save_workflow(workflow,event_type='test_late_write')


def test_ending_does_not_require_replanning_a_stale_goal(workflow_case):
    case=workflow_case
    ledger,request,workflow=workflow_goal(case)
    ledger.update_goal(request,{'goal':'加入新要求但暂时不做'})
    with pytest.raises(ValueError,match='旧目标'):
        confirm_task(case.store,request.conversation_id,acceptance(request))
    result=confirm_task(case.store,request.conversation_id,acceptance(request),outcome='ended')
    assert result['user_closure']['outcome']=='ended'
    assert ManagedTasks(case.store).get(result['task_id'])['plan']['revision']==workflow.revision


def install_run(case,workflow,status='running',backend='slurm',cached_state='SUSPENDED'):
    value=WorkflowRun(run_id='test-run',workflow_id=workflow.workflow_id,workflow_revision=workflow.revision,
        status=status,execution=RunExecutionOptions(backend=backend),output_directory='/isolated/test-output',
        plan_snapshot=workflow.model_dump(mode='json'),created_at='2026-10-14T00:00:00Z',
        updated_at='2026-10-14T00:00:00Z',steps=[RunStep(step_id='fod',title='FOD',runner_id='toy',
            method_id='mrtrix.dwi2fod',status='running' if status=='running' else 'cancelled',
            scheduler={'job_id':'123','submission_token':'owned-token','state':cached_state})])
    case.store.save_run(value)
    return value


@pytest.mark.parametrize('live_state,backend,expected',[
    ('SUSPENDED','slurm',202),('RUNNING','slurm',409),(None,'slurm',409),('SUSPENDED','local',409)])
def test_paused_end_uses_actual_scheduler_state_and_never_stops_running_work(workflow_case,live_state,backend,expected):
    case=workflow_case
    ledger,request,workflow=workflow_goal(case)
    value=install_run(case,workflow,backend=backend)
    query=Mock(return_value=None if live_state is None else {'state':live_state})
    service=SimpleNamespace(store=case.store,_lock=RLock(),slurm_backend=SimpleNamespace(_query=query))
    def cancel(identity):
        current=case.store.get_run(identity)
        current.status='cancel_requested'
        current.cancellation_requested=True
        case.store.save_run(current)
    service.cancel=Mock(side_effect=cancel)
    service.stop_paused_for_goal_end=lambda wid,**kwargs:RunService.stop_paused_for_goal_end(service,wid,**kwargs)
    app=FastAPI()
    services=SimpleNamespace(local_store=case.store,run_service=service,
        conversation_service=ConversationService(case.store,ChatService()),
        chat_turns=SimpleNamespace(active=lambda:dict(turns=[])))
    app.include_router(router_for(services))
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            url='/api/conversations/'+request.conversation_id+'/task/end'
            response=await client.post(url,json=acceptance(request).model_dump())
            assert response.status_code==expected
            assert not task_closure(ledger.load(request))  # Never archive before stop acknowledgement.
            if expected==202:
                assert ledger.load(request).get('awaiting_results')==[]
                service.cancel.assert_called_once_with(value.run_id)
                query.assert_called_once_with('123','owned-token')
                again=await client.post(url,json=acceptance(request).model_dump())
                assert again.status_code==202 and service.cancel.call_count==1
                stopped=case.store.get_run(value.run_id)
                stopped.status='cancelled'
                stopped.steps[0].status='cancelled'
                stopped.steps[0].scheduler['state']='CANCELLED'
                case.store.save_run(stopped)
                ended=await client.post(url,json=acceptance(request).model_dump())
                assert ended.status_code==200 and ended.json()['user_closure']['outcome']=='ended'
                assert case.store.get_run(value.run_id).status=='cancelled'
                assert ManagedTasks(case.store).get(request.assistant_task['task_id'])['entry']['latest_run_status']=='cancelled'
            else:
                service.cancel.assert_not_called()
                assert case.store.get_run(value.run_id).status=='running'
    asyncio.run(check())


def test_stopped_run_can_be_archived_with_existing_results_preserved(workflow_case):
    case=workflow_case
    ledger,request,workflow=workflow_goal(case)
    value=install_run(case,workflow,status='cancelled',cached_state='CANCELLED')
    before=case.store.get_run(value.run_id).model_dump(mode='json')
    confirm_task(case.store,request.conversation_id,acceptance(request),outcome='ended')
    assert case.store.get_run(value.run_id).model_dump(mode='json')==before


def test_stale_end_request_cannot_close_a_new_goal(data):
    owner,request,ledger=task_case(data)
    stale=acceptance(request)
    confirm_task(ledger.store,request.conversation_id,stale,outcome='ended')
    ledger.begin(request)
    ledger.update_goal(request,{'relation':'new','goal':'下一项','deliverable':'files',
        'decisions':[{'quote':request.message,'reference':'current'}]})
    with pytest.raises(ValueError,match='已更新'):
        confirm_task(ledger.store,request.conversation_id,stale,outcome='ended')
    assert not task_closure(ledger.load(request))


def test_archived_goal_does_not_schedule_a_late_result_verification(data):
    from app.agent_continuation import AgentContinuation
    from app.chat_turns import ChatTurns
    from app.data_auth import principal
    owner,request,ledger=task_case(data)
    ledger.save(request,{**request.assistant_task,'awaiting_results':['old-result']})
    confirm_task(ledger.store,request.conversation_id,acceptance(request),outcome='ended')
    state=ledger.load(request)
    assert state['awaiting_results']==[]
    # Simulate the old persisted waiting-list format and a watcher snapshot
    # captured before the user closed the goal.
    with ledger.store.transaction() as db:
        db.execute('UPDATE assistant_tasks SET payload=? WHERE conversation_id=? AND actor=?',
            (json.dumps({**state,'awaiting_results':['old-result']}),request.conversation_id,principal.get().name))
    submit=Mock()
    continuation=AgentContinuation(SimpleNamespace(local_store=ledger.store,chat_turns=SimpleNamespace(submit_feedback=submit)))
    asyncio.run(continuation.scan())
    submit.assert_not_called()
    request._execution_feedback={'source_id':'old-result','task_id':state['task_id']}
    turns=SimpleNamespace(store=ledger.store,tasks={},_start=Mock())
    assert ChatTurns.submit_feedback(turns,request,request._user_sequence) is False
    turns._start.assert_not_called()
