import copy
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.capability_registry import CapabilityRegistry
from app.library_routes import library_router
from app.local_store import LocalStore
from app.pipeline_library import PipelineLibrary
from app.pipeline_models import ComposeRequest, DraftSave


@pytest.fixture
def library(tmp_path):
    lib = PipelineLibrary(LocalStore(tmp_path / 'source.db'), CapabilityRegistry(Path(__file__).resolve().parents[2] / 'skills'))
    lib.registry.library_provider = lib.bindings
    lib.registry.definitions.load()
    return lib


def test_all_builtin_sources_preserve_defaults_connections_and_compile(library):
    for identity, original in library.registry.definitions.pipelines.items():
        before = copy.deepcopy(original)
        source = library.source(identity)['editor']
        compiled = library.compile(ComposeRequest.model_validate(source))
        public = {p['id']: p['default'] for p in original['parameters']}
        nodes = {s['id']: s for s in source['steps']}
        for stage in original['steps']:
            node = nodes[stage['id']]
            for target, public_id in stage.get('parameter_bindings', {}).items():
                expected = stage.get('fixed_parameters', {}).get(target, public[public_id])
                assert node['parameters'].get(target) == expected, (identity, target)
            for slot, binding in stage['input_bindings'].items():
                if binding['source'] == 'step_output':
                    assert node['inputs'][slot]['step_id'] == binding['step_id']
                    assert node['inputs'][slot]['output_slot_id'] == binding['output_slot_id']
            assert node.get('when', {}) == stage.get('when', {})
        assert len(compiled['steps']) == len(original['steps'])
        assert original == before
    assert library.records() == []
    assert library.drafts() == []


def test_copy_keeps_conditional_stages_and_shared_files(library):
    identity = 'reviewed-mrtrix3-existing-fod-true-ebb543ab01'
    original = library.get(identity)
    source = library.source(identity)['editor']
    source['name'] = 'Independent copy'
    copy_pipeline = library.save(ComposeRequest.model_validate(source))
    assert copy_pipeline['id'].startswith('user-')
    assert copy_pipeline['version'] == '1'
    conditional = copy_pipeline['steps'][1]
    assert conditional['when'] == {'postprocessing': ['sift2']}
    assert next(p for p in copy_pipeline['parameters'] if p['id'] == 'postprocessing')['default'] == 'none'
    assert conditional['input_bindings']['in_fod']['slot_id'] == copy_pipeline['steps'][0]['input_bindings']['fod']['slot_id']
    assert next(p for p in copy_pipeline['output_slots'] if p['id'].endswith('__out_weights'))['generated_when']['postprocessing'] == ['sift2']
    library.save_draft('copy-draft', DraftSave(editor=source))
    assert library.drafts()[0]['editor']['controls'][0]['id'] == 'postprocessing'
    source['controls'][0]['default'] = 'sift2'
    updated = library.save(ComposeRequest.model_validate({**source, 'expected_version': 1}), copy_pipeline['id'])
    assert updated['version'] == '2'
    assert library.get(identity) == original
    assert library.source(copy_pipeline['id'], 1)['editor']['controls'][0]['default'] == 'none'
    # Exercise the existing execution compiler in memory; no runners are launched.
    from types import SimpleNamespace
    from app.chat_service import ChatService
    from app.dataset_service import DatasetService
    from app.execution.recipe_compiler import RecipeCompiler
    from app.pipeline_models import UsePipeline
    from app.workflow_service import WorkflowService
    root = Path(__file__).resolve().parents[2]
    service = WorkflowService(ChatService(),
                              library.registry, library.store, DatasetService(root, library.store))
    workflow = library.workflow(copy_pipeline['id'], UsePipeline(version=1), service, SimpleNamespace())
    assert len(RecipeCompiler(library.registry).compile(workflow)) == 1
    workflow.steps[0].selected_parameters['postprocessing'] = 'sift2'
    assert len(RecipeCompiler(library.registry).compile(workflow)) == 2


def test_http_source_is_read_only_and_builtin_update_is_rejected(library):
    app = FastAPI()
    app.include_router(library_router(library, None, None, None))
    identity = next(iter(library.registry.definitions.pipelines))
    before = library.get(identity)
    with TestClient(app) as client:
        result = client.get(f'/api/library/pipelines/{identity}/source?version=1')
        assert result.status_code == 200
        assert client.put(f'/api/library/pipelines/{identity}', json=result.json()['editor']).status_code == 409
        assert client.get('/api/library/pipelines/missing/source').status_code == 404
    assert library.get(identity) == before
    assert library.records() == [] and library.drafts() == []


def test_unknown_condition_cannot_silently_skip_a_step(library):
    source = library.source('reviewed-mrtrix3-existing-fod-true-ebb543ab01')['editor']
    source['steps'][1]['when'] = {'unknown': ['sift2']}
    with pytest.raises(ValueError, match='控制参数'):
        library.compile(ComposeRequest.model_validate(source))
