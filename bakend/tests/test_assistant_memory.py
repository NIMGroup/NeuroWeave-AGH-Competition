"""Memory continuity, provenance, retrieval and manual execution across real temp stores."""
import asyncio
import copy
import json
from unittest.mock import AsyncMock, Mock

import pytest

from app.assistant_contract import AssistantStep, StepMismatch
from app.assistant_memory import ConversationMemory, MemoryUpdate
from app.assistant_step_tools import prepare_step
from app.assistant_tasks import AssistantIntent, AssistantTasks, completion_problem
from app.chat_service import ChatService
from app.conversation_service import ConversationService
from app.data_auth import Principal, principal
from app.llm.json_response import json_response
from app.models import ChatRequest, ChatResponse
from app.response_policy import ResponsePolicy
from test_assistant_task_logic import assistant, request_for, understanding
from test_data_operations import data


class Model:
    mode = 'model'

    def __init__(self, intent, calls):
        self.intent, self.calls, self.index, self.messages = intent, calls, 0, []

    async def json_response(self, messages, *, validate, **kwargs):
        if kwargs.get('stage') == 'assistant_verification':
            payload = {'decision': 'ready', 'issues': [], 'next_step': ''}
            validate(payload)
            return payload, []
        self.intent_messages = copy.deepcopy(messages)
        payload = copy.deepcopy(self.intent)
        validate(payload)
        return payload, []

    async def workspace_completion(self, messages, tools, **kwargs):
        self.messages = copy.deepcopy(messages)
        name, arguments = self.calls[self.index]
        self.index += 1
        return {'role': 'assistant', 'tool_calls': [{'id': str(self.index),
            'function': {'name': name, 'arguments': json.dumps(arguments)}}]}


def turn(data, content, model):
    service = ConversationService(data.service.store, ChatService())
    request, identity = service.begin_turn(request_for(data, message=content))
    answer = asyncio.run(assistant(data, model).reply(request))
    asyncio.run(service.finish_turn(identity, ChatResponse(content=answer, provider_used='fixture', model_used='fixture', mode='model', selected_skills=[])))
    return request, answer


def intent(**updates):
    return understanding(**updates)


def test_greeting_new_task_clarification_answers_prepare_pending_proposal(data):
    (data.output / 'prefix_scan_123.csv').write_text('id,value\n1,2\n')
    first, _ = turn(data, '你好', Model(intent(action='answer', relation='independent', goal='你好', outcome='answer', step={}),
        [('answer_without_action', {'answer': '你好，请说明需求。'})]))
    goal = '简化输出目录中的文件名'
    step = {'purpose': goal, 'source_scope': 'output', 'mode': 'inplace', 'actions': ['rename']}
    second, _ = turn(data, '清理输出目录的冗余文件名', Model(intent(action='clarify', goal=goal, outcome='preview', step=step,
        constraints=['只改文件名'], memory={'open_questions': ['短名是否修改', '去掉哪些成分', '序列名是否保留']}),
        [('inventory_data_files', {'source_scope': 'output', 'view': 'summary'}),
         ('answer_without_action', {'answer': '短名是否修改？哪些成分可以去掉？是否保留原厂序列名？'})]))
    state = AssistantTasks(data.service.store).load(second)
    assert state['goal'] == goal and state['step']['actions'] == ['rename'] and state['phase'] == 'clarifying'
    assert '你好' not in state['step']['purpose']
    message = '短名不改；去掉前缀和时间戳；保留原厂序列名'
    model = Model(intent(goal=goal, outcome='preview', step={**step, 'request_excerpt': '旧问题中的重命名需求'},
        constraints=['短名不改', '去掉前缀和时间戳', '保留原厂序列名'], memory={'open_questions': [], 'next_step': '审阅映射后点击运行'}),
        [('propose_data_changes', {'source_scope': 'output', 'mode': 'inplace',
            'edits': [{'action': 'rename', 'path': 'prefix_scan_123.csv', 'destination': 'scan.csv'}]}),
         ('answer_without_action', {'answer': '方案已准备。'})])
    final, answer = turn(data, message, model)
    restarted = AssistantTasks(data.service.store)
    memory = restarted.memory.load(final)
    state = restarted.load(final)
    assert state['goal'] == goal and state['step']['request_excerpt'] == message
    assert memory['task']['open_questions'] == [] and memory['task']['conditions'][-1]['source']['sequence'] == 5
    assert memory['task']['source']['sequence'] == 3
    assert memory['inputs'][-1]['text'] == message
    assert '短名是否修改' in json.dumps(model.intent_messages, ensure_ascii=False)
    assert memory['last_reply']['awaiting_answer'] is False
    task = data.service.list(final.conversation_id)[0]
    assert task['status'] == 'pending' and '点击运行' in answer
    assert (data.output / 'prefix_scan_123.csv').exists() and not (data.output / 'scan.csv').exists()
    assert not data.service.futures


