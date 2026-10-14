"""General dialogue migration, evidence contracts and deadline-safe delivery."""
import asyncio
import json
from unittest.mock import Mock

import pytest

from app.agent_facts import SharedFacts
from app.agent_reports import report_for, checkpoint_report
from app.agent_state import AgentTasks
from app.agent_team import AgentTeam
from app.chat_turns import ChatTurns
from app.chat_service import ChatService
from app.conversation_service import ConversationService
from app.data_models import PythonRequest
from app.script_contract import validate_script
from test_agent_team import TeamProvider, setup, goal, finish
from test_data_operations import data


def test_old_chat_alias_uses_same_coordinator_and_manual_plan(data):
    (data.output/'a.txt').write_text('original')
    provider=TeamProvider(coordinator=[[goal(), ('propose_data_changes',
        {'source_scope':'output','mode':'inplace','edits':[{'action':'rename','path':'a.txt','destination':'b.txt'}]})],finish])
    owner,req=setup(data,provider);req.interaction_mode='chat'
    result=asyncio.run(owner.reply(req))
    assert req.interaction_mode=='task' and result.assistant_task['phase']=='awaiting_run'
    assert (data.output/'a.txt').read_text()=='original' and not (data.output/'b.txt').exists()
    assert not data.service.futures


def test_shared_facts_survive_mode_alias_and_appended_context(data):
    (data.output/'a.txt').write_text('one')
    owner,req=setup(data,TeamProvider());req.interaction_mode='chat'
    team=AgentTeam(owner,req);owner.execute=Mock(wraps=owner.execute)
    first=asyncio.run(team.query('data','inspect_output_path',{'path':'a.txt'}))
    req.workspace_context+='\nextra process context'
    restarted=AgentTeam(owner,req)
    second=asyncio.run(restarted.query('data','inspect_output_path',{'path':'a.txt'}))
    assert first['fact_id']==second['fact_id'] and second['reused'] and owner.execute.call_count==1


def test_legacy_root_inventory_promoted_then_revalidated_without_losing_pages(data):
    owner,req=setup(data,TeamProvider());team=AgentTeam(owner,req)
    groups=[{'pattern':str(i),'count':1,'examples':[str(i)]} for i in range(24)]
    for offset in (0,12):
        result={'root_path':str(data.output),'complete_scan':True,'index_id':'same-index','file_count':24,
                'coverage':{'group_count':24,'group_offset':offset,'next_group_offset':offset+12 if offset==0 else None,
                            'groups':groups[offset:offset+12]}}
        team.ledger.memory.remember_evidence(req,'inventory_data_files',
            {'source_scope':'output','view':'summary','group_offset':offset,'limit':100},result)
    restarted=AgentTeam(owner,req)
    rows=restarted.facts.rows(req)
    assert len(rows)==1 and rows[0]['summary']['coverage']['groups_available_complete']
    assert rows[0]['source_version'] is None
    args={'source_scope':'output','view':'summary','group_offset':0,'limit':30}
    raw,ref=restarted.facts.raw_result(req,rows[0],args)
    version=restarted.facts.stamp(owner,req,'inventory_data_files',args)
    restarted.facts.record(req,'data','inventory_data_files',args,raw,ref,version)
    assert restarted.facts.lookup(owner,req,'inventory_data_files',{'source_scope':'output','view':'summary','group_offset':12,'limit':100})
    details=restarted.facts.read(req,{'fact_id':rows[0]['id'],'field':'coverage.groups','limit':100})
    assert details['items']==groups and details['coverage_complete']


def test_fact_reference_closes_only_grounded_need_and_bad_metadata_does_not_block_goal(data):
    (data.output/'a.txt').write_text('one')
    owner,req=setup(data,TeamProvider());team=AgentTeam(owner,req)
    fact=asyncio.run(team.query('data','inspect_output_path',{'path':'a.txt'}))
    team.ledger.update_goal(req,{**goal()[1],'decisions':[{'reference':'current','quote':'delete everything'}],
        'information_needs':[{'id':'file','question':'文件存在吗','status':'resolved','conclusion':'文件已存在',
            'evidence_refs':[fact['fact_id']]},{'id':'bad','question':'其他文件存在吗','status':'resolved',
                'conclusion':'未经查证','evidence_refs':['fact:does-not-exist']}]})
    task=req.assistant_task
    assert task['goal']==goal()[1]['goal'] and task['decisions']==[] and task['state_warnings']
    assert task['information_needs'][0]['status']=='resolved'
    assert task['information_needs'][0]['evidence_refs'][0].startswith('evidence:')
    assert task['information_needs'][1]['status']=='open'
    other=req.model_copy(update={'dataset_id':None})
    with pytest.raises(ValueError):team.facts.resolve_reference(other,fact['fact_id'])


