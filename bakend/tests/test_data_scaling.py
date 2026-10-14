"""Background jobs, audited environments and bounded copy/storage regressions."""
import json
import asyncio
import time
from threading import Barrier, Event, Lock

import pytest

from app.data_auth import principal
from app.data_environments import DataEnvironments
from app.data_models import DataEdit, DataPlanRequest, PythonRequest
from app.data_operation_store import DataOperationStore
from app.data_sandbox import DataSandbox, SandboxCancelled, configuration
from app import workspace_io as io
from app.models import ChatRequest
from app.response_policy import ResponsePolicy
from app.workspace_tools import WorkspaceTools
from test_data_operations import data, approval, finished, script_approval


IMAGE = 'sha256:' + 'a' * 64


def wait_final(data, task):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        data.service.futures[task['id']].result(timeout=15)
        result = data.service.get(task['id'], task['conversation_id'])
        if result['status'] not in {'queued', 'analyzing', 'running', 'cancel_requested'}:
            return result
        time.sleep(.01)
    raise AssertionError('Job did not finish')


def request(data, **extra):
    return PythonRequest(**dict(data.scope, mode='copy', code='print(1)', background=True, **extra))


def test_background_script_returns_before_completion_and_preserves_exact_overwrite_approval(data):
    started, release = Event(), Event()
    data.service.sandbox.image = IMAGE
    actor = principal.get()
    (data.output / 'table.csv').write_text('old')
    def sandbox(source, directory, code):
        assert principal.get() == actor
        assert configuration.get()['image'] == IMAGE
        started.set(); assert release.wait(10)
        artifacts = directory / 'artifacts'; artifacts.mkdir(parents=True)
        (artifacts / 'table.csv').write_text('new')
        return artifacts
    data.service.sandbox.run = sandbox
    task = data.service.python(request(data, execution='output'))
    try:
        assert task['status'] == 'pending' and task['can_run_script'] and not started.is_set()
        task = data.service.start_python(task['id'], script_approval(task))
        assert task['status'] in {'queued', 'analyzing'} and not task['can_confirm']
        assert started.wait(5)
        assert (data.output / 'table.csv').read_text() == 'old'
        assert data.service.progress(task['id'], task['conversation_id'])['status'] == 'analyzing'
    finally:
        release.set()
    done = wait_final(data, task)
    assert done['status'] == 'pending' and done['requires_overwrite_confirmation']
    assert done['file_count'] == 1 and done['sandbox_resources']['timeout_seconds'] == 60
    assert (data.output / 'table.csv').read_text() == 'old'
    with pytest.raises(PermissionError):
        data.service.confirm(task['id'], approval(done))
    accepted = approval(done).model_copy(update={'allow_overwrite': True})
    data.service.confirm(task['id'], accepted)
    assert finished(data, done)['status'] == 'succeeded'
    data.service.undo(task['id'], accepted)
    assert finished(data, done)['status'] == 'rolled_back'
    assert (data.output / 'table.csv').read_text() == 'old'


def test_running_script_cancellation_does_not_publish_artifacts(data):
    started = Event()
    data.service.sandbox.image = IMAGE
    def sandbox(source, directory, code):
        event = configuration.get()['cancel_event']
        started.set()
        assert event.wait(10)
        raise SandboxCancelled()
    data.service.sandbox.run = sandbox
    task = data.service.python(request(data, execution='output'))
    data.service.start_python(task['id'], script_approval(task))
    assert started.wait(5)
    cancel = data.service.cancel(task['id'], task['conversation_id'])
    assert cancel['status'] == 'cancel_requested'
    done = wait_final(data, task)
    assert done['status'] == 'cancelled' and not done['can_undo']
    assert not list(data.output.iterdir())