def test_partial_step_survives_understanding_and_compiles_after_inspection(data):
    (data.output / 'old.csv').write_text('id,value\n1,2\n')
    model = Model(intent(goal='整理刚才的表格', outcome='preview', step={},
        memory={'assumptions': ['需核实表格所在目录']}), [
        ('list_output_directory', {'path': '.'}),
        ('prepare_current_step', {'purpose': '调整输出表格', 'source_scope': 'output', 'mode': 'inplace',
                                  'actions': ['write_text'], 'selection': {'include': ['old.csv']}}),
        ('propose_data_changes', {'source_scope': 'output', 'mode': 'inplace',
            'edits': [{'action': 'write_text', 'path': 'old.csv', 'content': 'id,value\n1,3\n'}]}),
        ('answer_without_action', {'answer': '已准备修订。'})])
    request, answer = turn(data, '调整表格中的数值', model)
    task = data.service.list(request.conversation_id)[0]
    assert task['status'] == 'pending' and task['requires_overwrite_confirmation']
    assert (data.output / 'old.csv').read_text() == 'id,value\n1,2\n' and not data.service.futures
    assert '点击运行' in answer
    assert AssistantTasks(data.service.store).load(request)['step']['selection']['include'] == ['old.csv']


def test_incomplete_or_wrong_effect_draft_cannot_create_any_proposal(data):
    ledger = AssistantTasks(data.service.store)
    request = request_for(data)
    model = Model(intent(step={'purpose': '整理文件', 'source_scope': 'output', 'mode': 'analysis', 'actions': ['rename']}), [])
    assert asyncio.run(ledger.understand(request, model))[0]
    with pytest.raises(StepMismatch, match='补齐并核实'):
        assistant(data, model).execute('propose_data_changes', {'source_scope': 'output', 'mode': 'inplace',
            'edits': [{'action': 'write_text', 'path': 'never.txt', 'content': 'x'}]}, request)
    assert not data.service.list(request.conversation_id) and not list(data.output.iterdir())


def test_clarification_refinement_cannot_revision_a_nonexistent_operation(data):
    ledger = AssistantTasks(data.service.store)
    request = request_for(data, message='先给我简化名称的方案')
    ledger.merge(request, AssistantIntent.model_validate(intent(action='clarify', goal='简化名称', outcome='preview', step={})))
    model = Model(intent(goal='简化名称', step={'purpose': '应用已回答的命名条件', 'source_scope': 'output',
        'mode': 'inplace', 'actions': ['rename'], 'stage': 'revise_pending', 'base_operation_id': None}), [])
    assert asyncio.run(ledger.understand(request, model))[0]
    assert ledger.load(request)['step']['stage'] == 'new'
    (data.output / 'old.csv').write_text('x')
    task = assistant(data, model).execute('propose_data_changes', {'source_scope': 'output', 'mode': 'inplace',
        'edits': [{'action': 'rename', 'path': 'old.csv', 'destination': 'new.csv'}]}, request)
    assert task['status'] == 'pending' and not data.service.futures
    assert (data.output / 'old.csv').exists()


