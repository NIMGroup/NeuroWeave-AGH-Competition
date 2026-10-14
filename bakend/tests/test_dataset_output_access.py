import asyncio
import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dataset_service import DatasetService
from app.dataset_files import DatasetFiles
from app.local_store import LocalStore
from app.models import ChatRequest
from app.workspace_models import WorkspaceCreate, FileOperationRequest, DirectoryCreationRequest
from app.workspace_routes import workspace_router
from app.workspace_service import WorkspaceService
from app.workspace_tools import WorkspaceTools


@pytest.fixture
def scopes(tmp_path, monkeypatch):
    data = tmp_path / 'inputs' / 'study'
    output = tmp_path / 'results'
    data.mkdir(parents=True); output.mkdir()
    (data / 'sub-01').mkdir()
    (data / 'notes.txt').write_text('dataset marker 1234567890')
    (output / 'notes.txt').write_text('output marker')
    monkeypatch.setenv('LOCALFLOW_DATA_ROOTS', str(data.parent))
    monkeypatch.setenv('WORKSPACE_ALLOWED_ROOTS', str(output))
    monkeypatch.setenv('WORKSPACE_DEFAULT_ROOT', str(output))
    monkeypatch.setenv('WORKSPACE_TEXT_MAX_CHARS', '12')
    store = LocalStore(tmp_path / 'state' / 'state.db')
    datasets = DatasetService(tmp_path, store)
    dataset = datasets.register(datasets.roots()[0].root_id, 'study')
    fs = WorkspaceService(store, output)
    ws = fs.create(WorkspaceCreate(name='Results', root_path=str(output)))
    return fs, DatasetFiles(datasets), dataset, ws, data, output


def test_dataset_reads_need_no_output_selection_and_have_separate_roots(scopes):
    fs, files, dataset, ws, data, output = scopes
    tools = WorkspaceTools(fs, object(), files)
    request = ChatRequest(message='检查数据集', dataset_id=dataset.dataset_id, interaction_mode='task')
    names = [t['function']['name'] for t in tools.available_tools(request)]
    assert 'list_dataset_directory' in names
    assert not any('_output_' in name or name.startswith('delete_') for name in names)
    result = tools.execute('list_dataset_directory', {}, request)
    assert result['root_path'] == str(data)
    assert all(item['writable'] is False for item in result['items'])
    assert tools.execute('read_dataset_text', {'path': 'notes.txt'}, request)['text'] == 'dataset mark'
    request.workspace_id = ws.id
    assert tools.execute('read_output_text', {'path': 'notes.txt'}, request)['text'] == 'output marke'
    for arguments in ({'path': '../results'}, {'path': str(output)}, {'path': '.', 'root_path': str(output)}):
        with pytest.raises((ValueError, PermissionError)):
            tools.execute('list_dataset_directory', arguments, request)
    with pytest.raises(ValueError):
        tools.execute('delete_dataset_path', {'path': 'notes.txt'}, request)
    request.interaction_mode = 'chat'
    with pytest.raises(PermissionError):
        tools.execute('delete_output_path', {'path': 'notes.txt'}, request)


def test_natural_language_dataset_tool_routing_without_workspace(scopes):
    fs, files, dataset, ws, data, output = scopes
    class Model:
        mode = 'model'
        calls = 0
        async def workspace_completion(self, messages, tools, required=False):
            self.calls += 1
            if self.calls == 1:
                assert required
                assert '数据集查询工具只读' in messages[0]['content']
                return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'data1', 'type': 'function',
                    'function': {'name': 'list_dataset_directory', 'arguments': '{"path":"."}'}}]}
            assert json.loads(messages[-1]['content'])['root_path'] == str(data)
            return {'role': 'assistant', 'content': '已通过目录工具找到 sub-01 和 notes.txt。'}
    request = ChatRequest(message='当前数据集有哪些文件？', dataset_id=dataset.dataset_id)
    answer = asyncio.run(WorkspaceTools(fs, Model(), files).reply(request))
    assert 'sub-01' in answer
    assert fs.events(request.client_turn_id)[-1]['tool'] == 'list_dataset_directory'


