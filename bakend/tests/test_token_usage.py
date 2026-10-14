"""Exercise physical requests, retries, durable turns and conversation isolation."""
import asyncio
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

import app.chat_service as transport_module
from app.chat_service import ChatService
from app.chat_turns import ChatTurns
from app.conversation_service import ConversationService
from app.local_store import LocalStore
from app.models import ChatRequest, ChatResponse
from app.token_usage import TokenUsageLedger, current_usage, normalize_usage, usage_scope


def response(content="ok", usage=None, finish_reason="stop"):
    return {"choices": [{"finish_reason": finish_reason,
        "message": {"role": "assistant", "content": content}}], "usage": usage}


def usage(incoming=100, outgoing=20):
    return {"prompt_tokens": incoming, "completion_tokens": outgoing,
        "total_tokens": incoming + outgoing,
        "prompt_tokens_details": {"cached_tokens": 30},
        "completion_tokens_details": {"reasoning_tokens": 5}}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALFLOW_LLM_BASE_URL", "https://synthetic.example")
    monkeypatch.setenv("LOCALFLOW_LLM_MODEL", "synthetic")
    store = LocalStore(tmp_path / "usage.db")
    chat = ChatService()
    conversations = ConversationService(store, chat, compact_trigger_tokens=100000)
    identity = conversations.create("synthetic").conversation_id
    replies, requests = [], []
    async_client = httpx.AsyncClient

    def handle(request):
        requests.append(json.loads(request.content))
        item = replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return httpx.Response(200, json=item)

    monkeypatch.setattr(transport_module.httpx, "AsyncClient",
        lambda **kwargs: async_client(transport=httpx.MockTransport(handle)))
    return SimpleNamespace(store=store, chat=chat, conversations=conversations,
        identity=identity, replies=replies, requests=requests, client=async_client)


def test_counts_every_physical_request_including_json_repair_and_truncation(setup):
    data = setup
    data.replies.extend([response("invalid JSON", usage()), response('{"ok":true}', usage(50, 10)),
        response(None, usage(40, 8)), response("partial", usage(30, 7), "length")])

    async def run():
        with usage_scope(data.conversations.token_usage, data.identity, "turn_usage_123456"):
            assert (await data.chat.json_response([], stage="assistant_task"))[0] == {"ok": True}
            assert (await data.chat.workspace_completion([], []))["content"] is None
            with pytest.raises(RuntimeError, match="truncated"):
                await data.chat.complete([])
        assert current_usage.get() is None

    asyncio.run(run())
    result = data.conversations.token_usage.summary(data.identity)
    assert result.calls == result.reported_calls == 4 and result.complete
    assert (result.input_tokens, result.output_tokens, result.total_tokens) == (220, 45, 265)
    assert (result.reasoning_tokens, result.cached_input_tokens) == (20, 120)
    assert len(data.requests) == 4  # Subsets are not added to total usage.


def test_missing_partial_zero_and_network_failure_are_distinct(setup):
    data = setup
    data.replies.extend([response("ok"), response("ok", {"prompt_tokens": 9}),
        httpx.ReadTimeout("synthetic timeout"), response("ok", usage(100, 20)),
        response("ok", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})])
    async def run():
        with usage_scope(data.conversations.token_usage, data.identity):
            await data.chat.complete([])
            await data.chat.complete([])
            with pytest.raises(httpx.ReadTimeout):
                await data.chat.complete([])
            await data.chat.complete([])
            await data.chat.complete([])
    asyncio.run(run())
    result = data.conversations.token_usage.summary(data.identity)
    assert (result.calls, result.reported_calls, result.unavailable_calls) == (5, 2, 3)
    assert (result.input_tokens, result.output_tokens, result.total_tokens) == (109, 20, 120)
    assert not result.complete
    with data.store._connect() as db:
        rows = db.execute("SELECT * FROM model_token_usage ORDER BY started_at").fetchall()
    assert rows[0]["total_tokens"] is None and rows[2]["status"] == "failed"
    assert rows[4]["total_tokens"] == 0


def test_concurrent_scopes_do_not_mix_conversations_and_restore_outer_context(setup):
    data = setup
    other = data.conversations.create("other").conversation_id
    data.replies.extend([response("a", usage(100, 20)), response("b", usage(200, 40))])
    async def one(identity, turn):
        with usage_scope(data.conversations.token_usage, identity, turn):
            await asyncio.sleep(0)
            await data.chat.complete([])
    async def run():
        await asyncio.gather(one(data.identity, "turn-a"), one(other, "turn-b"))
        assert current_usage.get() is None
    asyncio.run(run())
    assert data.conversations.token_usage.summary(data.identity).total_tokens == 120
    assert data.conversations.token_usage.summary(other).total_tokens == 240


