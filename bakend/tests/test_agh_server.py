"""Server adapter authorization, session recovery and approval snapshot contracts."""
import asyncio
import sys
from pathlib import Path

import httpx
import pytest
from unittest.mock import Mock

from app.agh_server import AGHBusiness, PanelRequest, SavedRouteDraft, build_server_app
from app.application import create_app
from app.workspace_models import WorkspaceCreate
from app.workflow_models import Workflow


def test_3003_route_delivers_a_user_input_draft_without_investigating_files(business):
    business.s.workspace_tools.data_operations._roots = Mock(side_effect=AssertionError('Draft planning must not scan data'))
    arguments = {'task_contract': {'goal_artifact_types': ['fod_image'], 'allow_generated_intermediates': False},
        'preferred_methods': ['mrtrix.dwi2fod'], 'parameters': {'mrtrix.dwi2fod': {'algorithm': 'msmt_csd'}}}
    result = business.method_routes('route-session', arguments)
    assert result['status'] == 'matched'
    route = result['routes'][0]
    assert route['method_ids'] == ['mrtrix.dwi2fod']
    workflow = asyncio.run(business.prepare_workflow('route-session', {'goal': '使用已有三种响应函数生成 FOD 草稿', 'route_id': route['route_id']}))
    assert workflow['input_selection'] == 'user'
    assert len(workflow['steps']) == 1
    assert not workflow['contract_errors']
    assert workflow['steps'][0]['missing_required_inputs']
    assert not workflow['steps'][0]['input_bindings']
    assert business.s.local_store.list_runs(workflow_id=workflow['workflow_id']) == []
    assert business.shared_facts('route-session', {'owner': 'methods'})
    reloaded = AGHBusiness(business.app)
    assert reloaded.context('route-session')['method_routes']['status'] == 'matched'


def test_3003_saved_routes_cannot_cross_agh_conversations_or_override_the_graph(business):
    arguments = {'task_contract': {'goal_artifact_types': ['fod_image'], 'allow_generated_intermediates': False},
        'preferred_methods': ['mrtrix.dwi2fod']}
    route = business.method_routes('route-owner', arguments)['routes'][0]['route_id']
    with pytest.raises((ValueError, KeyError)):
        asyncio.run(business.prepare_workflow('other-route-owner', {'goal': 'foreign', 'route_id': route}))
    with pytest.raises(ValueError, match='cannot_be_overwritten'):
        asyncio.run(business.prepare_workflow('route-owner', {'goal': 'override', 'route_id': route,
            'plan': {'status': 'matched'}}))


def test_generic_csv_goal_respects_the_requested_roi_producer(business):
    result = business.method_routes('roi-route-session', {
        'task_contract': {'goal_artifact_types': ['generated_file'], 'allow_generated_intermediates': False},
        'preferred_methods': ['reviewed.dipy-tool.roi-stats'],
        'parameters': {'reviewed.dipy-tool.roi-stats': {'min_voxels': 2}}})
    assert result['status'] == 'matched'
    route = result['routes'][0]
    assert route['method_ids'] == ['reviewed.dipy-tool.roi-stats']
    assert route['input_selection'] == 'user'
    assert {slot['slot_id'] for slot in route['inputs_to_select']} == {'metric', 'roi'}
    workflow = asyncio.run(business.prepare_workflow('roi-route-session', {'goal': 'ROI statistics', 'route_id': route['route_id']}))
    assert workflow['steps'][0]['selected_parameters']['min_voxels'] == 2


def test_3003_legacy_model_job_routes_remain_blocked(business):
    workflow = confirmed_workflow(business)
    for endpoint in ('replan', 'complete-dependencies', 'replan-jobs', 'complete-dependencies-jobs'):
        with pytest.raises(PermissionError, match='AGH_PLANNING_REQUIRED'):
            asyncio.run(business.panel('session-one', {'path': f'/api/workflows/{workflow.workflow_id}/{endpoint}',
                'method': 'POST', 'body': {'expected_revision': workflow.revision}}))


