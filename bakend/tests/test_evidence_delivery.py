"""Stored data must reach a safe, manually runnable plan without rediscovery."""
# ruff: noqa: F811
import asyncio
import gzip
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.agent_claims import check_observations
from app.agent_delivery import current_gaps
from app.agent_reports import report_for
from app.agent_team import AgentTeam
from app.capability_registry import CapabilityRegistry
from app.evidence_fields import read_field
from app.goal_planner import GoalPlanner, RouteQuery, KnownInput, accepts_input, validate_evidence
from app.input_evidence import observed_inputs
from test_agent_team import TeamProvider, setup, goal
from test_data_operations import data as data
from test_goal_planner import query
from test_workflow_service import SKILLS_DIR


def image(path, count):
    header = bytearray(352)
    struct.pack_into('<i', header, 0, 348)
    struct.pack_into('<8h', header, 40, 4, 2, 2, 2, count, 1, 1, 1)
    header[344:348] = b'n+1\x00'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(header))
    path.with_name(path.name[:-7]+'.bval').write_text(' '.join(['0']+['1000']*(count-1)))
    path.with_name(path.name[:-7]+'.bvec').write_text('\n'.join(' '.join(['0']+['1']*(count-1)) for _ in range(3)))


def test_sequence_image_and_companions_are_reused_with_path_scoped_features(data):
    image(data.source/'sub-01/dwi/a.nii.gz', 4)
    image(data.source/'sub-01/dwi/b.nii.gz', 7)
    owner, req = setup(data, TeamProvider())
    team = AgentTeam(owner, req)
    observed = asyncio.run(team.query('data', 'inspect_data_sequences', {
        'source_scope': 'dataset', 'selection': {'path': 'sub-01/dwi'}, 'limit': 4}))
    calls = Mock(wraps=owner.execute)
    owner.execute = calls
    q = query(existing_inputs=[{'artifact_type': t, 'path': 'sub-01/dwi/a'+suffix,
        'fact_id': observed['fact_id'], 'features': features} for t, suffix, features in [
        ('preprocessed_dwi', '.nii.gz', {'volume_count': 4, 'shell_count': 1}),
        ('gradient_table', '.bval', {'volume_count': 4}),
        ('gradient_table', '.bvec', {'vector_count': 4})]])
    versions = validate_evidence(owner, team.ledger, req, q)
    assert set(versions) == {observed['fact_id']}
    assert all(i.features for i in q.existing_inputs)
    assert calls.call_count == 0  # No extra file query or model round to re-find a path.
    q.existing_inputs[0].features = {'volume_count': 7}
    validate_evidence(owner, team.ledger, req, q)
    assert q.existing_inputs[0].features == {} and q._input_notes
    q.existing_inputs[0].path = 'sub-01/dwi/not-observed.nii.gz'
    with pytest.raises(ValueError, match='尚未包含'):
        validate_evidence(owner, team.ledger, req, q)


def test_unknown_pairing_does_not_hide_an_observed_image_or_invent_missing_companions():
    row = {'tool': 'inspect_data_sequences', 'arguments': {'selection': {'path': 'sub-01/dwi'}}}
    raw = {'series': [{'status': 'unknown', 'image': 'sub-01/dwi/a.nii.gz',
        'header': {'volume_count': 4}, 'features': {'image': 'found', 'bval': 'missing'}}]}
    paths, records = observed_inputs(row, raw)
    assert 'sub-01/dwi/a.nii.gz' in paths and records['sub-01/dwi/a.nii.gz']
    assert 'sub-01/dwi' not in paths and not any(p.endswith('.bval') for p in paths)
    assert not observed_inputs(row, {'series': []})[0]


def response_query(labelled=True):
    q = query().model_dump()
    q['task_contract'].update(goal='使用已有响应函数计算FOD', goal_artifact_types=['fod_image'], allowed_existing_inputs=[])
    q['parameters'] = {'mrtrix.dwi2fod': {'algorithm': 'msmt_csd'}}
    q['existing_inputs'] += [{'artifact_type': 'response_functions', 'path': 'sub-01/dwi/'+role+'.txt',
        'slot_id': role if labelled else '', 'fact_id': 'fact:files'} for role in ['csf_response', 'gm_response', 'wm_response']]
    return RouteQuery.model_validate(q)