def test_report_requires_each_assignment_item_and_preserves_unknowns():
    job={'agent':'data','question':'核实结构和字段','requirements':['结构','字段']}
    payload={'answer':'结构已确认；字段未知','fact_ids':['fact:a'],'unresolved':[],
        'findings':[{'question':'结构','status':'confirmed','conclusion':'目录存在','fact_ids':['fact:a']}]}
    report=report_for(job,payload,{'fact:a'})
    assert report['status']=='partial' and report['unresolved']==['字段'] and not report['assignment_complete']
    checkpoint=checkpoint_report(job,[{'id':'fact:a'}])
    assert checkpoint['unresolved']==['结构','字段'] and checkpoint['status']=='partial'
    invalid=report_for(job,{**payload,'fact_ids':['fact:foreign'],
        'findings':[{'question':'结构','status':'confirmed','conclusion':'未经核实','fact_ids':['fact:foreign']}]},{'fact:a'})
    assert invalid['status']=='partial' and invalid['fact_ids']==[] and invalid['report_warnings']
    assert invalid['findings'][0]['status']=='unknown'


def test_specialist_missing_resource_emits_failure_and_explicit_gap(data):
    provider=TeamProvider(runtime=[[('get_workflow_result',{'workflow_id':'unknown'})],
        [('finish_agent',{'answer':'没有登记该任务','fact_ids':[], 'unresolved':['任务ID未知'],
            'findings':[{'question':'查询任务','status':'unknown','conclusion':'任务ID未登记','fact_ids':[]}]})]])
    owner,req=setup(data,provider);team=AgentTeam(owner,req);team.ledger.update_goal(req,goal()[1])
    result=asyncio.run(team.delegate({'agent':'runtime','question':'查询任务'}))
    assert result['status']=='partial' and not result['assignment_complete']
    events=data.workspace.events(req.client_turn_id)
    assert any(e['tool']=='get_workflow_result' and e['status']=='failed' for e in events)


@pytest.mark.parametrize('code',[
    'root="/output"\nreport=root+"/summary.txt"\nopen(report,"w").write("report")',
    'from pathlib import Path\n(Path("/output")/"report.csv").write_text("x")',
    'import os\nroot="/output"\np=os.path.join(root,"metrics.json")\nopen(p,"w").write("{}")',
])
def test_script_result_contract_is_checked_before_pending_task_creation(data,code):
    with pytest.raises(ValueError,match='输出契约冲突'):
        data.service.python(PythonRequest(**data.scope,mode='copy',result_kind='changes',change_actions=['copy'],code=code))
    assert data.service.list(data.scope['conversation_id'])==[] and not data.service.futures


def test_script_contract_allows_dynamic_manifest_and_analysis_reports():
    validate_script('import json\np="/output/changes.json"\nwith open(p,"w") as f: json.dump({"edits":[]},f)\nprint("report")','changes')
    validate_script('from pathlib import Path\nPath("/output/report.txt").write_text("x")','files')
    with pytest.raises(ValueError,match='语法错误'):validate_script('if invalid','files')


def test_timeout_has_nonempty_resumable_reply_and_honest_turn_message(data):
    def timeout(messages):raise TimeoutError()
    owner,req=setup(data,TeamProvider(coordinator=[timeout]))
    turns=ChatTurns(data.service.store,ConversationService(data.service.store,ChatService()),owner.reply)
    async def run():
        submitted=turns.submit(req)
        result=await turns.wait(submitted['turn_id'])
        assert result.content.strip() and result.assistant_task['phase']=='blocked'
        assert turns.get(submitted['turn_id'])['message']=='本轮已结束，任务尚未完成'
    asyncio.run(run())
    assert AgentTasks(data.service.store).load(req)['turn_outcome']['resumable']


def test_reserved_finalization_reviews_existing_script_without_running(data,monkeypatch):
    def timeout(messages):raise TimeoutError()
    def reserved_finish(messages):
        identity=json.loads(messages[-1]['content'])['current_proposals'][-1]['result']['id']
        return [('finish_task',{'status':'awaiting_run','summary':'已核验脚本方案，等待手动运行', 'artifact_ids':[identity]})]
    data.service.sandbox.image='synthetic:readonly'
    monkeypatch.setenv('NEUROWEAVE_DATA_PYTHON_IMAGE','synthetic:readonly')
    provider=TeamProvider(coordinator=[[goal('report'),('run_data_python',
        {'source_scope':'output','mode':'analysis','code':'print("full summary")'})],timeout,reserved_finish])
    owner,req=setup(data,provider)
    result=asyncio.run(owner.reply(req))
    plan=data.service.list(req.conversation_id)[0]
    assert result.assistant_task['phase']=='awaiting_run' and plan['can_run_script']
    assert not data.service.futures and result.content.strip()
    assert provider.requests[-1][2]=={'finish_task'}


def test_legacy_prose_is_not_a_complete_specialist_assessment():
    report=report_for({'agent':'data','question':'所有输入完整吗'},
        {'answer':'输入基本齐全','fact_ids':['fact:a'],'unresolved':[]},{'fact:a'})
    assert report['status']=='partial' and report['unresolved']==['所有输入完整吗']


