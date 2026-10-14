"""Role isolation, shared facts and real pending plans; never automatic execution."""
import asyncio
import copy
import json
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.agent_compression import ContextCompression
from app.agent_facts import SharedFacts, packed
from app.agent_state import AgentTasks
from app.agent_team import AgentTeam
from app.chat_service import ChatService
from app.conversation_service import ConversationService
from app.models import ChatRequest
from app.operation_context import OperationCancelled, OperationContext, current_operation
from app.workspace_tools import WorkspaceTools
from test_data_operations import data


class TeamProvider:
    mode, model, provider_name = 'model', 'test', 'test'
    def __init__(self, **replies):
        self.replies = {role: iter(rows) for role, rows in replies.items()}
        self.requests = []
    async def workspace_completion(self, messages, tools, **kwargs):
        role = next((r for r in ('data', 'methods', 'runtime') if f'的 {r} 功能agent' in messages[0]['content']), 'coordinator')
        self.requests.append((role, copy.deepcopy(messages), {t['function']['name'] for t in tools}))
        value = next(self.replies[role])
        if callable(value): value = value(messages)
        if isinstance(value, str): return {'role': 'assistant', 'content': value}
        return {'role': 'assistant', 'tool_calls': [{'id': f'c-{len(self.requests)}-{i}', 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(args)}} for i, (name, args) in enumerate(value)]}


def setup(data, provider, message='把a.txt改名为b.txt'):
    owner = WorkspaceTools(data.workspace, provider, data.service.datasets)
    owner.data_operations = data.service
    owner.workflow_service = SimpleNamespace()
    req = ChatRequest(message=message, conversation_id=data.scope['conversation_id'], dataset_id='ds_test',
        workspace_id=data.ws.id, interaction_mode='task', client_turn_id='team-test-turn-0001')
    req, _ = ConversationService(data.service.store, ChatService()).begin_turn(req)
    return owner, req


def goal(deliverable='file_changes'):
    return ('update_task_state', {'goal': '按用户原话处理数据', 'deliverable': deliverable,
        'success_criteria': ['产物满足用户原话且等待手动运行']})


def done(messages):
    refs = [json.loads(m['content'])['fact_id'] for m in messages if m['role']=='tool' and 'fact_id' in json.loads(m['content'])]
    question = json.loads(messages[1]['content'])['assignment']['question']
    return [('finish_agent', {'answer': '文件路径和属性已查证，具体状态见事实。', 'fact_ids': refs, 'unresolved': [],
        'findings': [{'question': question, 'status': 'confirmed', 'conclusion': '指定路径是已存在的文件', 'fact_ids': refs}]})]


def finish(messages):
    identity = next(json.loads(m['content'])['id'] for m in reversed(messages)
                    if m['role']=='tool' and json.loads(m['content']).get('kind')=='data')
    return [('finish_task', {'status': 'awaiting_run', 'summary': '改名方案已准备，点击运行后执行。', 'artifact_ids': [identity]})]


@pytest.mark.parametrize('edit', [
    {'action':'rename','path':'a.txt','destination':'b.txt'},
    {'action':'copy','path':'a.txt','destination':'b.txt'},
    {'action':'write_text','path':'a.txt','content':'replacement'},
    {'action':'delete','path':'a.txt'},
])
def test_on_demand_data_specialist_then_real_reviewed_plan(data, edit):
    (data.output/'a.txt').write_text('original')
    provider = TeamProvider(coordinator=[
        [goal(), ('delegate_agent', {'agent':'data','question':'核实输出目录a.txt的属性'})],
        [('propose_data_changes', {'source_scope':'output','mode':'inplace',
          'edits':[edit]})], finish],
        data=[[('inspect_output_path', {'path':'a.txt'})], done])
    owner, req = setup(data, provider)
    result = asyncio.run(owner.reply(req))
    assert result.assistant_task['phase']=='awaiting_run'
    assert [r[0] for r in provider.requests]==['coordinator','data','data','coordinator','coordinator']
    assert (data.output/'a.txt').read_text()=='original' and not (data.output/'b.txt').exists()
    assert not data.service.futures
    task=data.service.list(req.conversation_id)[0]
    if edit['action']=='write_text':
        assert task['requires_overwrite_confirmation']
    if edit['action']=='delete':
        assert task['requires_delete_confirmation']
    main, _, main_tools = provider.requests[0]
    assert 'inspect_output_path' not in main_tools and 'list_skill_methods' not in main_tools
    _, _, specialist_tools = provider.requests[1]
    assert 'propose_data_changes' not in specialist_tools and 'list_skill_methods' not in specialist_tools
    facts = SharedFacts(AgentTasks(data.service.store).memory).rows(req)
    assert len(facts)==1 and facts[0]['owner']=='data'


