"""Versioned type merge, stable summaries, relevance and final-report recovery."""
import asyncio
import json
from unittest.mock import AsyncMock

from app.agent_compression import ContextCompression
from app.agent_facts import SharedFacts, packed
from app.agent_team import AgentTeam
from app.fact_memory import identity
from test_agent_team import TeamProvider, setup, goal
from test_data_operations import data


def record(team, tool, args, result, version='v1', role='methods'):
    ref = team.ledger.record(team.request, tool, args, result)
    return team.facts.record(team.request, role, tool, args, result, ref, version)


def test_unchanged_fact_reuses_byte_identical_summary_after_observation_and_source_stamp_change(data):
    owner, req = setup(data, TeamProvider())
    team = AgentTeam(owner, req)
    args = {'path': '.'}
    result = {'file_count': 23, 'complete_scan': True, 'items': [{'path': 's/a.txt'}], 'snapshot_observed_at': 10}
    first = record(team, 'list_dataset_directory', args, result, role='data')
    before = team.facts.typed.read(req, identity(first)[1])
    second = record(team, 'list_dataset_directory', args, {**result, 'snapshot_observed_at': 99}, version='v2', role='data')
    after = team.facts.typed.read(req, identity(second)[1])
    assert before['content_hash'] == after['content_hash'] and after['summary_reused']
    assert packed(before['summary']) == packed(after['summary'])
    assert first['reference'] != second['reference']
    changed = record(team, 'list_dataset_directory', args, {**result, 'file_count': 24}, role='data')
    assert team.facts.typed.read(req, identity(changed)[1])['content_hash'] != after['content_hash']


def test_overlapping_searches_merge_by_method_id_without_rewriting_unchanged_summary(data):
    owner, req = setup(data, TeamProvider()); team = AgentTeam(owner, req)
    a = {'method_id': 'm.a', 'description': 'x'*8000, 'role': 'x'*8000}
    b = {'method_id': 'm.b', 'description': 'second'}
    first = record(team, 'list_skill_methods', {'q': 'first'}, {'items': [a, b]})
    entity = team.facts.typed.read(req, identity(first)[1]); summary = packed(entity['summary'])
    second = record(team, 'list_skill_methods', {'q': 'other synonyms'}, {'items': [a]})
    current = team.facts.typed.read(req, identity(second)[1])
    assert current['content']['candidates'].keys() == {'m.a', 'm.b'}
    assert current['summary_reused'] and packed(current['summary']) == summary
    assert len(current['fact_ids']) == 2 and len(summary) < 1200
    assert team.facts.raw_result(req, first)[0]['items'][0]['description'] == 'x'*8000
    replaced = record(team, 'list_skill_methods', {'q': 'new version'}, {'items': [b]}, version='v2')
    assert list(team.facts.typed.read(req, identity(replaced)[1])['content']['candidates']) == ['m.b']


def test_file_pages_merge_unique_paths_only_inside_one_snapshot(data):
    owner, req = setup(data, TeamProvider()); team = AgentTeam(owner, req)
    first = record(team, 'query_data_files', {'source_scope': 'dataset', 'offset': 0},
        {'index_id': 'one', 'complete_scan': True, 'items': [{'path': 's/a'}, {'path': 's/b'}]}, role='data')
    second = record(team, 'query_data_files', {'source_scope': 'dataset', 'offset': 1},
        {'index_id': 'one', 'complete_scan': True, 'items': [{'path': 's/b'}, {'path': 's/c'}]}, role='data')
    entity = team.facts.typed.read(req, identity(second)[1])
    assert entity['content']['items'].keys() == {'s/a', 's/b', 's/c'}
    next_row = record(team, 'query_data_files', {'source_scope': 'dataset', 'offset': 0},
        {'index_id': 'two', 'complete_scan': False, 'items': [{'path': 's/d'}]}, role='data')
    assert list(team.facts.typed.read(req, identity(next_row)[1])['content']['items']) == ['s/d']


def test_method_parameter_page_remains_a_detail_fact_with_its_exact_items(data):
    from app.capability_registry import CapabilityRegistry
    from app.skill_catalog import SkillCatalog
    from app.skill_loader import SkillLoader
    from pathlib import Path
    from types import SimpleNamespace
    root = Path(__file__).resolve().parents[2]
    owner, req = setup(data, TeamProvider())
    owner.skill_catalog = SkillCatalog(SkillLoader(root/'skills'), CapabilityRegistry(root/'skills'), SimpleNamespace(get=lambda _: None))
    team = AgentTeam(owner, req)
    result = asyncio.run(team.query('methods', 'read_skill_method', {'method_id': 'mrtrix.command.mrconvert', 'section': 'parameters', 'limit': 2}))
    assert len(result['items']) == 2 and result['has_more']
    assert team.facts.typed.read(req, result['entity_id'])['fact_type'] == 'methods.reference'


