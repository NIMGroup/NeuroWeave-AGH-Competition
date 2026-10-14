"""Current objects/actions across file, text, script and multi-stage tasks."""
import asyncio
import json
from unittest.mock import Mock
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.assistant_contract import AssistantStep, StepMismatch, validate_step
from app.assistant_runtime import preview
from app.assistant_tasks import AssistantIntent, AssistantTasks
from app.data_models import OrganizeRequest, PythonRequest
from app.data_selection import DataSelection
from app.model_validation import ModelValidationError
from test_assistant_task_logic import Model, assistant, request_for, understanding
from test_data_operations import data, approval, finished, run_script


def step(**updates):
    return {'request_excerpt': '处理选定范围', 'purpose': '修改已生成结果', 'source_scope': 'output',
            'mode': 'inplace', 'actions': ['rename'], 'stage': 'modify_results', **updates}


def req(data, **updates):
    return request_for(data, assistant_task={'step': step(**updates)})


def copied(data, count=1):
    (data.source / 'long_T1.nii.gz').write_bytes(b'image')
    for i in range(count):
        (data.source / f'prefix_unknown_{i}.txt').write_text(str(i))
    task = data.service.organize(OrganizeRequest(**data.scope, rules=[{'pattern': '*T1*', 'name': 'T1'}], unmatched_policy='preserve'))
    data.service.confirm(task['id'], approval(task))
    assert finished(data, task)['status'] == 'succeeded'
    return data.service.get(task['id'], task['conversation_id'])


def test_wrong_scope_action_and_subset_rejected_before_creating_any_task(data):
    (data.output / 'a.txt').write_text('keep')
    tools = assistant(data, Model(understanding(), []))
    request = req(data, selection={'include': ['a.txt']})
    for arguments in [
        {'source_scope': 'dataset', 'mode': 'copy', 'edits': [{'action': 'copy', 'path': 'a.txt', 'destination': 'b.txt'}]},
        {'source_scope': 'output', 'mode': 'inplace', 'edits': [{'action': 'delete', 'path': 'a.txt'}]},
        {'source_scope': 'output', 'mode': 'inplace', 'edits': [{'action': 'rename', 'path': 'other.txt', 'destination': 'b.txt'}]},
    ]:
        with pytest.raises(StepMismatch):
            tools.execute('propose_data_changes', arguments, request)
    assert not data.service.list(data.scope['conversation_id'])
    assert (data.output / 'a.txt').read_text() == 'keep'


def test_completed_copy_is_provenance_for_one_full_batch_transform(data):
    previous = copied(data, 205)
    selection = {'from_operation_id': previous['id'], 'category': 'preserved'}
    request = req(data, selection=selection)
    tools = assistant(data, Model(understanding(), []))
    inventory = tools.execute('inventory_data_files', {'source_scope': 'output', 'selection': selection, 'limit': 2}, request)
    assert inventory['file_count'] == inventory['coverage']['file_count'] == 205 and len(inventory['items']) == 2
    assert data.service.operation_files(previous['id'], previous['conversation_id'], 'preserved', limit=2)['total'] == 205
    task = tools.execute('propose_filename_transform', {'source_scope': 'output', 'mode': 'inplace',
        'transforms': [{'action': 'strip_prefix', 'old': 'prefix_'}]}, request)
    assert task['file_count'] == 205 and task['action_counts'] == {'rename': 205} and task['can_confirm']
    assert task['source_scope'] == 'output' and task['mode'] == 'inplace' and 'recipe' not in task
    rows = data.service.operation_files(task['id'], previous['conversation_id'], 'changes', full=True)['items']
    assert len(rows) == 205 and all(r['destination'].startswith('unknown_') for r in rows)
    assert (data.output / 'T1.nii.gz').exists() and (data.output / 'prefix_unknown_0.txt').exists()
    assert not (data.output / 'unknown_0.txt').exists() and not data.service.futures.get(task['id'])


def test_completed_plan_not_automatically_inherited_or_explicitly_revised(data):
    previous = copied(data)
    request = req(data)
    request.assistant_task.update(active_operation_id=previous['id'], operations={previous['id']: previous})
    tools = assistant(data, Model(understanding(), []))
    with pytest.raises(StepMismatch):
        tools.execute('propose_data_organization', {'base_operation_id': previous['id'], 'source_scope': 'output', 'mode': 'inplace',
            'rules': [{'pattern': '*.txt', 'kind': 'file', 'name': 'clean'}]}, request)
    assert len(data.service.list(previous['conversation_id'])) == 1
    task = tools.execute('propose_data_organization', {'source_scope': 'output', 'mode': 'inplace',
        'rules': [{'pattern': '*.txt', 'kind': 'file', 'name': 'clean'}]}, request)
    assert task['recipe']['base_operation_id'] is None and len(task['recipe']['rules']) == 1


