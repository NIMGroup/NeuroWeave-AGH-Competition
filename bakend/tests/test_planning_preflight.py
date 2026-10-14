import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

from app.models import ChatRequest
from app.workspace_tools import WorkspaceTools


def run_inspection(response):
    class Model:
        mode = 'model'
        async def workspace_completion(self, messages, tools, required=False):
            names = [tool['function']['name'] for tool in tools]
            assert 'list_skill_methods' not in names
            assert not any(name.startswith(('delete_', 'create_', 'move_')) for name in names)
            assert 'mrtrix.tckgen' in messages[0]['content']
            if isinstance(response, Exception):
                raise response
            return response
    service = Mock()
    tools = WorkspaceTools(service, Model(), object())
    tools.execute = Mock(return_value={'items': [{'name': 'existing_fod.mif'}], 'has_more': True})
    registry = Mock()
    registry.methods_for.return_value = [SimpleNamespace(method_id='mrtrix.tckgen', description='纤维追踪', input_slots=[])]
    request = ChatRequest(message='用已有FOD做纤维追踪', dataset_id='ds', interaction_mode='task')
    asyncio.run(tools.inspect_for_plan(request, {'capability_ids':['dwi.tractography']}, registry))
    evidence = json.loads(request.workspace_context.split('：\n')[-1])
    assert service.event.call_args.args[1]['tool'] == 'continue_workflow_planning'
    assert evidence['complete_scan'] is False
    return tools, evidence


def call(name, args, i):
    return {'id':str(i), 'type':'function', 'function':{'name':name, 'arguments':json.dumps(args)}}


def test_repeated_queries_are_not_reexecuted_and_cannot_block_planning():
    tools, evidence = run_inspection({'role':'assistant', 'tool_calls':[call('list_dataset_directory',{'path':'.'},1)]})
    assert tools.execute.call_count == 1
    assert len(evidence['observations']) == 3
    assert evidence['stop_reason'] == 'query_budget'
    assert evidence['observations'][0]['result']['items'][0]['name'] == 'existing_fod.mif'


def test_large_parallel_query_batch_is_bounded_and_hands_off():
    tools, evidence = run_inspection({'role':'assistant', 'tool_calls':[
        call('search_dataset_files',{'pattern':str(i)},i) for i in range(20)]})
    assert tools.execute.call_count == 6
    assert len(evidence['observations']) == 6


def test_write_calls_cannot_execute_during_input_inspection():
    tools, evidence = run_inspection({'role':'assistant', 'tool_calls':[call('delete_output_path',{'path':'x'},1)]})
    tools.execute.assert_not_called()
    assert all('error' in item['result'] for item in evidence['observations'])


def test_model_failure_and_unverified_text_still_handoff_without_invented_evidence():
    for response in (RuntimeError('provider unavailable'), {'role':'assistant','content':'I found files without calling tools'}):
        tools, evidence = run_inspection(response)
        tools.execute.assert_not_called()
        assert evidence['observations'] == []


def test_explicit_handoff_keeps_existing_observations():
    tools, evidence = run_inspection({'role':'assistant','tool_calls':[
        call('list_dataset_directory',{'path':'.'},1),call('continue_workflow_planning',{},2)]})
    assert tools.execute.call_count == 1
    assert evidence['stop_reason'] == 'handoff'
