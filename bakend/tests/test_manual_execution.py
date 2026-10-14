"""Model proposals never execute; only exact, explicit UI launch requests do."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.assistant_tasks import completion_problem
from app.data_auth import Principal, principal
from app.data_models import DataEdit, DataPlanRequest, PythonRequest
from app.data_operations import DataOperations
from app.data_routes import data_router
from app.models import ChatRequest
from app.response_policy import ResponsePolicy
from app.workspace_tools import WorkspaceTools
from test_data_operations import data, approval, finished, run_script, script_approval


@pytest.mark.parametrize('mode', ['analysis', 'copy', 'inplace'])
@pytest.mark.parametrize('background', [True, False])
@pytest.mark.parametrize('execution', ['preview', 'output'])
def test_every_python_submission_waits_for_run_even_for_read_only_analysis(data, mode, background, execution):
    data.service.sandbox.image = 'sha256:' + 'a' * 64
    calls = []
    def sandbox(source, directory, code):
        calls.append(code)
        artifacts = directory / 'artifacts'; artifacts.mkdir(parents=True)
        (artifacts / 'result.txt').write_text('result')
        return artifacts
    data.service.sandbox.run = sandbox
    task = data.service.python(PythonRequest(**data.scope, mode=mode, background=background, execution=execution, code='synthetic'))
    assert task['status'] == 'pending' and task['phase'] == 'script' and task['can_run_script']
    assert not task['can_confirm'] and not task['manifest_ready'] and task['execution_policy'] == 'manual'
    assert not task['requires_inplace_confirmation'] and not calls and not data.service.futures
    with pytest.raises(PermissionError, match='脚本尚未运行'):
        data.service.confirm(task['id'], approval(task, inplace=True))
    done = run_script(data, task)
    assert calls == ['synthetic'] and not list(data.output.iterdir())
    assert not list(data.source.iterdir())
    assert done['status'] == ('analyzed' if mode == 'analysis' else 'pending')
    assert not done['can_run_script'] and done['can_confirm'] == (mode != 'analysis')
    data.service.start_python(task['id'], script_approval(task))
    assert calls == ['synthetic']  # A duplicate or stale UI click cannot repeat a script.
    if mode != 'analysis':
        data.service.confirm(done['id'], approval(done, inplace=mode == 'inplace'))
        assert finished(data, done)['status'] == 'succeeded'
        assert ((data.source if mode == 'inplace' else data.output) / 'result.txt').read_text() == 'result'


@pytest.mark.parametrize('change', ['hash', 'conversation', 'actor', 'expiry', 'code', 'resource', 'root', 'environment'])
def test_script_launch_rejects_changed_or_unapproved_scope(data, change):
    data.service.sandbox.image = 'sha256:' + 'a' * 64
    task = data.service.python(PythonRequest(**data.scope, mode='copy', code='synthetic'))
    accepted = script_approval(task)
    token = None
    if change == 'hash':
        accepted = accepted.model_copy(update={'script_plan_hash': 'wrong'})
    elif change == 'conversation':
        accepted = accepted.model_copy(update={'conversation_id': 'other'})
    elif change == 'actor':
        token = principal.set(Principal('other', True))
    elif change == 'root':
        data.output.rename(data.output.with_name('old-output')); data.output.mkdir()
    elif change == 'environment':
        data.service.sandbox.image = 'sha256:' + 'b' * 64
    else:
        raw = data.service._raw(task['id'])
        if change == 'expiry': raw['expires_at'] = 0
        if change == 'code': raw['_python_request']['code'] = 'changed'
        if change == 'resource': raw['sandbox_resources']['memory_mb'] *= 2
        data.service._save(raw)
    try:
        with pytest.raises((PermissionError, ValueError)):
            data.service.start_python(task['id'], accepted)
        assert task['id'] not in data.service.futures
        assert not list(data.output.iterdir())
    finally:
        if token is not None: principal.reset(token)


def test_pending_scripts_survive_service_restart_and_cancellation_without_execution(data):
    data.service.sandbox.image = 'sha256:' + 'a' * 64
    task = data.service.python(PythonRequest(**data.scope, mode='analysis', code='print(1)'))
    data.service.shutdown()
    data.service = DataOperations(data.workspace, data.service.datasets)
    assert data.service.get(task['id'], task['conversation_id'])['status'] == 'pending'
    assert not data.service.futures
    cancelled = data.service.cancel(task['id'], task['conversation_id'])
    assert cancelled['status'] == 'cancelled' and not cancelled['can_run_script']
    assert data.service.start_python(task['id'], script_approval(task))['status'] == 'cancelled'
    assert not data.service.futures


def test_http_prepare_and_script_run_are_separate_and_confirmation_is_required(data):
    data.service.sandbox.image = 'sha256:' + 'a' * 64
    calls = []
    def sandbox(source, directory, code):
        calls.append(code)
        artifacts = directory / 'artifacts'; artifacts.mkdir(parents=True)
        return artifacts
    data.service.sandbox.run = sandbox
    app = FastAPI(); app.include_router(data_router(data.service))
    client = TestClient(app)
    prepared = client.post('/api/data-operations/python', json=dict(data.scope, mode='analysis', execution='output', background=True, code='print(1)'))
    assert prepared.status_code == 200
    task = prepared.json()
    assert task['status'] == 'pending' and not calls
    path = '/api/data-operations/' + task['id'] + '/run-script'
    for body in [{}, {**script_approval(task).model_dump(), 'confirmed': False},
                 {**script_approval(task).model_dump(), 'script_plan_hash': 'wrong'},
                 {**script_approval(task).model_dump(), 'code': 'injected'}]:
        assert client.post(path, json=body).status_code in {403, 422}
        assert not calls and not data.service.futures
    assert client.post(path, json=script_approval(task).model_dump()).status_code == 200
    assert finished(data, task)['status'] == 'analyzed' and calls == ['print(1)']


@pytest.mark.parametrize('action,args,target', [
    ('create_output_directory', {'path': 'new-dir'}, 'new-dir'),
    ('copy_output_path', {'path': 'a.txt', 'destination': 'b.txt', 'preview_only': False}, 'b.txt'),
    ('move_output_path', {'path': 'a.txt', 'destination': 'b.txt'}, 'b.txt'),
    ('rename_output_path', {'path': 'a.txt', 'destination': 'b.txt'}, 'b.txt'),
])
def test_model_output_file_and_directory_tools_only_prepare(data, action, args, target):
    (data.output / 'a.txt').write_text('original')
    tools = WorkspaceTools(data.workspace, object()); tools.data_operations = data.service
    query = ChatRequest(**data.scope, message='立即执行，重新执行', interaction_mode='task', response_policy=ResponsePolicy(action='operate'))
    task = tools.execute(action, args, query)
    assert task['status'] == 'pending' and not (data.output / target).exists()
    assert (data.output / 'a.txt').read_text() == 'original' and not data.service.futures


def test_manual_launch_is_not_an_assistant_tool_and_ready_proposal_finishes_turn(data):
    tools = WorkspaceTools(data.workspace, object()); tools.data_operations = data.service
    query = ChatRequest(**data.scope, message='执行', interaction_mode='task', response_policy=ResponsePolicy(action='operate'), assistant_task={'outcome': 'execute'})
    names = {t['function']['name'] for t in tools.available_tools(query)}
    assert not {'start_python', 'run_script', 'confirm_data_operation', 'confirm_output_operation'} & names
    task = data.service.propose(DataPlanRequest(**data.scope, mode='copy', execution='output', edits=[DataEdit(action='write_text', path='result.txt', content='new')]))
    assert completion_problem(query, [{'tool': 'propose_data_changes', 'result': task}]) is None
    assert not list(data.output.iterdir())
    query.response_policy = ResponsePolicy(action='inspect')
    names = {t['function']['name'] for t in tools.available_tools(query)}
    assert 'inventory_data_files' in names and 'run_data_python' not in names
    assert 'create_output_directory' not in names
