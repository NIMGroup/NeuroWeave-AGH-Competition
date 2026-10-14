"""Reachable query evidence, full-scope summaries and manual-only proposals."""
import asyncio
import base64
import json
from unittest.mock import Mock

import pytest

from app.assistant_runtime import ProgressBudget, preview
from app.directory_snapshots import DirectorySnapshots
from app.models import ChatRequest
from app.workspace_tools import WorkspaceTools
from test_data_operations import data
from test_assistant_task_logic import Model, assistant, request_for, understanding
from test_workspaces import setup


def test_filter_semantics_cannot_report_false_absence(setup):
    fs, ws, root = setup
    for suffix in ('.nii.gz','.json'):
        (root/('long_acquisition'+suffix)).write_text('synthetic')
    assert len(fs.list_directory(workspace_id=ws.id,filter='.nii.gz')['items']) == 1
    assert len(fs.list_directory(workspace_id=ws.id,filter='*.nii.gz',filter_mode='glob')['items']) == 1
    with pytest.raises(ValueError,match='filter_mode=glob'):
        fs.list_directory(workspace_id=ws.id,filter='*.nii.gz')


@pytest.mark.parametrize('view',['names','details'])
def test_directory_pages_follow_visible_rows_without_losing_any_items(setup,view):
    fs, ws, root = setup
    for i in range(172):
        (root/(f'acquisition-{i:03}-'+'x'*60+'.nii.gz')).write_text('synthetic')
    tools = WorkspaceTools(fs,object())
    req = ChatRequest(message='查看文件',workspace_id=ws.id)
    cursor, seen, calls = None, [], 0
    while True:
        args = {'limit':200,'view':view,**({'cursor':cursor} if cursor else {})}
        result = tools.execute('list_output_directory',args,req)
        model = preview(result)
        assert len(model['items']) == len(result['items'])
        seen.extend(r['relative_path'] for r in model['items']); calls+=1
        cursor = model['next_cursor']
        if not cursor:break
        assert model['has_more'] and calls<30
    assert len(seen)==len(set(seen))==172
    assert result['total_items']==172


def test_snapshot_lease_has_an_absolute_expiry_even_during_dialogue():
    now=[0]
    snapshots=DirectorySnapshots(clock=lambda:now[0])
    identity,_=snapshots.create(['same'],iter([(1,'a')]))
    now[0]=63
    assert snapshots.get(identity,['same'])==[(1,'a')]
    now[0]=650
    with pytest.raises(ValueError):
        snapshots.get(identity,['same'])
    now[0]=1251
    with pytest.raises(ValueError):snapshots.get(identity,['same'])


def test_expired_cursor_recovers_only_unchanged_directory(setup,monkeypatch):
    import app.directory_snapshots as module
    clock=[0]
    snapshots=DirectorySnapshots(ttl=60,clock=lambda:clock[0])
    monkeypatch.setattr(module,'snapshots',snapshots)
    fs,ws,root=setup
    for i in range(4):(root/f'{i}.txt').write_text('synthetic')
    first=fs.list_directory(workspace_id=ws.id,limit=2)
    clock[0]=63
    second=fs.list_directory(workspace_id=ws.id,limit=2,cursor=first['next_cursor'])
    assert second['snapshot_recovered'] and second['offset']==2 and not second['has_more']
    assert not set(r['path'] for r in first['items']) & set(r['path'] for r in second['items'])
    (root/'new.txt').write_text('synthetic')
    with pytest.raises(ValueError,match='从第一页'):
        fs.list_directory(workspace_id=ws.id,cursor=first['next_cursor'])
    with pytest.raises(ValueError):
        fs.list_directory(workspace_id=ws.id,cursor=first['next_cursor'],filter='.txt')
    token=json.loads(base64.urlsafe_b64decode(first['next_cursor']))
    token.pop('context');snapshots.items.clear()
    legacy=base64.urlsafe_b64encode(json.dumps(token).encode()).decode()
    with pytest.raises(ValueError):fs.list_directory(workspace_id=ws.id,cursor=legacy)


def test_backend_hidden_rows_do_not_count_as_model_evidence():
    result={'items':[{'relative_path':f'{i}.txt','description':'x'*1000} for i in range(172)]}
    visible=preview(result,1800)
    assert len(visible['items'])<172
    budget=ProgressBudget()
    assert budget.observe(visible)
    new_page={'items':[result['items'][-1]]}
    assert budget.observe(new_page) and not budget.stalled
    assert not budget.observe(new_page)