def test_3003_shared_fact_provenance_failure_does_not_repeat_a_successful_tool(business, monkeypatch):
    request = business.request('fact-session')
    monkeypatch.setattr(business.ledger.facts, 'stamp', Mock(side_effect=ValueError('No output selected')))
    result = {'file_count': 1, 'complete_scan': True, 'source_scope': 'dataset', 'coverage': {}}
    business.record_fact(request, 'inventory_data_files', {'view': 'summary'}, result)
    rows = business.ledger.facts.rows(request, 'data')
    assert len(rows) == 1 and rows[0]['source_version'] is None
    assert rows[0]['summary']['file_count'] == 1


def test_native_user_messages_prove_missing_input_declarations_without_duplicate_capture(business):
    text = '已有三种响应函数，没有 FOD；我要做纤维追踪。'
    body = {'messages':[{'sequence':12,'text':text}]}
    business.native_messages('declaration-session',body)
    business.native_messages('declaration-session',body)
    request = business.request('declaration-session')
    assert request.message == text and request._user_sequence == 1
    state = business.update_task('declaration-session',{'goal':'纤维追踪','deliverable':'workflow',
        'declared_inputs':[{'artifact_type':'fod_image','available':False,'quote':text,'reference':'current'},
                           {'artifact_type':'response_functions','available':True,'quote':text,'reference':'current'}]})
    assert state['task_id']
    business.s.workspace_tools.data_operations._roots = Mock(side_effect=AssertionError('Planning must not scan'))
    result = business.method_routes('declaration-session',{'targets':[{'method_id':'mrtrix.tckgen','output_slot_id':'tractogram'}]})
    assert result['status'] == 'matched'
    assert result['routes'][0]['method_ids'] == ['mrtrix.dwi2fod','mrtrix.tckgen']
    assert len(business.ledger.facts.declarations(business.request('declaration-session'))) == 2


def test_saved_routes_revise_one_canonical_goal_and_scope_the_new_task_index(business):
    key = 'canonical-session'
    goal = '保持用户的完整纤维追踪目标、所需输入和验证条件'
    business.native_messages(key,{'messages':[{'sequence':1,'text':goal}]})
    state = business.update_task(key,{'goal':goal,'deliverable':'workflow'})
    query = {'task_contract':{'goal':goal,'goal_artifact_types':['tractogram'],'allow_generated_intermediates':False},
             'preferred_methods':['mrtrix.tckgen']}
    route = business.method_routes(key,query)['routes'][0]['route_id']
    first = asyncio.run(business.prepare_workflow(key,{'goal':'简短步骤说明','route_id':route}))
    configured = business.workflow_owned(first['workflow_id'],key)
    tracking = next(step for step in configured.steps if step.selected_method_id == 'mrtrix.tckgen')
    tracking.selected_parameters['streamline_count'] = 35000
    business.s.local_store.save_workflow(configured,event_type='test_user_parameters')
    again = asyncio.run(business.prepare_workflow(key,{'goal':'第二个步骤说明','route_id':route}))
    assert first['workflow_id'] == again['workflow_id'] and again['revision'] > first['revision']
    tracking = next(step for step in again['steps'] if step['selected_method_id'] == 'mrtrix.tckgen')
    assert tracking['selected_parameters'].get('streamline_count') == 35000, tracking['selected_parameters']
    assert again['managed_task_id'] == state['task_id']
    assert business.context(key)['task']['goal'] == goal
    index = asyncio.run(business.panel(key,{'method':'GET','path':'/api/task-index','body':None}))['body']
    assert len(index['items']) == 1 and index['items'][0]['task_id'] == state['task_id']
    assert asyncio.run(business.panel('other-session',{'method':'GET','path':'/api/task-index','body':None}))['body']['items'] == []
    with pytest.raises(PermissionError,match='another_agh_session'):
        asyncio.run(business.panel('other-session',{'method':'GET','path':'/api/tasks/'+state['task_id'],'body':None}))
    with pytest.raises(PermissionError,match='AGH_PLANNING_REQUIRED'):
        asyncio.run(business.panel(key,{'method':'POST','path':'/api/tasks/'+state['task_id']+'/replan','body':{}}))
    with pytest.raises(PermissionError,match='USER_ACTION_DISPATCH_REQUIRED'):
        asyncio.run(business.panel(key,{'method':'POST','path':'/api/conversations/'+business.session(key)[0]+'/task/end',
            'body':{'task_id':state['task_id']}}))


