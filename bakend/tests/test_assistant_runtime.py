"""General progress across catalog, filesystem, text and task metadata scenarios."""
import asyncio
import json
from unittest.mock import Mock

import pytest

from app.assistant_runtime import ProgressBudget, checkpoint, compact_messages, preview
from app.assistant_tasks import AssistantIntent, AssistantTasks
from app.models import ChatRequest
from app.workspace_tools import WorkspaceTools
from test_assistant_task_logic import assistant, Model, request_for, understanding
from test_data_operations import data


def call(name, args, i):
    return {'role': 'assistant', 'tool_calls': [{'id': str(i), 'function':
            {'name': name, 'arguments': json.dumps(args)}}]}


@pytest.mark.parametrize('scenario', ['directory', 'catalog', 'text'])
def test_useful_queries_extend_past_ten_rounds_and_finish(scenario):
    class Provider:
        mode = 'model'
        index = 0

        async def workspace_completion(self, messages, tools, **kwargs):
            self.index += 1
            if self.index <= 15:
                return call('query', {'page': self.index}, self.index)
            return {'role': 'assistant', 'content': '根据15页证据已完成检查。'}

    model = Provider()
    tools = WorkspaceTools(Mock(), model)
    tools.skill_catalog = Mock()
    rows = {'directory': lambda i: {'items': [{'relative_path': f'sub/{i}.csv'}], 'has_more': i < 15},
            'catalog': lambda i: {'items': [{'method_id': f'family.method{i}'}]},
            'text': lambda i: {'content': f'不同段落的实际内容{i}'}}
    tools.execute = Mock(side_effect=lambda name, args, req: rows[scenario](args['page']))
    answer = asyncio.run(tools.reply(ChatRequest(message='检查完整范围', interaction_mode='task')))
    assert '15页' in answer and tools.execute.call_count == 15


def test_equivalent_queries_stop_and_get_strategy_feedback():
    class Provider:
        mode = 'model'
        index = 0
        messages = []

        async def workspace_completion(self, messages, tools, **kwargs):
            self.messages = messages
            self.index += 1
            return call('query', {'filter': f'variant{self.index}'}, self.index)

    model = Provider()
    tools = WorkspaceTools(Mock(), model)
    tools.skill_catalog = Mock()
    tools.execute = Mock(return_value={'items': [{'relative_path': 'table.csv'}]})
    req = ChatRequest(message='找到所需文件', interaction_mode='task')
    assert asyncio.run(tools.reply(req)) is None
    assert tools.execute.call_count == 7
    context = json.loads(req.workspace_context.split('：\n')[-1])
    assert context['stop_reason'] == 'no_progress'
    assert any('请改变工具' in m.get('content', '') for m in model.messages)


def test_repeated_call_reuses_facts_instead_of_replacing_them_with_error():
    class Provider:
        mode = 'model'
        index = 0
        messages = []

        async def workspace_completion(self, messages, tools, **kwargs):
            self.messages = messages
            self.index += 1
            if self.index < 3:
                return call('query', {'path': '.'}, self.index)
            return {'role': 'assistant', 'content': '已找到 table.csv。'}

    model = Provider()
    tools = WorkspaceTools(Mock(), model)
    tools.skill_catalog = Mock()
    tools.execute = Mock(return_value={'items': [{'relative_path': 'table.csv'}]})
    assert 'table.csv' in asyncio.run(tools.reply(ChatRequest(message='看看文件')))
    tools.execute.assert_called_once()
    evidence = [json.loads(m['content']) for m in model.messages if m['role'] == 'tool']
    assert len(evidence) == 2 and all('error' not in r and r['items'] for r in evidence)
    assert evidence[1]['query_cache_reused'] is True


def test_large_page_keeps_paths_counters_cursor_without_mutating_full_result():
    rows = [{'relative_path': f'sub/{i:03d}-' + 'x' * 210 + '.csv',
             'path': '/root/' + 'root/' * 60 + f'sub/{i:03d}-' + 'x' * 210 + '.csv',
             'size_bytes': i} for i in range(56)]
    original = {'items': rows, 'total': 560, 'has_more': True, 'next_cursor': '56'}
    before = json.dumps(original)
    reduced = preview(original)
    assert len(json.dumps(reduced, ensure_ascii=False)) <= 32000
    assert len(reduced['items']) == 56 and reduced['items'][0]['relative_path'] == rows[0]['relative_path']
    assert reduced['total'] == 560 and reduced['next_cursor'] == '56'
    assert reduced['backend_returned_count'] == reduced['model_visible_count'] == 56
    assert json.dumps(original) == before


