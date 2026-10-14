"""General regression: coverage, changing files, memory and manual-run planning."""
import asyncio
import json
from unittest.mock import Mock

import pytest

from app.agent_state import AgentTasks
from app.directory_snapshots import DirectorySnapshots
from test_data_operations import data
from test_unified_agent import fixture, goal, ready


def test_directory_views_and_page_sizes_reuse_index_but_new_files_invalidate_it(data, monkeypatch):
    import app.directory_snapshots as module
    snapshots = DirectorySnapshots()
    monkeypatch.setattr(module, 'snapshots', snapshots)
    create = Mock(wraps=snapshots.create)
    monkeypatch.setattr(snapshots, 'create', create)
    for i in range(8):
        (data.output / f'{i}.txt').write_text('original')
    owner, request, _ = fixture(data, [])
    first = owner.execute('list_output_directory', {'view':'names', 'limit':2}, request)
    details = owner.execute('list_output_directory', {'view':'details', 'limit':7}, request)
    assert first['snapshot_id'] == details['snapshot_id'] and create.call_count == 1
    assert details['snapshot_reused'] and details['items'][0]['permissions']
    (data.output / 'new.txt').write_text('new')
    changed = owner.execute('list_output_directory', {'limit':200}, request)
    assert changed['total_items'] == 9 and changed['snapshot_id'] != first['snapshot_id']
    assert create.call_count == 2


def test_information_gaps_require_scoped_real_evidence(data):
    owner, request, _ = fixture(data, [])
    ledger = AgentTasks(data.service.store)
    ledger.begin(request)
    update = {'goal':'处理表格','deliverable':'files','information_needs':[
        {'id':'columns','question':'实际有哪些列','status':'resolved'}]}
    ledger.update_goal(request, update)
    assert request.assistant_task['information_needs'][0]['status']=='open'
    update['information_needs'][0]['evidence_refs'] = ['evidence:invented']
    ledger.update_goal(request, update)
    assert request.assistant_task['information_needs'][0]['status']=='open'
    update['information_needs'][0]['evidence_refs'] = [f'message:{request._user_sequence}']
    update['information_needs'][0]['conclusion'] = '该问题已有用户原话可追溯；文件列名仍需专门查询'
    ledger.update_goal(request, update)
    ledger.update_goal(request, {'goal':'处理表格','strategy':'修改策略'})
    assert request.assistant_task['deliverable']=='files'
    assert request.assistant_task['information_needs'][0]['status']=='resolved'
    ledger.update_goal(request, {'goal':'另一个目标','relation':'new'})
    assert not request.assistant_task.get('information_needs')


def test_successful_read_does_not_hide_an_unfixed_plan_error(data):
    owner, request, _ = fixture(data, [])
    ledger = AgentTasks(data.service.store)
    ledger.begin(request)
    ledger.record(request, 'prepare_workflow', {}, {'error':'missing required output contract'})
    ledger.record(request, 'inspect_output_path', {}, {'exists':True,'relative_path':'a.csv'})
    assert 'missing required output contract' in ledger.context(request)
    ledger.record(request, 'prepare_workflow', {}, {'id':'wf','kind':'workflow','status':'pending','revision':1})
    assert not request.assistant_task.get('preparation_error')