@pytest.fixture
def business(tmp_path, monkeypatch):
    roots = tmp_path / 'data'
    output = tmp_path / 'output'
    roots.mkdir()
    output.mkdir()
    for key, value in {'NEUROWEAVE_HACKATHON': 'true', 'NEUROWEAVE_DATA_USERS': '{}',
        'LOCALFLOW_DATABASE_PATH': str(tmp_path/'state.sqlite'), 'LOCALFLOW_OUTPUT_ROOT': str(output),
        'LOCALFLOW_DATA_ROOTS': str(roots), 'LOCALFLOW_RUN_INPUT_ROOTS': str(roots),
        'WORKSPACE_ALLOWED_ROOTS': str(output), 'WORKSPACE_DEFAULT_ROOT': str(output),
        'LOCALFLOW_SLURM_ENABLED': 'false', 'LOCALFLOW_SKILL_PYTHON': sys.executable}.items():
        monkeypatch.setenv(key, value)
    adapter = AGHBusiness(create_app())
    yield adapter
    adapter.s.run_service.shutdown()
    adapter.s.operation_service.shutdown()
    adapter.s.data_operations.shutdown()


def confirmed_workflow(business, key='session-one'):
    output = business.s.workspace_service.create(WorkspaceCreate(name='Test', root_path=str(business.s.settings.output_root)))
    workflow = Workflow(workflow_id='wf_123456789abc', template_id='test', title='Approval contract',
        summary='', original_request='test', status='confirmed', conversation_id=business.session(key)[0],
        workspace_id=output.id, workspace_root_path=output.root_path, steps=[],
        created_at='2026-10-13', updated_at='2026-10-13')
    business.s.local_store.save_workflow(workflow, event_type='test_prepared')
    return workflow


def test_workspace_display_metadata_survives_restart_without_touching_directories(business, tmp_path):
    directory = tmp_path / 'native-workspace'
    directory.mkdir()
    sentinel = directory / 'dataset.csv'
    sentinel.write_text('preserved')
    result = business.update_workspace_preferences({'path': str(directory), 'name': '研究工作区', 'description': '比赛测试'})
    assert result['workspaces'] == [{'path': str(directory), 'name': '研究工作区', 'description': '比赛测试', 'removed': False}]
    restored = AGHBusiness(business.app)
    assert restored.workspace_preferences() == result
    assert sentinel.read_text() == 'preserved' and directory.exists()
    for name in ('', 'x' * 81, 'name\ncontrol', '\u202eoverride'):
        with pytest.raises(ValueError, match='invalid_workspace_name'):
            business.update_workspace_preferences({'path': str(directory), 'name': name, 'description': ''})
    with pytest.raises(ValueError, match='invalid_workspace_path'):
        business.update_workspace_preferences({'path': '../other', 'name': 'invalid', 'description': ''})


def test_workspace_removal_is_durable_reversible_and_preserves_files_and_sessions(business, tmp_path):
    directory = tmp_path / 'workspace'
    directory.mkdir()
    sentinel = directory / 'data.csv'
    sentinel.write_text('preserved')
    business.update_workspace_preferences({'path': str(directory), 'name': '保留名称', 'description': '保留说明'})
    conversation = business.session('preserved-native-session')[0]
    business.select('preserved-native-session', {'selected_skills': ['dipy-tool']})
    removed = business.remove_workspace({'path': str(directory), 'removed': True})
    assert removed['workspaces'][0]['removed'] is True
    restored_app = AGHBusiness(business.app)
    assert restored_app.workspace_preferences() == removed
    assert restored_app.context('preserved-native-session')['conversation_id'] == conversation
    assert restored_app.context('preserved-native-session')['selected']['selected_skills'] == ['dipy-tool']
    restored = restored_app.remove_workspace({'path': str(directory), 'removed': False})
    assert restored['workspaces'][0] == {'path': str(directory), 'name': '保留名称', 'description': '保留说明', 'removed': False}
    assert sentinel.read_text() == 'preserved'


