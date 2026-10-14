"""Regression cases for length recovery, data references and manual execution."""
import asyncio
import copy
import json

import pytest

from app.agent_context import tool_projection, prepared_plan_state
from app.agent_state import AgentTasks
from app.generation_budget import GenerationRecovery, generation_limit, requested_generation_tokens
from app.assistant_runtime import ProgressBudget
from test_data_operations import data
from test_unified_agent import fixture, goal, ready


class Responses:
    mode, model, provider_name = "model", "synthetic", "synthetic"
    thinking, assistant_max_tokens, thinking_max_tokens, generation_max_tokens = "enabled", 8192, 8192, 32768

    def __init__(self, items):
        self.items, self.rounds = iter(items), []

    async def workspace_completion(self, messages, tools, **kwargs):
        self.rounds.append((copy.deepcopy(messages), requested_generation_tokens.get()))
        item = next(self.items)
        if callable(item):
            item = item(messages)
        if isinstance(item, dict):
            return item
        return {"role": "assistant", "reasoning_content": "complete reasoning",
                "tool_calls": [{"id": f"call-{len(self.rounds)}-{i}", "type": "function",
                                "function": {"name": name, "arguments": json.dumps(args)}}
                               for i, (name, args) in enumerate(item)]}


def cut(**kwargs):
    return {"role": "assistant", "finish_reason": "length", "reasoning_content": "partial thinking",
            "usage": {"output_tokens": 8192, "reasoning_tokens": 8192}, **kwargs}


def plan():
    return [("propose_data_changes", {"source_scope": "output", "mode": "inplace",
             "edits": [{"action": "write_text", "path": "a.txt", "destination": "a.txt", "content": "new"}]})]


def ready_from_state(messages):
    state = json.loads(messages[2]["content"])["task"]
    identity = next(v for v, fact in state["artifacts"].items() if fact.get("kind") == "data")
    return [("finish_task", {"status": "awaiting_run", "summary": "方案就绪，等待运行。", "artifact_ids": [identity]})]


def test_budget_grows_but_success_resets_only_consecutive():
    state = GenerationRecovery(Responses([]))
    assert state.current == 8192
    assert state.truncated(cut()) and state.current == 16384
    assert state.truncated(cut()) and state.current == 32768
    state.succeeded()
    assert state.consecutive == 0 and state.total == 2
    assert state.truncated(cut()) and state.last["kind"] == "thinking"
    assert state.current == 32768


def test_generation_limit_is_local_and_restored_on_errors():
    async def one(tokens):
        with generation_limit(tokens):
            await asyncio.sleep(0)
            assert requested_generation_tokens.get() == tokens
        assert requested_generation_tokens.get() is None
    async def run():
        await asyncio.gather(one(16384), one(32768))
    asyncio.run(run())
    with pytest.raises(RuntimeError):
        with generation_limit(32768):
            raise RuntimeError("synthetic")
    assert requested_generation_tokens.get() is None


def test_large_json_report_has_small_valid_preview_and_retrievable_reference():
    report = {"total_files": 5000, "rows": [{"id": i, "label": "sample" * 50} for i in range(5000)]}
    result = {"path": "audit/report.json", "content": json.dumps(report), "has_more": False}
    projected = tool_projection(result, "evidence:synthetic")
    assert len(json.dumps(projected, ensure_ascii=False)) <= 6000
    assert projected["content_omitted"] and projected["evidence_reference"] == "evidence:synthetic"
    assert projected["structured_preview"]["total_files"] == 5000
    assert len(projected["structured_preview"]["rows"]) < 5000
    assert "content" not in projected
    assert "完整" in projected["preview_note"]


def test_full_query_evidence_is_readable_after_preview(data):
    owner, request, _ = fixture(data, ["answer"])
    ledger = AgentTasks(data.service.store)
    ledger.begin(request)
    content = json.dumps({"rows": list(range(3000))})
    reference = ledger.record(request, "read_output_text", {"path": "report.json"}, {"content": content})
    projected = tool_projection({"content": content}, reference)
    assert projected["evidence_reference"] == reference
    read = ledger.memory.execute(request, "read_conversation_memory", {"reference": reference, "max_chars": 12000})
    assert read["has_more"]
    assert read["historical"] and not read["current_state_verified"]


def test_truncated_tool_batch_is_not_prepared_and_retry_budget_grows(data):
    (data.output / "a.txt").write_text("original")
    owner, request, _ = fixture(data, [])
    partial = cut(tool_calls=[{"id": "partial-1", "type": "function",
        "function": {"name": "propose_data_changes", "arguments": json.dumps(plan()[0][1])}}])
    provider = owner.chat = Responses([partial, [goal(), *plan()], ready])
    result = asyncio.run(owner.reply(request))
    assert result.assistant_task["phase"] == "awaiting_run"
    assert [v[1] for v in provider.rounds] == [8192, 16384, 16384]
    assert len(data.service.list(request.conversation_id)) == 1
    assert (data.output / "a.txt").read_text() == "original"