def test_dataset_http_read_limits_and_no_write_endpoint(scopes):
    fs, files, dataset, ws, data, output = scopes
    (data / 'image.nii').write_text('not text despite its bytes')
    (data / '.env').write_text('synthetic secret')
    app = FastAPI(); app.include_router(workspace_router(fs, files))
    with TestClient(app) as client:
        base = f'/api/datasets/{dataset.dataset_id}/files'
        listed = client.get(base + '/list', params={'limit': 1}).json()
        assert len(listed['items']) == 1 and listed['has_more']
        assert client.get(base + '/list', params={'path': '..'}).status_code == 403
        text = client.get(base + '/read-text', params={'path': 'notes.txt', 'max_chars': 999}).json()
        assert len(text['text']) == 12 and text['truncated']
        assert client.get(base + '/read-text', params={'path': 'image.nii'}).status_code == 409
        assert client.get(base + '/read-text', params={'path': '.env'}).status_code == 403
        assert client.post(base + '/delete', json={'path': 'notes.txt', 'confirmed': True}).status_code in {404, 405}
        assert client.get('/api/output-directories').json()[0]['manageable']
        assert client.post('/api/fs/delete', json={'workspace_id': ws.id, 'dataset_id': dataset.dataset_id, 'path': 'notes.txt'}).status_code == 422


def test_output_scope_cannot_bypass_dataset_readonly_even_with_legacy_record(scopes, tmp_path):
    fs, files, dataset, ws, data, output = scopes
    fs.paths.roots = (tmp_path,)
    for path in (data, data.parent, data / 'sub-01'):
        with pytest.raises(PermissionError, match='只读保护'):
            fs.create(WorkspaceCreate(name='invalid output', root_path=str(path)))
    legacy = ws.model_copy(update={'id': 'legacy', 'root_path': str(data)})
    fs._save(legacy)
    assert not fs.get(legacy.id).manageable
    for action, destination in [('delete', None), ('rename', 'renamed.txt'), ('mkdir', None), ('copy', 'copy.txt')]:
        with pytest.raises(PermissionError):
            fs.propose(action, FileOperationRequest(workspace_id=legacy.id, path='notes.txt', destination=destination))
    assert (data / 'notes.txt').exists()


def test_output_registration_requires_os_write_and_search_permissions(scopes, monkeypatch):
    fs, files, dataset, ws, data, output = scopes
    original = os.access
    monkeypatch.setattr(os, 'access', lambda path, mode: False if Path(path) == output and mode & os.W_OK else original(path, mode))
    assert not fs.get(ws.id).manageable
    with pytest.raises(PermissionError, match='权限'):
        fs.create(WorkspaceCreate(name='denied', root_path=str(output)))


def test_mkdir_in_output_chooser_requires_confirmation_without_registering_parent(scopes, tmp_path):
    from app.conversation_models import ConversationRecord
    fs, files, dataset, ws, data, output = scopes
    fs.paths.roots = (tmp_path,)
    fs.store.create_conversation(ConversationRecord(conversation_id='chooser', workspace_id=ws.id,
        title='Existing output binding', created_at='2026-09-17', updated_at='2026-09-17'))
    app = FastAPI(); app.include_router(workspace_router(fs, files))
    with TestClient(app) as client:
        body = {'root_path': str(tmp_path), 'path': 'new-output', 'conversation_id': 'chooser'}
        proposed = client.post('/api/fs/mkdir', json=body)
        assert proposed.status_code == 200
        operation = proposed.json()
        assert operation['status'] == 'pending'
        assert not (tmp_path / 'new-output').exists()
        url = '/api/fs/operations/' + operation['id'] + '/confirm'
        assert client.post(url, json={'confirmed': False}).status_code == 422
        assert client.post(url, json={'confirmed': True}).status_code == 403
        completed = client.post(url, json={'confirmed': True, 'conversation_id': 'chooser'}).json()
        assert completed['status'] == 'succeeded'
        assert (tmp_path / 'new-output').is_dir()
        assert len(fs.list()) == 1  # Creating never silently registers a parent.
        assert fs.store.get_conversation('chooser').workspace_id == ws.id
        registered = client.post('/api/output-directories', json={
            'root_path': str(tmp_path / 'new-output'), 'name': 'New output'})
        assert registered.status_code == 200 and registered.json()['manageable']
        assert client.post(url, json={'confirmed': True, 'conversation_id': 'chooser'}).status_code == 409
        assert client.post('/api/fs/mkdir', json=body).status_code == 409
        assert (data / 'notes.txt').read_text().startswith('dataset marker')