@pytest.mark.parametrize('result', [
    {'content': '内容' * 40000, 'path': 'readme.txt'},
    {'items': [{'path': f'{i}.csv', 'description': '文' * 6000} for i in range(100)], 'total': 100, 'has_more': False},
    {'items': [{'field' + str(i): 'v' * 900 for i in range(300)}], 'next_cursor': 'page2', 'has_more': True},
])
def test_compressed_previews_are_bounded_explicit_and_valid(result):
    for limit in (1800, 32000):
        output = preview(result, limit)
        encoded = json.dumps(output, ensure_ascii=False)
        assert len(encoded) <= limit
        assert json.loads(encoded)['model_preview_truncated']
        if 'next_cursor' in result:
            assert output['next_cursor'] == result['next_cursor']
        if output.get('model_visible_count', 0) < len(result.get('items', [])):
            assert output.get('has_more') is result.get('has_more')


def test_old_tool_context_compacts_but_keeps_call_ids_and_recent_pages():
    messages = [{'role': 'user', 'content': '汇总全部表格'}]
    for i in range(6):
        messages += [{'role': 'assistant', 'tool_calls': [{'id': str(i)}], 'reasoning_content': 'provider state'},
                     {'role': 'tool', 'tool_call_id': str(i), 'content': json.dumps({'content': 'x' * 20000})}]
    recent = [m.copy() for m in messages[-4:]]
    compact_messages(messages)
    assert messages[-4:] == recent and messages[0]['content'] == '汇总全部表格'
    assert sum(len(m['content']) for m in messages if m['role'] == 'tool') <= 60000
    assert [m['tool_call_id'] for m in messages if m['role'] == 'tool'] == [str(i) for i in range(6)]


def test_progress_is_bounded_even_for_unique_results_and_time():
    clock = [0]
    budget = ProgressBudget(lambda: clock[0])
    while not budget.stop_reason():
        budget.rounds += 1
        budget.observe({'items': [{'id': str(budget.rounds)}]})
    assert budget.rounds == 24 and budget.stop_reason() == 'query_budget'
    clock[0] = 301
    assert budget.stop_reason() == 'time_budget'


def test_call_budget_bounds_large_parallel_batches_and_ignores_round_limit_mid_batch():
    budget = ProgressBudget()
    budget.rounds = 10
    assert budget.stop_reason(include_rounds=False) is None
    for i in range(48):
        assert budget.stop_reason(include_rounds=False) is None
        budget.observe({'content': f'unique evidence {i}'})
    assert budget.stop_reason(include_rounds=False) == 'query_budget'


def test_status_changes_are_evidence_and_result_rows_are_not_modified():
    budget = ProgressBudget()
    result = {'items': [{'id': 'a'}]}
    assert budget.observe(result)
    assert result == {'items': [{'id': 'a'}]}
    assert not budget.observe(result)
    assert budget.observe({'id': 'a', 'status': 'running', 'completed': 3})
    assert budget.observe({'id': 'a', 'status': 'succeeded', 'completed': 10})


def test_checkpoint_survives_restart_is_historical_and_scope_bound(data):
    req = request_for(data)
    ledger = AssistantTasks(data.service.store)
    ledger.merge(req, AssistantIntent.model_validate(understanding()))
    ledger.checkpoint(req, [{'tool': 'read_output_text', 'arguments': {'path': 'table.csv'},
                            'result': {'content': 'header\n1'}}], 'no_progress')
    saved = AssistantTasks(data.service.store).load(request_for(data))['query_checkpoint']
    assert saved['stop_reason'] == 'no_progress'
    assert saved['current_state_verified'] is False
    assert saved['observations'][0]['arguments']['path'] == 'table.csv'
    assert ledger.load(request_for(data, workspace_id='other')) == {}