def test_revisiting_inventory_page_does_not_rewrite_merged_summary(data):
    owner, req = setup(data, TeamProvider()); team = AgentTeam(owner, req)
    base = {'index_id': 'one', 'complete_scan': True, 'file_count': 2}
    groups = [{'id': 'a', 'count': 1}, {'id': 'b', 'count': 1}]
    def save(offset):
        return record(team, 'inventory_data_files', {'source_scope': 'dataset', 'view': 'summary', 'group_offset': offset, 'limit': 1},
            {**base, 'coverage': {'group_count': 2, 'groups': [groups[offset]], 'group_offset': offset,
                'summarized_file_count': 1}, 'items': [{'path': str(offset)}]}, role='data')
    save(0); second = save(1)
    before = team.facts.typed.read(req, identity(second)[1])
    repeated = save(0)
    after = team.facts.typed.read(req, identity(repeated)[1])
    assert before['content_hash'] == after['content_hash'] and after['summary_reused']
    assert packed(before['summary']) == packed(after['summary'])


def test_pipeline_prose_compression_cannot_change_structured_default_values(data):
    owner, req = setup(data, TeamProvider()); team = AgentTeam(owner, req)
    default = {'description': 'x'*1500, 'enabled': True}
    raw = {'pipeline_id': 'p', 'description': 'manual '*1000,
        'parameters': [{'id': 'payload', 'description': 'help '*1000, 'default': default}],
        'steps': [{'id': 'step', 'parameters': {'payload': default}}]}
    row = record(team, 'read_skill_pipeline', {'pipeline_id': 'p'}, raw)
    summary = team.facts.typed.read(req, identity(row)[1])['summary']['structured']
    assert summary['parameters'][0]['default'] == default
    assert summary['steps'][0]['parameters']['payload'] == default
    assert len(summary['description']) == 180


def test_context_keeps_data_and_selected_method_ahead_of_recent_auxiliary_contracts(data):
    owner, req = setup(data, TeamProvider()); team = AgentTeam(owner, req)
    primary = record(team, 'read_skill_method', {'method_id': 'main'}, {'method_id': 'main',
        'input_slots': [{'id': 'image', 'required_when': {'mode': ['x']}}],
        'essential_parameters': [{'id': 'mode', 'options': [{'value': 'x'}, {'value': 'y'}]}]})
    datum = record(team, 'query_data_files', {'source_scope': 'dataset'},
        {'complete_scan': True, 'pattern_counts': {'image': {'count': 44}}, 'items': []}, role='data')
    for i in range(12):
        record(team, 'read_skill_method', {'method_id': f'aux{i}'}, {'method_id': f'aux{i}',
            'input_slots': [{'id': 'x'*500}], 'description': 'manual '*600})
    req.assistant_task['method_selection'] = {'method_ids': ['main']}
    context = team.facts.context(req, budget=4300)
    included = {r['id'] for r in context['facts']}
    assert primary['id'] in included and datum['id'] in included
    selected = next(r for r in context['facts'] if r['id'] == primary['id'])
    assert selected['summary']['input_slots'][0]['required_when'] == {'mode': ['x']}
    assert selected['summary']['essential_parameters'][0]['options'] == [{'value': 'x'}, {'value': 'y'}]


def test_model_description_summary_is_cached_across_new_agent_and_not_used_as_authority(data):
    provider = TeamProvider(); provider.json_response = AsyncMock(return_value=({'notes': ['仅描述适用范围']}, []))
    owner, req = setup(data, provider); team = AgentTeam(owner, req)
    args = {'method_id': 'm'}; result = {'method_id': 'm', 'description': 'long manual '*500,
        'input_slots': [{'id': 'dwi', 'required': True}]}
    row = record(team, 'read_skill_method', args, result)
    asyncio.run(team.compression.summarize_facts(team.facts, 'methods', remaining_seconds=100))
    first = team.facts.typed.read(req, identity(row)[1])
    record(team, 'read_skill_method', args, result)
    restarted = AgentTeam(owner, req)
    asyncio.run(restarted.compression.summarize_facts(restarted.facts, 'methods', remaining_seconds=100))
    assert provider.json_response.await_count == 1
    assert restarted.facts.typed.read(req, identity(row)[1])['summary'] == first['summary']
    assert restarted.facts.read(req, {'fact_id': row['id'], 'field': 'input_slots'})['items'][0]['required']
    with __import__('pytest').raises(ValueError):
        restarted.facts.typed.read(req.model_copy(update={'dataset_id': None}), first['entity_id'])


