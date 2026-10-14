"""Current effects, scoped feedback, evidence honesty and generic script checks."""
import asyncio
import json
import pytest

from app.agent_artifacts import guidance, read_artifact, stage_summary
from app.agent_reports import report_for
from app.agent_team import AgentTeam, FINISH_AGENT
from app.data_models import DataEdit, PythonRequest
from app.script_verification import ScriptCase, sandbox_program, verify_manifest
from test_agent_team import TeamProvider, setup
from test_data_operations import data, run_script
from test_data_script_changes import prepared


def test_feedback_injects_current_manifest_and_finishes_without_history_or_execution(data):
    (data.output / 'a.txt').write_text('original')
    def review(messages):
        current = json.loads(messages[1]['content'])['current_artifact']
        assert current['phase'] == 'files' and current['manifest_ready']
        assert current['stage_guidance']['action'] == 'apply_manifest'
        assert current['script_source']['content']
        assert current['version'] != original_version
        return [('finish_task', {'status':'awaiting_run', 'summary':'清单已核验。', 'artifact_ids':[task['id']]})]
    provider = TeamProvider(coordinator=[review])
    owner, request = setup(data, provider)
    team = AgentTeam(owner, request)
    team.ledger.update_goal(request, {'goal':'整理文件','deliverable':'file_changes'})
    task = prepared(data, [{'action':'rename','path':'a.txt','destination':'b.txt'}])
    team.ledger.record(request, 'run_data_python', {}, task)
    asyncio.run(team.finish_task({'status':'awaiting_run','summary':'已准备脚本','artifact_ids':[task['id']]}))
    original_version = request.assistant_task['artifacts'][task['id']]['version']
    preview = run_script(data, data.service.get(task['id'],request.conversation_id))
    assert preview['phase'] == 'files' and not preview['can_confirm']
    team.ledger.save(request, {**request.assistant_task, 'prepared_this_turn':[]})
    request._execution_feedback = {'source_id':task['id'], 'status':'pending', 'phase':'script'}
    result = asyncio.run(team.run())
    assert len(provider.requests) == 1
    assert result.assistant_task['phase'] == 'awaiting_run'
    assert request.assistant_task['artifacts'][task['id']]['version'] == preview['manifest_hash']
    assert '文件预览已生成' in result.content
    assert 'search_conversation_history' not in provider.requests[0][2]
    assert (data.output/'a.txt').read_text() == 'original' and not (data.output/'b.txt').exists()
    assert data.service.get(task['id'],request.conversation_id)['can_confirm']


def test_artifact_read_is_scoped_validated_and_never_grants_run(data):
    owner, request = setup(data, TeamProvider())
    team = AgentTeam(owner, request)
    with pytest.raises(PermissionError):
        asyncio.run(read_artifact(owner,team.ledger,request,{'id':'other-task'}))
    with pytest.raises(ValueError):
        asyncio.run(read_artifact(owner,team.ledger,request,{'id':'other-task','offset':-1}))
    assert not data.service.futures


@pytest.mark.parametrize('value,stage,action', [
    ({'kind':'data','status':'pending','phase':'script','manifest_ready':False},'script_preview','run_script'),
    ({'kind':'data','status':'pending','phase':'files','manifest_ready':True},'file_application','apply_manifest'),
    ({'kind':'workflow','status':'pending'},'workflow_ready','run_workflow'),
    ({'kind':'workflow','status':'pending','missing_inputs':['input']},'missing_inputs','complete_inputs'),
    ({'status':'failed'},'failed','inspect_result'),
    ({'status':'succeeded'},'succeeded','verify_result'),
])
def test_guidance_uses_actual_state(value,stage,action):
    assert guidance(value)['stage'] == stage and guidance(value)['action'] == action


def test_canonical_stage_replaces_obsolete_next_step_but_keeps_findings():
    summary,hint=stage_summary('核验发现：有两个冲突。\n\n【下一步】运行脚本后再生成预览。',
        [{'kind':'data','status':'pending','phase':'files','manifest_ready':True}])
    assert '有两个冲突' in summary and '运行脚本后' not in summary
    assert '文件预览已生成' in hint


@pytest.mark.parametrize('expected,scope,complete,confirmed', [(169,'all',True,True),(170,'all',True,False),(169,'all',False,False)])
def test_observed_totals_are_checked_against_complete_snapshot(expected,scope,complete,confirmed):
    raw={'aggregation_scope':'current_file_page','scope_complete':complete,
         'snapshot_statistics':{'parent_count':169,'complete':complete},'complete':False}
    job={'agent':'data','question':'目录总数','requirement_items':[{'id':'r1','question':'目录总数'}]}
    payload={'answer':'目录统计','fact_ids':['fact:x'],'unresolved':[], 'findings':[
        {'requirement_id':'r1','status':'confirmed','conclusion':'目录总数','fact_ids':['fact:x'],
         'basis':'observation','checks':[{'fact_id':'fact:x','field':'snapshot_statistics.parent_count','expected':expected,'scope':scope}]}]}
    result=report_for(job,payload,{'fact:x'},{'fact:x':raw})
    assert result['findings'][0]['observation_verified'] is confirmed
    assert result['assignment_complete'] is confirmed


