"""Cross-scenario proposal grounding and supporting steps stay manual and scoped."""
import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest

from app.assistant_contract import StepMismatch
from app.assistant_proposal_review import ProposalReview, effects, grounded_review, verify_proposal, update_review
from app.assistant_step_tools import prepare_step
from app.assistant_tasks import AssistantTasks, canonical_operations, completion_problem
from app.workspace_tools import WorkspaceTools
from app.data_models import DataScriptConfirmation
from app.response_policy import ResponsePolicy
from test_assistant_step_contract import step
from test_assistant_task_logic import Model, assistant, request_for, understanding
from test_data_operations import data, approval


def review_model(decision='ready', issues=None):
    model = Mock()
    async def respond(messages, **kwargs):
        facts = json.loads(messages[-1]['content'])
        findings = []
        if decision == 'revise':
            evidence = facts.get('script') or json.dumps(facts['effects'], ensure_ascii=False)
            findings = [{'basis': 'user_requirement', 'requirement_quote': facts['current_request'][:100],
                         'evidence_quote': evidence[:100]} for _ in issues or []]
        payload = {'decision': decision, 'issues': issues or [], 'findings': findings,
                   'next_step': '修正实际规则' if issues else ''}
        kwargs['validate'](payload)
        return payload, []
    model.json_response = AsyncMock(side_effect=respond)
    return model


@pytest.mark.parametrize('action', ['rename', 'write_text'])
def test_semantic_mismatch_blocks_manual_run_without_losing_manifest(data, action):
    (data.output / 'prefix_20230910.csv').write_text('id,value\n1,2\n')
    tools = assistant(data, Model(understanding(), []))
    request = request_for(data, assistant_task={'goal': '去前缀和日期' if action == 'rename' else '将数值全部乘十',
        'step': step(actions=[action])})
    edit = {'action': action, 'path': 'prefix_20230910.csv'}
    edit.update(destination='20230910.csv') if action == 'rename' else edit.update(content='id,value\n1,2\n')
    task = tools.execute('propose_data_changes', {'source_scope': 'output', 'mode': 'inplace',
        'title': '全部要求已实现', 'edits': [edit]}, request)
    assert not task['can_confirm']
    rows_before = data.service.repository.items(task['id'])
    with pytest.raises(ValueError):
        data.service.confirm(task['id'], approval(task))
    model = review_model('revise', ['实际改动没有满足用户条件'])
    checked = asyncio.run(verify_proposal(data.service, model, request, task))
    assert not checked['can_confirm'] and checked['quality']['proposal_review']['decision'] == 'revise'
    facts = json.loads(model.json_response.call_args.args[0][1]['content'])
    assert facts['effects'][0]['path'] == 'prefix_20230910.csv' and facts['goal'] == request.assistant_task['goal']
    assert data.service.repository.items(task['id']) == rows_before
    assert not data.service.futures and (data.output / 'prefix_20230910.csv').read_text().endswith('1,2\n')


def test_review_approves_only_exact_immutable_proposal_and_retains_overwrite_confirmation(data):
    (data.output / 'table.csv').write_text('id,value\n1,2\n')
    tools = assistant(data, Model(understanding(), []))
    request = request_for(data, assistant_task={'goal': '把数值乘十', 'step': step(actions=['write_text'])})
    task = tools.execute('propose_data_changes', {'source_scope': 'output', 'mode': 'inplace',
        'edits': [{'action': 'write_text', 'path': 'table.csv', 'content': 'id,value\n1,20\n'}]}, request)
    checked = asyncio.run(verify_proposal(data.service, review_model(), request, task))
    assert checked['can_confirm'] and checked['requires_overwrite_confirmation']
    assert checked['manifest_hash'] == task['manifest_hash']
    with pytest.raises(ValueError, match='已变化'):
        update_review(data.service, task['id'], request.conversation_id, {'decision': 'ready'}, 'different-hash')
    assert not data.service.futures and (data.output / 'table.csv').read_text().endswith('1,2\n')