def test_external_same_type_inputs_keep_their_roles_and_do_not_regenerate_responses():
    registry = CapabilityRegistry(SKILLS_DIR)
    state, result = GoalPlanner(registry).search(response_query(), {})
    assert result['status'] == 'matched', result
    route = state['routes'][0]
    assert [s['method_id'] for s in route['steps']] == ['mrtrix.dwi2fod']
    bindings = {b['slot_id']: b for b in route['bindings']}
    for role in ['wm_response', 'gm_response', 'csf_response']:
        assert bindings[role]['path'].endswith(role+'.txt')
    assert {i['slot_id'] for i in result['routes'][0]['input_interpretations']} >= {
        'wm_response', 'gm_response', 'csf_response'}


def test_unlabelled_distinct_inputs_request_mapping_instead_of_choosing_wrong_response():
    _, result = GoalPlanner(CapabilityRegistry(SKILLS_DIR)).search(response_query(False), {})
    assert result['status'] == 'needs_facts'
    assert any(g['status'] == 'ambiguous' and '无需重复' in g['reason'] for g in result['gaps'])


def test_consumer_port_alias_does_not_prevent_reuse_of_a_unique_preprocessed_image():
    q = response_query()
    q.existing_inputs[0].slot_id = 'dwi4d'
    state, result = GoalPlanner(CapabilityRegistry(SKILLS_DIR)).search(q, {})
    assert result['status'] == 'matched'
    assert len(state['routes'][0]['steps']) == 1


def test_distinct_roles_survive_generic_image_ports_and_txt_gradient_containers():
    contract = query().task_contract
    contract.input_policy = 'unspecified'
    need = {'type': 'imaging_input', 'slot': 'target', 'formats': ['.nii.gz'], 'distinct_roles': ['source', 'target']}
    value = KnownInput(artifact_type='brain_mask', path='mask.nii.gz', slot_id='source')
    assert not accepts_input(value, need, contract)
    value.slot_id = 'target'
    assert accepts_input(value, need, contract)
    gradient = {'type': 'gradient_table', 'slot': 'bvecs', 'formats': ['.txt'], 'distinct_roles': ['bvecs', 'bvals']}
    value = KnownInput(artifact_type='b_values', path='gradient.txt')
    assert not accepts_input(value, gradient, contract)


def test_literal_dotted_keys_json_pointer_arrays_and_view_prefixes():
    source = {'pattern_counts': {'wm_response.txt': {'count': 499}}, 'series': [{'header': {'volume_count': 127}}]}
    for field, expected in [('/pattern_counts/wm_response.txt/count', 499),
                            ('pattern_counts.wm_response.txt.count', 499),
                            ('summary.series.0.header.volume_count', 127),
                            ('/series/0/header/volume_count', 127)]:
        result = check_observations([{'fact_id': 'fact:s', 'field': field, 'expected': expected}], {'fact:s': source})
        assert result[0]['passed'], result
    ambiguous = {'a.b': 1, 'a': {'b': 2}}
    with pytest.raises(KeyError):
        read_field(ambiguous, 'a.b')
    assert read_field(ambiguous, '/a.b') == 1 and read_field(ambiguous, '/a/b') == 2


def test_shared_fact_fields_read_array_and_literal_filename_from_the_original_source(data):
    owner, req = setup(data, TeamProvider())
    team = AgentTeam(owner, req)
    args = {'source_scope': 'dataset', 'selection': {'path': '.'}, 'patterns': ['*.txt']}
    raw = {'pattern_counts': {'wm_response.txt': {'count': 499}}, 'series': [{'image': 'a.nii.gz'}]}
    ref = team.ledger.record(req, 'query_data_files', args, raw)
    fact = team.facts.record(req, 'data', 'query_data_files', args, raw, ref, 'synthetic')
    assert team.facts.read(req, {'fact_id': fact['id'], 'field': '/pattern_counts/wm_response.txt/count'})['value'] == 499
    assert team.facts.read(req, {'fact_id': fact['id'], 'field': 'series.0.image'})['text'] == 'a.nii.gz'


