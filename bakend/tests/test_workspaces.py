import asyncio
import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.local_store import LocalStore
from app.conversation_service import ConversationService
from app.chat_service import ChatService
from app.models import ChatRequest
from app.workspace_models import WorkspaceCreate, FileOperationRequest, WorkspaceUpdate
from app.workspace_routes import workspace_router
from app.workspace_service import WorkspaceService
from app.workspace_tools import WorkspaceTools


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / 'workspace'
    root.mkdir()
    monkeypatch.setenv('WORKSPACE_ALLOWED_ROOTS', str(root))
    monkeypatch.setenv('WORKSPACE_DEFAULT_ROOT', str(root))
    monkeypatch.setenv('WORKSPACE_TEXT_MAX_CHARS', '32')
    monkeypatch.delenv('WORKSPACE_TRASH_DIR', raising=False)
    store = LocalStore(tmp_path / 'state' / 'db.sqlite')
    fs = WorkspaceService(store, root)
    workspace = fs.create(WorkspaceCreate(name='Test', root_path=str(root)))
    return fs, workspace, root


def test_workspaces_history_migration_and_disk_preserved(setup):
    fs, ws, root = setup
    conversations = ConversationService(fs.store, ChatService())
    old = conversations.create('legacy')
    assert fs.store.get_conversation(old.conversation_id).workspace_id is None
    fs.bind_conversation(old.conversation_id, ws.id)
    assert conversations.snapshot(old.conversation_id).conversation.workspace_id == ws.id
    assert fs.store.list_conversations()[0].workspace_id == ws.id
    imported = old.model_copy(update={"conversation_id": "imported", "workspace_id": ws.id})
    fs.store.create_conversation(imported)
    assert fs.store.get_conversation("imported").workspace_id == ws.id
    fs.update(ws.id, WorkspaceUpdate(name='Renamed'))
    assert fs.get(ws.id).name == 'Renamed'
    assert fs.remove(ws.id)['disk_preserved'] and root.exists()
    assert conversations.snapshot(old.conversation_id).conversation.workspace_id == ws.id


def test_pagination_large_directory_and_stale_cursor(setup):
    fs, ws, root = setup
    (root / 'z-folder').mkdir()
    for i in range(217):
        (root / f'file-{i:04}.txt').write_text('test')
    (root / '.env').write_text('secret')
    first = fs.list_directory(workspace_id=ws.id, limit=17, show_hidden=True)
    assert first['items'][0]['type'] == 'directory'
    all_paths = [p['path'] for p in first['items']]
    cursor = first['next_cursor']
    while cursor:
        page = fs.list_directory(workspace_id=ws.id, limit=17, cursor=cursor, show_hidden=True)
        assert len(page['items']) <= 17
        all_paths += [p['path'] for p in page['items']]
        cursor = page['next_cursor']
    assert len(all_paths) == len(set(all_paths)) == 218
    (root / 'new.txt').write_text('new')
    with pytest.raises(ValueError):
        fs.list_directory(workspace_id=ws.id, cursor=first['next_cursor'], show_hidden=True)


def test_traversal_symlink_sensitive_and_text_limits(setup, tmp_path):
    fs, ws, root = setup
    (root / 'notes.txt').write_text('x' * 100)
    (root / 'image.nii').write_bytes(b'pretend text')
    (root / 'binary.txt').write_bytes(b'\x00\x01test')
    (root / 'late-binary.txt').write_bytes(b'x' * 9000 + b'\x00' * 100)
    (root / '.env').write_text('private')
    for path in ('../outside', str(tmp_path / 'outside'), '.env'):
        with pytest.raises(PermissionError):
            fs.inspect(path, ws.id)
    result = fs.read_text('notes.txt', ws.id, max_chars=10000)
    assert len(result['text']) == 32 and result['truncated']
    assert fs.read_text('notes.txt', ws.id, offset=96)['text'] == 'xxxx'
    for name in ('image.nii', 'binary.txt'):
        with pytest.raises(ValueError):
            fs.read_text(name, ws.id)
    with pytest.raises(ValueError):
        fs.read_text('late-binary.txt', ws.id, offset=9000)
    outside = tmp_path / 'outside'
    outside.mkdir(exist_ok=True)
    try:
        (root / 'link').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Host requires symlink privilege; same test is mandatory on Linux server')
    with pytest.raises(PermissionError):
        fs.list_directory('link', ws.id)
    with pytest.raises(PermissionError):
        fs.propose('mkdir', FileOperationRequest(workspace_id=ws.id, path='link/new'))


