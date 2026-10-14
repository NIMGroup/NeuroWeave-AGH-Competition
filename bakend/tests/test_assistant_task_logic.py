"""General task continuity, grounded completion and repair without extra consent."""
import asyncio
import json
import copy

import pytest

from app.assistant_tasks import AssistantIntent, AssistantTasks, completion_problem, history_context
from app.conversation_service import ConversationService
from app.chat_service import ChatService
from app.models import ChatRequest, Message
from app.data_models import OrganizeRequest
from app.response_policy import ResponsePolicy
from app.workspace_tools import WorkspaceTools
from test_data_operations import data


def request_for(data, **updates):
    return ChatRequest(**{**data.scope, 'message': '处理选定范围', 'interaction_mode': 'task', **updates})


def understanding(action='operate', relation='continue', **updates):
    return {'response_policy': {'action': action, 'relation': relation}, 'goal': '统一整理结果',
            'outcome': 'execute', 'coverage': 'selected', 'constraints': [], 'completion_checks': [],
            'step': {'request_excerpt': '处理选定范围', 'purpose': '处理当前文件', 'source_scope': 'unspecified'}, **updates}


class Model:
    mode = 'model'

    def __init__(self, intent, calls):
        self.intent, self.calls, self.index, self.messages = intent, calls, 0, []

    async def json_response(self, messages, *, validate, **kwargs):
        if kwargs.get('stage') == 'assistant_verification':
            payload = {'decision': 'ready', 'issues': [], 'next_step': ''}
            validate(payload)
            return payload, []
        self.messages = messages
        intent = copy.deepcopy(self.intent)
        step = intent['step']
        step['request_excerpt'] = messages[-1]['content'][:1200]
        if step['source_scope'] == 'unspecified':
            for item in self.calls:
                if isinstance(item, str):
                    continue
                name, args = item
                if name.startswith('propose_') or name == 'run_data_python':
                    step.update(source_scope=args.get('source_scope', 'dataset'), mode=args.get('mode', 'analysis' if name == 'run_data_python' else 'copy'))
                    step['actions'] = ((['script'] if step['mode'] == 'analysis' else ['script', 'write_text']) if name == 'run_data_python' else
                                       list({e['action'] for e in args.get('edits', [])}) or ['copy' if step['mode'] == 'copy' else 'rename'])
                    break
        validate(intent)
        return intent, []

    async def workspace_completion(self, messages, tools, **kwargs):
        self.messages = messages
        item = self.calls[min(self.index, len(self.calls) - 1)]
        self.index += 1
        if isinstance(item, str):
            return {'role': 'assistant', 'content': item}
        name, args = item
        return {'role': 'assistant', 'tool_calls': [{'id': str(self.index),
                'function': {'name': name, 'arguments': json.dumps(args)}}]}


def assistant(data, model):
    tools = WorkspaceTools(data.workspace, model, data.service.datasets)
    tools.data_operations = data.service
    return tools


def test_revising_a_preparation_step_preserves_the_original_target_and_goal(data):
    ledger = AssistantTasks(data.service.store)
    request = request_for(data)
    target = {'mode': 'inplace', 'actions': ['rename'], 'source_scope': 'output'}
    ledger.save(request, {'goal': '修改原始文件名', 'outcome': 'execute',
        'step_role': 'preparation', 'target_step': target, 'step': {'mode': 'analysis', 'actions': ['script']}})
    intent = AssistantIntent.model_validate(understanding(goal='生成映射', step={
        'mode': 'analysis', 'actions': ['script'], 'stage': 'revise_pending', 'base_operation_id': 'pending'}))
    result = ledger.merge(request, intent)
    assert result['step_role'] == 'preparation' and result['target_step'] == target
    assert result['goal'] == '修改原始文件名'
    new = AssistantIntent.model_validate(understanding(relation='switch', goal='回答新问题'))
    assert 'target_step' not in ledger.merge(request, new)