def test_simple_question_does_not_enable_specialists(data):
    provider=TeamProvider(coordinator=['可以先讨论你的整理规则。'])
    owner,req=setup(data,provider)
    result=asyncio.run(owner.reply(req))
    assert result.assistant_task['phase']=='answered' and len(provider.requests)==1
    assert SharedFacts(AgentTasks(data.service.store).memory).rows(req)==[]


def test_specialist_yields_fact_batch_and_same_assignment_is_not_restarted(data):
    (data.output/'a.txt').write_text('original')
    provider=TeamProvider(data=[[('inspect_output_path', {'path':'a.txt'})], '继续检查', '继续检查', '继续检查'])
    owner,req=setup(data,provider)
    team=AgentTeam(owner,req);team.ledger.update_goal(req,goal()[1])
    assignment={'agent':'data','question':'核查a.txt属性'}
    report=asyncio.run(team.delegate(assignment))
    assert report['status']=='partial' and report['assignment_complete'] is False
    assert report['unresolved']==[assignment['question']]
    assert len(provider.requests)==4 and len(report['fact_ids'])==1
    repeated=asyncio.run(team.delegate(assignment))
    assert repeated['reused'] and len(provider.requests)==4
    assert 'shared_facts' not in report


def test_existing_facts_survive_new_agent_and_are_reused_without_source_query(data):
    (data.output/'a.txt').write_text('original')
    owner,req=setup(data,TeamProvider())
    team=AgentTeam(owner,req)
    owner.execute=Mock(wraps=owner.execute)
    first=asyncio.run(team.query('data','inspect_output_path',{'path':'a.txt'}))
    second=asyncio.run(team.query('data','inspect_output_path',{'path':'a.txt'}))
    assert second['reused'] and first['fact_id']==second['fact_id'] and owner.execute.call_count==1
    restarted=AgentTeam(owner,req)
    third=asyncio.run(restarted.query('data','inspect_output_path',{'path':'a.txt'}))
    assert third['reused'] and owner.execute.call_count==1
    assert len(restarted.facts.rows(req))==1
    (data.output/'a.txt').write_text('changed')
    fourth=asyncio.run(restarted.query('data','inspect_output_path',{'path':'a.txt'}))
    assert not fourth.get('reused') and owner.execute.call_count==2
    assert len(restarted.facts.rows(req))==1


def test_contract_deduplication_and_context_omission_are_lossless(data):
    owner,req=setup(data,TeamProvider())
    team=AgentTeam(owner,req)
    result={'method_id':'method.long','definition_revision':'v1','input_slots':[{'key':f'in-{i}','required':True,'type':'image'} for i in range(100)],
        'output_slots':[{'key':'result','type':'report'}], 'essential_parameters':[{'key':'algorithm','options':['x'*1000,'y'*1000]}]}
    for _ in range(4):
        ref=team.ledger.record(req,'read_skill_method',{'method_id':'method.long','section':'contract'},result)
        team.facts.record(req,'methods','read_skill_method',{'method_id':'method.long','section':'contract'},result,ref,'v1')
    rows=team.facts.rows(req)
    assert len(rows)==1 and len(rows[0]['summary']['input_slots'])==100
    compressed=team.facts.context(req,budget=600)
    assert compressed['facts']==[] and compressed['omitted_fact_ids']==[rows[0]['id']]
    read=team.facts.read(req,{'fact_id':rows[0]['id']})
    assert read['summary']['input_slots']==result['input_slots']
    assert read['summary']['essential_parameters']==result['essential_parameters']
    raw=team.facts.read(req,{'fact_id':rows[0]['id'],'field':'input_slots','offset':90,'limit':10})
    assert raw['items']==result['input_slots'][90:] and raw['next_offset'] is None