def test_confirm_delete_restore_overwrite_and_replay(setup):
    fs, ws, root = setup
    (root / 'data.txt').write_text('original')
    request = FileOperationRequest(workspace_id=ws.id, path='data.txt')
    proposal = fs.propose('delete', request)
    assert (root / 'data.txt').exists()
    assert fs.operation(proposal['id'])['status'] == 'pending'
    done = fs.confirm(proposal['id'])
    assert done['status'] == 'succeeded', done
    assert not (root / 'data.txt').exists()
    assert len(fs.trash(ws.id)) == 1
    restore = fs.propose_restore(done['result']['id'])
    assert not (root / 'data.txt').exists()
    assert fs.confirm(restore['id'])['status'] == 'succeeded'
    assert (root / 'data.txt').read_text() == 'original'
    with pytest.raises(ValueError):
        fs.confirm(proposal['id'])
    (root / 'target.txt').write_text('old target')
    with pytest.raises(ValueError):
        fs.propose('copy', FileOperationRequest(workspace_id=ws.id, path='data.txt', destination='target.txt'))
    replacement = fs.propose('copy', FileOperationRequest(workspace_id=ws.id, path='data.txt', destination='target.txt', overwrite=True))
    assert (root / 'target.txt').read_text() == 'old target'
    assert fs.confirm(replacement['id'])['status'] == 'succeeded'
    assert (root / 'target.txt').read_text() == 'original'
    backup = fs.trash(ws.id)[0]
    assert Path(backup['trash_path']).read_text() == 'old target'


def test_mutations_changed_inputs_active_paths_and_audit(setup):
    fs, ws, root = setup
    proposal = fs.propose('mkdir', FileOperationRequest(workspace_id=ws.id, path='sub'))
    assert not (root / 'sub').exists()
    assert fs.confirm(proposal['id'])['status'] == 'succeeded'
    (root / 'sub' / 'file').write_text('one')
    proposal = fs.propose('rename', FileOperationRequest(workspace_id=ws.id, path='sub/file', destination='renamed'))
    (root / 'sub' / 'file').write_text('changed')
    assert fs.confirm(proposal['id'])['status'] == 'failed'
    assert not (root / 'sub' / 'renamed').exists()
    fs.protected_paths = lambda: [root / 'sub' / 'file']
    with pytest.raises(PermissionError):
        fs.propose('delete', FileOperationRequest(workspace_id=ws.id, path='sub'))
    with fs.store._connect() as db:
        entries = [json.loads(row[0]) for row in db.execute('SELECT payload FROM workspace_audit')]
    assert any(item['result'] == 'succeeded' and item['confirmed'] for item in entries)
    assert any(item['result'] == 'failed' for item in entries)


def test_bounded_search_and_real_function_dispatch(setup):
    fs, ws, root = setup
    for i in range(12):
        (root / f'{i}.txt').write_text(str(i))
    nested = root / 'a' / 'b'
    nested.mkdir(parents=True)
    (nested / 'deep.txt').write_text('deep')
    result = fs.search(workspace_id=ws.id, pattern='*.txt', max_depth=0, max_results=3)
    assert result['truncated'] and len(result['items']) == 3
    assert not any(item['name'] == 'deep.txt' for item in result['items'])
    class Model:
        mode = 'model'
        calls = 0
        async def workspace_completion(self, messages, tools, required=False):
            self.calls += 1
            assert any(t['function']['name'] == 'list_output_directory' for t in tools)
            assert not any('confirm' in t['function']['name'] for t in tools)
            if self.calls == 1:
                assert required
                return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call1', 'type': 'function',
                    'function': {'name': 'list_output_directory', 'arguments': '{"path":".","limit":3}'}}]}
            assert messages[-1]['role'] == 'tool'
            data = json.loads(messages[-1]['content'])
            assert len(data['items']) == 3
            return {'role': 'assistant', 'content': '已通过工具读取 3 项。'}
    request = ChatRequest(message='请告诉我当前目录有哪些文件', workspace_id=ws.id, interaction_mode='task')
    tools = WorkspaceTools(fs, Model())
    assert '3 项' in asyncio.run(tools.reply(request))
    assert fs.events(request.client_turn_id)[-1]['count'] == 3
    request.interaction_mode = 'chat'
    with pytest.raises(PermissionError):
        tools.execute('delete_workspace_path', {'path': '0.txt'}, request)
    with pytest.raises(ValueError):
        tools.execute('read_workspace_text', {'path': '0.txt', 'root_path': '/'}, request)


