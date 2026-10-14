"""Sandbox computed plans still need a distinct, exact file-change approval."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.data_auth import Principal, principal
from app.data_models import DataConfirmation, PythonRequest
from app.data_routes import data_router
from test_data_operations import data, finished, run_script, script_approval


def prepared(data, edits, *, mode='inplace', actions=None, selection=None, log='computed', after=None):
    def compute(source, directory, code):
        directory.mkdir(parents=True, exist_ok=True)
        artifacts = directory / 'artifacts'
        artifacts.mkdir()
        (artifacts / 'changes.json').write_text(json.dumps({'edits': edits}), encoding='utf8')
        (directory / 'execution.log').write_text(log, encoding='utf8', newline='\n')
        if after:
            after()
        return artifacts
    data.service.sandbox.image = 'sha256:' + 'a' * 64
    data.service.sandbox.run = compute
    return data.service.python(PythonRequest(**{**data.scope, 'source_scope': 'output'},
        mode=mode, result_kind='changes', change_actions=actions or ['rename'],
        selection=selection or {}, code="# Compute changes only\nprint('prepared')"))


def confirm(task, **grants):
    return DataConfirmation(confirmed=True, conversation_id=task['conversation_id'],
                            manifest_hash=task['manifest_hash'], **grants)


def test_script_to_manifest_to_execution_keeps_one_card_and_two_manual_approvals(data):
    (data.output / 'old.txt').write_text('keep')
    proposed = prepared(data, [{'action': 'rename', 'path': 'old.txt', 'destination': 'new.txt'}])
    assert proposed['can_run_script'] and not proposed['can_confirm'] and not data.service.futures
    with pytest.raises(PermissionError):
        data.service.confirm(proposed['id'], confirm(proposed))
    preview = run_script(data, proposed)
    assert preview['id'] == proposed['id'] and preview['phase'] == 'files' and preview['can_confirm']
    assert preview['action_counts'] == {'rename': 1} and not preview['can_run_script']
    assert preview['manifest_hash'] != proposed['manifest_hash'] and preview['script_plan_hash'] == proposed['script_plan_hash']
    assert (data.output / 'old.txt').exists() and not (data.output / 'new.txt').exists()
    assert 'grant' not in data.service._raw(preview['id'])
    # Repeating the first approval neither reruns the script nor applies changes.
    assert data.service.start_python(preview['id'], script_approval(proposed))['phase'] == 'files'
    with pytest.raises(PermissionError):
        data.service.confirm(preview['id'], confirm(proposed))
    data.service.confirm(preview['id'], confirm(preview))
    assert finished(data, preview)['status'] == 'succeeded'
    assert (data.output / 'new.txt').read_text() == 'keep' and not (data.output / 'old.txt').exists()
    data.service.undo(preview['id'], confirm(preview))
    assert finished(data, preview)['status'] == 'rolled_back' and (data.output / 'old.txt').exists()


@pytest.mark.parametrize('edit', [
    {'action': 'delete', 'path': 'allowed.txt'},
    {'action': 'rename', 'path': 'private.txt', 'destination': 'new.txt'},
    {'action': 'rename', 'path': '/input/allowed.txt', 'destination': 'new.txt'},
    {'action': 'rename', 'path': 'allowed.txt', 'destination': '../escape.txt'},
    {'action': 'rename', 'path': 'allowed.txt', 'destination': 'C:\\escape.txt'},
])
def test_computed_edits_cannot_expand_declared_actions_selection_or_roots(data, edit):
    (data.output / 'allowed.txt').write_text('a')
    (data.output / 'private.txt').write_text('private')
    preview = run_script(data, prepared(data, [edit], selection={'include': ['allowed.txt']}))
    assert preview['status'] == 'failed' and not preview['can_confirm']
    assert (data.output / 'allowed.txt').read_text() == 'a' and (data.output / 'private.txt').read_text() == 'private'


def test_computed_changes_keep_conflicts_and_delete_overwrite_confirmation(data):
    (data.output / 'a.txt').write_text('original')
    (data.output / 'b.txt').write_text('other')
    conflict = run_script(data, prepared(data, [{'action': 'rename', 'path': 'a.txt', 'destination': 'b.txt'}]))
    assert conflict['conflicts'] and not conflict['can_confirm']
    data.service.cancel(conflict['id'], conflict['conversation_id'])
    task = prepared(data, [{'action': 'write_text', 'path': 'a.txt', 'content': 'changed'},
                           {'action': 'delete', 'path': 'b.txt'}], actions=['write_text', 'delete'])
    preview = run_script(data, task)
    assert preview['requires_overwrite_confirmation'] and preview['requires_delete_confirmation']
    for grants in [{}, {'allow_delete': True}, {'allow_overwrite': True}]:
        with pytest.raises(PermissionError):
            data.service.confirm(preview['id'], confirm(preview, **grants))
    assert (data.output / 'a.txt').read_text() == 'original' and (data.output / 'b.txt').exists()
    data.service.confirm(preview['id'], confirm(preview, allow_delete=True, allow_overwrite=True))
    assert finished(data, preview)['status'] == 'succeeded'
    assert (data.output / 'a.txt').read_text() == 'changed' and not (data.output / 'b.txt').exists()


def test_computed_plan_is_rejected_when_input_changes_during_analysis(data):
    (data.output / 'a.txt').write_text('a')
    task = prepared(data, [{'action': 'rename', 'path': 'a.txt', 'destination': 'b.txt'}],
                    after=lambda: (data.output / 'a.txt').write_text('external change'))
    result = run_script(data, task)
    assert result['status'] == 'failed' and '输入清单变化' in result['error']
    assert not result['can_confirm'] and not (data.output / 'b.txt').exists()


def test_full_script_result_is_downloadable_and_never_an_execution_grant(data):
    (data.output / 'a.txt').write_text('a')
    full = '完整分析结果\n' * 15000
    result = run_script(data, prepared(data, [], log=full))
    assert result['status'] == 'analyzed' and not result['can_confirm']
    assert result['execution_log_truncated'] and result['execution_log_available']
    assert result['execution_log_bytes'] == len(full.encode('utf8'))
    app = FastAPI()
    app.include_router(data_router(data.service))
    client = TestClient(app)
    response = client.get('/api/data-operations/' + result['id'] + '/log', params={'conversation_id': result['conversation_id']})
    assert response.status_code == 200 and response.content == full.encode('utf8')
    token = principal.set(Principal('other', True))
    try:
        with pytest.raises(PermissionError):
            data.service.get(result['id'], result['conversation_id'])
    finally:
        principal.reset(token)


def test_generated_manifests_can_exceed_model_argument_limit_without_truncation(data):
    (data.output / 'source.txt').write_text('a')
    edits = [{'action': 'copy', 'path': 'source.txt', 'destination': f'result-{i}.txt'} for i in range(10001)]
    result = run_script(data, prepared(data, edits, mode='copy', actions=['copy']))
    assert result['file_count'] == 10001 and result['can_confirm'] and result['preview_truncated']
    assert len(data.service.repository.items(result['id'])) == 10001
    assert not (data.output / 'result-10000.txt').exists()


@pytest.mark.parametrize('updates', [
    {'mode': 'analysis', 'change_actions': ['rename']},
    {'mode': 'inplace', 'change_actions': []},
    {'mode': 'copy', 'change_actions': ['delete']},
])
def test_generated_manifest_request_cannot_hide_effects_in_analysis_or_copy(updates):
    with pytest.raises(ValidationError):
        PythonRequest(conversation_id='c', result_kind='changes', code='print(1)', **updates)


def test_legacy_pending_script_hash_and_scope_remain_usable_with_new_request_defaults(data):
    data.service.sandbox.image = 'sha256:' + 'a' * 64
    def analyze(source, directory, code):
        directory.mkdir(parents=True, exist_ok=True)
        artifacts = directory / 'artifacts'; artifacts.mkdir()
        (directory / 'execution.log').write_text('legacy result', encoding='utf8')
        return artifacts
    data.service.sandbox.run = analyze
    task = data.service.python(PythonRequest(**data.scope, code="print('legacy result')"))
    original = data.service._raw(task['id'])
    original['_python_request'].pop('result_kind')
    original['_python_request'].pop('change_actions')
    original.pop('script_result_kind')
    original['script_plan_hash'] = data.service._script_plan_hash(original)
    data.service._save(original)
    result = run_script(data, data.service.get(task['id'], task['conversation_id']))
    assert result['status'] == 'analyzed' and result['execution_log'] == 'legacy result'