@pytest.mark.parametrize('ending', ['answer_without_action', 'continue_workflow_planning', 'content'])
def test_model_background_submission_is_reported_without_claiming_completion_or_planning(data, ending):
    started, release = Event(), Event()
    data.service.sandbox.image = IMAGE
    def sandbox(source, directory, code):
        started.set(); assert release.wait(10)
        artifacts = directory / 'artifacts'; artifacts.mkdir(parents=True)
        return artifacts
    data.service.sandbox.run = sandbox
    class Model:
        mode = 'model'
        calls = 0
        async def workspace_completion(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                name, arguments = 'run_data_python', {'mode': 'analysis', 'code': 'print(1)'}
            elif ending == 'content':
                return {'role': 'assistant', 'content': '已经完成分析'}
            else:
                name, arguments = ending, {'answer': '已经完成分析'} if ending == 'answer_without_action' else {}
            return {'role': 'assistant', 'tool_calls': [{'id': str(self.calls), 'type': 'function',
                    'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
    tools = WorkspaceTools(data.workspace, Model()); tools.data_operations = data.service
    query = ChatRequest(message='分析数据', conversation_id=data.scope['conversation_id'], dataset_id='ds_test',
                        workspace_id=data.ws.id, interaction_mode='task', response_policy=ResponsePolicy(action='operate'))
    try:
        answer = asyncio.run(tools.reply(query))
        assert '点击运行' in answer and '已经完成' not in answer and not query.planning_handoff
        events = data.workspace.events(query.client_turn_id)
        event = next(e for e in events if e.get('data_operation'))
        assert event['data_operation']['status'] == 'pending'
        assert not started.is_set() and event['data_operation']['id'] not in data.service.futures
    finally:
        release.set()
    task = event['data_operation']
    data.service.start_python(task['id'], script_approval(task))
    assert wait_final(data, task)['status'] == 'analyzed'


def test_failed_script_retains_log_and_cannot_approve_partial_outputs(data):
    data.service.sandbox.image = IMAGE
    def sandbox(source, directory, code):
        directory.mkdir(parents=True)
        (directory / 'execution.log').write_text('synthetic failure')
        raise ValueError('script failed')
    data.service.sandbox.run = sandbox
    task = data.service.python(request(data, execution='output'))
    data.service.start_python(task['id'], script_approval(task))
    result = wait_final(data, task)
    assert result['status'] == 'failed' and result['execution_log'] == 'synthetic failure'
    assert not result['can_confirm'] and result['file_count'] == 0


def test_library_environment_is_fixed_and_resource_context_does_not_leak(data, monkeypatch):
    monkeypatch.delenv('NEUROWEAVE_DATA_PYTHON_ENVIRONMENTS_FILE', raising=False)
    monkeypatch.setenv('NEUROWEAVE_DATA_PYTHON_ENVIRONMENTS', json.dumps({'table': {'image': IMAGE, 'packages': {'pandas': '3.0.6'}}}))
    data.service.sandbox = DataSandbox()
    def sandbox(source, directory, code):
        selected = configuration.get()
        assert selected['environment'] == 'table' and selected['resources']['timeout_seconds'] == 900
        assert selected['resources']['memory_mb'] == 4096
        artifacts = directory / 'artifacts'; artifacts.mkdir(parents=True)
        return artifacts
    data.service.sandbox.run = sandbox
    task = data.service.python(request(data, environment='table', resource_profile='extended'))
    data.service.start_python(task['id'], script_approval(task))
    result = wait_final(data, task)
    assert result['status'] == 'analyzed' and result['sandbox_packages'] == {'pandas': '3.0.6'}
    assert configuration.get() is None
    with pytest.raises(ValueError, match='未启用'):
        data.service.python(request(data, environment='imaging'))
    monkeypatch.setenv('NEUROWEAVE_DATA_PYTHON_ENVIRONMENTS', '{"table":{"image":"python:latest"}}')
    with pytest.raises(ValueError, match='固定镜像'):
        DataEnvironments()


def test_bounded_parallel_copy_uses_compact_headers_and_recovers_all_files(data, monkeypatch):
    original, active, peak = io.copy, 0, 0
    mutex, barrier = Lock(), Barrier(2)
    first = 0
    def copy(source, target, policy):
        nonlocal active, peak, first
        with mutex:
            active += 1; peak = max(peak, active); first += 1; number = first
        try:
            if number <= 2:
                barrier.wait(timeout=10)
            return original(source, target, policy)
        finally:
            with mutex:
                active -= 1
    monkeypatch.setattr(io, 'copy', copy)
    for index in range(12):
        (data.source / f'{index}.bin').write_bytes(bytes([index]) * 8192)
    task = data.service.propose(DataPlanRequest(**data.scope, mode='copy', execution='output',
        edits=[DataEdit(action='copy', path=f'{i}.bin', destination=f'sub/{i}.bin') for i in range(12)]))
    assert task['status'] == 'pending' and peak == 0
    data.service.confirm(task['id'], approval(task))
    done = finished(data, task)
    assert done['status'] == 'succeeded', done
    assert peak == 2 and done['completed'] == 12 and done['processed_bytes'] == 12 * 8192
    with data.service.store.connection() as db:
        payload = db.execute('SELECT payload FROM data_operations WHERE id=?', (task['id'],)).fetchone()[0]
        assert len(payload) < 3000 and 'manifest' not in json.loads(payload)
        assert db.execute('SELECT count(*) FROM data_operation_journal WHERE operation_id=?', (task['id'],)).fetchone()[0] == 12
    progress = data.service.progress(task['id'], task['conversation_id'])
    assert 'items' not in progress and 'script' not in progress
    data.service.undo(task['id'], approval(done))
    assert finished(data, done)['status'] == 'rolled_back'
    assert not (data.output / 'sub').exists()
    assert len(list(data.source.iterdir())) == 12


def test_cancellation_interrupts_copy_before_publish_and_allows_cleanup(data, monkeypatch):
    started, release = Event(), Event()
    original = io.copy
    def copy(source, target, policy):
        io.copy_observer.get()(1024 * 1024)
        started.set(); assert release.wait(10)
        return original(source, target, policy)
    monkeypatch.setattr(io, 'copy', copy)
    (data.source / 'big.bin').write_bytes(b'x' * (2 * 1024 * 1024))
    task = data.service.propose(DataPlanRequest(**data.scope, mode='copy', execution='output',
        edits=[DataEdit(action='copy', path='big.bin', destination='sub/big.bin')]))
    data.service.confirm(task['id'], approval(task))
    try:
        assert started.wait(5)
        progress = data.service.progress(task['id'], task['conversation_id'])
        assert progress['processed_bytes'] == 1024 * 1024
        data.service.cancel(task['id'], task['conversation_id'])
    finally:
        release.set()
    done = finished(data, task)
    assert done['status'] == 'cancelled' and done['completed'] == 0 and done['can_undo']
    assert not (data.output / 'sub/big.bin').exists()
    monkeypatch.setattr(io, 'copy', original)
    data.service.undo(task['id'], approval(done))
    assert finished(data, done)['status'] == 'rolled_back'
    assert not (data.output / 'sub').exists()


def test_legacy_failed_payload_is_untouched_by_startup_reads(data):
    task = data.service.propose(DataPlanRequest(**data.scope, mode='copy',
        edits=[DataEdit(action='write_text', path='report.json', content='{}')]))
    raw = data.service._raw(task['id'])
    raw = {k: v for k, v in raw.items() if not k.startswith('_')}
    raw['status'] = 'failed'
    payload = json.dumps(raw, ensure_ascii=False, indent=2)
    with data.service.store.transaction() as db:
        db.execute('UPDATE data_operations SET status=?,payload=? WHERE id=?', ('failed', payload, task['id']))
    repo = DataOperationStore(data.service.store)
    assert repo.interrupted() == []
    assert repo.load(task['id'], 'preview')['status'] == 'failed'
    with data.service.store.connection() as db:
        assert db.execute('SELECT payload FROM data_operations WHERE id=?', (task['id'],)).fetchone()[0] == payload