def test_stable_requirement_ids_keep_valid_findings_when_other_item_is_invalid():
    job={'agent':'methods','question':'选择方法','requirements':['输入要求','输出要求']}
    report=report_for(job,{'answer':'输入已确定，输出待查','fact_ids':['fact:input'], 'unresolved':[],
        'findings':[{'requirement_id':'r1','question':'输入契约（改写措辞）','status':'confirmed',
                     'conclusion':'需要已查证输入','fact_ids':['fact:input']},
                    {'requirement_id':'not-assigned','status':'confirmed','conclusion':'虚构结论','fact_ids':[]}]},{'fact:input'})
    assert report['findings'][0]['question']=='输入要求' and report['unresolved']==['输出要求']
    assert report['fact_ids']==['fact:input'] and report['report_warnings']


def test_methods_report_can_cite_shared_data_facts_by_evidence_reference(data):
    provider=TeamProvider(methods=[]);owner,req=setup(data,provider);team=AgentTeam(owner,req)
    (data.output/'a.txt').write_text('one')
    fact=asyncio.run(team.query('data','inspect_output_path',{'path':'a.txt'}))
    provider.replies['methods']=iter([[('finish_agent',{'answer':'方法输入是现有文件','fact_ids':[fact['evidence_reference']],
        'unresolved':[],'findings':[{'requirement_id':'r1','status':'confirmed','conclusion':'输入文件存在',
                                    'fact_ids':[fact['evidence_reference']], 'basis':'observation',
                                    'checks':[{'fact_id':fact['evidence_reference'],'field':'exists','expected':True}]}]})]])
    result=asyncio.run(team.delegate({'agent':'methods','question':'方法输入存在吗'}))
    assert result['status']=='answered' and result['fact_ids']==[fact['fact_id']]
    repeated=asyncio.run(team.delegate({'agent':'methods','question':'方法输入存在吗'}))
    assert repeated['reused'] and len(provider.requests)==1


def test_partial_method_report_does_not_lock_an_incomplete_candidate_set(data):
    provider=TeamProvider();owner,req=setup(data,provider);team=AgentTeam(owner,req)
    args={'method_id':'method.one','section':'contract'}
    raw={'method_id':'method.one','input_slots':[],'output_slots':[]}
    ref=team.ledger.record(req,'read_skill_method',args,raw)
    row=team.facts.record(req,'methods','read_skill_method',args,raw,ref,'v1')
    provider.replies['methods']=iter([[('finish_agent',{'answer':'第一步已知，第二步待查','fact_ids':[row['id']],
        'selected_methods':['method.one'],'unresolved':['第二步'],
        'findings':[{'requirement_id':'r1','status':'confirmed','conclusion':'第一步契约已读','fact_ids':[row['id']]}]})]])
    result=asyncio.run(team.delegate({'agent':'methods','question':'完整链路','requirements':['第一步','第二步']}))
    assert result['status']=='partial' and result['selected_methods']==['method.one']
    assert not req.assistant_task.get('method_selection')


def test_dynamic_or_shadowed_output_paths_stay_for_runtime_validation():
    validate_script('p="/output/report.txt"\nif True: p="/output/changes.json"\nopen(p,"w").write("{}")','changes')
    validate_script('p="/output/report.txt"\ndef save(p):\n    open(p,"w").write("{}")\nsave("/output/changes.json")','changes')


def test_migrated_evidence_is_not_duplicated_after_current_revalidation(data):
    owner,req=setup(data,TeamProvider());team=AgentTeam(owner,req)
    args={'source_scope':'output','view':'summary','limit':30}
    raw={'root_path':str(data.output),'complete_scan':True,'index_id':'index-one','file_count':0,
         'coverage':{'group_count':0,'group_offset':0,'groups':[],'next_group_offset':None}}
    legacy=team.ledger.memory.remember_evidence(req,'inventory_data_files',args,raw)
    with data.service.store.transaction() as db:
        db.execute('UPDATE assistant_evidence SET scope=? WHERE id=?',('old-client-context-hash',legacy))
    restarted=AgentTeam(owner,req);row=restarted.facts.rows(req)[0]
    assert set(row['imported_ids'])=={legacy,row['reference']}
    imported_ids=row['imported_ids']
    current=restarted.ledger.memory.remember_evidence(req,'inventory_data_files',args,raw)
    restarted.facts.record(req,'data','inventory_data_files',args,raw,current,
        restarted.facts.stamp(owner,req,'inventory_data_files',args))
    with data.service.store.connection() as db:
        before=db.execute('SELECT COUNT(*) FROM assistant_evidence').fetchone()[0]
    third=AgentTeam(owner,req)
    with data.service.store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM assistant_evidence').fetchone()[0]==before
    assert third.facts.rows(req)[0]['imported_ids']==imported_ids
    assert third.facts.rows(req)[0]['reference']==current