def test_workspace_preferences_migrate_the_previous_schema(business):
    with business.s.local_store._connect() as db:
        db.execute('DROP TABLE agh_workspace_preferences')
        db.execute('CREATE TABLE agh_workspace_preferences(path TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,updated_at TEXT NOT NULL)')
        db.execute("INSERT INTO agh_workspace_preferences VALUES('/home/existing','已有名称','已有说明','2026-10-14')")
    updated = AGHBusiness(business.app)
    assert updated.workspace_preferences()['workspaces'][0] == {'path': '/home/existing', 'name': '已有名称', 'description': '已有说明', 'removed': False}


def test_session_removal_guard_blocks_own_active_runs_and_preserves_empty_sessions(business):
    from app.run_models import WorkflowRun
    workflow = confirmed_workflow(business)
    run = WorkflowRun(run_id='run_123456789abc', workflow_id=workflow.workflow_id, workflow_revision=workflow.revision,
        output_directory=workflow.workspace_root_path, status='running', created_at='2026-10-14', updated_at='2026-10-14')
    business.s.local_store.save_run(run, event_type='test')
    with pytest.raises(ValueError, match='服务器任务运行'):
        business.session_removal_check({'session_id': 'session-one'})
    assert business.session_removal_check({'session_id': 'empty-session'}) == {'allowed': True}
    with business.s.local_store._connect() as db:
        assert db.execute('SELECT 1 FROM agh_sessions WHERE session_key=?', ('empty-session',)).fetchone() is None
    run.status = 'succeeded'
    business.s.local_store.save_run(run, event_type='test')
    assert business.session_removal_check({'session_id': 'session-one'}) == {'allowed': True}
    assert business.s.local_store.get_run(run.run_id).status == 'succeeded'


def execution_request(workflow):
    return {'path': f'/api/workflows/{workflow.workflow_id}/runs', 'method': 'POST',
            'body': {'expected_revision': workflow.revision, 'execution': {'backend': 'local', 'cpus': 1}}}


def test_context_is_persistent_and_separate_per_agh_session(business):
    one = business.session('session-one')[0]
    two = business.session('session-two')[0]
    assert one != two
    business.select('session-one', {'selected_skills': ['dipy-tool']})
    restored = AGHBusiness(business.app)
    assert restored.context('session-one')['selected']['selected_skills'] == ['dipy-tool']
    assert restored.context('session-two')['selected'] == {}


def test_prompt_context_preserves_selected_state_without_scheduler_or_workflow_enumeration(business, monkeypatch):
    workflow = confirmed_workflow(business)
    business.select('session-one', {'workflow_id': workflow.workflow_id, 'selected_skills': ['dipy-tool']})
    full = business.context('session-one')
    monkeypatch.setattr(business.s.run_service, 'execution_backends', Mock(side_effect=AssertionError('Prompt must not probe Slurm')))
    monkeypatch.setattr(business.s.workflow_service, 'list', Mock(side_effect=AssertionError('Prompt must not enumerate full workflows')))
    light = business.context('session-one', for_prompt=True)
    for field in ('selected', 'task', 'workspace', 'selected_guidance', 'method_routes', 'shared_facts', 'business_baseline'):
        assert light[field] == full[field]
    assert light['workflow'] == {field: full['workflow'][field] for field in ('workflow_id', 'revision', 'status')}
    assert light['workflows'] == [] and light['execution'] is None
    assert light['facts_are_not_permission'] is True
    assert business.s.local_store.list_runs(workflow_id=workflow.workflow_id) == []


def test_prompt_context_cannot_expose_another_sessions_workflow(business):
    workflow = confirmed_workflow(business)
    # Even a stale selection record cannot disclose a foreign workflow.
    conversation = business.session('session-two')[0]
    with business.s.local_store._connect() as db:
        db.execute('UPDATE agh_sessions SET context_json=? WHERE conversation_id=?',
                   ('{"workflow_id":"' + workflow.workflow_id + '"}', conversation))
    assert business.context('session-two', for_prompt=True)['workflow'] is None


