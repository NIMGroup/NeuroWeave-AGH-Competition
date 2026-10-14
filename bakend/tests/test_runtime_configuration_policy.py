"""Plan-dependent configuration, scoped caches and delivery without false runtime claims."""
# ruff: noqa: F401, F811 -- imported pytest fixture is resolved by parameter name.
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.agent_artifacts import guidance
from app.agent_delivery import current_gaps
from app.agent_execution_config import ConfigurationIdentityError, configuration_result, configuration_view, method_configuration, workflow_configuration
from app.agent_runtime_policy import RuntimeDependencyError, infer_dependencies, investigation_scope, runtime_tools
from app.agent_team import AgentTeam
from app.fact_memory import fact_type
from test_agent_team import TeamProvider, goal, setup
from test_data_operations import data


def science(team, owner, methods=('real.method',), executable='/configured/software'):
    team.ledger.update_goal(team.request, goal('workflow')[1])
    runner = SimpleNamespace(executable=Path(executable),
        runtime_status=Mock(side_effect=AssertionError('No software probes during planning')))
    catalog = {mid: SimpleNamespace(id=mid, runner_id='runner.' + mid, execution_mode='runner') for mid in methods}
    owner.skill_catalog = SimpleNamespace(registry=SimpleNamespace(method_for=lambda _, mid: catalog.get(mid)),
        runners=SimpleNamespace(get=lambda _: runner))
    team.ledger.save(team.request, {**team.request.assistant_task, 'method_selection': {
        'method_ids': list(methods), 'task_id': team.request.assistant_task['task_id']}})
    return runner


def test_workflow_environment_without_selected_methods_never_starts_agent(data):
    provider = TeamProvider()
    owner, req = setup(data, provider)
    team = AgentTeam(owner, req)
    team.ledger.update_goal(req, goal('workflow')[1])
    with pytest.raises(RuntimeDependencyError, match='先选择真实方法'):
        asyncio.run(team.delegate({'agent': 'runtime', 'question': '检查所有运行环境'}))
    assert not provider.requests and not req.assistant_task.get('agent_jobs')


def test_file_changes_do_not_expose_environment_tools(data):
    owner, req = setup(data, TeamProvider())
    team = AgentTeam(owner, req)
    team.ledger.update_goal(req, goal()[1])
    tools = runtime_tools(copy.deepcopy(team.tools), req.assistant_task, 'execution_result')
    assert not {'read_execution_configuration', 'list_data_environments'} & {t['function']['name'] for t in tools}
    with pytest.raises(RuntimeDependencyError, match='尚无 Python'):
        asyncio.run(team.query('runtime', 'list_data_environments', {}))
    assert not data.service.futures and not team.facts.rows(req)


def test_scientific_python_never_uses_data_sandbox_quotas(data):
    owner, req = setup(data, TeamProvider())
    team = AgentTeam(owner, req)
    runner = science(team, owner, executable='/science/bin/python3')
    result = asyncio.run(team.query('runtime', 'read_execution_configuration', {'method_ids': ['real.method']}))
    assert result['execution_scope'] == 'scientific_runner' and result['items'][0]['python_environment_needed']
    assert 'resource_profiles' not in result
    with pytest.raises(RuntimeDependencyError):
        asyncio.run(team.query('runtime', 'list_data_environments', {}))
    assert fact_type('read_execution_configuration') != fact_type('list_data_environments')
    runner.runtime_status.assert_not_called()


def test_scientific_queries_cannot_expand_beyond_selected_methods(data):
    owner, req = setup(data, TeamProvider())
    team = AgentTeam(owner, req)
    science(team, owner)
    with pytest.raises(RuntimeDependencyError, match='仅限当前已选方法'):
        asyncio.run(team.query('runtime', 'read_execution_configuration', {'method_ids': ['guessed.method']}))
    assert not team.facts.rows(req)