def test_contract_survives_restart_questions_compaction_and_explicit_revisions(data):
    req = request_for(data)
    ledger = AssistantTasks(data.service.store)
    ledger.merge(req, AssistantIntent.model_validate(understanding(relation='independent', coverage='all',
        constraints=['保留输入', '输出中区分不确定类型'], completion_checks=['覆盖全部指定文件'])))
    next_turn = request_for(data, message='为什么还有遗漏', conversation_summary='摘要不包含命名选择')
    restarted = AssistantTasks(data.service.store)
    restarted.merge(next_turn, AssistantIntent.model_validate(understanding(action='answer', outcome='answer', coverage='selected')))
    state = restarted.load(next_turn)
    assert state['coverage'] == 'all' and state['outcome'] == 'execute'
    assert state['constraints'] == ['保留输入', '输出中区分不确定类型']
    assert state['completion_checks'] == ['覆盖全部指定文件']
    restarted.merge(next_turn, AssistantIntent.model_validate(understanding(remove_constraints=['输出中区分不确定类型'], constraints=['按原名保留未知文件'])))
    assert restarted.load(next_turn)['constraints'] == ['保留输入', '按原名保留未知文件']
    restarted.merge(next_turn, AssistantIntent.model_validate(understanding(relation='switch', goal='汇总表格')))
    assert restarted.load(next_turn)['constraints'] == []
    assert restarted.load(request_for(data, dataset_id='other')) == {}
    assert restarted.load(request_for(data, workspace_id='another-output')) == {}


def test_client_cannot_inject_saved_goal_or_authorization(data):
    service = ConversationService(data.service.store, ChatService())
    req = request_for(data, assistant_task={'coverage': 'selected', 'grant': 'approve_everything'})
    prepared, _ = service.begin_turn(req)
    assert prepared.assistant_task == {}
    assert 'assistant_task' not in prepared.model_dump()


def test_initial_goal_and_user_choices_are_not_lost_after_six_messages():
    rows = [Message(role='user', content='处理全部文件，保留目录')]
    rows += [Message(role='assistant', content='冗长说明' * 200)] * 10
    rows += [Message(role='user', content='已接受的格式规则')]
    rows += [Message(role='assistant', content='其他说明')] * 8
    context = json.dumps(history_context(ChatRequest(message='继续', history=rows)), ensure_ascii=False)
    assert '处理全部文件' in context and '已接受的格式规则' in context


def test_explanation_is_read_only_even_when_provider_calls_a_write_tool(data):
    model = Model(understanding(action='answer', outcome='answer'), [
        ('propose_data_changes', {'source_scope': 'output', 'mode': 'inplace', 'execution': 'output',
                                 'edits': [{'action': 'write_text', 'path': 'answer.csv', 'content': 'x'}]}),
        ('answer_without_action', {'answer': '这是当前规则的解释。'})])
    answer = asyncio.run(assistant(data, model).reply(request_for(data, message='解释当前规则')))
    assert answer == '这是当前规则的解释。'
    assert not (data.output / 'answer.csv').exists()


def test_schema_error_is_repaired_with_field_feedback_without_a_user_question(data):
    base = {'source_scope': 'output', 'mode': 'inplace', 'execution': 'output',
            'edits': [{'action': 'write_text', 'path': 'clean.csv', 'content': 'a,b\n1,2\n'}]}
    model = Model(understanding(), [('propose_data_changes', {**base, 'invalid_parameter': 'private fixture'}),
        '已完成', ('propose_data_changes', base), ('answer_without_action', {'answer': '已保存整理后的表格。'})])
    answer = asyncio.run(assistant(data, model).reply(request_for(data)))
    assert '点击运行' in answer and 'private fixture' not in answer and 'ValidationError' not in answer
    task = data.service.list(data.scope['conversation_id'])[0]
    assert task['status'] == 'pending' and task['id'] not in data.service.futures
    assert not (data.output / 'clean.csv').exists()
    feedback = [json.loads(m['content']) for m in model.messages if m.get('role') == 'tool' and 'fields' in m['content']]
    assert feedback and feedback[0]['fields'][0]['field'] == 'invalid_parameter'
    assert 'private fixture' not in json.dumps(feedback)


def test_incomplete_preview_cannot_finish_and_incremental_repair_reaches_full_goal(data):
    (data.source / 'prefix_alpha.nii.gz').write_bytes(b'alpha')
    (data.source / 'prefix_beta.nii.gz').write_bytes(b'beta')
    model = Model(understanding(coverage='all', outcome='preview'), [
        ('propose_data_organization', {'rules': [{'pattern': '*alpha*', 'name': 'alpha'}]}),
        ('answer_without_action', {'answer': '请确认完整方案'}),
        ('propose_data_organization', {'rules': [{'pattern': '*beta*', 'name': 'beta'}]}),
        ('answer_without_action', {'answer': '方案已补全'})])
    answer = asyncio.run(assistant(data, model).reply(request_for(data)))
    tasks = data.service.list(data.scope['conversation_id'])
    latest = tasks[0]
    assert latest['scan']['matched_files'] == 2 and latest['scan']['unmatched_files'] == 0
    assert len(latest['recipe']['rules']) == 2 and latest['recipe']['base_operation_id'] == tasks[1]['id']
    assert '尚未执行' in answer and '点击运行' in answer and '未覆盖' not in answer
    assert not list(data.output.iterdir())


