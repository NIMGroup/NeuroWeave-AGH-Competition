"""Stop requests interrupt transport and drain tools without touching Runner jobs."""
import asyncio
import threading
import time

import httpx
import pytest
from fastapi import HTTPException

from app.chat_service import ChatService
from app.chat_turns import ChatTurns
from app.conversation_service import ConversationService
from app.local_store import LocalStore
from app.models import ChatRequest, ChatResponse
from app.operation_context import OperationContext, OperationCancelled, checkpoint, current_operation, operation_thread


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALFLOW_LLM_BASE_URL', 'https://synthetic.example')
    monkeypatch.setenv('LOCALFLOW_LLM_MODEL', 'synthetic')
    store = LocalStore(tmp_path / 'chat.db')
    chat = ChatService()
    conversations = ConversationService(store, chat)
    return store, chat, conversations


def answer(request):
    return ChatResponse(content='synthetic', provider_used='test', model_used='test',
                        mode='demo', selected_skills=[], conversation_id=request.conversation_id)


def test_stop_interrupts_inflight_model_transport_and_does_not_publish_reply(setup, monkeypatch):
    store, chat, conversations = setup
    async def run():
        entered, closed = asyncio.Event(), asyncio.Event()
        calls = []
        async def transport(request):
            calls.append(request)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        client = httpx.AsyncClient
        monkeypatch.setattr('app.chat_service.httpx.AsyncClient', lambda **kw: client(transport=httpx.MockTransport(transport)))
        async def handler(request):
            prepared, identity = conversations.begin_turn(request)
            await chat.complete([])
            await conversations.finish_turn(identity, answer(prepared), prepared.client_turn_id)
            return answer(prepared)
        turns = ChatTurns(store, conversations, handler)
        first = turns.submit(ChatRequest(message='synthetic', client_turn_id='cancel_transport_123'))
        await entered.wait()
        stopped = turns.cancel(first['turn_id'])
        assert stopped['cancel_requested'] and stopped['status'] == 'running'
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(turns.wait(first['turn_id']), 1)
        assert error.value.status_code == 499 and closed.is_set() and len(calls) == 1
        final = turns.get(first['turn_id'])
        assert final['status'] == 'cancelled' and not final['cancel_requested']
        assert final['token_usage']['calls'] == 1 and final['token_usage']['reported_calls'] == 0
        assert [m.role for m in store.conversation_messages(first['conversation_id'])] == ['user']
        assert turns.cancel(first['turn_id']) == final
        await turns.shutdown()
    asyncio.run(run())


def test_stop_queued_request_prevents_handler_entry(setup):
    store, _, conversations = setup
    async def run():
        calls = []
        async def handler(request):
            calls.append(request)
            return answer(request)
        turns = ChatTurns(store, conversations, handler)
        first = turns.submit(ChatRequest(message='synthetic', client_turn_id='cancel_queue_12345'))
        assert turns.cancel(first['turn_id'])['cancel_requested']
        with pytest.raises(HTTPException): await turns.wait(first['turn_id'])
        assert turns.get(first['turn_id'])['status'] == 'cancelled' and not calls
        await turns.shutdown()
    asyncio.run(run())


@pytest.mark.parametrize('cooperative', [True, False])
def test_stopping_drains_thread_before_unlocking_and_retrying(setup, cooperative):
    store, _, conversations = setup
    async def run():
        entered, release, exited = threading.Event(), threading.Event(), threading.Event()
        attempts, followups = [], []
        def tool():
            entered.set()
            try:
                while not release.wait(.01):
                    if cooperative: checkpoint('scanning')
                checkpoint()
            finally:
                exited.set()
        async def handler(request):
            attempts.append(request)
            if len(attempts) == 1: await operation_thread(tool)
            followups.append('next tool')
            return answer(request)
        turns = ChatTurns(store, conversations, handler)
        first = turns.submit(ChatRequest(message='synthetic', client_turn_id='cancel_thread_12345'))
        while not entered.is_set(): await asyncio.sleep(.01)
        turns.cancel(first['turn_id'])
        if not cooperative:
            await asyncio.sleep(.1)
            assert turns.get(first['turn_id'])['status'] == 'running'
            assert turns.get(first['turn_id'])['cancel_requested']
            with pytest.raises(HTTPException): turns.submit(ChatRequest(message='new', conversation_id=first['conversation_id']))
            assert turns.retry(first['turn_id'])['status'] == 'running'
            assert not exited.is_set()
        release.set()
        with pytest.raises(HTTPException): await asyncio.wait_for(turns.wait(first['turn_id']), 1)
        assert exited.is_set() and not followups
        assert turns.get(first['turn_id'])['status'] == 'cancelled'
        turns.retry(first['turn_id'])
        assert (await turns.wait(first['turn_id'])).content == 'synthetic'
        assert len(attempts) == 2 and followups == ['next tool']
        await turns.shutdown()
    asyncio.run(run())


def test_tool_timeout_drains_only_that_tool_without_cancelling_the_turn():
    async def run():
        event, exited = threading.Event(), threading.Event()
        def tool():
            try:
                while True:
                    checkpoint()
                    time.sleep(.005)
            finally: exited.set()
        token = current_operation.set(OperationContext(event, lambda _: None))
        try:
            with pytest.raises(TimeoutError): await asyncio.wait_for(operation_thread(tool), .05)
            assert exited.is_set() and not event.is_set()
            checkpoint()
            assert await operation_thread(lambda: 'next') == 'next'
        finally: current_operation.reset(token)
    asyncio.run(run())


