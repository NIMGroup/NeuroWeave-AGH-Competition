"""Actual overlap, dependency order, shared facts, cancellation and local recovery budgets."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.agent_dispatch import dispatch_agents
from app.agent_team import AgentTeam, DELEGATE_TOOL
from app.operation_context import OperationCancelled
from test_agent_team import TeamProvider, done, goal, setup
from test_data_operations import data


def call(role, depends=()):
    return {'id': role, 'function': {'name': 'delegate_agent',
        'arguments': json.dumps({'agent': role, 'question': '核实对应事实', 'depends_on': list(depends)})}}


def test_independent_agents_overlap_and_keep_result_order():
    async def run():
        started, all_started = set(), asyncio.Event()
        async def invoke(item):
            started.add(item['id'])
            if len(started) == 3:
                all_started.set()
            await asyncio.wait_for(all_started.wait(), .5)
            return {'agent': item['id']}, None
        values = await dispatch_agents([call(r) for r in ('data', 'methods', 'runtime')], invoke)
        assert [v[0]['agent'] for v in values] == ['data', 'methods', 'runtime']
    asyncio.run(run())


def test_dependency_waits_for_fact_publication_even_when_listed_first():
    async def run():
        facts, events = {}, []
        async def invoke(item):
            role = item['id']
            events.append(role)
            if role == 'data':
                await asyncio.sleep(0)
                facts['layout'] = 'observed'
            else:
                assert facts['layout'] == 'observed'
            return {'agent': role}, None
        await dispatch_agents([call('methods', ['data']), call('data')], invoke)
        assert events == ['data', 'methods']
    asyncio.run(run())


def test_same_role_is_serialized_but_other_role_can_overlap():
    async def run():
        active, overlap = set(), asyncio.Event()
        async def invoke(item):
            role = item['id']
            assert role not in active
            active.add(role)
            if len(active) == 2:
                overlap.set()
            await asyncio.wait_for(overlap.wait(), .5)
            active.remove(role)
            return {'agent': role}, None
        await dispatch_agents([call('data'), call('data'), call('runtime')], invoke)
    asyncio.run(run())


@pytest.mark.parametrize('calls', [[call('data', ['methods']), call('methods', ['data'])],
                                 [call('data', ['data'])], [call('data', ['runtime'])]])
def test_invalid_or_cyclic_dependencies_do_not_start_investigation(calls):
    invoke = AsyncMock()
    results = asyncio.run(dispatch_agents(calls, invoke))
    assert all(r[0]['error'] and not r[0]['executed'] for r in results)
    invoke.assert_not_called()


def test_failure_blocks_dependent_without_suppressing_independent_peer():
    async def run():
        seen = []
        async def invoke(item):
            seen.append(item['id'])
            return ({'error': 'invalid assignment'} if item['id']=='data' else {'ok': True}), None
        values = await dispatch_agents([call('data'), call('methods', ['data']), call('runtime')], invoke)
        assert seen == ['data', 'runtime'] and values[1][0]['error']
    asyncio.run(run())


def test_stop_cancels_and_drains_all_siblings():
    async def run():
        started, released, cleaned = set(), asyncio.Event(), set()
        async def invoke(item):
            role = item['id']
            started.add(role)
            if len(started) == 3:
                released.set()
            try:
                await released.wait()
                if role == 'data':
                    raise OperationCancelled('stopped')
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.add(role)
        with pytest.raises(OperationCancelled):
            await dispatch_agents([call(r) for r in ('data', 'methods', 'runtime')], invoke)
        assert cleaned == started == {'data', 'methods', 'runtime'}
    asyncio.run(run())


def test_parallel_team_persists_both_jobs_and_facts(data):
    (data.output/'a.txt').write_text('one')
    (data.output/'b.txt').write_text('two')
    provider = TeamProvider(data=[[('inspect_output_path', {'path': 'a.txt'})], done],
                            runtime=[[('list_data_operations', {})], done])
    original = provider.workspace_completion
    active, gate = set(), None
    async def response(messages, tools, **kwargs):
        nonlocal gate
        role = 'runtime' if '的 runtime 功能agent' in messages[0]['content'] else 'data'
        if role not in active:
            active.add(role)
            if len(active) == 2:
                gate.set()
            await asyncio.wait_for(gate.wait(), .5)
        return await original(messages, tools, **kwargs)
    provider.workspace_completion = response
    owner, req = setup(data, provider)
    team = AgentTeam(owner, req)
    async def run():
        nonlocal gate
        gate = asyncio.Event()
        update = {'id': 'goal', 'function': {'name': 'update_task_state', 'arguments': json.dumps(goal()[1])}}
        values = await team.call_batch([update, call('data'), call('runtime')], [*team.tools, DELEGATE_TOOL])
        assert len(values) == 3 and all(not v[1].get('error') for v in values)
    asyncio.run(run())
    assert len(req.assistant_task['agent_jobs']) == 2
    assert {row['owner'] for row in team.facts.rows(req)} == {'data', 'runtime'}
    assert not data.service.futures and (data.output/'a.txt').read_text() == 'one'


def test_generation_recovery_is_independent_between_parallel_roles(data):
    provider = TeamProvider()
    provider.assistant_max_tokens, provider.thinking_max_tokens = 8192, 8192
    provider.workspace_completion = AsyncMock(side_effect=[
        {'finish_reason': 'length', 'tool_calls': [{'function': {'arguments': 'partial'}}],
         'usage': {'output_tokens': 8192, 'reasoning_tokens': 1000}}, {'content': 'ok'}, {'content': 'recovered'}])
    owner, req = setup(data, provider)
    team = AgentTeam(owner, req)
    team.compression.compact = AsyncMock()
    async def run():
        await asyncio.gather(team.completion([], [], 'methods'), team.completion([], [], 'data'))
    asyncio.run(run())
    assert team.generations['methods'].consecutive == 0 and team.generations['methods'].total == 1
    assert team.generations['methods'].current == 16384
    assert team.generations['data'].consecutive == 0 and team.generations['data'].current == 8192
    assert req.assistant_task['agent_generation_recovery']['methods']['recovered'] is True