def test_tool_loop_counts_newly_visible_pages_before_stopping():
    class Provider:
        mode='model'
        index=0
        async def workspace_completion(self,messages,tools,**kwargs):
            self.index+=1
            if self.index==10:return {'role':'assistant','content':'已查看新增样例'}
            return {'role':'assistant','tool_calls':[{'id':str(self.index),'function':
                {'name':'query','arguments':json.dumps({'page':self.index})}}]}
    rows=[{'relative_path':f'{i}.txt','description':'x'*5000} for i in range(172)]
    provider=Provider();tools=WorkspaceTools(Mock(),provider);tools.skill_catalog=Mock()
    tools.execute=Mock(side_effect=lambda name,args,request: {'items':rows if args['page']==1 else [rows[100+args['page']]]})
    answer=asyncio.run(tools.reply(ChatRequest(message='查看样例')))
    assert '新增样例' in answer and tools.execute.call_count==9


def test_summary_covers_nested_scope_and_cached_pages_do_not_rescan(data):
    for subject in range(3):
        sub=data.output/f'sub-{subject}';sub.mkdir()
        for i in range(75):(sub/f'prefix_series-{i:03}.csv').write_text('a,b\n1,2\n')
    request=request_for(data)
    tools=assistant(data,Model(understanding(),[]))
    from app.query_index import QueryIndexes
    data.service._query_indexes=QueryIndexes(data.service)
    data.service._query_indexes.advance=Mock(wraps=data.service._query_indexes.advance)
    result=tools.execute('inventory_data_files',{'source_scope':'output','view':'summary'},request)
    assert result['complete_scan'] and result['file_count']==225
    assert result['coverage']['extension_counts']=={'.csv':225}
    assert result['coverage']['group_count']==1 and len(result['items'])==1
    assert result['root_path']==str(data.output) and not result['page_complete']
    assert 'revision' not in result['items'][0] and len(json.dumps(result))<32000
    identity=result['inventory_id'];seen=[];offset=0
    while True:
        page=tools.execute('inventory_data_files',{'source_scope':'output','view':'files',
            'inventory_id':identity,'offset':offset,'limit':100},request)
        seen.extend(r['path'] for r in page['items'])
        offset=page['next_offset']
        if offset is None:break
    assert len(seen)==len(set(seen))==225 and data.service._query_indexes.advance.call_count==1
    assert not data.service.list(data.scope['conversation_id'])
    other=request_for(data)
    with pytest.raises(ValueError,match='当前范围'):
        tools.execute('inventory_data_files',{'source_scope':'output','inventory_id':identity},other)
    with pytest.raises(ValueError,match='当前范围'):
        tools.execute('inventory_data_files',{'source_scope':'dataset','inventory_id':identity},request)


def test_summary_groups_have_reachable_continuation(data):
    for i in range(30):(data.output/(chr(97+i//26)+chr(97+i%26)+'_long.csv')).write_text('synthetic')
    request=request_for(data);tools=assistant(data,Model(understanding(),[]))
    group_offset=0;seen=[]
    while True:
        result=tools.execute('inventory_data_files',{'source_scope':'output','view':'summary','group_offset':group_offset},request)
        assert result['coverage']['group_count']==30 and len(json.dumps(result,ensure_ascii=False))<32000
        seen.extend(g['pattern'] for g in result['coverage']['groups'])
        group_offset=result['coverage']['next_group_offset']
        if group_offset is None:break
    assert len(seen)==len(set(seen))==30


def test_full_scope_summary_advances_to_one_pending_plan_without_execution(data):
    for i in range(225):(data.output/f'prefix_series-{i:03}.csv').write_text('a,b\n1,2\n')
    intent=understanding(coverage='all',step={'request_excerpt':'处理选定范围','purpose':'去掉冗余前缀',
        'source_scope':'output','mode':'inplace','actions':['rename'],'stage':'modify_results'})
    model=Model(intent,[('inventory_data_files',{'source_scope':'output','view':'summary'}),
        ('propose_filename_transform',{'source_scope':'output','mode':'inplace','transforms':[{'action':'strip_prefix','old':'prefix_'}]}),
        '已准备改名方案'])
    tools=assistant(data,model)
    answer=asyncio.run(tools.reply(request_for(data)))
    task=data.service.list(data.scope['conversation_id'])[0]
    assert task['file_count']==225 and task['status']=='pending' and task['can_confirm']
    assert '尚未执行' in answer and not data.service.futures
    assert len(list(data.output.glob('prefix_*')))==225 and not list(data.output.glob('series-*'))
    assert any('全量扫描已完成' in m.get('content','') for m in model.messages)