def test_repeated_incomplete_answers_stop_with_concrete_counts_without_approval(data):
    (data.source / 'known.nii.gz').write_bytes(b'a')
    (data.source / 'unknown.txt').write_bytes(b'b')
    model = Model(understanding(coverage='all'), [('propose_data_organization',
        {'rules': [{'pattern': 'known*', 'name': 'known'}], 'execution': 'output'}),
        ('answer_without_action', {'answer': '全部完成，请确认'})])
    answer = asyncio.run(assistant(data, model).reply(request_for(data)))
    assert '未覆盖 1' in answer and '不能执行' in answer and '请确认' not in answer
    assert not list(data.output.iterdir())


def test_read_success_does_not_hide_failed_action_or_unfinished_independent_work():
    req = ChatRequest(message='调整文件', response_policy=ResponsePolicy(action='operate'), assistant_task={'outcome': 'execute'})
    observations = [{'tool': 'read_output_text', 'result': {'content': 'ok'}},
                    {'tool': 'propose_data_changes', 'result': {'error': 'bad parameter'}}]
    assert completion_problem(req, observations)['reason'] == 'tool_error'
    observations = [{'tool': 'propose_data_changes', 'result': {'kind': 'data', 'id': 'a', 'status': 'failed'}},
                    {'tool': 'propose_data_changes', 'result': {'kind': 'data', 'id': 'b', 'status': 'succeeded'}}]
    assert completion_problem(req, observations)['reason'] == 'execution_failed'


def test_latest_sibling_revision_supersedes_old_incomplete_drafts():
    req = ChatRequest(message='修正规则', response_policy=ResponsePolicy(action='operate'), assistant_task={'outcome':'preview','coverage':'all'})
    observations = [{'tool':'propose_data_organization','result':{'kind':'data','id':'first','status':'pending','scan':{'unmatched_files':1}}},
        {'tool':'propose_data_organization','result':{'kind':'data','id':'bad-revision','status':'pending','recipe':{'base_operation_id':'first'},'scan':{'unmatched_files':4}}},
        {'tool':'propose_data_organization','result':{'kind':'data','id':'good-revision','status':'pending','recipe':{'base_operation_id':'first'},'scan':{'unmatched_files':0}}}]
    assert completion_problem(req, observations) is None


def test_model_cannot_discard_agreed_rules_during_an_incremental_revision(data):
    req = request_for(data, assistant_task={'goal':'继续整理','allow_recipe_replacement':False})
    with pytest.raises(ValueError, match='增量'):
        assistant(data, Model(understanding(),[])).execute('propose_data_organization',
            {'replace_rules':True,'rules':[{'pattern':'*','name':'other'}]},req)


def test_lineage_works_without_loading_previous_turns_manifests():
    req = ChatRequest(message='继续', response_policy=ResponsePolicy(action='operate'), assistant_task={'outcome':'preview','coverage':'all'})
    observations = [{'tool':'propose_data_organization','result':{'kind':'data','id':'a','status':'pending',
        'recipe':{'base_operation_id':'old-a','lineage_root_id':'root'},'scan':{'unmatched_files':4}}},
        {'tool':'propose_data_organization','result':{'kind':'data','id':'b','status':'pending',
         'recipe':{'base_operation_id':'old-b','lineage_root_id':'root'},'scan':{'unmatched_files':0}}}]
    assert completion_problem(req, observations) is None


def test_paged_list_preserves_all_operation_counts_without_manifest_overflow(data):
    (data.source / 'test.nii.gz').write_bytes(b'a')
    task = data.service.organize(OrganizeRequest(
        **data.scope, rules=[{'pattern': '*', 'name': 'scan'}]))
    req = request_for(data)
    result = assistant(data, Model(understanding(), [])).execute('list_data_operations', {}, req)
    assert result['items'][0]['id'] == task['id']
    assert 'items' not in result['items'][0] and result['items'][0]['scan']['file_count'] == 1