def test_interleaved_success_does_not_report_consecutive_truncation(data):
    (data.output / "a.txt").write_text("original")
    owner, request, _ = fixture(data, [])
    provider = owner.chat = Responses([cut(), cut(), [goal(), *plan()], cut(), cut(), ready_from_state])
    result = asyncio.run(owner.reply(request))
    assert result.assistant_task["phase"] == "awaiting_run"
    assert len(provider.rounds) == 6
    state = AgentTasks(data.service.store).load(request)
    assert state["generation_recovery"]["consecutive"] == 0
    assert state["generation_recovery"]["total"] == 4
    assert state["generation_recovery"]["recovered"]
    assert (data.output / "a.txt").read_text() == "original"


def test_recovery_stops_at_bounded_attempts_without_claiming_completion(data):
    owner, request, _ = fixture(data, [])
    provider = owner.chat = Responses([cut(), cut(), cut()])
    result = asyncio.run(owner.reply(request))
    assert result.assistant_task["phase"] == "blocked"
    assert "模型输出连续截断" in result.content and "共享事实已保存" in result.content
    assert [v[1] for v in provider.rounds] == [8192, 16384, 32768]
    assert data.service.list(request.conversation_id) == []
    assert not list(data.output.iterdir())


def pending_script():
    from app.assistant_proposal_review import REVIEW_BLOCK
    return {"id": "script-one", "kind": "data", "status": "pending", "phase": "script",
            "manifest_ready": False, "script_plan_hash": "immutable-version", "can_run_script": False,
            "quality": {"blocking_issues": [REVIEW_BLOCK], "proposal_review": {"decision": "pending"}}}


def test_review_handshake_survives_large_tool_preview_without_granting_execution():
    result = pending_script()
    result["script"] = "large script\n" * 4000
    original = copy.deepcopy(result)
    value = tool_projection(result, "evidence:plan")["prepared_plan_state"]
    assert value["stage"] == "needs_agent_review" and value["blocking_issues"] == []
    assert value["next_tool"] == {"name": "finish_task", "status": "awaiting_run", "artifact_ids": ["script-one"]}
    assert value["version"] == "immutable-version" and not value["model_can_execute"]
    assert result == original and not result["can_run_script"]


@pytest.mark.parametrize("changes", [
    {"conflicts": ["destination already exists"]}, {"preview_expired": True},
    {"quality": {"blocking_issues": ["script validation failed"]}},
    {"quality": {"proposal_review": {"decision": "revise"}}},
    {"quality": {"proposal_review": {"superseded_by": "script-two"}}},
])
def test_real_plan_blocks_do_not_offer_finish_as_ready(changes):
    value = prepared_plan_state({**pending_script(), **changes})
    assert value["stage"] == "needs_revision" and "next_tool" not in value
    assert not value["model_can_execute"]


def test_prepared_review_can_finish_without_reading_unrun_results(data):
    (data.output / "a.txt").write_text("original")
    owner, request, _ = fixture(data, [])
    def finish_from_handshake(messages):
        assert "progress" in json.loads(messages[2]["content"])
        observation = next(json.loads(m['content'])["prepared_plan_state"]
            for m in reversed(messages) if m['role']=='tool' and 'prepared_plan_state' in json.loads(m['content']))
        assert observation["stage"] == "needs_agent_review"
        args = dict(observation["next_tool"])
        name = args.pop("name")
        return [(name, {**args, "summary": "方案已核验，等待用户运行。"})]
    provider = owner.chat = Responses([[goal(), *plan()], finish_from_handshake])
    result = asyncio.run(owner.reply(request))
    assert result.assistant_task["phase"] == "awaiting_run"
    assert len(provider.rounds) == 2
    assert data.service.list(request.conversation_id)[0]["can_confirm"]
    assert (data.output / "a.txt").read_text() == "original"


def test_changing_evidence_reference_is_not_new_query_progress():
    budget = ProgressBudget()
    result = {"kind": "data", "id": "plan-one", "status": "pending", "file_count": 12}
    assert budget.observe(tool_projection(result, "evidence:first"))
    assert not budget.observe(tool_projection(result, "evidence:second"))
    assert budget.stalled == 1
    assert budget.observe(tool_projection({**result, "status": "succeeded"}, "evidence:third"))
    assert budget.stalled == 0


def test_paged_manifest_does_not_replace_the_verified_task_type(data):
    (data.output / "a.txt").write_text("original")
    owner, request, _ = fixture(data, [])
    def query_page(messages):
        state = json.loads(messages[2]["content"])["task"]
        identity = next(iter(state["artifacts"]))
        return [("list_data_operation_files", {"id": identity, "kind": "changes", "limit": 5})]
    def finish_with_verified_type(messages):
        state = json.loads(messages[2]["content"])["task"]
        fact = next(iter(state["artifacts"].values()))
        assert fact["kind"] == "data" and fact["prepared_plan_state"]["version"]
        durable = AgentTasks(data.service.store).load(request)["artifacts"][fact["id"]]
        assert durable["version"] == fact["prepared_plan_state"]["version"]
        return ready_from_state(messages)
    provider = owner.chat = Responses([[goal(), *plan()], query_page, finish_with_verified_type])
    result = asyncio.run(owner.reply(request))
    assert len(provider.rounds) == 3
    assert result.assistant_task["phase"] == "awaiting_run"
    assert data.service.list(request.conversation_id)[0]["can_confirm"]
    assert (data.output / "a.txt").read_text() == "original"
