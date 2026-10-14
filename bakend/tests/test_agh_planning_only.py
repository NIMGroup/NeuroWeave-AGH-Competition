"""Planning uses registered contracts without a scientific runtime or scheduler."""
import asyncio
import sys

import pytest

from app.agh_server import AGHBusiness
from app.application import create_app
from app.execution.run_service import RunConflict
from app.pipeline_models import ComposeRequest, UsePipeline


@pytest.fixture
def planning(tmp_path, monkeypatch):
    for key, value in {
        'NEUROWEAVE_HACKATHON': 'true',
        'NEUROWEAVE_PLANNING_ONLY': 'true',
        'NEUROWEAVE_DATA_USERS': '{}',
        'LOCALFLOW_DATABASE_PATH': str(tmp_path/'state.sqlite'),
        'LOCALFLOW_OUTPUT_ROOT': str(tmp_path/'output'),
        'LOCALFLOW_DATA_ROOTS': str(tmp_path/'no-dataset'),
        'LOCALFLOW_RUN_INPUT_ROOTS': str(tmp_path/'no-dataset'),
        'WORKSPACE_DEFAULT_ROOT': str(tmp_path/'output'),
        'WORKSPACE_ALLOWED_ROOTS': str(tmp_path/'output'),
        'LOCALFLOW_SLURM_ENABLED': 'false',
        'LOCALFLOW_SLURM_WORKER_ROOT': str(tmp_path/'no-worker'),
        'LOCALFLOW_SLURM_PYTHON': str(tmp_path/'no-scientific-python'),
        'LOCALFLOW_SKILL_PYTHON': sys.executable,
        'NEUROWEAVE_DATA_PYTHON_IMAGE': '',
        'NEUROWEAVE_DATA_PYTHON_ENVIRONMENTS_FILE': '',
    }.items():
        monkeypatch.setenv(key, value)
    app = create_app()
    business = AGHBusiness(app)
    for identity in business.s.runner_registry.ids():
        monkeypatch.setattr(business.s.runner_registry.get(identity), 'runtime_status',
                            lambda: (_ for _ in ()).throw(AssertionError('Planning must not probe scientific software')))
    monkeypatch.setattr(business.s.run_service.slurm_backend, 'status',
                        lambda: (_ for _ in ()).throw(AssertionError('Planning must not query Slurm')))
    yield business
    business.s.run_service.shutdown()
    business.s.operation_service.shutdown()
    business.s.data_operations.shutdown()


def tracking_draft(business):
    key = 'planning-session'
    message = '已有三种响应函数，没有 FOD；先生成 FOD 再进行纤维追踪。'
    business.native_messages(key, {'messages': [{'sequence': 1, 'text': message}]})
    business.update_task(key, {'goal': message, 'deliverable': 'workflow',
        'declared_inputs': [
            {'artifact_type': 'fod_image', 'available': False, 'quote': message, 'reference': 'current'},
            {'artifact_type': 'response_functions', 'available': True, 'quote': message, 'reference': 'current'}]})
    route = business.method_routes(key, {'targets': [{'method_id': 'mrtrix.tckgen', 'output_slot_id': 'tractogram'}]})
    assert route['status'] == 'matched'
    saved = asyncio.run(business.prepare_workflow(key, {'goal': message, 'route_id': route['routes'][0]['route_id']}))
    return key, saved


def test_no_dataset_or_runtime_can_deliver_connected_editable_draft(planning):
    key, value = tracking_draft(planning)
    assert value['dataset'] is None and value['workspace_id'] is None
    assert [s['selected_method_id'] for s in value['steps']] == ['mrtrix.dwi2fod', 'mrtrix.tckgen']
    assert not value['contract_errors']
    tracking = value['steps'][1]
    assert any(binding['source'] == 'upstream_output' for binding in tracking['input_bindings'].values())
    assert planning.s.local_store.list_runs(workflow_id=value['workflow_id']) == []
    assert planning.context(key, for_prompt=True)['planning_only'] is True


def test_catalog_contracts_remain_available_and_do_not_claim_execution(planning):
    listing = planning.s.skill_catalog.list_methods(q='mrtrix.tckgen', executable_only=True)
    assert any(item['method_id']=='mrtrix.tckgen' for item in listing['items'])
    method = planning.s.skill_catalog.method(method_id='mrtrix.tckgen')
    assert method['input_slots'] and method['output_slots'] and method['parameters']
    assert method['runtime_status']['available'] is False
    assert method['runtime_status']['planning_only'] is True
    assert planning.s.runner_registry.available_ids() == set()
    assert all(item['available'] is False for item in planning.s.runner_registry.public_catalog())
    index = planning.s.workflow_service.method_index
    index.refresh()
    assert index.context(['mrtrix.tckgen'], planning.s.runner_registry)[0]['runtime']['planning_only'] is True
    candidates = planning.s.workflow_service.catalog.candidate_context(['dwi.tractography'])
    assert candidates and all(not method['runtime_status']['available'] for method in candidates[0]['methods'])
    detail = asyncio.run(planning.panel('planning-catalog-session', {
        'method': 'GET', 'path': '/api/library/methods/mrtrix.tckgen'}))
    assert detail['status'] == 200 and detail['body']['runtime_status']['planning_only'] is True


def test_pipeline_creation_does_not_require_installed_software(planning):
    method = planning.s.capability_registry.definitions.method('mrtrix.command.mrthreshold')
    output = next(slot for slot in method.output_slots if slot.type=='scalar_image' and not slot.generated_when)
    input_slot = next(slot for slot in method.input_slots if slot.type=='scalar_image')
    pipeline = planning.s.pipeline_library.save(ComposeRequest(name='Planning threshold chain', steps=[
        {'id': 'first', 'method_id': method.id},
        {'id': 'second', 'method_id': method.id, 'inputs': {
            input_slot.id: {'step_id': 'first', 'output_slot_id': output.id}}}]))
    value = planning.s.pipeline_library.workflow(pipeline['id'], UsePipeline(),
        planning.s.workflow_service, planning.s.workspace_service)
    assert value.steps[0].selected_method_id
    assert value.locked_at and not value.contract_errors
    assert planning.s.local_store.list_runs(workflow_id=value.workflow_id) == []


def test_readiness_and_execution_guards_remain_honest(planning):
    status = planning.s.run_service.execution_backends()
    assert status['planning_only'] is True
    assert status['local']['available'] is False and status['slurm']['available'] is False
    assert planning.s.run_service.enabled is False
    with pytest.raises(RunConflict, match='仅规划'):
        planning.s.run_service.create('wf_not_executed', 1)
    key, value = tracking_draft(planning)
    with pytest.raises(ValueError, match='仅规划'):
        planning.s.workflow_service.dry_run(value['workflow_id'], value['revision'])
    with pytest.raises(PermissionError, match='PLANNING_ONLY'):
        asyncio.run(planning.panel(key, {'method': 'POST',
            'path': '/api/workflows/'+value['workflow_id']+'/runs',
            'body': {'expected_revision': value['revision']}}, execution=True))
    assert planning.s.local_store.list_runs(workflow_id=value['workflow_id']) == []