def test_step_requires_current_user_anchor_and_pending_revision_facts():
    with pytest.raises(ValueError, match='当前消息'):
        validate_step(AssistantStep.model_validate(step(request_excerpt='旧复制请求')), '在输出目录改名', {})
    pending = step(source_scope='dataset', mode='copy', actions=['copy'], stage='revise_pending', base_operation_id='old')
    with pytest.raises(ValueError, match='已完成'):
        validate_step(AssistantStep.model_validate(pending), '处理选定范围', {'old': {'status': 'succeeded', 'recipe': {'rules': []}}})
    validate_step(AssistantStep.model_validate(pending), '处理选定范围',
                  {'old': {'status': 'pending', 'source_scope': 'dataset', 'mode': 'copy', 'recipe': {'rules': [{}]}}})


def test_duplicate_provenance_reference_does_not_become_execution_baseline():
    value = AssistantStep.model_validate(step(base_operation_id='old', selection={'from_operation_id': 'old', 'category': 'preserved'}))
    assert value.base_operation_id is None and value.selection.from_operation_id == 'old'
    validate_step(value, '处理选定范围', {})


def test_semantic_validation_retry_gives_static_field_hint_without_raw_input():
    from app.llm.json_response import json_response
    client = Mock()
    client.complete = AsyncMock(side_effect=['{"field":"private-fixture-value"}', '{"field":null}'])
    def validate(value):
        if value['field'] is not None:
            raise ModelValidationError(('step', 'base_operation_id'), 'provenance_not_baseline', '结果来源不是执行基准，请将 base_operation_id 设为 null')
    result, diagnostics = asyncio.run(json_response(client, [{'role': 'user', 'content': '处理结果'}], stage='assistant_task', validate=validate))
    assert result == {'field': None} and len(diagnostics) == 1
    correction = client.complete.call_args_list[1].args[0][-1]['content']
    assert 'base_operation_id' in correction and 'null' in correction and 'private-fixture-value' not in correction


def test_nested_step_validation_explains_output_mode_without_exposing_request():
    from app.llm.json_response import json_response
    client = Mock()
    original = step(purpose='private fixture data', source_scope='dataset', mode='analysis', actions=['script', 'write_text'], stage='new')
    client.complete = AsyncMock(side_effect=[json.dumps(original), json.dumps({**original, 'mode': 'copy'})])
    result, diagnostics = asyncio.run(json_response(client, [{'role': 'user', 'content': '准备汇总文件'}],
        stage='assistant_task', validate=AssistantStep.model_validate))
    assert result['mode'] == 'copy' and diagnostics[0]['schema_errors']
    correction = client.complete.call_args_list[1].args[0][-1]['content']
    fields = json.loads(correction[correction.index('['):])
    assert any('生成新文件用 copy' in item.get('correction', '') for item in fields) and 'private fixture data' not in correction


def test_new_step_resets_active_template_but_keeps_user_choices(data):
    ledger = AssistantTasks(data.service.store)
    request = request_for(data)
    ledger.save(request, {'goal': '整理数据', 'constraints': ['保留原始数据'], 'active_operation_id': 'old',
                          'operations': {'old': {'id': 'old', 'kind': 'data', 'status': 'succeeded'}}, 'step_operation_id': 'old'})
    ledger.merge(request, AssistantIntent.model_validate(understanding(step=step(), completion_checks=['已有输出改名'])))
    value = ledger.load(request)
    assert value['active_operation_id'] is None and value['step_operation_id'] is None
    assert value['constraints'] == ['保留原始数据'] and value['operations']['old']['status'] == 'succeeded'
    ledger.merge(request, AssistantIntent.model_validate(understanding(action='answer', step=step(actions=[], source_scope='unspecified'))))
    assert ledger.load(request)['step']['actions'] == ['rename']


def test_existing_table_edit_stays_output_scoped_and_requires_overwrite_run(data):
    (data.output / 'table.csv').write_text('id,value\n1,2\n')
    request = req(data, actions=['write_text'], selection={'include': ['table.csv']})
    tools = assistant(data, Model(understanding(), []))
    args = {'source_scope': 'output', 'mode': 'inplace', 'edits': [{'action': 'write_text', 'path': 'table.csv', 'content': 'id,value\n1,3\n'}]}
    task = tools.execute('propose_data_changes', args, request)
    assert task['requires_overwrite_confirmation'] and task['can_confirm'] and task['status'] == 'pending'
    assert (data.output / 'table.csv').read_text() == 'id,value\n1,2\n'
    assert not data.service.futures