def test_turn_retry_persists_failed_cost_without_recounting_polls_or_reopening(setup):
    data = setup
    data.replies.extend([response("partial", usage(), "length"), response("finished", usage(50, 10))])

    async def handler(request):
        prepared, identity = data.conversations.begin_turn(request)
        content = await data.chat.complete([])
        answer = ChatResponse(content=content, provider_used="test", model_used="test",
            mode="model", selected_skills=[], conversation_id=identity)
        await data.conversations.finish_turn(identity, answer, prepared.client_turn_id)
        return answer

    async def run():
        turns = ChatTurns(data.store, data.conversations, handler)
        request = ChatRequest(message="synthetic", conversation_id=data.identity,
            client_turn_id="token_retry_123456")
        first = turns.submit(request)
        with pytest.raises(HTTPException):
            await turns.wait(first["turn_id"])
        assert turns.get(first["turn_id"])["token_usage"]["total_tokens"] == 120
        turns.retry(first["turn_id"])
        answer = await turns.wait(first["turn_id"])
        assert answer.token_usage.total_tokens == 180 and answer.token_usage.calls == 2
        for _ in range(3):
            assert turns.submit(request)["token_usage"]["total_tokens"] == 180
            assert turns.get(first["turn_id"])["result"]["token_usage"]["total_tokens"] == 180
        assert len(data.store.conversation_messages(data.identity)) == 2
        assert len(data.requests) == 2
        await turns.shutdown()
    asyncio.run(run())
    reopened = LocalStore(data.store.database_path)
    conversations = ConversationService(reopened, data.chat)
    snapshot = conversations.snapshot(data.identity, limit=1)
    assert snapshot.token_usage.total_tokens == 180
    assert snapshot.turn_token_usage["token_retry_123456"].calls == 2 and snapshot.has_more
    assert conversations.snapshot(data.identity, limit=1, before_sequence=2).token_usage.total_tokens == 180


def test_automatic_and_manual_compaction_are_accounted_without_changing_history_estimates(setup):
    data = setup
    for n in range(8):
        data.store.append_conversation_message(data.identity, role="user" if n % 2 == 0 else "assistant",
            content="synthetic long history " * 30, estimated_tokens=300, metadata={}, created_at="now")
    data.replies.extend([response("summary", usage()), response("second summary", usage(50, 10))])
    async def run():
        with usage_scope(data.conversations.token_usage, data.identity, "current-turn"):
            assert await data.conversations.compact_if_needed(data.identity, force=True)
        assert await data.conversations.compact_if_needed(data.identity, force=True)
    asyncio.run(run())
    assert data.conversations.token_usage.summary(data.identity, "current-turn").calls == 1
    assert data.conversations.token_usage.summary(data.identity).calls == 2
    assert len(data.store.conversation_messages(data.identity)) == 8
    with data.store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM model_token_usage WHERE turn_id IS NULL").fetchone()[0] == 1