def test_full_raw_evidence_is_preserved_over_old_200k_limit(data):
    owner,req=setup(data,TeamProvider())
    team=AgentTeam(owner,req)
    result={'method_id':'large','content':'x'*220000,'input_slots':[]}
    reference=team.ledger.record(req,'read_skill_method',{'method_id':'large'},result)
    with data.service.store.connection() as db:
        saved=json.loads(db.execute('select payload from assistant_evidence where id=?',(reference,)).fetchone()[0])
    assert saved['result']==result


def test_new_goal_keeps_shared_fact_evidence_protected_past_history_window(data):
    owner,req=setup(data,TeamProvider())
    team=AgentTeam(owner,req)
    team.ledger.update_goal(req, {**goal()[1], 'relation':'new'})
    assert req.assistant_task['architecture']=='coordinator-specialists-v1'
    assert req.assistant_task['phase']=='preparing'
    first=None
    for i in range(86):
        result={'method_id':f'method-{i}', 'input_slots':[{'id':'input','required':True}], 'output_slots':[]}
        args={'method_id':result['method_id']}
        ref=team.ledger.record(req,'read_skill_method',args,result)
        row=team.facts.record(req,'methods','read_skill_method',args,result,ref,'v1')
        first=first or row['id']
    assert team.facts.read(req,{'fact_id':first,'field':'input_slots'})['items'][0]['required']
    one=team.facts.read(req,{'owner':'methods','limit':50})
    two=team.facts.read(req,{'owner':'methods','offset':one['next_offset'],'limit':50})
    assert len(one['manifest'])+len(two['manifest'])==86 and two['next_offset'] is None
    assert SharedFacts.key('read_skill_method',{'method_id':'x'})==SharedFacts.key('read_skill_method',{'method_id':'x','section':'contract'})


def test_fact_scope_isolation(data):
    owner,req=setup(data,TeamProvider())
    team=AgentTeam(owner,req)
    (data.output/'a.txt').write_text('one')
    result=asyncio.run(team.query('data','inspect_output_path',{'path':'a.txt'}))
    other=req.model_copy(update={'dataset_id':None})
    assert team.facts.rows(other)==[]
    with pytest.raises(ValueError):team.facts.read(other,{'fact_id':result['fact_id']})


def test_specialist_cannot_prepare_or_execute_a_script(data):
    provider=TeamProvider(coordinator=[
        [goal(),('delegate_agent',{'agent':'data','question':'核实输入文件'})],
        [('finish_task',{'status':'blocked','summary':'缺少必要输入。','artifact_ids':[]})]],
        data=[[('run_data_python',{'source_scope':'dataset','code':'print(1)'})],
              [('finish_agent',{'answer':'未核实输入','fact_ids':[],'unresolved':['输入尚未找到']})]])
    owner,req=setup(data,provider)
    result=asyncio.run(owner.reply(req))
    assert result.assistant_task['phase']=='blocked' and not data.service.list(req.conversation_id)
    replies=[json.loads(m['content']) for m in provider.requests[2][1] if m['role']=='tool']
    assert any('无权调用' in r.get('error','') for r in replies)


def test_stop_propagates_out_of_specialist_instead_of_becoming_blocked(data):
    stop=Event()
    def stopped(messages):
        stop.set()
        return [('finish_agent',{'answer':'停止','fact_ids':[],'unresolved':[]})]
    provider=TeamProvider(coordinator=[[goal(),('delegate_agent',{'agent':'data','question':'查看输入'})]],data=[stopped])
    owner,req=setup(data,provider)
    token=current_operation.set(OperationContext(stop,lambda _:None))
    try:
        with pytest.raises(OperationCancelled):asyncio.run(owner.reply(req))
    finally:current_operation.reset(token)
    state=AgentTasks(data.service.store).load(req)
    assert state['phase']!='blocked' and next(iter(state['agent_jobs'].values()))['status']=='cancelled'