def test_configuration_cache_survives_turns_and_invalidates_on_path_or_catalog_changes(data):
    owner, req = setup(data, TeamProvider())
    team = AgentTeam(owner, req)
    runner = science(team, owner, methods=('b', 'a'))
    first = asyncio.run(team.query('runtime', 'read_execution_configuration', {'method_ids': ['b', 'a']}))
    repeat = asyncio.run(team.query('runtime', 'read_execution_configuration', {'method_ids': ['a', 'b', 'a']}))
    assert repeat['reused'] and first['fact_id'] == repeat['fact_id']
    restarted = AgentTeam(owner, req)
    cached = asyncio.run(restarted.query('runtime', 'read_execution_configuration', {'method_ids': ['a', 'b']}))
    assert cached['reused'] and cached['configuration_version'] == first['configuration_version']
    entities = restarted.facts.typed.rows(req, 'runtime')
    previous = entities[0]['summary']
    assert len(entities) == 1
    assert previous == team.facts.typed.rows(req, 'runtime')[0]['summary']
    runner.executable = Path('.')
    changed = asyncio.run(restarted.query('runtime', 'read_execution_configuration', {'method_ids': ['a', 'b']}))
    assert not changed.get('reused') and changed['status'] == 'missing_configuration'
    assert changed['configuration_version'] != first['configuration_version']
    owner.skill_catalog.registry.method_for = lambda *_: None
    with pytest.raises(ConfigurationIdentityError):
        asyncio.run(restarted.query('runtime', 'read_execution_configuration', {'method_ids': ['a', 'b']}))
    runner.runtime_status.assert_not_called()


def test_script_environment_cache_tracks_image_and_packages_but_live_results_are_not_cached(data):
    owner, req = setup(data, TeamProvider())
    team = AgentTeam(owner, req)
    team.ledger.update_goal(req, {**goal('report')[1], 'execution_kind': 'python_script'})
    first = asyncio.run(team.query('runtime', 'list_data_environments', {}))
    repeat = asyncio.run(team.query('runtime', 'list_data_environments', {}))
    assert repeat['reused'] and first['execution_scope'] == 'data_script_sandbox'
    data.service.sandbox.image = 'sha256:' + 'f' * 64
    changed = asyncio.run(team.query('runtime', 'list_data_environments', {}))
    assert not changed.get('reused') and changed['configuration_version'] != first['configuration_version']
    data.service.sandbox.environments.configured['table'] = {'image': 'sha256:' + 'a'*64, 'packages': {'pandas': '3'}}
    libraries = asyncio.run(team.query('runtime', 'list_data_environments', {}))
    assert not libraries.get('reused') and libraries['configuration_version'] != changed['configuration_version']
    assert team.facts.stamp(owner, req, 'get_workflow_result', {}) is None
    assert team.facts.stamp(owner, req, 'list_data_operations', {}) is None
    assert not data.service.futures


def test_explicit_environment_question_remains_read_only_and_stops_after_invalid_identity(data):
    owner, req = setup(data, TeamProvider())
    team = AgentTeam(owner, req)
    owner.skill_catalog = SimpleNamespace(registry=SimpleNamespace(method_for=Mock(return_value=None)), runners=None)
    team.runtime_scope = 'environment_question'
    assert asyncio.run(team.query('runtime', 'list_data_environments', {}))['execution_scope'] == 'data_script_sandbox'
    with pytest.raises(ConfigurationIdentityError):
        asyncio.run(team.query('runtime', 'read_execution_configuration', {'method_ids': ['guess']}))
    count = owner.skill_catalog.registry.method_for.call_count
    with pytest.raises(ValueError, match='不能换名称'):
        asyncio.run(team.query('runtime', 'read_execution_configuration', {'method_ids': ['another-guess']}))
    assert count == owner.skill_catalog.registry.method_for.call_count and not data.service.futures


def test_runtime_configuration_waits_for_methods_while_execution_feedback_remains_independent():
    task = {'deliverable': 'workflow', 'task_id': 't'}
    jobs = [{'agent': 'runtime'}, {'agent': 'methods', 'depends_on': ['data']}, {'agent': 'data'},
            {'agent': 'runtime', 'runtime_scope': 'execution_result'}]
    infer_dependencies(task, jobs)
    assert jobs[0]['depends_on'] == ['methods'] and 'depends_on' not in jobs[2] and 'depends_on' not in jobs[3]
    task['method_selection'] = {'task_id': 't', 'method_ids': ['known']}
    independent = [{'agent': 'runtime'}, {'agent': 'methods'}]
    infer_dependencies(task, independent)
    assert not independent[0].get('depends_on')
    feedback = {'deliverable':'workflow','phase':'verifying'}
    assert investigation_scope(feedback, {}) == 'execution_result'
    retry = [{'agent':'runtime'}, {'agent':'methods'}]
    infer_dependencies(feedback, retry)
    assert 'depends_on' not in retry[0]


