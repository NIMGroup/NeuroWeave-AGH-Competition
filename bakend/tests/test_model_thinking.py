"""Exercise provider payloads and multi-call reasoning state without live credentials."""
import asyncio
import copy
import json

import httpx
import pytest

import app.chat_service as module
from app.assistant_runtime import compact_messages
from app.chat_service import ChatService


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setenv('LOCALFLOW_LLM_BASE_URL', 'https://synthetic.example')
    monkeypatch.setenv('LOCALFLOW_LLM_MODEL', 'synthetic')
    monkeypatch.setenv('LOCALFLOW_LLM_MAX_TOKENS', '800')
    monkeypatch.setenv('LOCALFLOW_ASSISTANT_MAX_TOKENS', '2400')
    monkeypatch.delenv('LOCALFLOW_LLM_THINKING', raising=False)
    monkeypatch.delenv('LOCALFLOW_LLM_THINKING_MAX_TOKENS', raising=False)
    requests, replies = [], []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [replies.pop(0)]})

    client = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, 'AsyncClient',
                        lambda **kwargs: client(transport=httpx.MockTransport(handle)))
    return requests, replies


@pytest.mark.parametrize('setting', [None, '', 'invalid', ' ENABLED '])
def test_thinking_enabled_for_answer_structured_and_tool_calls(transport, monkeypatch, setting):
    requests, replies = transport
    if setting is not None:
        monkeypatch.setenv('LOCALFLOW_LLM_THINKING', setting)
    replies.extend([
        {'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'answer'}},
        {'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': '{"ok": true}'}},
        {'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'tool answer'}},
    ])
    chat = ChatService()
    assert chat.thinking == 'enabled'
    assert asyncio.run(chat.complete([])) == 'answer'
    assert asyncio.run(chat.json_response([], stage='assistant_task', max_tokens=3000))[0] == {'ok': True}
    assert asyncio.run(chat.workspace_completion([], []))['content'] == 'tool answer'
    assert all(r['thinking'] == {'type': 'enabled'} and r['max_tokens'] == 8192
               and 'temperature' not in r for r in requests)


def test_explicit_disable_applies_to_all_calls_and_preserves_budgets(transport, monkeypatch):
    requests, replies = transport
    monkeypatch.setenv('LOCALFLOW_LLM_THINKING', 'disabled')
    replies.extend([{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'ok'}}] * 2)
    chat = ChatService()
    asyncio.run(chat.complete([]))
    asyncio.run(chat.workspace_completion([], []))
    assert [r['max_tokens'] for r in requests] == [800, 2400]
    assert all(r['thinking'] == {'type': 'disabled'} and r['temperature'] == 0.1 for r in requests)


def test_thinking_budget_can_be_configured_without_shrinking_final_output(transport, monkeypatch):
    requests, replies = transport
    monkeypatch.setenv('LOCALFLOW_LLM_THINKING_MAX_TOKENS', '4096')
    replies.extend([{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'ok'}}] * 2)
    chat = ChatService()
    asyncio.run(chat.complete([]))
    asyncio.run(chat.complete([], max_tokens=6000))
    assert [r['max_tokens'] for r in requests] == [4096, 6000]


def test_generation_ceiling_and_per_call_recovery_keep_thinking_enabled(transport, monkeypatch):
    from app.generation_budget import generation_limit
    requests, replies = transport
    monkeypatch.setenv('LOCALFLOW_LLM_MAX_GENERATION_TOKENS', '65536')
    monkeypatch.setenv('LOCALFLOW_ASSISTANT_MAX_TOKENS', '16384')
    monkeypatch.setenv('LOCALFLOW_LLM_THINKING_MAX_TOKENS', '12288')
    replies.extend([{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'ok'}}] * 3)
    chat = ChatService()
    asyncio.run(chat.workspace_completion([], []))
    with generation_limit(32768):
        asyncio.run(chat.workspace_completion([], []))
    with generation_limit(131072):
        asyncio.run(chat.workspace_completion([], []))
    assert [r['max_tokens'] for r in requests] == [16384, 32768, 65536]
    assert all(r['thinking'] == {'type': 'enabled'} for r in requests)


def test_tool_continuation_keeps_full_reasoning_through_context_compaction(transport):
    requests, replies = transport
    first = {'role': 'assistant', 'content': '', 'reasoning_content': 'synthetic provider state ' * 500,
             'tool_calls': [{'id': 'call-1', 'type': 'function',
                             'function': {'name': 'read_fixture', 'arguments': '{}'}}]}
    replies.extend([{'finish_reason': 'tool_calls', 'message': first},
                    {'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': '37'}}])
    messages = [{'role': 'user', 'content': 'read synthetic fixture'}]
    chat = ChatService()
    response = asyncio.run(chat.workspace_completion(messages, [], required=True))
    messages.append({k: response[k] for k in ('role', 'content', 'tool_calls', 'reasoning_content') if k in response})
    messages += [{'role': 'tool', 'tool_call_id': 'call-1', 'content': '{"value": 37}'}]
    for i in range(3):
        messages += [
            {'role': 'assistant', 'content': '', 'reasoning_content': 'continuation state',
             'tool_calls': [{'id': str(i), 'type': 'function',
                             'function': {'name': 'read_fixture', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': str(i), 'content': json.dumps({'rows': 'x' * 4000})},
        ]
    compact_messages(messages, budget=5000)
    before = copy.deepcopy(messages)
    assert asyncio.run(chat.workspace_completion(messages, []))['content'] == '37'
    assert requests[-1]['messages'][1] == first
    assert messages == before


def test_partial_structured_output_is_not_accepted_as_a_complete_answer(transport):
    _, replies = transport
    replies.append({'finish_reason': 'length', 'message': {'role': 'assistant', 'content': '{"partial": true}'}})
    with pytest.raises(RuntimeError, match='truncated'):
        asyncio.run(ChatService().complete([], max_tokens=3000))