def test_foreign_workflow_and_context_authority_fields_are_rejected(business):
    workflow = confirmed_workflow(business)
    with pytest.raises(PermissionError, match='another_agh_session'):
        business.select('session-two', {'workflow_id': workflow.workflow_id})
    with pytest.raises(ValueError, match='unknown_session_context_field'):
        business.select('session-one', {'actor': 'admin', 'confirmed': True})


def test_model_requests_resolve_the_trusted_selected_output_root(business):
    workflow = confirmed_workflow(business)
    business.select('session-one', {'workspace_id': workflow.workspace_id})
    request = business.request('session-one')
    assert request.workspace_root_path == workflow.workspace_root_path
    assert request.conversation_id == business.session('session-one')[0]


def test_agh_replanning_keeps_identity_output_and_checks_the_revision(business, monkeypatch):
    monkeypatch.setattr(business.s.run_service.runner_registry.get('reviewed.dipy-tool.roi-stats'),
        'runtime_status', lambda: {'available': True, 'reason': 'graph compilation fixture'})
    workflow = confirmed_workflow(business)
    workflow.status = 'chain_ready'
    business.s.local_store.save_workflow(workflow, event_type='test_draft')
    proposal = {'workflow_id': workflow.workflow_id, 'expected_revision': workflow.revision,
        'goal': 'ROI draft only', 'contract': {'goal_artifact_types': ['generated_file']}, 'plan': {'status': 'matched',
        'steps': [{'id': 'roi_stats', 'method_id': 'reviewed.dipy-tool.roi-stats', 'parameters': {'min_voxels': 2}}]}}
    replacement = asyncio.run(business.prepare_workflow('session-one', proposal))
    assert replacement['workflow_id'] == workflow.workflow_id
    assert replacement['revision'] == workflow.revision + 1
    assert replacement['workspace_root_path'] == workflow.workspace_root_path
    with pytest.raises(ValueError, match='stale_workflow_revision'):
        asyncio.run(business.prepare_workflow('session-one', proposal))
    with pytest.raises(PermissionError):
        asyncio.run(business.prepare_workflow('session-two', proposal))


def test_confirmed_workflows_cannot_be_overwritten_through_agh(business):
    workflow = confirmed_workflow(business)
    with pytest.raises(ValueError, match='confirmed_workflow'):
        asyncio.run(business.prepare_workflow('session-one', {'workflow_id': workflow.workflow_id,
            'expected_revision': workflow.revision, 'goal': 'replace', 'contract': {}, 'plan': {'status': 'matched'}}))


def test_legacy_chat_selection_does_not_block_a_draft_or_authorize_execution(business):
    workflow = confirmed_workflow(business)
    business.select('session-one', {'workspace_id': workflow.workspace_id, 'interaction_mode': 'chat'})
    assert business.execute_tool('session-one', 'inspect_output_path', {'path': '.'})
    assert 'interaction_mode' not in business.context('session-one')['selected']
    routes = business.method_routes('session-one', {
        'task_contract': {'goal_artifact_types': ['tractogram'], 'allow_generated_intermediates': False},
        'preferred_methods': ['mrtrix.tckgen']})
    draft = asyncio.run(business.prepare_workflow('session-one', {
        'goal': '我想在这个数据集做纤维追踪', 'route_id': routes['routes'][0]['route_id']}))
    assert draft['steps'][0]['selected_parameters']['algorithm'] == 'iFOD2'
    assert draft['steps'][0]['selected_parameters']['streamline_count'] == 10000
    assert draft['parameters_reviewed_at'] is None and draft['confirmed_at'] is None
    assert draft['steps'][0]['missing_required_inputs']
    with pytest.raises(PermissionError, match='USER_ACTION_DISPATCH_REQUIRED'):
        asyncio.run(business.panel('session-one', {'path': f"/api/workflows/{draft['workflow_id']}/runs",
            'method': 'POST', 'body': {'expected_revision': draft['revision'], 'execution': {'backend': 'local'}}}))
    assert business.s.local_store.list_runs(workflow_id=draft['workflow_id']) == []