def test_selected_configuration_is_answered_without_specialist_model(data):
    provider = TeamProvider()
    owner, req = setup(data, provider)
    team = AgentTeam(owner, req)
    runner = science(team, owner)
    result = asyncio.run(team.delegate({'agent': 'runtime', 'question': '检查必要方法配置和镜像资源'}))
    assert not provider.requests and result['configuration']['status'] == 'configured'
    assert result['assignment_complete'] is False and result['unchecked_requirements']
    assert result['fact_ids'] and not result['unresolved']
    runner.runtime_status.assert_not_called()


def test_large_selected_configuration_uses_complete_saved_items_not_model_samples(data):
    owner, req = setup(data, TeamProvider())
    team = AgentTeam(owner, req)
    ids = tuple('method.'+str(i) for i in range(20))
    runner = science(team, owner, methods=ids)
    runner.spec = {'modules':['module.'+str(i) for i in range(60)]}
    owner.skill_catalog.runners.get = lambda mid: None if mid == 'runner.method.19' else runner
    result = asyncio.run(team.delegate({'agent':'runtime','question':'检查所选方法配置'}))
    assert result['configuration']['total_items'] == 20
    assert result['configuration']['status'] == 'missing_configuration'
    assert result['configuration']['required_before_run'][0]['method_id'] == 'method.19'
    assert not result['configuration']['items_complete'] and len(result['configuration']['items']) == 6
    row = next(r for r in team.facts.rows(req) if r['id'] == result['fact_ids'][0])
    raw = team.facts.raw_result(req, row)[0]
    assert {i['method_id'] for i in raw['items']} == set(ids)
    assert not configuration_view(result['configuration'])['items_complete']


def test_explicit_environment_question_keeps_pending_scientific_goal(data):
    owner, req = setup(data, TeamProvider())
    team = AgentTeam(owner, req)
    science(team, owner)
    team.runtime_scope = 'environment_question'
    result = asyncio.run(team.query('runtime', 'list_data_environments', {}))
    assert result['execution_scope'] == 'data_script_sandbox'
    assert req.assistant_task['deliverable'] == 'workflow'


def test_configuration_status_does_not_claim_available_software_and_internal_runners_need_no_path():
    method = SimpleNamespace(id='builtin', runner_id='builtin', execution_mode='runner')
    runner = SimpleNamespace(runtime_status=Mock(side_effect=AssertionError('no probe')))
    result = method_configuration('builtin', method, SimpleNamespace(get=lambda _: runner))
    assert result['status'] == 'configured' and not result['path_required']
    assert 'available' not in result
    runner.runtime_status.assert_not_called()


def test_recipe_configuration_uses_actual_executable_units_not_only_pipeline_wrapper(monkeypatch):
    from app.execution.recipe_compiler import RecipeCompiler
    methods = [SimpleNamespace(id='internal.'+n, runner_id='runner.'+n, execution_mode='runner') for n in ('a','b')]
    runner = SimpleNamespace(executable=Path('/configured/python'))
    owner = SimpleNamespace(skill_catalog=SimpleNamespace(registry=object(), runners=SimpleNamespace(get=lambda _: runner)))
    monkeypatch.setattr(RecipeCompiler, 'compile', lambda *_: [SimpleNamespace(method=m) for m in methods])
    result = workflow_configuration(owner, object())
    assert {i['method_id'] for i in result['items']} == {'internal.a','internal.b'}
    assert result['status'] == 'configured' and not result['execution_verified']


def test_recipe_wrapper_is_unknown_until_resolved_without_claiming_runner_missing():
    method = SimpleNamespace(runner_id='wrapper', execution_mode='runner', execution_recipe=object())
    item = method_configuration('composite', method, SimpleNamespace(get=lambda _: None))
    result = configuration_result([item])
    assert result['status'] == 'unknown' and result['required_before_run']
    assert 'Runner 尚未登记' not in item['reason']