def test_review_does_not_truncate_large_manifest_and_reports_sample_limits(data):
    for index in range(205):
        (data.output / f'prefix_{index}.txt').write_text(str(index))
    request = request_for(data, assistant_task={'goal': '移除所有前缀', 'step': step()})
    task = assistant(data, Model(understanding(), [])).execute('propose_filename_transform',
        {'transforms': [{'action': 'strip_prefix', 'old': 'prefix_'}]}, request)
    checked = asyncio.run(verify_proposal(data.service, review_model(), request, task))
    assert checked['file_count'] == len(data.service.repository.items(task['id'])) == 205
    assert effects(checked)['sample_only'] and len(effects(checked)['effects']) == 8


@pytest.mark.parametrize('actions', [['rename'], ['write_text']])
def test_supporting_script_changes_implementation_without_expanding_target(data, actions):
    ledger = AssistantTasks(data.service.store)
    original = step(actions=actions, selection={'include': ['table.csv'], 'exclude': ['private*']})
    request = request_for(data, response_policy=ResponsePolicy(action='operate'))
    ledger.save(request, {'goal': '完整修改目标', 'constraints': ['保留原文件'], 'step': original})
    arguments = {**original, 'mode': 'copy', 'actions': ['script'], 'stage': 'new',
                 'preparation': True, 'purpose': '输出完整映射报告'}
    arguments.pop('request_excerpt')
    prepare_step(ledger, request, arguments)
    assert request.assistant_task['target_step'] == original
    assert request.assistant_task['step_role'] == 'preparation' and request.assistant_task['goal'] == '完整修改目标'
    data.service.sandbox.image = 'sha256:' + 'a' * 64
    task = assistant(data, Model(understanding(), [])).execute('run_data_python', {
        'source_scope': 'output', 'mode': 'copy', 'code': "open('/output/mapping.csv','w').write('source,target\\n')"}, request)
    assert task['status'] == 'pending' and not task['can_run_script']
    confirmation = DataScriptConfirmation(confirmed=True, conversation_id=request.conversation_id,
                                          script_plan_hash=task['script_plan_hash'])
    with pytest.raises(ValueError, match='目标核验'):
        data.service.start_python(task['id'], confirmation)
    checked = asyncio.run(verify_proposal(data.service, review_model(), request, task))
    assert checked['can_run_script'] and not checked['can_confirm'] and not data.service.futures
    assert not (data.output / 'mapping.csv').exists()


@pytest.mark.parametrize('updates', [{'mode': 'inplace'}, {'actions': ['delete']},
    {'source_scope': 'dataset'}, {'selection': {}}, {'stage': 'repeat', 'base_operation_id': 'old'}])
def test_preparation_cannot_relax_edit_or_scope_guards(data, updates):
    ledger = AssistantTasks(data.service.store)
    request = request_for(data, response_policy=ResponsePolicy(action='operate'))
    ledger.save(request, {'step': step(selection={'include': ['table.csv']})})
    args = {'purpose': '生成报告', 'source_scope': 'output', 'mode': 'analysis', 'actions': ['script'],
            'selection': {'include': ['table.csv']}, 'preparation': True, **updates}
    with pytest.raises((StepMismatch, ValueError)):
        prepare_step(ledger, request, args)
    assert request.assistant_task['step']['mode'] == 'inplace'