def test_pending_recipe_can_be_revised_after_refresh_across_turns(data):
    (data.source / 'alpha.nii.gz').write_bytes(b'a')
    (data.source / 'beta.nii.gz').write_bytes(b'b')
    task = data.service.organize(OrganizeRequest(**data.scope, rules=[{'pattern': '*alpha*', 'name': 'alpha'}], require_complete=True))
    ledger = AssistantTasks(data.service.store)
    request = request_for(data)
    ledger.save(request, {'active_operation_id': task['id'], 'operations': {task['id']: task}})
    asyncio.run(ledger.refresh(request, data.service))
    assert request.assistant_task['operations'][task['id']]['recipe']
    assert request.assistant_task['phase'] == 'refining'
    current = step(source_scope='dataset', mode='copy', actions=['copy'], stage='revise_pending', base_operation_id=task['id'])
    model = Model(understanding(coverage='all', step=current), [('propose_data_organization',
        {'rules': [{'pattern': '*beta*', 'name': 'beta'}]}), ('answer_without_action', {'answer': '已准备'})])
    answer = asyncio.run(assistant(data, model).reply(request_for(data)))
    latest = data.service.list(task['conversation_id'])[0]
    assert len(latest['recipe']['rules']) == 2 and latest['recipe']['base_operation_id'] == task['id']
    assert '点击运行' in answer and not list(data.output.iterdir())


def test_analysis_script_stays_pending_and_cannot_turn_into_file_modification(data):
    data.service.sandbox.image = 'sha256:' + 'a' * 64
    tools = assistant(data, Model(understanding(), []))
    request = req(data, source_scope='dataset', mode='analysis', actions=['script'], stage='new')
    with pytest.raises(StepMismatch):
        tools.execute('run_data_python', {'mode': 'copy', 'code': "print('summary')"}, request)
    task = tools.execute('run_data_python', {'mode': 'analysis', 'code': "print('summary')"}, request)
    assert task['status'] == 'pending' and task['can_run_script'] and not data.service.futures


def test_script_generated_manifest_cannot_escape_selected_edit_subset(data):
    (data.output / 'table.csv').write_text('keep')
    def generated(source, directory, code):
        directory.mkdir()
        artifacts = directory / 'artifacts'
        artifacts.mkdir()
        (artifacts / 'other.csv').write_text('unexpected')
        return artifacts
    data.service.sandbox.image = 'sha256:' + 'a' * 64
    data.service.sandbox.run = generated
    task = data.service.python(PythonRequest(**{**data.scope, 'source_scope': 'output'}, mode='inplace',
        selection={'include': ['table.csv']}, code='# Synthetic script\nprint("prepared")'))
    failed = run_script(data, task)
    assert failed['status'] == 'failed' and '超出本轮修改范围' in failed['error']
    assert not (data.output / 'other.csv').exists() and (data.output / 'table.csv').read_text() == 'keep'


def test_data_operation_cannot_handoff_to_unrequested_scientific_workflow(data):
    data.service.sandbox.image = 'sha256:' + 'a' * 64
    current = step(source_scope='dataset', mode='analysis', actions=['script'], stage='new')
    model = Model(understanding(step=current), [('continue_workflow_planning', {}),
        ('run_data_python', {'source_scope': 'dataset', 'mode': 'analysis', 'code': "print('summary')"}),
        ('answer_without_action', {'answer': '准备好了'})])
    tools = assistant(data, model)
    request = request_for(data)
    answer = asyncio.run(tools.reply(request))
    assert '点击运行' in answer and not request.planning_handoff and not data.service.futures
    assert 'continue_workflow_planning' not in {t['function']['name'] for t in tools.available_tools(request)}


def test_transform_conflicts_report_refining_and_terminal_checkpoint(data):
    (data.output / 'prefix_a.txt').write_text('a')
    (data.output / 'a.txt').write_text('existing')
    model = Model(understanding(step=step()), [('propose_filename_transform', {
        'transforms': [{'action': 'strip_prefix', 'old': 'prefix_'}]}), ('answer_without_action', {'answer': '可运行'})])
    answer = asyncio.run(assistant(data, model).reply(request_for(data)))
    task = data.service.list(data.scope['conversation_id'])[0]
    state = AssistantTasks(data.service.store).load(request_for(data))
    assert not task['can_confirm'] and '不能执行' in answer
    assert state['phase'] == 'refining' and state['query_checkpoint']['stop_reason'] == 'verification_failed'
    assert not data.service.futures and (data.output / 'prefix_a.txt').exists()