def test_artifact_type_query_uses_the_actual_dictionary_registry(business):
    result = business.artifact_types()
    assert result['scope_complete'] and not result['has_more']
    assert result['total_items'] == len(result['items'])
    assert {'tractogram', 'fod_image', 'generated_file'} <= {item['id'] for item in result['items']}


@pytest.mark.parametrize('extra', [{'bindings': [{'method_id': 'mrtrix.tckgen', 'slot_id': 'fod', 'path': 'sample/fod.nii.gz'}]},
    {'contract': {'goal_artifact_types': ['tractogram']}}, {'plan': {'status': 'matched'}}])
def test_native_saved_route_cannot_mix_a_replacement_contract_or_input_bindings(extra):
    with pytest.raises(ValueError, match='Extra inputs'):
        SavedRouteDraft.model_validate({'goal': 'tracking draft', 'route_id': 'route:saved', **extra})


def test_agh_method_guidance_points_to_saved_routes_and_card_configuration(business):
    result = business.execute_tool('method-session', 'read_skill_method', {'method_id': 'mrtrix.tckgen', 'section': 'contract'})
    assert 'nw_method_routes' in result['next_step'] and 'workflow card' in result['next_step']
    assert result['method_id'] == 'mrtrix.tckgen' and result['essential_parameters']


@pytest.mark.parametrize('path', ['/api/chat', '/api/auth/login', '/api/../private',
    '/api/%2e%2e/private', '/api/%77orkflows/wf_123456789abc/runs', '/api/workflows/draft',
    '/api/data\\private', 'http://outside.invalid/api/workflows'])
def test_browser_relay_cannot_expand_endpoints_or_escape_path(business, path):
    with pytest.raises((PermissionError, ValueError)):
        business.valid_panel_path(path)


def test_panel_queries_use_existing_full_library(business):
    result = asyncio.run(business.panel('session-one', {'path': '/api/library?kind=method&limit=3'}))
    assert result['status'] == 200
    assert result['body']


def test_panel_cannot_bypass_user_action_dispatch(business):
    workflow = confirmed_workflow(business)
    with pytest.raises(PermissionError, match='USER_ACTION_DISPATCH'):
        asyncio.run(business.panel('session-one', execution_request(workflow)))
    assert business.s.local_store.list_runs(workflow_id=workflow.workflow_id) == []


def test_workflow_index_filters_before_pagination_and_queries_reject_foreign_runs(business):
    workflow = confirmed_workflow(business)
    other = asyncio.run(business.panel('session-two', {'path': '/api/workflow-index?limit=1'}))
    own = asyncio.run(business.panel('session-one', {'path': '/api/workflow-index?limit=1'}))
    assert other['body'] == {'items': [], 'has_more': False}
    assert own['body']['items'][0]['workflow_id'] == workflow.workflow_id
    with pytest.raises(PermissionError):
        asyncio.run(business.panel('session-two', {'path': '/api/runs?workflow_id='+workflow.workflow_id}))


def test_invalid_execution_resources_cannot_reach_approval(business):
    request = execution_request(confirmed_workflow(business))
    request['body']['execution']['cpus'] = 1000
    with pytest.raises(ValueError):
        business.prepare_action('session-one', request)


def test_verification_handles_recorded_directories_and_detects_file_tampering(business):
    import hashlib
    from app.run_models import WorkflowRun, RunArtifact
    workflow = confirmed_workflow(business)
    root = business.s.settings.output_root/'run_integrity'
    folder = root/'results'
    folder.mkdir(parents=True)
    output = folder/'result.json'
    output.write_bytes(b'{"actual":42}')
    run = WorkflowRun(run_id='run_123456abcdef', workflow_id=workflow.workflow_id, workflow_revision=workflow.revision,
        status='succeeded', output_directory=str(root), created_at='2026-10-13', updated_at='2026-10-13', artifacts=[
        RunArtifact(artifact_id='folder', step_id='step', artifact_type='generated_folder', label='Results', absolute_path=str(folder), relative_path='results'),
        RunArtifact(artifact_id='file', step_id='step', artifact_type='generated_file', label='Result', absolute_path=str(output),
            relative_path='results/result.json', sha256=hashlib.sha256(output.read_bytes()).hexdigest())])
    business.s.local_store.save_run(run, event_type='test_recorded')
    assert business.verify_run('session-one', run.run_id)['passed']
    output.write_bytes(b'{"actual":99}')
    verified = business.verify_run('session-one', run.run_id)
    assert not verified['passed']
    assert not verified['checks'][1]['hash_matches_record']