def test_semantic_correction_can_downgrade_but_cannot_expand_permissions(data):
    ledger = AssistantTasks(data.service.store)
    request = request_for(data, response_policy=ResponsePolicy(action='operate'))
    model = Model(intent(action='clarify', goal='核实具体范围', outcome='answer', step={}), [])
    assert asyncio.run(ledger.understand(request, model, feedback={'reason': 'missing_scope'}))[0]
    assert request.response_policy.action == 'clarify'
    forbidden = Model(intent(action='operate', step={}), [])
    with pytest.raises(ValueError, match='扩大'):
        asyncio.run(ledger.understand(request, forbidden, feedback={'reason': 'missing_scope'}))


def test_explanation_cannot_promote_memory_into_write_authority(data):
    request = request_for(data, response_policy=ResponsePolicy(action='answer'))
    ledger = AssistantTasks(data.service.store)
    ledger.memory.capture(request)
    with pytest.raises(PermissionError):
        prepare_step(ledger, request, {'purpose': '删除', 'source_scope': 'output', 'mode': 'inplace', 'actions': ['delete']})
    names = {t['function']['name'] for t in assistant(data, Mock()).available_tools(request)}
    assert 'search_conversation_history' in names and 'prepare_current_step' not in names and 'propose_data_changes' not in names
    assert not data.service.list(request.conversation_id)


def test_preparing_step_cannot_expand_known_scope_actions_or_subset(data):
    request = request_for(data)
    ledger = AssistantTasks(data.service.store)
    ledger.merge(request, AssistantIntent.model_validate(intent(step={'purpose': '只改指定文件', 'source_scope': 'output',
        'mode': 'inplace', 'actions': ['rename'], 'selection': {'include': ['one.csv']}})))
    good = {'purpose': '改名', 'source_scope': 'output', 'mode': 'inplace', 'actions': ['rename'], 'selection': {'include': ['one.csv']}}
    for bad in ({**good, 'source_scope': 'dataset'}, {**good, 'actions': ['delete']}, {**good, 'selection': {}}):
        with pytest.raises(StepMismatch):
            prepare_step(ledger, request, bad)
    assert prepare_step(ledger, request, good)['executed'] is False


def test_input_survives_parse_failure_and_model_can_discuss_without_writes(data):
    model = Model(intent(), [('answer_without_action', {'answer': '我理解你要汇总当前表格；还需确定汇总指标。'})])
    model.json_response = AsyncMock(return_value=(None, [{'error_type': 'ValueError'}]))
    request, answer = turn(data, '按刚才的范围汇总这些表格', model)
    memory = ConversationMemory(data.service.store).load(request)
    assert memory['inputs'][-1]['text'] == request.message and '汇总指标' in answer
    assert request.response_policy.action == 'clarify' and not data.service.futures
    assert not data.service.list(request.conversation_id)


def test_corrected_json_keeps_original_user_identity_and_history(data):
    client = Mock()
    client.complete = AsyncMock(side_effect=['invalid', '{"answer":"ok"}'])
    messages = [{'role': 'system', 'content': '规则'}, {'role': 'user', 'content': '旧目标'},
                {'role': 'assistant', 'content': '哪个指标？'}, {'role': 'user', 'content': '按均值'}]
    payload, errors = asyncio.run(json_response(client, messages, stage='assistant_task'))
    retried = client.complete.call_args_list[1].args[0]
    assert payload['answer'] == 'ok' and errors
    assert retried[-1]['role'] == 'system'
    assert [m for m in retried if m['role'] == 'user'][-1]['content'] == '按均值'