def test_latest_conversation_transform_revision_can_prepare_full_mapping_script(data):
    (data.output / 'prefix_20230910_qsm_1703_e1.json').write_text('{}')
    first = assistant(data, Model(understanding(), [])).execute('propose_filename_transform',
        {'transforms': [{'action': 'strip_prefix', 'old': 'prefix_'}]}, request_for(data, assistant_task={'step': step()}))
    ledger = AssistantTasks(data.service.store)
    ledger.save(request_for(data), {'operations': {first['id']: first}, 'active_operation_id': first['id']})
    original = step(stage='revise_pending', base_operation_id=first['id'])
    prepare = {k: v for k, v in step(mode='copy', actions=['script'], stage='new', purpose='输出完整映射供审阅').items() if k != 'request_excerpt'}
    prepare['preparation'] = True
    model = Model(understanding(step=original), [
        ('run_data_python', {'source_scope': 'output', 'mode': 'analysis', 'code': "print('sample')"}),
        ('prepare_current_step', prepare),
        ('run_data_python', {'source_scope': 'output', 'mode': 'copy', 'code': "open('/output/mapping.csv','w').write('source,target\\n')"}),
        ('answer_without_action', {'answer': '已准备'})])
    data.service.sandbox.image = 'sha256:' + 'a' * 64
    current_request = request_for(data)
    answer = asyncio.run(assistant(data, model).reply(current_request))
    task = data.service.list(first['conversation_id'])[0]
    assert '前置方案' in answer and task['phase'] == 'script' and task['can_run_script']
    assert not data.service.futures and len(data.service.list(first['conversation_id'])) == 2
    events = data.workspace.events(current_request.client_turn_id)
    assert any(e.get('data_operation', {}).get('id') == task['id'] and e['data_operation']['can_run_script'] for e in events)
    assert task['quality']['proposal_review']['decision'] == 'ready'


def test_replacement_does_not_keep_wrong_old_proposal_in_completion_targets(data):
    (data.output / 'a.txt').write_text('a')
    request = request_for(data, assistant_task={'goal': '改为 c.txt', 'step': step()})
    tools = assistant(data, Model(understanding(), []))
    def propose(name):
        return tools.execute('propose_data_changes', {'source_scope': 'output', 'mode': 'inplace',
            'edits': [{'action': 'rename', 'path': 'a.txt', 'destination': name}]}, request)
    first = propose('b.txt')
    request.assistant_task['step_operation_id'] = first['id']
    second = propose('c.txt')
    observations = [{'tool': 'propose_data_changes', 'result': data.service.get(t['id'], request.conversation_id)} for t in (first, second)]
    assert [t['id'] for t in canonical_operations(observations)] == [second['id']]
    assert not data.service.get(first['id'], request.conversation_id)['can_confirm']


def test_incomplete_or_failed_review_never_approves_script(data):
    request = request_for(data, assistant_task={'goal': '输出全部记录', 'step': step(mode='analysis', actions=['script'], stage='new')})
    data.service.sandbox.image = 'sha256:' + 'a' * 64
    task = assistant(data, Model(understanding(), [])).execute('run_data_python',
        {'source_scope': 'output', 'mode': 'analysis', 'code': "print('first 15 only')"}, request)
    checked = asyncio.run(verify_proposal(data.service, review_model('revise', ['只输出样例，没有完整结果']), request, task))
    assert not checked['can_run_script']
    assert '没有完整结果' in checked['quality']['proposal_review']['issues'][0]
    assert not data.service.futures


@pytest.mark.parametrize('title,area', [('扫描命名', '序列识别'), ('整理数据表', '缺失值处理'), ('生成统计报告', '完整结果输出')])
def test_revision_summary_separates_model_fixes_from_technical_evidence(title, area):
    evidence = '实现未满足用户已确认条件：' + '详细代码依据。' * 50
    review = ProposalReview(decision='revise', issues=[evidence], summary='当前实现遗漏已确认条件',
                            fixes=[{'area': area, 'change': '按已确认条件修正处理规则'}]).model_dump()
    task = {'id': 'new', 'kind': 'data', 'status': 'pending', 'title': title,
            'quality': {'blocking_issues': ['核验未通过'], 'proposal_review': review}}
    answer = WorkspaceTools.pending_summary([{'tool': 'propose_data_changes', 'result': task}])
    assert '需要模型修订' in answer and '无需手动修改代码' in answer and area in answer
    assert '暂不能执行' in answer and evidence not in answer and len(answer) < 300