def test_checks_supply_their_own_valid_refs_without_false_full_scope_confirmation():
    payload = {'answer': '数量已确认', 'unresolved': ['响应函数数量'], 'fact_ids': [], 'findings': [{
        'requirement_id': 'r1', 'status': 'confirmed', 'conclusion': '已观测499个响应函数', 'basis': 'observation',
        'checks': [{'fact_id': 'fact:r', 'field': '/pattern_counts/wm_response.txt/count', 'expected': 499}]}]}
    job = {'agent': 'data', 'question': '响应函数数量'}
    source = {'pattern_counts': {'wm_response.txt': {'count': 499}}, 'scope_complete': False}
    result = report_for(job, payload, {'fact:r'}, {'fact:r': source})
    assert result['assignment_complete'] and result['fact_ids'] == ['fact:r']
    payload['findings'][0]['checks'][0]['scope'] = 'all'
    result = report_for(job, payload, {'fact:r'}, {'fact:r': source})
    assert not result['assignment_complete'] and result['findings'][0]['status'] == 'partial'


def route_state():
    return {'task_id': 'task', 'method_planning': {'task_id': 'task', 'state': {'summary': {
        'status': 'matched', 'routes': [{'route_id': 'route:ready', 'method_ids': ['mrtrix.dwi2fod'], 'fact_ids': []}]}}}}


def test_gaps_use_latest_report_and_matched_graph_supersedes_discovery_questions():
    task = {'agent_jobs': {
        'old': {'agent': 'data', 'report': {'unresolved': ['路径尚未知']}},
        'new': {'agent': 'data', 'report': {'unresolved': ['覆盖尚待绑定']}},
        'method': {'agent': 'methods', 'report': {'unresolved': ['请复制所有契约参数']}},
        'runtime': {'agent': 'runtime', 'report': {'unresolved': ['软件路径未配置']}}}}
    assert '路径尚未知' not in current_gaps(task)
    task.update(route_state())
    assert current_gaps(task) == ['软件路径未配置']
    task['preparation_error'] = '没有共同满足必需输入的样本'
    assert current_gaps(task) == ['没有共同满足必需输入的样本']


def test_reserved_time_can_prepare_a_matched_route_and_review_it_without_execution(data, monkeypatch):
    def timeout(messages):
        raise TimeoutError()
    provider = TeamProvider(coordinator=[timeout, [('prepare_workflow', {'goal': '计算FOD', 'route_id': 'route:ready'})],
        [('finish_task', {'status': 'awaiting_run', 'summary': '流程已准备，等待手动运行', 'artifact_ids': ['wf-test']})]])
    owner, req = setup(data, provider)
    owner.workflow_service.get = lambda identity: SimpleNamespace(revision=1, title='test', steps=[], input_selection='user', contract_errors=[])
    team = AgentTeam(owner, req)
    team.ledger.update_goal(req, goal('workflow')[1])
    state = route_state()
    state['method_planning']['task_id'] = req.assistant_task['task_id']
    team.ledger.save(req, {**req.assistant_task, 'method_planning': state['method_planning']})
    async def execute(owner, ledger, request, name, args, observations):
        assert name == 'prepare_workflow' and args['route_id'] == 'route:ready'
        return {'id': 'wf-test', 'kind': 'workflow', 'status': 'pending', 'revision': 1, 'inputs_ready': True, 'missing_inputs': []}
    monkeypatch.setattr('app.agent_runtime.execute', execute)
    # This test substitutes preparation/storage; configuration itself is covered
    # by real-plan and runtime-policy tests, so keep the same substitution here.
    monkeypatch.setattr('app.agent_plan_config.attach_workflow_configuration', lambda *_: {
        'status':'configured', 'check':'configuration_only', 'execution_scope':'scientific_runner',
        'execution_verified':False, 'required_before_run':[]})
    team.compression.compact = AsyncMock()
    result = asyncio.run(team.run())
    assert result.assistant_task['phase'] == 'awaiting_run'
    assert provider.requests[-2][2] == {'prepare_workflow'} and provider.requests[-1][2] == {'finish_task'}
    assert not data.service.futures
    team.compression.compact.assert_not_awaited()