def test_compressed_wide_result_keeps_source_mode_quality_and_permissions():
    result = {**{f'field{i}': 'x' * 500 for i in range(100)}, 'source_scope': 'output', 'mode': 'inplace',
              'quality': {'complete': False, 'blocking_issues': ['conflicts']}, 'can_confirm': False,
              'requires_overwrite_confirmation': True, 'action_counts': {'rename': 205},
              'scan': {'matched_files': 205, 'unmatched_files': 0}}
    reduced = preview(result, 1800)
    assert len(json.dumps(reduced, ensure_ascii=False)) <= 1800
    for key in ('source_scope', 'mode', 'quality', 'can_confirm', 'requires_overwrite_confirmation', 'action_counts', 'scan'):
        assert reduced[key] == result[key]


def test_mismatched_parameters_get_one_semantic_recheck_and_fresh_context(data):
    (data.output / 'a.txt').write_text('a')
    model = Model(understanding(step=step()), [
        ('propose_data_changes', {'source_scope': 'dataset', 'mode': 'copy',
            'edits': [{'action': 'copy', 'path': 'a.txt', 'destination': 'b.txt'}]}),
        ('propose_data_changes', {'source_scope': 'output', 'mode': 'inplace',
            'edits': [{'action': 'rename', 'path': 'a.txt', 'destination': 'b.txt'}]}),
        ('answer_without_action', {'answer': '已准备'})])
    answer = asyncio.run(assistant(data, model).reply(request_for(data)))
    tasks = data.service.list(data.scope['conversation_id'])
    assert len(tasks) == 1 and tasks[0]['action_counts'] == {'rename': 1} and '点击运行' in answer
    feedback = [json.loads(m['content']) for m in model.messages if m['role'] == 'tool' and 'step_rechecked' in m['content']]
    assert len(feedback) == 1 and feedback[0]['step_rechecked']
    state = AssistantTasks(data.service.store).load(request_for(data))
    assert state['step_operation_id'] == tasks[0]['id'] and tasks[0]['id'] in state['operations']
    assert model.index == 2  # A verified plan needs no extra model round just to repeat its state.
    assert (data.output / 'a.txt').exists() and not (data.output / 'b.txt').exists()


def test_provenance_selection_rejects_unfinished_source_and_wrong_scope(data):
    (data.source / 'a.txt').write_text('a')
    task = data.service.organize(OrganizeRequest(**data.scope, rules=[{'pattern': '*.txt', 'kind': 'file', 'name': 'a'}]))
    request = req(data, selection={'from_operation_id': task['id'], 'category': 'preserved'})
    tools = assistant(data, Model(understanding(), []))
    with pytest.raises(ValueError, match='已完成'):
        tools.execute('propose_filename_transform', {'transforms': [{'action': 'replace', 'old': 'a', 'new': 'b'}]}, request)
    assert len(data.service.list(task['conversation_id'])) == 1


def test_literal_transform_preserves_extensions_and_stays_pending(data):
    (data.output / 'prefix_img_noise.nii.gz').write_bytes(b'image')
    (data.output / 'prefix_img_noise.json').write_text('{}')
    tools = assistant(data, Model(understanding(), []))
    task = tools.execute('propose_filename_transform', {'transforms': [
        {'action': 'strip_prefix', 'old': 'prefix_'}, {'action': 'strip_suffix', 'old': '_noise'},
        {'action': 'replace', 'old': 'img', 'new': 'image'}]}, req(data))
    assert {r['destination'] for r in task['items']} == {'image.nii.gz', 'image.json'}
    assert task['status'] == 'pending' and not data.service.futures


def test_no_change_transform_is_verified_noop_and_invalid_names_create_no_task(data):
    (data.output / 'a.txt').write_text('a')
    tools = assistant(data, Model(understanding(), []))
    task = tools.execute('propose_filename_transform', {'transforms': [{'action': 'replace', 'old': 'absent', 'new': 'b'}]}, req(data))
    assert task['status'] == 'succeeded' and task['completed'] == 0 and task['file_count'] == 0
    assert not tools.writes_started([{'tool': 'propose_filename_transform', 'result': task}])
    with pytest.raises(ValueError, match='无效文件名'):
        tools.execute('propose_filename_transform', {'transforms': [{'action': 'replace', 'old': 'a', 'new': '../bad'}]}, req(data))
    assert len(data.service.list(data.scope['conversation_id'])) == 1 and not data.service.futures


@pytest.mark.parametrize('path', ['../outside', '/absolute', 'C:/absolute'])
def test_selection_cannot_escape_registered_directory(path):
    with pytest.raises(ValidationError):
        DataSelection(path=path)