def test_http_requires_explicit_confirmation(setup):
    fs, ws, root = setup
    app = FastAPI()
    app.include_router(workspace_router(fs))
    with TestClient(app) as client:
        assert client.get('/api/fs/list', params={'workspace_id': ws.id}).status_code == 200
        assert client.get('/api/fs/list', params={'workspace_id': ws.id, 'path': '..'}).status_code == 403
        proposal = client.post('/api/fs/mkdir', json={'workspace_id': ws.id, 'path': 'new'}).json()
        url = f"/api/fs/operations/{proposal['id']}/confirm"
        assert client.post(url, json={'confirmed': False}).status_code == 422
        assert not (root / 'new').exists()
        assert client.post(url, json={'confirmed': True}).json()['status'] == 'succeeded'
        assert (root / 'new').is_dir()


def test_sensitive_confirmation_is_one_time_and_not_persisted(setup):
    fs, ws, root = setup
    (root / 'native-backend.env').write_text('synthetic-private-test-value')
    with pytest.raises(PermissionError):
        fs.read_text('native-backend.env', ws.id)
    proposal = fs.propose('read-sensitive', FileOperationRequest(workspace_id=ws.id, path='native-backend.env'))
    result = fs.confirm(proposal['id'])
    assert result['result']['text'] == 'synthetic-private-test-value'
    assert 'text' not in fs.operation(proposal['id'])['result']
    with fs.store._connect() as db:
        assert all('synthetic-private-test-value' not in row[0] for row in db.execute('SELECT payload FROM workspace_audit'))


def test_model_cannot_claim_success_without_tool_observation(setup):
    fs, ws, root = setup
    class UnreliableModel:
        mode = 'model'
        async def workspace_completion(self, *args, **kwargs):
            return {'role': 'assistant', 'content': '目录有一千个文件，我已经删除全部文件。'}
    request = ChatRequest(message='目录有哪些文件', workspace_id=ws.id)
    result = asyncio.run(WorkspaceTools(fs, UnreliableModel()).reply(request))
    assert '尚未调用' in result
    assert '一千' not in result


def test_safe_copy_move_permanent_delete_and_special_roots(setup):
    fs, ws, root = setup
    (root / 'source').mkdir()
    (root / 'source' / 'file.txt').write_text('payload')
    for action, path, destination in [('copy', 'source', 'copy'), ('move', 'copy', 'moved'), ('rename', 'moved', 'renamed')]:
        proposal = fs.propose(action, FileOperationRequest(workspace_id=ws.id, path=path, destination=destination))
        assert fs.confirm(proposal['id'])['status'] == 'succeeded'
    assert (root / 'renamed' / 'file.txt').read_text() == 'payload'
    proposal = fs.propose('delete', FileOperationRequest(workspace_id=ws.id, path='renamed', permanent=True))
    assert (root / 'renamed').exists()
    assert fs.confirm(proposal['id'])['status'] == 'succeeded'
    assert not (root / 'renamed').exists()
    if os.name != 'nt':
        assert fs.paths.special(Path('/proc/self/fd'))
        assert fs.paths.special(Path('/dev/zero'))


def test_copy_does_not_hold_runner_lock_or_replace_target_on_failure(setup, monkeypatch):
    import threading
    from app import workspace_io
    fs, ws, root = setup
    fs.run_lock = threading.Lock()
    (root / 'from.txt').write_text('source')
    (root / 'to.txt').write_text('keep me')
    proposal = fs.propose('copy', FileOperationRequest(workspace_id=ws.id, path='from.txt', destination='to.txt', overwrite=True))
    def interrupted_copy(source, target, policy):
        assert fs.run_lock.acquire(blocking=False), 'A file copy must not block Runner progress'
        fs.run_lock.release()
        target.write_text('partial')
        raise OSError('synthetic copy failure')
    monkeypatch.setattr(workspace_io, 'copy', interrupted_copy)
    result = fs.confirm(proposal['id'])
    assert result['status'] == 'failed'
    assert (root / 'to.txt').read_text() == 'keep me'
    assert Path(result['staging_path']).read_text() == 'partial'