def test_user_information_and_unavailable_review_are_not_code_repair(data):
    (data.output / 'a.txt').write_text('a')
    request = request_for(data, assistant_task={'goal': '调整文件', 'step': step()})
    task = assistant(data, Model(understanding(), [])).execute('propose_data_changes',
        {'source_scope': 'output', 'mode': 'inplace', 'edits': [{'action': 'rename', 'path': 'a.txt', 'destination': 'b.txt'}]}, request)
    checked = asyncio.run(verify_proposal(data.service, review_model(), request, task, timeout=0))
    observation = [{'tool': 'propose_data_changes', 'result': checked}]
    assert checked['quality']['proposal_review']['decision'] == 'unavailable' and not checked['can_confirm']
    assert completion_problem(request, observation)['repairable'] is False
    answer = WorkspaceTools.pending_summary(observation)
    assert '重新核验' in answer and '不能据此判定代码有错' in answer and '需要模型修订' not in answer
    clarify = ProposalReview(decision='clarify', issues=['目标名称缺少用户选择'],
                             questions=['报告标题使用哪个名称？']).model_dump()
    checked = update_review(data.service, task['id'], request.conversation_id, clarify, task['manifest_hash'])
    observation[0]['result'] = checked
    assert not checked['can_confirm'] and completion_problem(request, observation)['repairable'] is False
    answer = WorkspaceTools.pending_summary(observation)
    assert '需要你补充信息' in answer and '报告标题使用哪个名称' in answer
    assert not data.service.futures and (data.output / 'a.txt').read_text() == 'a'


@pytest.mark.parametrize('payload', [
    {'decision': 'ready', 'issues': ['仍有问题']},
    {'decision': 'ready', 'fixes': [{'area': '代码', 'change': '修订'}]},
    {'decision': 'clarify', 'issues': ['缺少选择']},
    {'decision': 'revise', 'issues': ['实现错误'], 'questions': ['再次确认许可？']},
])
def test_review_rejects_conflicting_readiness_or_question_ownership(payload):
    with pytest.raises(ValueError):
        ProposalReview.model_validate(payload)


def test_summary_uses_refreshed_current_version_and_preserves_independent_ready_plan():
    ready = {'id': 'ready', 'kind': 'data', 'title': '生成报告', 'status': 'pending', 'file_count': 1,
             'quality': {'blocking_issues': [], 'proposal_review': {'decision': 'ready'}}}
    old = {**ready, 'id': 'old', 'title': '旧版本',
           'quality': {'blocking_issues': ['旧版'], 'proposal_review': {'decision': 'revise', 'superseded_by': 'new'}}}
    new = {**ready, 'id': 'new', 'title': '当前版本',
           'quality': {'blocking_issues': ['实现错误'], 'proposal_review': {'decision': 'revise', 'issues': ['规则遗漏']}}}
    observation = [{'tool': 'propose_data_changes', 'result': t} for t in [old, ready, {**new, 'quality': {}}, new]]
    answer = WorkspaceTools.pending_summary(observation)
    assert '旧版本' not in answer and answer.count('当前版本') == 1
    assert '需要模型修订' in answer and '可运行，尚未执行' in answer and '生成报告' in answer
    assert WorkspaceTools.pending_summary([
        {'tool': 'propose_data_changes', 'result': ready},
        {'tool': 'get_data_operation', 'result': {**ready, 'status': 'succeeded'}}]) is None