@pytest.mark.parametrize('source_scope', ['dataset', 'output'])
def test_path_correction_is_actionable_without_echoing_private_model_input(source_scope):
    first = intent(step={'source_scope': source_scope, 'selection': {'path': '/private-model-only/path'}})
    corrected = intent(step={'source_scope': source_scope, 'selection': {'path': '.'}})
    client = Mock()
    client.complete = AsyncMock(side_effect=[json.dumps(first), json.dumps(corrected)])
    messages = [{'role': 'system', 'content': '按 schema 理解当前范围'}, {'role': 'user', 'content': '汇总当前目录'}]
    payload, errors = asyncio.run(json_response(client, messages, stage='assistant_task', validate=AssistantIntent.model_validate))
    assert payload['step']['selection']['path'] == '.' and payload['step']['source_scope'] == source_scope
    feedback = client.complete.call_args_list[1].args[0][-1]['content']
    assert 'unsafe_relative_path' in feedback and '安全相对路径' in feedback
    assert '/private-model-only/path' not in feedback and '/private-model-only/path' not in json.dumps(errors)
    assert any(e['location'] == ['step', 'selection', 'path'] and e.get('correction') for e in errors[0]['schema_errors'])


def test_retrieval_reads_compacted_history_with_pagination_and_provenance(data):
    service = ConversationService(data.service.store, ChatService(), compact_trigger_tokens=256, keep_recent_messages=4)
    for i in range(10):
        data.service.store.append_conversation_message(data.scope['conversation_id'], role='user' if i % 2 == 0 else 'assistant',
            content=('保留单位和缺失值' if i == 0 else f'其他讨论{i}') + '；' * 500,
            estimated_tokens=600, metadata={'dataset_id': data.scope['dataset_id'], 'workspace_id': data.scope['workspace_id']}, created_at=service._now())
    assert asyncio.run(service.compact_if_needed(data.scope['conversation_id'], force=True))
    request, _ = service.begin_turn(request_for(data, message='继续最初的要求'))
    memory = ConversationMemory(data.service.store)
    memory.capture(request)
    found = memory.execute(request, 'search_conversation_history', {'query': '保留单位'})
    assert found['items'][0]['sequence'] == 1 and found['historical']
    page = memory.execute(request, 'read_conversation_memory', {'reference': 'message:1', 'max_chars': 8})
    following = memory.execute(request, 'read_conversation_memory', {'reference': 'message:1', 'offset': page['next_offset']})
    assert page['content'] + following['content'] == data.service.store.conversation_messages(request.conversation_id)[0].content
    with pytest.raises(ValueError, match='之后'):
        memory.execute(request, 'read_conversation_memory', {'reference': f'message:{request._user_sequence+1}'})


def test_history_search_paginates_without_duplicates_and_validates_inputs(data):
    service = ConversationService(data.service.store, ChatService())
    for i in range(15):
        service.begin_turn(request_for(data, message=f'决定 {i}'))
    request, _ = service.begin_turn(request_for(data, message='继续'))
    memory = ConversationMemory(data.service.store)
    first = memory.execute(request, 'search_conversation_history', {'query': '决定', 'limit': 10})
    second = memory.execute(request, 'search_conversation_history', {'query': '决定', 'limit': 10, 'before_sequence': first['next_before_sequence']})
    assert len(first['items']) + len(second['items']) == 15
    assert not set(i['sequence'] for i in first['items']) & set(i['sequence'] for i in second['items'])
    for args in ({'limit': True}, {'before_sequence': -1}, {'query': []}, {'conversation_id': 'foreign'}):
        with pytest.raises(ValueError):
            memory.execute(request, 'search_conversation_history', args)


def test_evidence_survives_restart_but_is_historical_and_scope_bound(data):
    request = request_for(data)
    memory = ConversationMemory(data.service.store)
    memory.capture(request)
    memory.remember_evidence(request, 'read_output_text', {'path': 'table.csv'}, {'content': 'id,value\n1,2\n'})
    reference = memory.load(request)['evidence'][0]['reference']
    restarted = ConversationMemory(data.service.store)
    result = restarted.execute(request, 'read_conversation_memory', {'reference': reference})
    assert json.loads(result['content'])['result']['content'] == 'id,value\n1,2\n'
    assert result['historical'] and not result['current_state_verified']
    other = request_for(data, workspace_id='other-output')
    assert restarted.load(other) == {}
    with pytest.raises(ValueError):
        restarted.execute(other, 'read_conversation_memory', {'reference': reference})
    changed_root = request.model_copy(update={'workspace_root_path': '/different/root'})
    # Client descriptions cannot change the identity of a registered server directory.
    assert restarted.load(changed_root) == restarted.load(request)
    token = principal.set(Principal('bob', True, ('ds_test',), (data.ws.id,)))
    try:
        with pytest.raises(PermissionError):
            restarted.load(request)
    finally:
        principal.reset(token)