def test_model_compaction_cannot_alter_shared_facts(data):
    provider=TeamProvider();owner,req=setup(data,provider)
    team=AgentTeam(owner,req)
    (data.output/'a.txt').write_text('one')
    result=asyncio.run(team.query('data','inspect_output_path',{'path':'a.txt'}))
    before=team.facts.rows(req)
    provider.json_response=AsyncMock(return_value=({'notes':['已确认文件属性，下一步构建改名方案'], 'fact_ids':[result['fact_id']]},[]))
    messages=[{'role':'system','content':'coordinator'},{'role':'user','content':'原始需求'},
        {'role':'assistant','tool_calls':[{'id':'old'}]}, {'role':'tool','tool_call_id':'old','content':packed(result)},
        {'role':'assistant','tool_calls':[{'id':'new'}]}, {'role':'tool','tool_call_id':'new','content':'{}'}]
    asyncio.run(team.compression.compact(messages,2,team.facts,'coordinator',force=True))
    assert provider.json_response.await_count==1 and team.facts.rows(req)==before
    assert messages[-1]['tool_call_id']=='new' and '已确认文件属性' in messages[2]['content']


def test_selected_methods_limit_future_specialist_tools(data):
    provider=TeamProvider(methods=[[('finish_agent',{'answer':'继续复用已核实契约','fact_ids':[],'unresolved':[]})]])
    owner,req=setup(data,provider)
    team=AgentTeam(owner,req);team.ledger.update_goal(req,dict(goal()[1]))
    team.ledger.save(req,{**req.assistant_task,'method_selection':{'method_ids':['m1'],'reason':'满足目标'}})
    result=asyncio.run(team.delegate({'agent':'methods','question':'补充方法参数','purpose':'fill_gap'}))
    assert result['agent']=='methods' and 'list_skill_methods' not in provider.requests[0][2]
    with pytest.raises(ValueError,match='具体不兼容原因'):
        asyncio.run(team.delegate({'agent':'methods','question':'换一组候选','purpose':'reconsider'}))


def test_object_relative_paths_and_partial_unknown_counts(data,monkeypatch):
    from app.agent_file_queries import query_files
    from app.dataset_models import DatasetSample
    ds=data.service.store.get_dataset('ds_test')
    ds.inspection.samples=[DatasetSample(sample_id=f's-{i}',label=str(i),relative_root=f's-{i}') for i in range(2)]
    ds.inspection.sample_count=2;data.service.store.save_dataset(ds)
    owner,req=setup(data,TeamProvider())
    inventory={'source_scope':'dataset','root_path':str(data.source),'file_count':1,'complete_scan':False,
        'scope_complete':False,'inventory_cached':True,'inventory_id':'i'}
    rows=[{'path':'s-0/dwi/DTI.nii.gz','size':1}]
    monkeypatch.setattr('app.agent_file_queries.rows_for',lambda *args:(inventory,rows))
    args={'source_scope':'dataset','required_patterns':{'image':'dwi/DTI.nii.gz'}}
    result=query_files(data.service,req,{**data.scope,'mode':'inplace'},args)
    assert result['ready_object_count']==1 and result['unknown_counts']=={'image':1} and result['missing_counts']=={}
    assert result['coverage_state']=='partial' and not result['input_coverage_complete']
    inventory.update(complete_scan=True,scope_complete=True)
    result=query_files(data.service,req,{**data.scope,'mode':'inplace'},args)
    assert result['missing_counts']=={'image':1} and result['unknown_counts']=={}


def test_no_required_patterns_does_not_claim_inputs_ready(data):
    from app.agent_file_queries import query_files
    (data.source/'s').mkdir();(data.source/'s/file.txt').write_text('one')
    owner,req=setup(data,TeamProvider())
    result=query_files(data.service,req,{**data.scope,'mode':'inplace'},{'source_scope':'dataset'})
    assert result['ready_object_count']==0 and result['coverage_state']=='not_evaluated'
