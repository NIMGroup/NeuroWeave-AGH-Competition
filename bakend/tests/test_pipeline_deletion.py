"""Deleting a publication removes selection, never its immutable execution history."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.capability_registry import CapabilityRegistry
from app.execution.recipe_compiler import RecipeCompiler
from app.library_routes import library_router
from app.local_store import LocalStore
from app.pipeline_library import PipelineLibrary
from app.pipeline_models import ComposeRequest, DraftSave
from app.workflow_models import Workflow, WorkflowStep


@pytest.fixture
def library(tmp_path):
    registry = CapabilityRegistry(Path(__file__).resolve().parents[2] / 'skills')
    library = PipelineLibrary(LocalStore(tmp_path / 'pipeline-deletion.db'), registry)
    registry.library_provider = library.bindings
    return library


def publish(library, identity=None, version=None):
    return library.save(ComposeRequest(name='Publication', expected_version=version,
                        steps=[dict(id='convert', method_id='mrtrix.command.mrconvert')]), identity)


def test_all_versions_disappear_without_changing_snapshots_or_drafts(library):
    first = publish(library)
    second = publish(library, first['id'], 1)
    library.lifecycle(first['id'], 1, 'archived')
    draft = DraftSave(editor=first['editor'], pipeline_id=first['id'], pipeline_version=1)
    library.save_draft('editing', draft)
    method = library.as_method(second)
    snapshot = RecipeCompiler._method_option(method)
    wf = Workflow(workflow_id='existing', template_id='pipeline:'+first['id'],
                  title='Existing task', summary='', original_request='', status='confirmed',
                  steps=[WorkflowStep(id='pipeline', order=1, title='Pipeline', description='',
                          capability=method.capability, status='confirmed', method_options=[snapshot],
                          selected_method_id=snapshot.id)], created_at='now', updated_at='now')
    before = [(u.step.model_dump(), u.method.model_dump()) for u in RecipeCompiler(library.registry).compile(wf)]
    with library.store.connection() as db:
        stored = [tuple(r) for r in db.execute('SELECT * FROM pipeline_library ORDER BY version')]
    library.delete(first['id'], 2, 0)
    assert not library.records()
    assert first['id'] not in {p['id'] for p in library.catalog('pipeline')['items']}
    assert not any(m.definition_id == first['id'] for m in library.bindings())
    assert library.drafts()[0]['pipeline_id'] == first['id']
    with pytest.raises(KeyError, match='已删除'):
        library.get(first['id'], 1)
    with pytest.raises(KeyError, match='已删除'):
        library.workflow(first['id'], type('Request', (), {'version': 2})(), None, None)
    after = [(u.step.model_dump(), u.method.model_dump()) for u in RecipeCompiler(library.registry).compile(wf)]
    assert after == before
    with library.store.connection() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM pipeline_library ORDER BY version')] == stored
    deleted = library.trash()[0]
    assert deleted['version_count'] == 2 and deleted['deletion_revision'] == 1
    library.restore(first['id'], 1)
    assert library.get(first['id'], 1)['status'] == 'archived'
    assert library.get(first['id'], 2)['status'] == 'published'
    assert library.get(first['id'])['deletion_revision'] == 2
    assert any(m.definition_id == first['id'] for m in library.bindings())
    assert library.trash() == []


def test_stale_windows_cannot_delete_new_versions_or_resurrect_deleted_publications(library):
    p = publish(library)
    publish(library, p['id'], 1)
    with pytest.raises(ValueError, match='新版本'):
        library.delete(p['id'], 1, 0)
    assert library.get(p['id'])['version'] == '2'
    result = library.delete(p['id'], 2, 0)
    assert library.delete(p['id'], 2, 0) == result
    with pytest.raises(ValueError, match='已删除'):
        publish(library, p['id'], 2)
    with pytest.raises((ValueError, KeyError)):
        library.lifecycle(p['id'], 2, 'published')
    library.restore(p['id'], 1)
    with pytest.raises(ValueError, match='状态已变化'):
        library.delete(p['id'], 2, 0)
    library.delete(p['id'], 2, 2)
    with pytest.raises(ValueError, match='状态已变化'):
        library.restore(p['id'], 1)
    library.restore(p['id'], 3)
    assert publish(library, p['id'], 2)['version'] == '3'


def test_deletion_is_durable_and_preserves_archived_latest_state(library):
    p = publish(library)
    library.lifecycle(p['id'], 1, 'archived')
    library.delete(p['id'], 1, 0)
    reopened = PipelineLibrary(LocalStore(library.store.database_path), library.registry)
    assert reopened.records() == [] and reopened.trash()[0]['id'] == p['id']
    reopened.restore(p['id'], 1)
    assert reopened.get(p['id'])['status'] == 'archived'
    assert not any(m.definition_id == p['id'] for m in reopened.bindings())


def test_http_delete_restore_conflicts_and_builtin_protection(library):
    p = publish(library)
    app = FastAPI()
    app.include_router(library_router(library, None, None, None))
    endpoint = '/api/library/pipelines/' + p['id']
    with TestClient(app) as client:
        body = {'expected_version': 1, 'expected_deletion_revision': 0}
        assert client.request('DELETE', endpoint, json={**body, 'expected_version': 2}).status_code == 409
        assert client.request('DELETE', endpoint, json=body).status_code == 200
        assert client.get('/api/library/pipelines/trash').json()['items'][0]['id'] == p['id']
        assert client.get(endpoint).status_code == 404
        assert client.get(endpoint+'/source').status_code == 404
        assert client.get(endpoint+'/versions').json()['items'] == []
        update = client.put(endpoint, json=ComposeRequest.model_validate(p['editor']).model_dump())
        assert update.status_code == 409 and update.json()['detail']['code'] == 'pipeline_deleted'
        assert client.post(endpoint+'/restore', json={'expected_deletion_revision': 2}).status_code == 409
        assert client.post(endpoint+'/restore', json={'expected_deletion_revision': 1}).status_code == 200
        assert client.get(endpoint).json()['deletion_revision'] == 2
        assert client.request('DELETE', endpoint, json=body).status_code == 409
        assert client.request('DELETE', endpoint, json={'expected_version': 1}).status_code == 422
        assert client.request('DELETE', '/api/library/pipelines/user-missing', json=body).status_code == 404
        builtin = next(iter(library.registry.definitions.pipelines))
        assert client.request('DELETE', '/api/library/pipelines/'+builtin, json=body).status_code == 422
        assert library.get(builtin)
