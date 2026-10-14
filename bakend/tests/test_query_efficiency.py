"""Shared evidence, resumable scans and complete drafts with real temporary files."""
import asyncio
import json
from pathlib import Path
from threading import Event
from unittest.mock import Mock, AsyncMock

import pytest

from app.agent_context import tool_projection
from app.assistant_inventory import inventory_query
from app.agent_file_queries import query_files, indexed_search
from app.data_selection import DataSelection
from app.dataset_files import DatasetReader
from app.dataset_models import DatasetSample
from app.operation_context import OperationCancelled, OperationContext, current_operation
from app.query_index import QueryIndexes
from test_data_operations import data
from test_assistant_task_logic import request_for


def scope(data):
    return {**data.scope, 'mode':'inplace'}


def test_search_statistics_and_metadata_share_one_full_index(data):
    for subject in range(3):
        path=data.source/f'sub-{subject}';path.mkdir()
        (path/'dwi.nii.gz').write_bytes(b'image')
        (path/'dwi.bval').write_text('0 1000 2000')
    indexes=QueryIndexes(data.service)
    data.service._query_indexes=indexes
    indexes.advance=Mock(wraps=indexes.advance)
    req=request_for(data)
    result=query_files(data.service,req,scope(data),{'source_scope':'dataset','patterns':['*.nii.gz','*.bval'],
        'required_patterns':{'image':'*.nii.gz','gradients':'*.bval'}})
    assert result['ready_object_count']==result['object_count']==3 and not result['exceptions']
    for pattern in ('*.nii.gz','*.bval','*missing*'):
        search=indexed_search(data.service,DatasetReader(data.source),req,'dataset',{'pattern':pattern})
        assert search['query_complete'] and search['index_reused']
    from app.agent_metadata import inspect_metadata
    metadata=inspect_metadata(data.service,req,scope(data),{'source_scope':'dataset','format':'file','fields':['name','extension','size']})
    assert metadata['scanned_count']==6 and metadata['missing_counts']=={}
    text=inspect_metadata(data.service,req,scope(data),{'source_scope':'dataset','format':'text','fields':['numeric_count','numeric_max']})
    assert text['matched_count']==3 and text['items'][0]['values']=={'numeric_count':3,'numeric_max':2000.0}
    assert indexes.advance.call_count==1 and not data.service.futures


def test_registered_empty_subject_and_ambiguous_inputs_cannot_disappear(data):
    dataset=data.service.store.get_dataset('ds_test')
    dataset.inspection.samples=[DatasetSample(sample_id=f'sub-{i}',label=str(i),relative_root=f'sub-{i}') for i in range(3)]
    dataset.inspection.sample_count=3
    data.service.store.save_dataset(dataset)
    for i in range(3):(data.source/f'sub-{i}').mkdir()
    (data.source/'sub-0'/'dwi.nii.gz').write_bytes(b'one')
    (data.source/'sub-1'/'dwi.nii.gz').write_bytes(b'two')
    (data.source/'sub-1'/'other_dwi.nii.gz').write_bytes(b'three')
    (data.source/'notes.txt').write_text('root file')
    report=query_files(data.service,request_for(data),scope(data),{'source_scope':'dataset','required_patterns':{'dwi':'*dwi.nii.gz'}})
    assert report['object_count']==3 and report['ready_object_count']==1
    assert report['missing_counts']=={'dwi':1} and report['ambiguous_counts']=={'dwi':1}
    assert report['unassigned_file_count']==1 and report['object_scope']=='registered_samples'
    visible=tool_projection(report)
    assert visible['object_count']==3 and len(visible['exceptions'])==2


def test_partial_index_resumes_after_service_restart_without_losing_rows(data,monkeypatch):
    import app.query_index as module
    real=module.ScopedScan
    class Sliced(real):
        def walk(self,start=None):
            count=0
            for row in super().walk(start):
                yield row
                count+=1
                if count==4 and self.pending:
                    self.stop_reason='slice';return
    monkeypatch.setattr(module,'ScopedScan',Sliced)
    for i in range(31):(data.source/f'{i:02}.txt').write_text(str(i))
    index=QueryIndexes(data.service)
    first=index.acquire(scope(data))
    assert not first['complete'] and 0<len(first['rows'])<31
    previous=first['scanned']
    for _ in range(10):
        index=QueryIndexes(data.service)
        result=index.acquire(scope(data))
        assert result['scanned']>previous and result['id']==first['id']
        previous=result['scanned']
        if result['complete']:break
    assert result['complete'] and len({row['path'] for row in result['rows']})==31