def test_memory_does_not_satisfy_required_live_file_inspection():
    request = ChatRequest(message='现在目录里有什么', response_policy=ResponsePolicy(action='inspect', needs_files=True))
    old = [{'tool': 'read_conversation_memory', 'result': {'historical': True, 'content': '过去有三个文件'}}]
    assert completion_problem(request, old)['reason'] == 'missing_evidence'


def test_changed_conditions_keep_original_sources_and_archive_superseded_choices(data):
    ledger = AssistantTasks(data.service.store)
    first = request_for(data, message='求均值，保留缺失值')
    first._user_sequence = 1
    ledger.memory.capture(first)
    ledger.merge(first, AssistantIntent.model_validate(intent(goal='汇总表格', constraints=['求均值', '保留缺失值'],
        memory={'open_questions': ['按哪个分组'], 'assumptions': ['尚未核实列名']})))
    second = request_for(data, message='改成中位数，按组别汇总')
    second._user_sequence = 3
    ledger.memory.capture(second)
    ledger.merge(second, AssistantIntent.model_validate(intent(goal='汇总表格', remove_constraints=['求均值'],
        constraints=['求中位数', '按组别汇总'], memory={'open_questions': [], 'assumptions': []})))
    value = ledger.memory.load(second)
    conditions = {c['text']: c for c in value['task']['conditions']}
    assert conditions['保留缺失值']['source']['sequence'] == 1
    assert conditions['求中位数']['source']['sequence'] == 3
    assert value['superseded_conditions'][0]['text'] == '求均值'
    assert not value['task']['open_questions'] and not value['task']['assumptions']
    third = request_for(data, message='先改输出文件名')
    third._user_sequence = 5
    ledger.memory.capture(third)
    ledger.merge(third, AssistantIntent.model_validate(intent(relation='switch', goal='重命名文件', constraints=[])))
    value = ledger.memory.load(third)
    assert value['task']['conditions'] == [] and value['previous_tasks'][-1]['goal'] == '汇总表格'


def test_stale_reply_cannot_replace_newer_memory_and_client_fields_are_reset(data):
    memory = ConversationMemory(data.service.store)
    old, new = request_for(data, message='旧要求'), request_for(data, message='新的要求')
    old._user_sequence, new._user_sequence = 1, 3
    memory.capture(old); memory.capture(new)
    memory.update(old, {'goal': '旧要求'}, MemoryUpdate())
    memory.remember_reply(old, '旧回答')
    assert memory.load(new)['latest_input']['sequence'] == 3 and not memory.load(new).get('last_reply')
    service = ConversationService(data.service.store, ChatService())
    submitted = request_for(data)
    submitted._conversation_memory = {'grant': 'run_everything'}
    submitted._user_sequence = 9999
    prepared, _ = service.begin_turn(submitted)
    assert prepared._conversation_memory == {} and prepared._user_sequence != 9999
    assert '_conversation_memory' not in prepared.model_dump() and '_user_sequence' not in prepared.model_dump()


def test_deleting_conversation_removes_memory_and_evidence(data):
    request = request_for(data)
    memory = ConversationMemory(data.service.store)
    memory.capture(request)
    memory.remember_evidence(request, 'list_output_directory', {}, {'items': []})
    service = ConversationService(data.service.store, ChatService())
    service.update(request.conversation_id, archived=True)
    service.delete(request.conversation_id)
    with data.service.store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM assistant_memories').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM assistant_evidence').fetchone()[0] == 0