def test_tool_schema_check_refs_resolve_from_parameter_root():
    params=FINISH_AGENT['function']['parameters']
    assert 'FactCheck' in params['$defs']
    assert params['properties']['findings']['items']['properties']['checks']['items']['$ref'] == '#/$defs/FactCheck'


def test_function_cases_cover_a_single_category_and_missing_fields():
    code='def clean(row):\n    return {k:v for k,v in row.items() if v is not None}\n'
    cases=[ScriptCase(name='ordinary',function='clean',args=[{'value':2,'empty':None}],expected={'value':2}),
           ScriptCase(name='empty',function='clean',args=[{}],expected={})]
    exec(sandbox_program(code,cases),{})
    wrong=ScriptCase(name='missing',function='clean',args=[{}],expected={'value':0})
    with pytest.raises(ValueError,match='边界案例'):
        exec(sandbox_program(code,[wrong]),{})
    case=ScriptCase(name='meaningful category',function='clean',args=['T1_801'],expected='T1_801')
    with pytest.raises(ValueError):
        exec(sandbox_program('def clean(value):\n    return value.split("_")[-1]\n',[case]),{})


def test_manifest_validates_path_sets_and_original_group_members(data):
    request=PythonRequest(**data.scope,mode='inplace',code='print(1)', result_kind='changes',change_actions=['rename'],
        manifest_invariants={'cover_selected_files':True,'preserve_stem_groups':['.csv','.json']})
    rows=[{'path':'s/table.csv'},{'path':'s/table.json'}]
    edits=[DataEdit(action='rename',path='s/table.csv',destination='s/report.csv'),
           DataEdit(action='rename',path='s/table.json',destination='s/report.json')]
    assert len(verify_manifest(edits,request,rows)['checks']) == 3
    assert verify_manifest(edits,request,rows)['input_snapshot']['source_file_count'] == 2
    with pytest.raises(ValueError,match='覆盖'):
        verify_manifest(edits[:1],request,rows)
    request.manifest_invariants.cover_selected_files=False
    with pytest.raises(ValueError,match='遗漏'):
        verify_manifest(edits[:1],request,rows)
    edits[1].destination='s/unrelated.json'
    with pytest.raises(ValueError,match='关联关系'):
        verify_manifest(edits,request,rows)


def test_provenance_selection_coverage_uses_only_allowed_paths(data):
    request=PythonRequest(**data.scope,mode='inplace',code='print(1)',result_kind='changes',change_actions=['rename'],
        manifest_invariants={'cover_selected_files':True})
    rows=[{'path':'a.csv'},{'path':'b.csv'}]
    edit=DataEdit(action='rename',path='a.csv',destination='new.csv')
    assert verify_manifest([edit],request,rows,{'a.csv'})['checks'][1]['count'] == 1


def test_feedback_history_sees_prior_reply_but_not_messages_after_snapshot(data):
    from app.agent_runtime import response
    from app.conversation_service import ConversationService
    from app.chat_service import ChatService
    from app.models import ChatRequest
    owner, request=setup(data,TeamProvider())
    team=AgentTeam(owner,request)
    conversations=ConversationService(data.service.store,ChatService())
    asyncio.run(conversations.finish_turn(request.conversation_id,response(owner,request,'先前已生成预览')))
    follow=ChatRequest(message='检查结果',conversation_id=request.conversation_id,client_turn_id='feedback-history-1',
                       dataset_id=request.dataset_id,workspace_id=request.workspace_id)
    follow._execution_feedback={'id':'synthetic'}
    follow,_=conversations.begin_turn(follow)
    memory=team.ledger.memory
    assert follow._user_sequence==1 and follow._history_through_sequence==2
    assert '先前已生成预览' in memory.execute(follow,'read_conversation_memory',{'reference':'message:2'})['content']
    newer=follow.model_copy(deep=True);newer._execution_feedback=None;newer.client_turn_id='new-human-2'
    conversations.begin_turn(newer)
    with pytest.raises(ValueError):
        memory.execute(follow,'read_conversation_memory',{'reference':'message:3'})


def test_manual_script_cannot_report_passed_cases_if_wrapper_did_not_finish(data):
    from app.data_models import DataScriptConfirmation
    from test_data_operations import finished
    (data.output/'a.txt').write_text('original')
    def incomplete(source,directory,program):
        directory.mkdir(parents=True,exist_ok=True);result=directory/'artifacts';result.mkdir()
        (result/'changes.json').write_text(json.dumps({'edits':[{'action':'rename','path':'a.txt','destination':'b.txt'}]}))
        (directory/'execution.log').write_text('script exited early')
        return result
    data.service.sandbox.image='synthetic:readonly';data.service.sandbox.run=incomplete
    task=data.service.python(PythonRequest(**{**data.scope,'source_scope':'output'},mode='inplace',result_kind='changes',
        change_actions=['rename'],code='def clean(x):\n    return x\n',test_cases=[
            {'name':'normal','function':'clean','args':['a'],'expected':'a'},
            {'name':'empty','function':'clean','args':[''],'expected':''}]))
    assert not data.service.futures
    data.service.start_python(task['id'],DataScriptConfirmation(confirmed=True,conversation_id=task['conversation_id'],script_plan_hash=task['script_plan_hash']))
    actual=finished(data,task)
    assert actual['status']=='failed' and not actual['can_confirm'] and '未完成' in actual['error']
    assert not (data.output/'b.txt').exists()