def test_final_specialist_batch_retries_truncation_without_more_queries_or_losing_report(data):
    (data.output/'a.txt').write_text('original')
    def final(messages):
        fact_id = next(json.loads(m['content'])['fact_id'] for m in messages if m['role'] == 'tool')
        return [('finish_agent', {'answer': '属性已核实', 'fact_ids': [fact_id], 'unresolved': [],
            'findings': [{'requirement_id': 'r1', 'status': 'confirmed', 'conclusion': '文件存在',
                'basis': 'observation', 'fact_ids': [fact_id],
                'checks': [{'fact_id': fact_id, 'field': 'type', 'expected': 'file'}]}]})]
    provider = TeamProvider(data=[[('inspect_output_path', {'path': 'a.txt'})], '继续', '继续', final])
    original = provider.workspace_completion; count = 0
    async def output(messages, tools, **kwargs):
        nonlocal count
        count += 1
        if count == 4:
            provider.requests.append(('data', __import__('copy').deepcopy(messages), {t['function']['name'] for t in tools}))
            return {'finish_reason': 'length', 'tool_calls': [{'function': {'name': 'finish_agent', 'arguments': '{partial'}}]}
        return await original(messages, tools, **kwargs)
    provider.workspace_completion = output
    owner, req = setup(data, provider); team = AgentTeam(owner, req)
    team.ledger.update_goal(req, goal()[1])
    report = asyncio.run(team.delegate({'agent': 'data', 'question': '核实文件'}))
    assert report['assignment_complete'] and count == 5
    assert provider.requests[-1][2] == {'finish_agent'}
    assert team.generations['data'].total == 1 and team.generations['data'].consecutive == 0
    assert (data.output/'a.txt').read_text() == 'original' and not data.service.futures


def test_next_specialist_round_receives_current_typed_facts_without_requery(data):
    (data.output/'a.txt').write_text('original')
    def finish(messages):
        state = json.loads(messages[1]['content'])
        assert state['existing_facts']['facts'][0]['summary']['exists']
        ref = state['existing_facts']['facts'][0]['id']
        return [('finish_agent', {'answer': '已核实', 'fact_ids': [ref], 'unresolved': [],
            'findings': [{'requirement_id': 'r1', 'status': 'confirmed', 'conclusion': '存在', 'basis': 'observation',
                'fact_ids': [ref], 'checks': [{'fact_id': ref, 'field': 'exists', 'expected': True}]}]})]
    provider = TeamProvider(data=[[('inspect_output_path', {'path': 'a.txt'})], finish])
    owner, req = setup(data, provider); team = AgentTeam(owner, req)
    team.ledger.update_goal(req, goal()[1])
    assert asyncio.run(team.delegate({'agent': 'data', 'question': '核实文件'}))['assignment_complete']
    assert len(provider.requests) == 2


def test_type_catalog_retains_all_identifiers_in_delivery_and_durable_context(data):
    owner, req = setup(data, TeamProvider()); team = AgentTeam(owner, req)
    raw = {'items': [{'id': f'type{i}', 'label': f'Type {i}', 'description': 'manual '*200}
                     for i in range(39)], 'total_items': 39, 'scope_complete': True, 'has_more': False}
    row = record(team, 'list_workflow_artifact_types', {}, raw)
    entity = team.facts.typed.read(req, identity(row)[1])
    expected = {v['id'] for v in raw['items']}
    delivered = team.deliver_fact(entity, raw)
    assert {v['id'] for v in delivered['items']} == expected
    assert 'sample_only' not in delivered and delivered['scope_complete']
    assert all(len(v['description']) <= 160 for v in delivered['items'])
    context = team.facts.context(req, 'methods')
    assert {v['id'] for v in context['facts'][0]['summary']['items']} == expected
    restarted = AgentTeam(owner, req)
    assert restarted.facts.context(req, 'methods')['facts'][0]['summary'] == context['facts'][0]['summary']
    assert team.facts.raw_result(req, row)[0] == raw

    # Previously stored representative projections must hydrate from exact
    # evidence without contacting the source catalog again.
    with team.facts.store.transaction() as db:
        entity['content'].pop('catalog_kind')
        entity['summary']['structured'] = {'sample_items': raw['items'][:3], 'sample_only': True}
        db.execute('UPDATE assistant_fact_entities SET payload=? WHERE entity_id=?',
                   (packed(entity), entity['entity_id']))
    migrated = AgentTeam(owner, req)
    assert {v['id'] for v in migrated.facts.context(req, 'methods')['facts'][0]['summary']['items']} == expected