def test_task_state_refreshed_before_model_interprets_followup(data):
    req = request_for(data)
    ledger = AssistantTasks(data.service.store)
    ledger.save(req, {'goal': '调整表格', 'active_operation_id': 'prior',
                      'operations': {'prior': {'id': 'prior', 'kind': 'data', 'status': 'running'}}})
    data.service.progress = Mock(return_value={'id': 'prior', 'kind': 'data', 'status': 'succeeded', 'completed': 10})
    model = Model(understanding(action='answer', outcome='answer'), [('answer_without_action', {'answer': '上一任务已完成。'})])
    assert '已完成' in asyncio.run(assistant(data, model).reply(request_for(data, message='目前状态')))
    data.service.progress.assert_called_once_with('prior', req.conversation_id)
    assert ledger.load(req)['operations']['prior']['status'] == 'succeeded'
    assert ledger.load(req)['phase'] == 'result_available'


def test_new_operational_stage_replaces_obsolete_checks_preserves_choices(data):
    req = request_for(data)
    ledger = AssistantTasks(data.service.store)
    ledger.merge(req, AssistantIntent.model_validate(understanding(completion_checks=['复制全部输入'], constraints=['保留原始数据'])))
    ledger.merge(req, AssistantIntent.model_validate(understanding(completion_checks=['表格按组汇总'])))
    value = ledger.load(req)
    assert value['completion_checks'] == ['表格按组汇总'] and value['constraints'] == ['保留原始数据']
    ledger.merge(req, AssistantIntent.model_validate(understanding(action='answer', goal='为什么选择这个字段', completion_checks=['解释字段'])))
    assert ledger.load(req)['completion_checks'] == ['表格按组汇总']
    assert ledger.load(req)['goal'] == '统一整理结果'


def test_generic_limit_reports_real_blocker_without_starting_tasks(data):
    class Provider(Model):
        async def complete(self, messages, **kwargs):
            self.report = messages
            return '目录中已找到两张表，但尚未找到关联键。下一步检查表头和字段关系。'

    model = Provider(understanding(), [('list_output_directory', {'path': '.', 'limit': 20})])
    (data.output / 'a.csv').write_text('key,value\n1,2')
    answer = asyncio.run(assistant(data, model).reply(request_for(data, message='合并两张表')))
    assert '尚未完成' in answer and '关联键' in answer
    assert 'reason' in model.report[1]['content'] and 'no_progress' in model.report[1]['content']
    assert not data.service.list(data.scope['conversation_id'])
    assert not data.service.futures


def test_extended_investigation_can_prepare_general_script_without_executing(data):
    model = Model(understanding(goal='汇总表格'), [('list_output_directory', {'path': '.', 'limit': i}) for i in range(1, 13)] +
                  [('run_data_python', {'source_scope': 'output', 'mode': 'analysis', 'code': "print('summary')"}),
                   ('answer_without_action', {'answer': '已分析全部数据'})])
    data.service.sandbox.image = 'sha256:' + 'a' * 64
    data.service.sandbox.run = Mock(side_effect=AssertionError('script must wait for UI run'))
    tools = assistant(data, model)
    execute = tools.execute
    def query(name, arguments, req):
        if name == 'list_output_directory':
            return {'items': [{'relative_path': f"table-{arguments['limit']}.csv"}]}
        return execute(name, arguments, req)
    tools.execute = query
    answer = asyncio.run(tools.reply(request_for(data)))
    assert '点击运行' in answer and '已分析全部数据' not in answer
    task = data.service.list(data.scope['conversation_id'])[0]
    assert task['status'] == 'pending' and task['phase'] == 'script' and task['can_run_script']
    assert not data.service.futures and not list(data.output.iterdir())
    data.service.sandbox.run.assert_not_called()


def test_live_task_status_is_not_reused_from_cache():
    class Provider:
        mode = 'model'
        index = 0

        async def workspace_completion(self, messages, tools, **kwargs):
            self.index += 1
            if self.index < 3:
                return call('list_data_operations', {}, self.index)
            return {'role': 'assistant', 'content': '当前任务已结束。'}

    tools = WorkspaceTools(Mock(), Provider())
    tools.skill_catalog = Mock()
    tools.execute = Mock(side_effect=[{'items': [{'id': 'a', 'status': 'running'}]},
                                     {'items': [{'id': 'a', 'status': 'succeeded'}]}])
    assert '已结束' in asyncio.run(tools.reply(ChatRequest(message='检查进度')))
    assert tools.execute.call_count == 2