def test_output_chooser_mkdir_keeps_dataset_and_path_boundaries(scopes, tmp_path):
    fs, files, dataset, ws, data, output = scopes
    app = FastAPI(); app.include_router(workspace_router(fs, files))
    with TestClient(app) as client:
        assert client.post('/api/fs/mkdir', json={'root_path': str(tmp_path), 'path': 'outside'}).status_code == 403
        fs.paths.roots = (tmp_path,)
        for body, status in [
            ({'root_path': str(data), 'path': 'forbidden'}, 403),
            ({'root_path': str(output), 'path': '../escape'}, 403),
            ({'root_path': str(output), 'path': str(data / 'forbidden')}, 403),
            ({'root_path': str(output), 'path': '.env-results'}, 403),
            ({'root_path': str(output), 'path': '.', 'overwrite': True}, 409),
            ({'root_path': str(tmp_path), 'path': 'results/nested'}, 409),
            ({'root_path': str(output), 'path': 'new', 'workspace_id': ws.id}, 409),
        ]:
            response = client.post('/api/fs/mkdir', json=body)
            assert response.status_code == status, response.text
        for action in ('copy', 'move', 'rename', 'delete'):
            assert client.post('/api/fs/' + action, json={
                'root_path': str(output), 'path': 'notes.txt'}).status_code == 422
        assert not (data / 'forbidden').exists()


def test_output_chooser_mkdir_rechecks_permissions_and_dataset_registration(scopes, monkeypatch):
    fs, files, dataset, ws, data, output = scopes
    app = FastAPI(); app.include_router(workspace_router(fs, files))
    with TestClient(app) as client:
        denied = client.post('/api/fs/mkdir', json={'root_path': str(output), 'path': 'permission-test'}).json()
        original = os.access
        monkeypatch.setattr(os, 'access', lambda path, mode: False if Path(path) == output and mode & os.W_OK else original(path, mode))
        result = client.post('/api/fs/operations/' + denied['id'] + '/confirm', json={'confirmed': True}).json()
        assert result['status'] == 'failed' and not (output / 'permission-test').exists()
        monkeypatch.setattr(os, 'access', original)
        proposed = client.post('/api/fs/mkdir', json={'root_path': str(output), 'path': 'dataset-test'}).json()
        fs.store.save_dataset(dataset.model_copy(update={'dataset_id': 'new-input', 'relative_path': 'results', 'absolute_path': str(output)}))
        assert client.post('/api/fs/operations/' + proposed['id'] + '/confirm', json={'confirmed': True}).status_code == 403
        assert not (output / 'dataset-test').exists()


def test_pending_write_is_rechecked_if_folder_becomes_dataset(scopes):
    fs, files, dataset, ws, data, output = scopes
    proposal = fs.propose('delete', FileOperationRequest(workspace_id=ws.id, path='notes.txt'))
    fs.store.save_dataset(dataset.model_copy(update={'dataset_id': 'new-input', 'relative_path': 'results', 'absolute_path': str(output)}))
    with pytest.raises(PermissionError, match='只读保护'):
        fs.confirm(proposal['id'])
    assert (output / 'notes.txt').exists()


def test_dataset_registration_cannot_race_running_file_operation(scopes):
    fs, files, dataset, ws, data, output = scopes
    proposal = fs.propose('mkdir', FileOperationRequest(workspace_id=ws.id, path='new'))
    with fs.store._connect() as db:
        db.execute("UPDATE workspace_operations SET status='running' WHERE id=?", (proposal['id'],))
    with pytest.raises(ValueError, match='文件操作'):
        fs.guard_dataset_registration(output)


def test_dataset_symlink_escape(scopes):
    fs, files, dataset, ws, data, output = scopes
    try:
        (data / 'escape').symlink_to(output, target_is_directory=True)
    except OSError:
        pytest.skip('Requires Linux or Windows symlink privilege')
    with pytest.raises(PermissionError):
        files.reader(dataset.dataset_id).read_text('escape/notes.txt')
    (output / 'alias').symlink_to(output, target_is_directory=True)
    with pytest.raises(PermissionError):
        fs.propose('mkdir', DirectoryCreationRequest(root_path=str(output / 'alias'), path='forbidden'))


def test_output_child_permission_denied_on_linux(scopes):
    if os.name == 'nt' or os.geteuid() == 0:
        pytest.skip('Real POSIX permissions require an unprivileged Linux account')
    fs, files, dataset, ws, data, output = scopes
    output.chmod(0o500)
    try:
        assert not fs.get(ws.id).manageable
        with pytest.raises(PermissionError):
            fs.propose('mkdir', FileOperationRequest(workspace_id=ws.id, path='not-created'))
        with pytest.raises(PermissionError):
            fs.propose('mkdir', DirectoryCreationRequest(root_path=str(output), path='not-created'))
    finally:
        output.chmod(0o700)