@pytest.mark.parametrize('decision', ['unavailable', 'clarify'])
def test_followup_can_verify_same_content_once_without_rewriting_or_running(data, decision):
    (data.output / 'a.txt').write_text('a')
    request = request_for(data, client_turn_id='first-review-request', response_policy=ResponsePolicy(action='operate'),
                          assistant_task={'goal': '调整文件', 'step': step()})
    task = assistant(data, Model(understanding(), [])).execute('propose_data_changes',
        {'source_scope': 'output', 'mode': 'inplace', 'edits': [{'action': 'rename', 'path': 'a.txt', 'destination': 'b.txt'}]}, request)
    review = {'decision': decision, 'issues': ['核验未完成' if decision == 'unavailable' else '需要用户选择'], 'reviewed_turn_id': request.client_turn_id}
    if decision == 'clarify': review['questions'] = ['使用哪个标题？']
    task = update_review(data.service, task['id'], request.conversation_id, review, task['manifest_hash'])
    same = review_model()
    assert asyncio.run(verify_proposal(data.service, same, request, task)) == task
    same.json_response.assert_not_awaited()
    request.client_turn_id = 'user-followup-request'
    failed = review_model();failed.json_response = AsyncMock(side_effect=TimeoutError())
    checked = asyncio.run(verify_proposal(data.service, failed, request, task))
    assert checked['quality']['proposal_review']['decision'] == 'unavailable'
    assert asyncio.run(verify_proposal(data.service, failed, request, checked)) == checked
    assert failed.json_response.await_count == 1
    request.client_turn_id = 'next-user-followup'
    checked = asyncio.run(verify_proposal(data.service, review_model(), request, checked))
    assert checked['id'] == task['id'] and checked['manifest_hash'] == task['manifest_hash'] and checked['can_confirm']
    assert len(data.service.list(request.conversation_id)) == 1 and not data.service.futures
    assert (data.output / 'a.txt').read_text() == 'a' and not (data.output / 'b.txt').exists()


def test_review_blockers_need_original_user_requirements_and_real_code_evidence(data):
    request = request_for(data, message='将数值全部乘十', assistant_task={'completion_checks': ['生成用户没有要求的图表']})
    result = {'script': "value = value * 10", 'items': []}
    payload = {'decision': 'revise', 'issues': ['需要图表'], 'findings': [{
        'basis': 'user_requirement', 'requirement_quote': '生成用户没有要求的图表', 'evidence_quote': 'value * 10'}]}
    with pytest.raises(ValueError, match='用户原话'):
        grounded_review(payload, request, result)
    payload['findings'][0]['requirement_quote'] = '将数值全部乘十'
    payload['findings'][0]['evidence_quote'] = 'value * 100'
    with pytest.raises(ValueError, match='实际代码'):
        grounded_review(payload, request, result)
    suggestion = {'decision': 'ready', 'issues': [], 'suggestions': ['可选增加图表']}
    assert grounded_review(suggestion, request, result).decision == 'ready'


def test_proposal_is_reviewed_immediately_and_repaired_in_the_same_turn_without_extra_state_queries(data):
    (data.output / 'table.csv').write_text('id,value\n1,2\n')
    args = {'source_scope': 'output', 'mode': 'inplace', 'edits': [{'action': 'write_text',
        'path': 'table.csv', 'content': 'id,value\n1,2\n'}]}
    revised = {**args, 'edits': [{**args['edits'][0], 'content': 'id,value\n1,20\n'}]}
    model = Model(understanding(goal='将数值全部乘十', step=step(actions=['write_text'])), [
        ('propose_data_changes', args), ('propose_data_changes', revised), '不应需要这个额外回答轮次'])
    original_response = model.json_response
    reviews = []
    async def response(messages, **kwargs):
        if kwargs.get('stage') != 'assistant_verification':
            return await original_response(messages, **kwargs)
        reviews.append(json.loads(messages[-1]['content']))
        payload = ({'decision': 'revise', 'issues': ['数值仍为2'], 'findings': [{
            'basis': 'user_requirement', 'requirement_quote': '将数值全部乘十', 'evidence_quote': '"diff": ""'}],
            'fixes': [{'area': '数值', 'change': '使用乘十后的完整内容'}]} if len(reviews) == 1
            else {'decision': 'ready', 'issues': []})
        kwargs['validate'](payload)
        return payload, []
    model.json_response = response
    answer = asyncio.run(assistant(data, model).reply(request_for(data, message='将数值全部乘十')))
    assert model.index == 2 and len(reviews) == 2 and '点击运行' in answer
    latest = data.service.list(data.scope['conversation_id'])[0]
    assert latest['can_confirm'] and len(data.service.list(data.scope['conversation_id'])) == 2
    assert (data.output / 'table.csv').read_text() == 'id,value\n1,2\n' and not data.service.futures