def test_cancelled_index_keeps_a_restartable_checkpoint(data,monkeypatch):
    import app.query_index as module
    for i in range(20):(data.source/f'{i:02}.txt').write_text(str(i))
    stop=Event();real=module.ScopedScan
    class CancelScan(real):
        def walk(self,start=None):
            for row in super().walk(start):
                yield row
                if self.scanned==6:stop.set()
    monkeypatch.setattr(module,'ScopedScan',CancelScan)
    token=current_operation.set(OperationContext(stop,lambda _:None))
    try:
        with pytest.raises(OperationCancelled):QueryIndexes(data.service).acquire(scope(data))
    finally:current_operation.reset(token)
    monkeypatch.setattr(module,'ScopedScan',real)
    result=QueryIndexes(data.service).acquire(scope(data))
    assert result['complete'] and len(result['rows'])==len({r['path'] for r in result['rows']})==20


def test_search_cursor_follows_only_rows_actually_visible_to_model(data):
    for i in range(70):(data.source/(f'{i:03}-'+'x'*85+'.txt')).write_text('file')
    req=request_for(data);reader=DatasetReader(data.source)
    cursor=None;seen=[]
    for _ in range(70):
        result=indexed_search(data.service,reader,req,'dataset',{'pattern':'*.txt','max_results':100,**({'cursor':cursor} if cursor else {})})
        shown=tool_projection(result,budget=1800)
        seen.extend(r['relative_path'] for r in shown['items'])
        cursor=shown['next_cursor']
        if not cursor:break
    assert len(seen)==len(set(seen))==70


def test_incomplete_workflow_cannot_be_announced_as_awaiting_run(data):
    from app.agent_state import AgentTasks
    from app.workspace_tools import WorkspaceTools
    req=request_for(data);ledger=AgentTasks(data.service.store);ledger.begin(req)
    ledger.update_goal(req,{'goal':'完成科学计算','deliverable':'workflow'})
    result={'id':'wf','kind':'workflow','status':'pending','revision':1,
            'missing_inputs':[{'step':'one','slots':['image']}],'inputs_ready':False}
    ledger.record(req,'prepare_workflow',{},result)
    with pytest.raises(ValueError,match='必需输入未绑定'):
        ledger.finish(req,{'status':'awaiting_run','summary':'ready','artifact_ids':['wf']})
    summary=WorkspaceTools.pending_summary([{'tool':'prepare_workflow','result':result}])
    assert '尚未就绪' in summary and '点击运行' not in summary


def test_completed_deep_mutation_invalidates_index_even_when_root_mtime_unchanged(data):
    from app.data_models import DataPlanRequest
    from test_data_operations import approval,finished
    nested=data.output/'nested';nested.mkdir();(nested/'before.txt').write_text('original')
    req=request_for(data);args={'source_scope':'output','view':'files'}
    output_scope={**scope(data),'source_scope':'output'}
    first=inventory_query(data.service,req,output_scope,args)
    timestamp=data.output.stat().st_mtime_ns
    task=data.service.propose(DataPlanRequest(**output_scope,edits=[{'action':'rename','path':'nested/before.txt','destination':'nested/after.txt'}]))
    pending=inventory_query(data.service,req,output_scope,args)
    assert pending['inventory_id']==first['inventory_id']
    data.service.confirm(task['id'],approval(task));assert finished(data,task)['status']=='succeeded'
    assert data.output.stat().st_mtime_ns==timestamp
    with pytest.raises(ValueError,match='数据已变化'):
        inventory_query(data.service,req,output_scope,{**args,'inventory_id':first['inventory_id']})
    current=inventory_query(data.service,req,output_scope,args)
    assert current['inventory_id']!=first['inventory_id'] and [r['path'] for r in current['items']]==['nested/after.txt']


def test_valid_ready_plan_is_not_blocked_by_auxiliary_memory_ids(data):
    from app.agent_state import AgentTasks
    req=request_for(data);ledger=AgentTasks(data.service.store);ledger.begin(req)
    ledger.update_goal(req,{'goal':'科学计算','deliverable':'workflow','information_needs':[
        {'id':'input','question':'核实输入'}]})
    ledger.record(req,'prepare_workflow',{}, {'id':'wf','kind':'workflow','status':'pending','revision':1,'inputs_ready':True,'missing_inputs':[]})
    ledger.finish(req,{'status':'awaiting_run','summary':'prepared','artifact_ids':['wf'],'resolved_need_ids':['input','invented']})
    assert req.assistant_task['phase']=='awaiting_run'
    assert req.assistant_task['information_needs'][0]['status']=='open'
    assert len(req.assistant_task['need_resolution']['unresolved'])==2
    with pytest.raises(ValueError):
        ledger.resolve_needs(req,['invented'])