def test_missing_configuration_delivers_draft_with_configuration_action_and_only_current_gaps():
    task = {'task_id': 't', 'deliverable': 'workflow', 'agent_jobs': {
        'old': {'agent':'runtime', 'runtime_scope':'scientific_configuration', 'updated_at':1,
                'report':{'unresolved':['未知镜像', '假设的沙箱配额不足']}}},
        'execution_configuration':{'task_id':'t', 'status':'missing_configuration',
                                   'required_before_run':[{'method_id':'m','reason':'必要软件路径未配置'}]}}
    assert current_gaps(task) == ['运行前配置：必要软件路径未配置']
    result = {'kind':'workflow','status':'pending','execution_configuration':task['execution_configuration']}
    assert guidance(result)['action'] == 'configure_execution'
    task['execution_configuration'].update(status='configured', required_before_run=[])
    assert current_gaps(task) == [] and guidance(result)['action'] == 'run_workflow'
    task['agent_jobs']['new'] = {'agent':'runtime', 'runtime_scope':'execution_result', 'updated_at':2,
                                'report':{'unresolved':['本次实际运行失败，需要检查日志']}}
    assert current_gaps(task) == ['本次实际运行失败，需要检查日志']


def test_old_runtime_report_is_excluded_from_current_model_context_when_plan_configuration_exists(data):
    owner, req = setup(data, TeamProvider())
    team = AgentTeam(owner, req)
    science(team, owner)
    team.ledger.save(req, {**req.assistant_task, 'agent_jobs': {'old': {'id':'old', 'agent':'runtime',
        'purpose':'initial', 'updated_at':1, 'status':'partial', 'report':{'unresolved':['未知镜像']}}},
        'execution_configuration':{'task_id':req.assistant_task['task_id'], 'status':'configured'}})
    context = json.loads(team.ledger.context(req))
    assert not context['agent_jobs'] and '未知镜像' not in team.ledger.context(req)
    assert req.assistant_task['agent_jobs']['old']['report']['unresolved'] == ['未知镜像']


def test_dispatch_infers_method_dependency_before_starting_configuration(data):
    owner, req = setup(data, TeamProvider())
    team = AgentTeam(owner, req)
    team.ledger.update_goal(req, goal('workflow')[1])
    events = []
    async def invoke(item, *_args, **_kwargs):
        args = json.loads(item['function']['arguments'])
        role = args['agent']
        events.append(role)
        if role == 'methods':
            team.ledger.save(req, {**req.assistant_task, 'method_selection':{
                'method_ids':['real.method'], 'task_id':req.assistant_task['task_id']}})
        if role == 'runtime':
            assert events.index('methods') < events.index('runtime')
            assert 'methods' in args['depends_on']
        return {'agent':role}, None
    team.call = invoke
    calls = [{'id':r, 'function':{'name':'delegate_agent','arguments':json.dumps({'agent':r,'question':'必要事实'})}}
             for r in ('runtime','methods','data')]
    asyncio.run(team.call_batch(calls, team.tools))
    assert sorted(events) == ['data','methods','runtime']
    assert events.index('methods') < events.index('runtime')


def test_existing_generic_configuration_entities_are_retyped_without_duplicate_model_facts(data):
    owner, req = setup(data, TeamProvider())
    team = AgentTeam(owner, req)
    science(team, owner)
    asyncio.run(team.query('runtime','read_execution_configuration',{'method_ids':['real.method']}))
    current = team.facts.typed.rows(req)[0]
    legacy = {**current,'entity_id':'entity:legacy-config','fact_type':'runtime.configuration'}
    scope = team.facts.memory.identity(req)
    with data.service.store.transaction() as db:
        db.execute('DELETE FROM assistant_fact_entities WHERE entity_id=?', (current['entity_id'],))
        db.execute('INSERT INTO assistant_fact_entities VALUES(?,?,?,?,?)', (*scope,legacy['entity_id'],json.dumps(legacy)))
    team.facts._hydrated.clear()
    context = team.facts.context(req)
    assert [f['fact_type'] for f in context['facts']] == ['runtime.scientific_configuration']
    assert len(team.facts.typed.rows(req)) == 2  # Old entity/source remains readable as history.