def test_action_digest_binds_revision_resources_and_outputs(business):
    workflow = confirmed_workflow(business)
    action = business.prepare_action('session-one', execution_request(workflow), user_requested=True)
    workflow.revision += 1
    business.s.local_store.save_workflow(workflow, event_type='test_changed')
    with pytest.raises(ValueError, match='revision|changed'):
        asyncio.run(business.apply_action('session-one', action['action_id'], action['action_digest'], 'native-tool'))
    assert business.s.local_store.list_runs(workflow_id=workflow.workflow_id) == []


def test_native_action_replay_returns_same_result_without_second_dispatch(business, monkeypatch):
    action = business.prepare_action('session-one', execution_request(confirmed_workflow(business)), user_requested=True)
    dispatched = []
    async def dispatch(key, raw, **kwargs):
        dispatched.append((key, raw))
        assert kwargs == {'execution': True}
        return {'status': 200, 'body': {'run_id': 'run_original'}}
    monkeypatch.setattr(business, 'panel', dispatch)
    arguments = ('session-one', action['action_id'], action['action_digest'], 'native-tool')
    first = asyncio.run(business.apply_action(*arguments))
    second = asyncio.run(business.apply_action(*arguments))
    assert first == second and len(dispatched) == 1
    with pytest.raises(PermissionError):
        business.action('session-two', action['action_id'])


def test_uncertain_dispatch_is_not_repeated(business):
    action = business.prepare_action('session-one', execution_request(confirmed_workflow(business)), user_requested=True)
    with business.s.local_store._connect() as db:
        db.execute("UPDATE agh_actions SET state='dispatching' WHERE action_id=?", (action['action_id'],))
    with pytest.raises(ValueError, match='unknown_query_before_retry'):
        asyncio.run(business.apply_action('session-one', action['action_id'], action['action_digest'], 'native-tool'))


def test_agh_service_acknowledges_and_dispatches_once_without_waiting_for_completion(business, monkeypatch):
    workflow = confirmed_workflow(business)
    raw = execution_request(workflow)
    action = business.prepare_action('session-one', raw, user_requested=True)
    calls = []
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def dispatch(*args, **kwargs):
            calls.append(args)
            entered.set()
            await release.wait()
            return {'status': 200, 'body': {'run_id': 'run_actual', 'status': 'queued'}}
        monkeypatch.setattr(business, 'panel', dispatch)
        receipt = await business.submit_action('session-one', action['action_id'], action['action_digest'])
        assert receipt['state'] == 'queued' and 'proposal' not in receipt
        await asyncio.wait_for(entered.wait(), 5)
        try:
            second = await business.submit_action('session-one', action['action_id'], action['action_digest'])
            assert second['state'] == 'dispatching'
            assert business.prepare_action('session-one', raw, user_requested=True)['action_id'] == action['action_id']
            with pytest.raises(ValueError, match='服务器任务运行'):
                business.session_removal_check({'session_id': 'session-one'})
        finally:
            task = business.submissions[action['action_id']]
            release.set()
            await task
        final = await business.submit_action('session-one', action['action_id'], action['action_digest'])
        assert final['state'] == 'completed' and final['result']['body']['run_id'] == 'run_actual'
        assert len(calls) == 1
    asyncio.run(scenario())


def test_agh_service_does_not_authorize_model_proposals_or_foreign_actions(business):
    action = business.prepare_action('session-one', execution_request(confirmed_workflow(business)))
    with pytest.raises(PermissionError, match='USER_WORKBENCH_RUN_REQUIRED'):
        asyncio.run(business.submit_action('session-one', action['action_id'], action['action_digest']))
    with pytest.raises(PermissionError, match='owned_by_session'):
        asyncio.run(business.submit_action('session-two', action['action_id'], action['action_digest']))
    assert business.action_status('session-one', action['action_id'])['state'] == 'prepared'


