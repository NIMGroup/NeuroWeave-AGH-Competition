"""General understanding, pagination, batching and recovery with real synthetic files."""
import asyncio
import copy
import json
from unittest.mock import Mock

import pytest

from app.agent_context import tool_projection
from app.agent_state import AgentTasks
from app.agent_runtime import available_tools
from app.agent_identity import query_signature
from app.assistant_runtime import ProgressBudget, compact_messages
from app.data_auth import principal, Principal
from app.conversation_service import ConversationService
from app.chat_service import ChatService
from test_data_operations import data
from test_unified_agent import fixture, goal, ready, Provider


def test_inventory_projection_reaches_every_group_without_skipping(data):
    for i in range(40):
        (data.output / (chr(97+i//26)+chr(97+i%26)+'x'*100+'.csv')).write_text('key,value\n1,2')
    owner, request, _ = fixture(data, [])
    offset, seen = 0, []
    for _ in range(60):
        full = owner.execute('inventory_data_files', {'source_scope':'output','view':'summary','group_offset':offset}, request)
        shown = tool_projection(full, 'evidence:page', budget=1800)
        messages = [{'role':'tool','content':json.dumps(shown,ensure_ascii=False)},
                    {'role':'tool','content':'{}'}, {'role':'tool','content':'{}'}]
        compact_messages(messages,budget=1)
        shown = json.loads(messages[0]['content'])
        groups = shown['coverage'].get('groups', [])
        assert groups
        seen.extend(g['pattern'] for g in groups)
        next_offset = shown['coverage']['next_group_offset']
        if next_offset is None:
            break
        assert next_offset == offset + len(groups)
        offset = next_offset
    assert len(seen) == len(set(seen)) == 40


@pytest.mark.parametrize('view', ['names','details'])
def test_actual_directory_cursor_tracks_visible_rows_after_projection(data, view):
    for i in range(72):
        (data.output / (f'{i:03}-'+'x'*60+'.csv')).write_text('synthetic')
    owner, request, _ = fixture(data, [])
    cursor, seen = None, []
    for _ in range(100):
        full = owner.execute('list_output_directory', {'view':view,'limit':100,**({'cursor':cursor} if cursor else {})}, request)
        original = copy.deepcopy(full)
        shown = tool_projection(full, 'evidence:page', budget=1800)
        assert '_page_cursor' not in shown and '_item_offsets' not in shown
        assert full == original
        seen.extend(row['relative_path'] for row in shown['items'])
        cursor = shown['next_cursor']
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 72


def test_text_projection_next_offset_follows_visible_unicode():
    text = '多字节文字的实际内容。'*1000
    full = {'text':text,'offset':100,'next_offset':100+len(text),'truncated':False}
    shown = tool_projection(full, budget=1800)
    assert shown['truncated'] and shown['next_offset']==100+len(shown['text'])
    assert text.startswith(shown['text'])


def test_semantic_cache_preserves_selection_and_filter_differences():
    first = query_signature('inventory_data_files', {'source_scope':'output','view':'summary','path':'./'})
    other = query_signature('inventory_data_files', {'source_scope':'output','view':'summary',
        'inventory_id':'lease','selection':{'path':'.','include':[],'exclude':[],'category':'all'}})
    assert first == other
    assert first != query_signature('inventory_data_files', {'source_scope':'dataset','view':'summary'})
    assert first != query_signature('inventory_data_files', {'source_scope':'output','view':'summary','selection':{'exclude':['backups']}})
    assert query_signature('list_output_directory', {'path':'./'}) == query_signature('list_output_directory',
        {'path':'.','view':'names','limit':50,'sort':'name','filter':'','filter_mode':'text','show_hidden':False})
    assert query_signature('list_output_directory', {'filter':'*.csv','filter_mode':'glob'}) != query_signature(
        'list_output_directory', {'filter':'*.csv'})


def test_batch_json_fields_cover_missing_and_invalid_files_without_execution(data):
    sub=data.output/'mixed';sub.mkdir()
    for i in range(30):
        (sub/f'{i}.json').write_text(json.dumps({'kind':'even' if i%2==0 else 'odd','nested':{'value':i}}))
    (sub/'missing.json').write_text('{}')
    (sub/'invalid.json').write_text('{bad')
    (sub/'input.csv').write_text('key,value\n1,2')
    owner, request, _=fixture(data, [])
    result=owner.execute('inspect_data_metadata', {'source_scope':'output','selection':{'path':'mixed'},
        'fields':['kind','nested.value'],'group_by':['kind']}, request)
    assert result['complete'] and result['selected_format_count']==32
    assert result['scanned_count']==32 and result['invalid_count']==1 and result['matched_count']==31
    assert result['missing_counts']=={'kind':1,'nested.value':1}
    assert sum(v['count'] for v in result['items'])==31
    assert {g['values']['kind'] for g in result['items']}=={'even','odd',None}
    assert not data.service.list(request.conversation_id) and not data.service.futures
    assert len(list(sub.iterdir()))==33


def test_batch_csv_profile_and_partial_coverage_are_explicit(data):
    for i in range(5):
        (data.output/f'{i}.csv').write_text('key,value\n1,2\n3,4\n')
    owner, request, _=fixture(data, [])
    args={'source_scope':'output','format':'csv','fields':['columns','row_count'],'max_files':2}
    result=owner.execute('inspect_data_metadata', args, request)
    assert not result['complete'] and result['next_file_offset']==2
    assert result['selected_format_count']==5 and result['scanned_count']==2
    assert result['items'][0]['values']['columns']==['key','value']
    assert result['items'][0]['examples'][0]['values']['row_count']==2
    result=owner.execute('inspect_data_metadata', {**args,'offset':2,'max_files':10},request)
    assert not result['complete'] and result['next_file_offset'] is None
    assert result['scanned_count']==3 and result['aggregation_scope']=='current_file_page'


def test_metadata_group_pages_reuse_scans_and_align_after_compression(data,monkeypatch):
    import app.agent_metadata as module
    for i in range(12):
        (data.output/f'{i}.json').write_text(json.dumps({'label':'x'*100+str(i)}))
    opened=Mock(wraps=module.workspace_io.open_read)
    monkeypatch.setattr(module.workspace_io,'open_read',opened)
    owner,request,_=fixture(data,[])
    args={'source_scope':'output','fields':['label'],'group_by':['label'],'limit':12}
    full=owner.execute('inspect_data_metadata',args,request)
    shown=tool_projection(full,budget=1800)
    assert len(shown['items'])<12
    assert shown['next_group_offset']==len(shown['items'])
    seen=[v['values']['label'] for v in shown['items']]
    offset=shown['next_group_offset']
    for _ in range(20):
        full=owner.execute('inspect_data_metadata',{**args,'group_offset':offset},request)
        shown=tool_projection(full,budget=1800)
        seen += [v['values']['label'] for v in shown['items']]
        offset=shown['next_group_offset']
        if offset is None:break
    assert len(seen)==len(set(seen))==12 and opened.call_count==12


@pytest.mark.parametrize('arguments', [
    {'source_scope':'output','selection':{'path':'../elsewhere'}},
    {'source_scope':'output','root_path':'/other'},
    {'source_scope':'output','format':'csv','fields':['arbitrary_calculation']},
])
def test_metadata_queries_cannot_expand_scope_or_execute_code(data,arguments):
    owner,request,_=fixture(data,[])
    with pytest.raises((ValueError,PermissionError)):
        owner.execute('inspect_data_metadata',arguments,request)


@pytest.mark.parametrize('edit', [
    {'action':'copy','path':'a.txt','destination':'copy.txt'},
    {'action':'write_text','path':'a.txt','destination':'a.txt','content':'changed'},
])
def test_inline_understanding_prepares_general_plan_without_extra_state_call(data,edit):
    (data.output/'a.txt').write_text('original')
    task={'goal':'按要求处理文件','deliverable':'file_changes','scope_notes':'当前选定输出目录',
          'success_criteria':['产生准确的待运行变更清单'],'strategy':'先预览再等待运行'}
    owner,request,provider=fixture(data,[
        [('propose_data_changes',{'source_scope':'output','mode':'inplace','task':task,'edits':[edit]})], ready])
    result=asyncio.run(owner.reply(request))
    assert len(provider.rounds)==2 and result.assistant_task['phase']=='awaiting_run'
    assert result.assistant_task['success_criteria']==task['success_criteria']
    assert (data.output/'a.txt').read_text()=='original'
    assert not (data.output/'copy.txt').exists()
    snapshot=ConversationService(data.service.store,ChatService()).snapshot(request.conversation_id)
    assert snapshot.assistant_task['scope_notes']==task['scope_notes']


def test_same_batch_goal_can_follow_proposal_in_provider_order(data):
    (data.output/'a.txt').write_text('original')
    owner,request,_=fixture(data,[[
        ('propose_data_changes',{'source_scope':'output','mode':'inplace','edits':[{'action':'copy','path':'a.txt','destination':'b.txt'}]}),
        goal()],ready])
    assert asyncio.run(owner.reply(request)).assistant_task['phase']=='awaiting_run'
    assert not any(e['status']=='failed' for e in data.workspace.events(request.client_turn_id))


def test_schema_refs_for_inline_goal_resolve_at_tool_root(data):
    owner,request,_=fixture(data,[])
    definitions=available_tools(owner,request)
    plan=next(t['function']['parameters'] for t in definitions if t['function']['name']=='propose_data_changes')
    assert set(plan['properties']['task']['properties']) == {'goal','relation','deliverable','scope_notes','success_criteria','strategy','execution_kind'}
    # Full sourced choices and information needs remain in the state tool;
    # don't duplicate that schema in every proposal definition.
    state=next(t['function']['parameters'] for t in definitions if t['function']['name']=='update_task_state')
    assert state['properties']['decisions']['items']['$ref']=='#/$defs/Decision'
    assert 'Decision' in state['$defs']


def test_budget_preserves_closure_time_and_cached_queries_do_not_consume_call_limit():
    now=[0]
    budget=ProgressBudget(clock=lambda:now[0],duration=100,reserve_seconds=20)
    budget.observe({'items':[{'path':'a'}]})
    for _ in range(2):budget.observe({'items':[{'path':'a'}]},cached=True)
    assert budget.calls==1 and budget.stalled==2
    now[0]=81
    assert budget.stop_reason()=='time_budget' and budget.remaining_seconds==19
