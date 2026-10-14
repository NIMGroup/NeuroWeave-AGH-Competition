from __future__ import annotations

import base64
import binascii
import json
import math
import os
import re
from datetime import datetime, timezone
from uuid import uuid4

from .chat_service import ChatService
from .conversation_models import (
    ConversationListItem,
    ConversationMemoryStatus,
    ConversationPage,
    ConversationRecord,
    ConversationSnapshot,
)
from .local_store import LocalStore
from .models import ChatRequest, ChatResponse, Message
from .token_usage import TokenUsageLedger, usage_scope


class ConversationService:
    """Own server-side history and compact older turns into a working summary."""

    def __init__(
        self,
        store: LocalStore,
        chat_service: ChatService,
        *,
        compact_trigger_tokens: int | None = None,
        keep_recent_messages: int | None = None,
    ) -> None:
        self.store = store
        self.chat_service = chat_service
        self.token_usage = TokenUsageLedger(store)
        self.compact_trigger_tokens = max(
            256,
            compact_trigger_tokens
            if compact_trigger_tokens is not None
            else int(os.getenv("LOCALFLOW_HISTORY_COMPACT_TRIGGER_TOKENS", "12000")),
        )
        self.keep_recent_messages = max(
            4,
            keep_recent_messages
            if keep_recent_messages is not None
            else int(os.getenv("LOCALFLOW_HISTORY_KEEP_RECENT_MESSAGES", "8")),
        )

    def create(self, title: str = "新数据处理对话") -> ConversationRecord:
        now = self._now()
        conversation = ConversationRecord(
            conversation_id=f"conv_{uuid4().hex[:16]}",
            title=title.strip()[:120] or "新数据处理对话",
            created_at=now,
            updated_at=now,
        )
        self.store.create_conversation(conversation)
        return conversation

    def list(
        self,
        limit: int = 100,
        *,
        query: str = "",
        archived: bool = False,
    ) -> list[ConversationListItem]:
        return self.store.list_conversations(
            limit,
            query=query,
            archived=archived,
        )

    def list_page(
        self,
        limit: int = 20,
        *,
        query: str = "",
        archived: bool = False,
        cursor: str = "",
    ) -> ConversationPage:
        safe_limit = max(1, min(limit, 50))
        before_updated_at, before_conversation_id = self._decode_cursor(cursor)
        rows = self.store.list_conversations(
            safe_limit + 1,
            query=query,
            archived=archived,
            before_updated_at=before_updated_at,
            before_conversation_id=before_conversation_id,
        )
        has_more = len(rows) > safe_limit
        conversations = rows[:safe_limit]
        next_cursor = None
        if has_more and conversations:
            last = conversations[-1]
            next_cursor = self._encode_cursor(last.updated_at, last.conversation_id)
        return ConversationPage(
            conversations=conversations,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    def update(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        archived: bool | None = None,
    ) -> ConversationRecord:
        from .data_auth import check_conversation
        check_conversation(self.store, conversation_id)
        if title is None and archived is None:
            raise ValueError("至少需要提供一个要更新的字段")
        normalized_title = None
        if title is not None:
            normalized_title = re.sub(r"\s+", " ", title).strip()
            if not normalized_title:
                raise ValueError("对话标题不能为空")
            normalized_title = normalized_title[:120]
        conversation = self.store.update_conversation(
            conversation_id,
            title=normalized_title,
            archived=archived,
            updated_at=self._now(),
        )
        if conversation is None:
            raise KeyError(conversation_id)
        return conversation

    def delete(self, conversation_id: str) -> None:
        if not self.store.delete_archived_conversation(conversation_id):
            raise KeyError(conversation_id)

    def begin_turn(self, request: ChatRequest) -> tuple[ChatRequest, str]:
        conversation = (
            self.store.get_conversation(request.conversation_id)
            if request.conversation_id
            else None
        )
        if request.conversation_id and conversation is None:
            raise KeyError("Conversation not found")
        if conversation is None:
            conversation = self.create(self._title_from_message(request.message))
            self._import_legacy_history(conversation, request.history)

        history_records = self.store.conversation_messages(
            conversation.conversation_id,
            after_sequence=conversation.summarized_through_sequence,
        )
        prior=self.store.conversation_turn_sequence(conversation.conversation_id,request.client_turn_id) if request.client_turn_id else None
        history = [Message(role=m.role,content=m.content) for m in history_records if prior is None or m.sequence<prior]
        if prior is None and not request._execution_feedback:current = self.store.append_conversation_message(
            conversation.conversation_id,
            role="user",
            content=request.message,
            estimated_tokens=self.estimate_tokens(request.message),
            metadata={
                "client_turn_id": request.client_turn_id,
                "interaction_mode": request.interaction_mode,
                "dataset_id": request.dataset_id,
                "workspace_id": request.workspace_id,
                "selected_skills": request.selected_skills,
            },
            created_at=self._now(),
        )
        prepared = request.model_copy(
            update={
                "conversation_id": conversation.conversation_id,
                "conversation_summary": conversation.summary,
                "history": history,
                "current_workflow_context": self._workflow_context(conversation.conversation_id, request.dataset_id),
                "planning_handoff": False,
                "response_policy": None,
                "assistant_task": {},
            }
        )
        if request._execution_feedback:
            prepared._user_sequence = max((m.sequence for m in history_records if m.role == "user"), default=0)
            if not prepared._user_sequence:
                with self.store.connection() as db:
                    prepared._user_sequence = db.execute("SELECT MAX(sequence) FROM conversation_messages WHERE conversation_id=? AND role='user'",
                                                        (conversation.conversation_id,)).fetchone()[0]
        else:
            prepared._user_sequence = prior if prior is not None else current.sequence
        prepared._conversation_memory = {}
        prepared._history_through_sequence = prepared._user_sequence
        if request._execution_feedback:
            with self.store.connection() as db:
                prepared._history_through_sequence = db.execute(
                    'SELECT MAX(sequence) FROM conversation_messages WHERE conversation_id=?',
                    (conversation.conversation_id,)).fetchone()[0]
        return prepared, conversation.conversation_id

    def _workflow_context(self, conversation_id: str, dataset_id: str | None) -> dict:
        """Load the actual selected steps, including edits absent from chat text.

        A compact snapshot is context for follow-up intent, never authorization to
        run or silently modify a locked plan. Do not carry it into another dataset.
        """
        from .parameter_rules import effective_parameters, input_required

        identity = self.store.conversation_context(conversation_id).get("workflow_id")
        workflow = self.store.get_workflow(identity) if identity else None
        if workflow is None or workflow.dataset_id != dataset_id:
            return {}
        steps = []
        for step in workflow.steps[:20]:
            method = next((m for m in step.method_options if m.id == step.selected_method_id), None)
            if method is None:
                continue
            values = effective_parameters(method, step.selected_parameters)
            steps.append({"id": step.id, "method_id": method.definition_id or method.id,
                "label": method.label,
                "selected_parameters": values,
                "upstream_inputs": {key: {"step_id": binding.upstream_step_id, "output_slot_id": binding.output_slot_id}
                                    for key, binding in step.input_bindings.items() if binding.source == "upstream_output"},
                "inputs": [
                    {"id": slot.id, "type": slot.type, "label": slot.label,
                     "required": input_required(slot, values),
                     "binding_source": step.input_bindings[slot.id].source if slot.id in step.input_bindings else None}
                    for slot in method.input_slots[:40]],
                "missing_required_inputs": step.missing_required_inputs[:40]})
        return {"workflow_id": workflow.workflow_id, "revision": workflow.revision,
                "status": workflow.status, "original_request": workflow.original_request[:2000],
                "task_contract": workflow.task_contract.model_dump(),
                "excluded_capabilities": workflow.excluded_capabilities,
                "steps": steps, "steps_truncated": len(workflow.steps) > 20}

    async def finish_turn(
        self,
        conversation_id: str,
        response: ChatResponse,
        client_turn_id: str | None = None,
    ) -> ConversationMemoryStatus:
        from .operation_context import checkpoint
        checkpoint()
        self.store.append_conversation_message(
            conversation_id,
            role="assistant",
            completed_turn=(client_turn_id,response.model_copy(update={"conversation_id":conversation_id}).model_dump_json()) if client_turn_id else None,
            content=response.content,
            estimated_tokens=self.estimate_tokens(response.content),
            metadata={
                "response_type": response.type,
                "assistant_task": response.assistant_task,
                "client_turn_id": client_turn_id,
                "planning_offer": response.planning_offer,
                "workspace_id": response.workspace_id,
                "workspace_tool_events": response.workspace_tool_events,
                "selected_skills": response.selected_skills,
                "recommended_capabilities": response.recommended_capabilities,
                "workflow_id": response.workflow.workflow_id if response.workflow else None,
                "interaction_mode": response.interaction_mode,
                "workflow_recommended": response.workflow_recommended,
                "workflow_decision_source": response.workflow_decision_source,
                "clarification_required": response.clarification_required,
                "capability_routing": (
                    response.capability_routing.model_dump(mode="json")
                    if response.capability_routing
                    else None
                ),
                "capability_recommendation": (
                    response.capability_recommendation.model_dump(mode="json")
                    if response.capability_recommendation
                    else None
                ),
            },
            created_at=self._now(),
        )
        compacted = await self.compact_if_needed(conversation_id)
        return self.memory_status(conversation_id, compacted_this_turn=compacted)

    async def compact_if_needed(
        self,
        conversation_id: str,
        *,
        force: bool = False,
    ) -> bool:
        conversation = self._require(conversation_id)
        messages = self.store.conversation_messages(
            conversation_id,
            after_sequence=conversation.summarized_through_sequence,
        )
        estimated = self.estimate_tokens(conversation.summary) + sum(
            message.estimated_tokens for message in messages
        )
        if not force and estimated < self.compact_trigger_tokens:
            return False
        keep = min(self.keep_recent_messages, max(2, len(messages) // 2))
        candidates = messages[:-keep]
        if not candidates:
            return False
        try:
            with usage_scope(self.token_usage, conversation_id, reuse_current=True):
                summary = await self.chat_service.compact_history(
                    conversation.summary,
                    candidates,
                )
        except Exception:
            return False
        if not summary.strip():
            return False
        self.store.update_conversation_summary(
            conversation_id,
            summary=summary.strip(),
            summary_version=conversation.summary_version + 1,
            summarized_through_sequence=candidates[-1].sequence,
            updated_at=self._now(),
        )
        return True

    def snapshot(self, conversation_id: str, *, limit: int | None = None, before_sequence: int | None = None) -> ConversationSnapshot:
        conversation = self._require(conversation_id)
        if limit is not None:
            limit = max(1,min(limit,100))
            rows = self.store.conversation_messages(conversation_id,limit=limit+1,
                before_sequence=before_sequence,newest_first=True)
            more = len(rows) > limit
            messages = list(reversed(rows[:limit]))
            return ConversationSnapshot(conversation=conversation,memory=self.memory_status(conversation_id),
                messages=messages,has_more=more,before_sequence=messages[0].sequence if more and messages else None,
                context=self.store.conversation_context(conversation_id),
                **self._token_usage_snapshot(conversation_id, messages))
        messages = self.store.conversation_messages(conversation_id)
        recent = self.store.conversation_messages(
            conversation_id,
            after_sequence=conversation.summarized_through_sequence,
        )
        return ConversationSnapshot(
            **self._token_usage_snapshot(conversation_id, messages),
            conversation=conversation,
            memory=self.memory_status(conversation_id),
            messages=messages,
            recent_messages=recent,
        )

    def _token_usage_snapshot(self, conversation_id, messages):
        from .data_auth import principal
        task = None
        with self.store.connection() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_tasks'").fetchone():
                row = db.execute("SELECT payload FROM assistant_tasks WHERE conversation_id=? AND actor=?",
                                 (conversation_id, principal.get().name)).fetchone()
                value = json.loads(row[0]) if row else {}
                if value.get("protocol_version") == 2:
                    from .agent_identity import public_understanding
                    task = public_understanding(value)
        turn_ids = list({m.metadata.get("client_turn_id") for m in messages if m.metadata.get("client_turn_id")})
        return {
            "assistant_task": task,
            "token_usage": self.token_usage.summary(conversation_id),
            "turn_token_usage": self.token_usage.turn_summaries(conversation_id, turn_ids),
        }

    def memory_status(
        self,
        conversation_id: str,
        *,
        compacted_this_turn: bool = False,
    ) -> ConversationMemoryStatus:
        conversation = self._require(conversation_id)
        with self.store._connect() as connection:
            row = connection.execute("""SELECT COUNT(*) AS count,COALESCE(SUM(estimated_tokens),0) AS tokens
                FROM conversation_messages WHERE conversation_id=? AND sequence>?""",
                (conversation_id,conversation.summarized_through_sequence)).fetchone()
        return ConversationMemoryStatus(summary_version=conversation.summary_version,
            summarized_messages=conversation.summarized_through_sequence,recent_messages=row["count"],
            estimated_recent_tokens=row["tokens"],compacted_this_turn=compacted_this_turn)

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Conservative local estimate for mixed Chinese and ASCII text."""
        cjk = len(re.findall(r"[\u3400-\u9fff]", text))
        non_cjk = max(0, len(text) - cjk)
        return max(1, cjk + math.ceil(non_cjk / 4) + 4)

    def _import_legacy_history(
        self,
        conversation: ConversationRecord,
        history: list[Message],
    ) -> None:
        for message in history[-20:]:
            self.store.append_conversation_message(
                conversation.conversation_id,
                role=message.role,
                content=message.content,
                estimated_tokens=self.estimate_tokens(message.content),
                metadata={"source": "legacy_client_history"},
                created_at=self._now(),
            )

    def _require(self, conversation_id: str) -> ConversationRecord:
        conversation = self.store.get_conversation(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        return conversation

    @staticmethod
    def _title_from_message(message: str) -> str:
        title = re.sub(r"\s+", " ", message).strip()
        return title[:48] or "新数据处理对话"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _encode_cursor(updated_at: str, conversation_id: str) -> str:
        payload = json.dumps(
            {"updated_at": updated_at, "conversation_id": conversation_id},
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[str | None, str | None]:
        if not cursor:
            return None, None
        try:
            if len(cursor) > 1024:
                raise ValueError
            padding = "=" * (-len(cursor) % 4)
            payload = json.loads(base64.b64decode(cursor + padding, altchars=b"-_", validate=True))
            updated_at = payload["updated_at"]
            conversation_id = payload["conversation_id"]
            if not isinstance(updated_at, str) or not updated_at:
                raise ValueError
            if not isinstance(conversation_id, str) or not conversation_id:
                raise ValueError
            return updated_at, conversation_id
        except (binascii.Error, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("无效的对话分页游标") from exc