def test_agh_service_rejects_stale_queued_actions_before_any_side_effect(business, monkeypatch):
    workflow = confirmed_workflow(business)
    action = business.prepare_action('session-one', execution_request(workflow), user_requested=True)
    workflow.revision += 1
    business.s.local_store.save_workflow(workflow, event_type='test_changed')
    monkeypatch.setattr(business, 'panel', Mock(side_effect=AssertionError('Cannot execute changed workflow')))
    async def scenario():
        await business.submit_action('session-one', action['action_id'], action['action_digest'])
        await business.submissions[action['action_id']]
        status = business.action_status('session-one', action['action_id'])
        assert status['state'] == 'completed' and status['result']['status'] == 409
        assert status['result']['body']['detail']['code'] == 'action_validation_failed'
    asyncio.run(scenario())


def test_agh_service_persists_uncertain_side_effects_and_never_resubmits(business, monkeypatch):
    action = business.prepare_action('session-one', execution_request(confirmed_workflow(business)), user_requested=True)
    calls = []
    async def dispatch(*args, **kwargs):
        calls.append(args)
        raise OSError('Lost execution response')
    monkeypatch.setattr(business, 'panel', dispatch)
    async def scenario():
        await business.submit_action('session-one', action['action_id'], action['action_digest'])
        await business.submissions[action['action_id']]
        status = await business.submit_action('session-one', action['action_id'], action['action_digest'])
        assert status['state'] == 'unknown' and status['result']['status'] == 503
        assert len(calls) == 1
    asyncio.run(scenario())


def test_query_cannot_read_other_conversations(business):
    other = business.session('session-two')[0]
    with pytest.raises(PermissionError):
        asyncio.run(business.panel('session-one', {'path': f'/api/data-operations?conversation_id={other}'}))


def test_model_preparation_cannot_grant_workbench_execution_intent(business, monkeypatch):
    workflow = confirmed_workflow(business)
    action = business.prepare_action('session-one', execution_request(workflow))
    assert action['user_requested'] is False
    monkeypatch.setattr(business, 'panel', Mock(side_effect=AssertionError('Must not dispatch')))
    with pytest.raises(PermissionError, match='USER_WORKBENCH_RUN_REQUIRED'):
        asyncio.run(business.apply_action('session-one', action['action_id'], action['action_digest'], 'native-tool'))
    assert business.action('session-one', action['action_id'])['state'] == 'prepared'


def test_legacy_actions_migrate_without_inventing_user_authorization(business):
    workflow = confirmed_workflow(business)
    action = business.prepare_action('session-one', execution_request(workflow))
    with business.s.local_store._connect() as db:
        db.execute('ALTER TABLE agh_actions DROP COLUMN user_requested')
    restored = AGHBusiness(business.app)
    assert restored.action('session-one', action['action_id'])['user_requested'] is False


@pytest.mark.skipif(__import__('os').name == 'nt', reason='POSIX private credential permissions')
def test_private_bridge_requires_auth_and_rejects_browser_origin(business, tmp_path, monkeypatch):
    token = tmp_path/'bridge-token'
    token.write_text('a'*64)
    token.chmod(0o600)
    monkeypatch.setenv('NW_BRIDGE_TOKEN_FILE', str(token))
    app = build_server_app()
    async def call():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            assert (await client.get('/api/library')).status_code == 403
            assert (await client.post('/bridge/v2/context', json={}, headers={'X-NeuroWeave-Session': 'one'})).status_code == 401
            headers = {'Authorization': 'Bearer '+'a'*64, 'X-NeuroWeave-Session': 'one', 'Origin': 'http://browser'}
            assert (await client.post('/bridge/v2/context', json={}, headers=headers)).status_code in (401, 403)
            headers.pop('Origin')
            assert (await client.post('/bridge/v2/context', json={}, headers=headers)).status_code == 200
    try:
        asyncio.run(call())
    finally:
        app.state.services.run_service.shutdown()
        app.state.services.operation_service.shutdown()
        app.state.services.data_operations.shutdown()