def test_cancelled_call_remains_unavailable_and_start_is_durable(setup, monkeypatch):
    data = setup
    async def cancel(request):
        assert data.conversations.token_usage.summary(data.identity).calls == 1
        raise asyncio.CancelledError()
    client = data.client
    monkeypatch.setattr(transport_module.httpx, "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(cancel)))
    async def run():
        with usage_scope(data.conversations.token_usage, data.identity):
            with pytest.raises(asyncio.CancelledError):
                await data.chat.complete([])
    asyncio.run(run())
    result = data.conversations.token_usage.summary(data.identity)
    assert result.calls == result.unavailable_calls == 1 and result.total_tokens is None


def test_migration_preserves_legacy_rows_and_conversation_delete_cascades(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE conversations(conversation_id TEXT PRIMARY KEY,title TEXT NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0,summary TEXT NOT NULL DEFAULT '',summary_version INTEGER NOT NULL DEFAULT 0,
            summarized_through_sequence INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
        db.execute("INSERT INTO conversations VALUES('legacy','Original title',0,'Original summary',0,0,'old','old')")
    store = LocalStore(path)
    ledger = TokenUsageLedger(store)
    assert store.get_conversation("legacy").summary == "Original summary"
    assert ledger.summary("legacy").total_tokens is None
    call = ledger.start("legacy", "legacy-turn", "test", "completion")
    ledger.finish(call, response(usage=usage()), returned=True)
    ledger.finish(call, response(usage=usage(999, 999)), returned=True)  # Same callback cannot recount.
    assert ledger.summary("legacy").total_tokens == 120
    store.update_conversation("legacy", archived=True, updated_at="now")
    assert store.delete_archived_conversation("legacy")
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM model_token_usage").fetchone()[0] == 0


@pytest.mark.parametrize("value", [None, "123", -1, True, 1.5, 2**63])
def test_malformed_counts_never_become_zero(value):
    result = normalize_usage({"usage": {"prompt_tokens": value}})
    assert result["input_tokens"] is None and result["total_tokens"] is None


def test_exact_total_can_be_derived_without_inventing_optional_details():
    value = normalize_usage({"usage": {"prompt_tokens": 3, "completion_tokens": 4}})
    assert value["total_tokens"] == 7 and value["reasoning_tokens"] is None
    assert normalize_usage({"usage": {"prompt_tokens": 100, "prompt_cache_hit_tokens": 40}})["cached_input_tokens"] == 40


def test_reply_polling_waits_for_memory_usage_but_committed_answer_remains_durable(setup, monkeypatch):
    data = setup
    data.replies.extend([response("reply", usage()), response("summary", usage(50, 10))])
    gate, entered = asyncio.Event(), asyncio.Event()
    async def compact(*args, **kwargs):
        entered.set()
        await gate.wait()
        await data.chat.complete([])
        return False
    monkeypatch.setattr(data.conversations, "compact_if_needed", compact)
    async def handler(request):
        prepared, identity = data.conversations.begin_turn(request)
        answer = ChatResponse(content=await data.chat.complete([]), provider_used="test", model_used="test",
            mode="model", selected_skills=[], conversation_id=identity)
        await data.conversations.finish_turn(identity, answer, prepared.client_turn_id)
        return answer
    async def run():
        turns = ChatTurns(data.store, data.conversations, handler)
        request = ChatRequest(message="synthetic", conversation_id=data.identity, client_turn_id="memory_usage_123456")
        first = turns.submit(request)
        await entered.wait()
        interim = turns.get(first["turn_id"])
        assert interim["status"] == "running" and interim["token_usage"]["total_tokens"] == 120
        assert interim["result"]["content"] == "reply"
        with data.store._connect() as db:
            assert db.execute("SELECT status FROM chat_turns WHERE id=?", (first["turn_id"],)).fetchone()[0] == "succeeded"
        gate.set()
        answer = await turns.wait(first["turn_id"])
        assert turns.get(first["turn_id"])["status"] == "succeeded"
        assert answer.token_usage.total_tokens == 180 and answer.token_usage.calls == 2
        await turns.shutdown()
    asyncio.run(run())


def test_token_reads_follow_existing_conversation_ownership_rules(setup):
    from app.data_auth import Principal, principal
    token = principal.set(Principal("other-user", True, ("dataset-a",), ("output-a",)))
    try:
        with pytest.raises(PermissionError):
            setup.conversations.token_usage.summary(setup.identity)
        with pytest.raises(PermissionError):
            setup.conversations.token_usage.turn_summaries(setup.identity, ["turn"])
    finally:
        principal.reset(token)


def test_usage_http_contract_for_turn_reply_and_paginated_history(setup):
    from app.api.chat import router_for as chat_router
    from app.api.conversations import router_for as conversation_router
    data = setup
    data.replies.append(response("synthetic reply", usage()))
    async def handler(request):
        prepared, identity = data.conversations.begin_turn(request)
        answer = ChatResponse(content=await data.chat.complete([]), provider_used="test",
            model_used="test", mode="model", selected_skills=[], conversation_id=identity)
        await data.conversations.finish_turn(identity, answer, prepared.client_turn_id)
        return answer
    async def run():
        turns = ChatTurns(data.store, data.conversations, handler)
        services = SimpleNamespace(chat_turns=turns, conversation_service=data.conversations)
        app = FastAPI()
        app.include_router(chat_router(services))
        app.include_router(conversation_router(services))
        async with data.client(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            answer = await client.post("/api/chat", json={"message":"synthetic",
                "conversation_id":data.identity,"client_turn_id":"http_usage_123456"})
            assert answer.status_code == 200 and answer.json()["token_usage"]["total_tokens"] == 120
            turn = (await client.get("/api/chat/turns/http_usage_123456")).json()
            assert turn["token_usage"] == turn["result"]["token_usage"]
            snapshot = (await client.get("/api/conversations/" + data.identity, params={"limit":1})).json()
            assert snapshot["token_usage"]["total_tokens"] == 120
            assert snapshot["turn_token_usage"]["http_usage_123456"]["total_tokens"] == 120
        await turns.shutdown()
    asyncio.run(run())