def test_stop_after_durable_reply_preserves_answer_and_skips_memory_wait(setup, monkeypatch):
    store, _, conversations = setup
    async def run():
        entered, closed = asyncio.Event(), asyncio.Event()
        async def compact(*args, **kwargs):
            entered.set()
            try: await asyncio.Event().wait()
            finally: closed.set()
        monkeypatch.setattr(conversations, 'compact_if_needed', compact)
        async def handler(request):
            prepared, identity = conversations.begin_turn(request)
            response = answer(prepared)
            await conversations.finish_turn(identity, response, request.client_turn_id)
            return response
        turns = ChatTurns(store, conversations, handler)
        first = turns.submit(ChatRequest(message='synthetic', client_turn_id='cancel_memory_12345'))
        await entered.wait()
        assert turns.get(first['turn_id'])['result']['content'] == 'synthetic'
        active = turns.active()['turns']
        assert len(active) == 1 and active[0]['status'] == 'running' and active[0]['result'] is None
        with pytest.raises(HTTPException): turns.submit(ChatRequest(message='new',conversation_id=first['conversation_id']))
        turns.cancel(first['turn_id'])
        assert (await asyncio.wait_for(turns.wait(first['turn_id']), 1)).content == 'synthetic'
        assert closed.is_set() and turns.get(first['turn_id'])['status'] == 'succeeded'
        assert turns.active() == {'turns': []}
        assert [m.role for m in store.conversation_messages(first['conversation_id'])] == ['user','assistant']
        assert turns.cancel(first['turn_id'])['status'] == 'succeeded'
        await turns.shutdown()
    asyncio.run(run())


def test_stop_isolated_from_other_conversation_and_shutdown_drains_existing_stop(setup):
    store, _, conversations = setup
    async def run():
        entered, release, exited = threading.Event(), threading.Event(), threading.Event()
        def tool():
            entered.set()
            try: release.wait()
            finally: exited.set()
        async def handler(request):
            if request.message == 'slow': await operation_thread(tool)
            return answer(request)
        turns = ChatTurns(store, conversations, handler)
        first = turns.submit(ChatRequest(message='slow',client_turn_id='cancel_shutdown_123'))
        while not entered.is_set(): await asyncio.sleep(.005)
        turns.cancel(first['turn_id'])
        second = turns.submit(ChatRequest(message='other',client_turn_id='cancel_other_123456'))
        assert (await turns.wait(second['turn_id'])).content == 'synthetic'
        await asyncio.sleep(.1)
        stopping = asyncio.create_task(turns.shutdown())
        await asyncio.sleep(.05)
        assert not stopping.done() and not exited.is_set()
        release.set()
        await asyncio.wait_for(stopping, 1)
        assert exited.is_set() and turns.get(second['turn_id'])['status'] == 'succeeded'
        assert turns.get(first['turn_id'])['status'] in {'cancelled','interrupted'}
    asyncio.run(run())


def test_finish_turn_checks_stop_before_writing_assistant_message(setup):
    store, _, conversations = setup
    async def run():
        identity = conversations.create('synthetic').conversation_id
        event = threading.Event(); event.set()
        token = current_operation.set(OperationContext(event, lambda _: None))
        try:
            with pytest.raises(OperationCancelled):
                await conversations.finish_turn(identity, answer(ChatRequest(message='test',conversation_id=identity)))
            assert not store.conversation_messages(identity)
        finally: current_operation.reset(token)
    asyncio.run(run())


def test_server_verification_is_discoverable_cancellable_and_does_not_insert_user_messages(setup):
    store, _, conversations = setup
    async def run():
        entered = asyncio.Event()
        async def handler(request):
            if request._execution_feedback:
                entered.set()
                await asyncio.Event().wait()
            return answer(request)
        turns = ChatTurns(store, conversations, handler)
        request, identity = conversations.begin_turn(ChatRequest(message='original', client_turn_id='original-synthetic'))
        sequence = store.conversation_messages(identity)[-1].sequence
        feedback = request.model_copy(update={'client_turn_id': 'result-synthetic-123'})
        feedback._execution_feedback = {'operation_id': 'synthetic', 'status': 'analyzed'}
        assert turns.submit_feedback(feedback, sequence)
        await entered.wait()
        discovered = turns.active()['turns']
        assert len(discovered) == 1
        assert discovered[0]['request_kind'] == 'result_verification'
        assert discovered[0]['conversation_id'] == identity
        assert [m.content for m in store.conversation_messages(identity)] == ['original']
        with pytest.raises(HTTPException) as busy:
            turns.submit(ChatRequest(message='new', conversation_id=identity))
        assert busy.value.status_code == 409
        stopped = turns.cancel(discovered[0]['turn_id'])
        assert stopped['cancel_requested']
        with pytest.raises(HTTPException) as cancelled:
            await asyncio.wait_for(turns.wait(discovered[0]['turn_id']), 1)
        assert cancelled.value.status_code == 499 and turns.active() == {'turns': []}
        later = turns.submit(ChatRequest(message='new', conversation_id=identity))
        assert later['request_kind'] == 'user_message'
        assert (await turns.wait(later['turn_id'])).content == 'synthetic'
        await turns.shutdown()
    asyncio.run(run())


def test_active_discovery_respects_conversation_access(setup, monkeypatch):
    store, _, conversations = setup
    async def run():
        async def handler(request):
            await asyncio.Event().wait()
        turns = ChatTurns(store, conversations, handler)
        first = turns.submit(ChatRequest(message='hidden', client_turn_id='hidden-synthetic'))
        second = turns.submit(ChatRequest(message='visible', client_turn_id='visible-synthetic'))
        original = store.get_conversation
        def scoped(identity):
            if identity == first['conversation_id']:
                raise PermissionError('Forbidden')
            return original(identity)
        monkeypatch.setattr(store, 'get_conversation', scoped)
        assert [v['turn_id'] for v in turns.active()['turns']] == [second['turn_id']]
        await turns.shutdown()
    asyncio.run(run())
