"""Conversation orchestration: resolve trusted context, route, then persist the answer.

The coordinator owns turn ordering only. File tools, planning and model transport
are injected services; structured Runner submission remains an explicit API action.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from .chat_service import ChatService
from .conversation_service import ConversationService
from .dataset_files import DatasetFiles
from .errors import ServiceError
from .models import ChatRequest, ChatResponse
from .skill_loader import SkillLoader
from .task_contract import PlanNotMatched
from .workflow_service import WorkflowService
from .workspace_service import WorkspaceService
from .workspace_tools import WorkspaceTools
from .data_auth import principal, inplace_available
from .operation_context import OperationCancelled, checkpoint


@dataclass
class ChatCoordinator:
    conversation_service: ConversationService
    workspace_service: WorkspaceService
    dataset_files: DatasetFiles
    workspace_tools: WorkspaceTools
    skill_loader: SkillLoader
    workflow_service: WorkflowService
    chat_service: ChatService

    def resolve_context(self, request: ChatRequest) -> ChatRequest:
        workspace_id = request.workspace_id
        if request.conversation_id:
            conversation = self.conversation_service.store.get_conversation(
                request.conversation_id
            )
            if conversation:
                if (
                    workspace_id
                    and conversation.workspace_id
                    and workspace_id != conversation.workspace_id
                ):
                    raise ServiceError(409, "工作区与对话不一致，请先显式切换工作区")
                workspace_id = workspace_id or conversation.workspace_id
        try:
            workspace = (
                self.workspace_service.get(workspace_id) if workspace_id else None
            )
            dataset_root = (
                self.dataset_files.reader(request.dataset_id).root()
                if request.dataset_id
                else None
            )
            context = json.dumps(
                {
                    "output_directory_id": workspace_id,
                    "output_root": workspace.root_path if workspace else None,
                    "output_manageable": workspace.manageable if workspace else False,
                    "dataset_id": request.dataset_id,
                    "dataset_root": str(dataset_root) if dataset_root else None,
                    "dataset_access": "read_only",
                    "default_output": workspace.root_path if workspace else None,
                    "data_actor": principal.get().name,
                    "inplace_authorization_available": inplace_available(),
                    "data_operations": self.workspace_tools.data_operations.context(request.conversation_id)
                        if self.workspace_tools.data_operations and request.conversation_id else [],

                },
                ensure_ascii=False,
            )
        except (KeyError, ValueError, OSError) as exc:
            raise ServiceError(409, str(exc)) from exc
        # Never accept a root/context supplied by an HTTP client as authoritative.
        return request.model_copy(
            update={
                "workspace_id": workspace_id,
                "workspace_context": context if workspace or dataset_root else "",
                "workspace_root_path": workspace.root_path if workspace else None,
            }
        )

    async def reply(self, request: ChatRequest) -> ChatResponse:
        # Old clients may still send chat/task; both enter the same assistant.
        # Execution permission is granted only by the existing manual Run APIs.
        request = request.model_copy(update={'interaction_mode': 'task'})
        checkpoint()
        request = self.resolve_context(request)
        try:
            request, conversation_id = self.conversation_service.begin_turn(request)
            if request.workspace_id:
                self.workspace_service.bind_conversation(
                    conversation_id, request.workspace_id
                )
        except KeyError as exc:
            raise ServiceError(
                status_code=404, detail="Conversation not found"
            ) from exc

        async def finalize(response: ChatResponse) -> ChatResponse:
            checkpoint()
            policy = request.response_policy
            if (
                policy
                and policy.offer_workflow
                and policy.action in {"answer", "explore"}
                and not response.workflow
            ):
                focus = policy.focus or request.message
                offer = (
                    "请根据刚才讨论的建议，为以下需求生成可审阅的流程："
                    + focus
                    + "。保留此前仍适用的输入复用与排除要求；如仍有不同路线，请先让我选择。只生成流程，不执行。"
                    if request.language.startswith("zh")
                    else "Create a reviewable workflow for the goal we just discussed: "
                    + focus
                    + ". Preserve applicable input-reuse restrictions and exclusions. If alternatives remain, ask me to select a route. Do not execute."
                )
                response = response.model_copy(update={"planning_offer": offer})
            response = response.model_copy(
                update={
                    "workspace_id": request.workspace_id,
                    "workspace_tool_events": self.workspace_service.events(
                        request.client_turn_id
                    )
                    if request.client_turn_id
                    else [],
                }
            )
            memory = await self.conversation_service.finish_turn(
                conversation_id, response, request.client_turn_id
            )
            return response.model_copy(
                update={
                    "conversation_id": conversation_id,
                    "memory": memory,
                }
            )

        requested_names = list(dict.fromkeys(request.selected_skills))
        selected = [
            skill for name in requested_names if (skill := self.skill_loader.find(name))
        ]
        auto_selected = False
        if not requested_names:
            selected = self.skill_loader.infer(request.message)
            auto_selected = bool(selected)
        try:
            # Task-mode users get a tool-capable assistant. Scientific planning
            # is an explicit handoff, rather than a gate on all data operations.
            assistant_answer = None
            assistant_started = bool(self.workspace_tools.data_operations and
                                     self.chat_service.mode == "model")
            if assistant_started:
                request.response_policy = None
                assistant_answer = await self.workspace_tools.reply(request)
                if assistant_answer is not None:
                    if isinstance(assistant_answer, ChatResponse):
                        return await finalize(assistant_answer)
                    return await finalize(ChatResponse(
                        content=assistant_answer, provider_used=self.chat_service.provider_name,
                        model_used=self.chat_service.model or "assistant",
                        mode=self.chat_service.mode, selected_skills=[s.name for s in selected],
                        interaction_mode=request.interaction_mode,
                        workflow_decision_source="assistant_tools"))
            # Route processing intent before any open-ended filesystem/catalog loop.
            # A lexical method search must never gate access to the semantic planner.
            decision = await self.workflow_service.assess_task(
                request, conversation=True
            )
            routed_task = decision and (
                decision.get("recommend_workflow")
                or decision.get("recommend_skills")
                or decision.get("clarification_required")
            )
            needs_lookup = (
                request.response_policy is None or request.response_policy.needs_tools
            )
            workspace_answer = (
                None
                if assistant_started or routed_task or not needs_lookup
                else await self.workspace_tools.reply(request)
            )
            # A bounded lookup can discover a processing need after the initial
            # intent was chat. Reassess once with its evidence; never rerun the tool
            # loop or force a method merely because a lookup budget was exhausted.
            if (
                workspace_answer is None
                and request.interaction_mode == "task"
                and request.planning_handoff
            ):
                decision = await self.workflow_service.assess_task(request)
            if workspace_answer is not None:
                return await finalize(
                    ChatResponse(
                        content=workspace_answer,
                        provider_used=self.chat_service.provider_name,
                        model_used=self.chat_service.model or "conversation-demo",
                        mode=self.chat_service.mode,
                        selected_skills=[skill.name for skill in selected],
                        interaction_mode=request.interaction_mode,
                        workflow_decision_source=decision.get(
                            "decision_source", "not_applicable"
                        )
                        if decision
                        else "not_applicable",
                        capability_routing=decision.get("routing_trace")
                        if decision
                        else None,
                    )
                )
            if request.interaction_mode == "task":
                if decision.get("clarification_required"):
                    return await finalize(
                        ChatResponse(
                            type="done",
                            content=(
                                str(
                                    decision.get("clarification_question") or ""
                                ).strip()
                                or "请再说明希望完成的具体数据处理目标。"
                            ),
                            provider_used=self.chat_service.provider_name,
                            model_used=self.chat_service.model or "conversation-demo",
                            mode=self.chat_service.mode,
                            selected_skills=[skill.name for skill in selected],
                            auto_selected=auto_selected,
                            interaction_mode="task",
                            workflow_recommended=False,
                            workflow_decision_source=decision["decision_source"],
                            clarification_required=True,
                            recommended_capabilities=[],
                            capability_routing=decision.get("routing_trace"),
                        )
                    )
                if decision["recommend_workflow"]:
                    await self.workspace_tools.inspect_for_plan(
                        request, decision, self.workflow_service.registry
                    )
                    workflow, content = await self.workflow_service.draft(
                        request, decision
                    )
                    related_skill_ids = {
                        skill_id
                        for step in workflow.steps
                        if (
                            node := self.workflow_service.capability_tree.leaf(
                                step.capability
                            )
                        )
                        is not None
                        for skill_id in node.skill_ids
                    }
                    selected = [
                        skill
                        for skill_id in sorted(related_skill_ids)
                        if (skill := self.skill_loader.find(skill_id)) is not None
                    ]
                    auto_selected = bool(selected) and {
                        skill.name for skill in selected
                    } != set(requested_names)
                    return await finalize(
                        ChatResponse(
                            type="workflow",
                            content=content,
                            provider_used=self.chat_service.provider_name,
                            model_used=self.chat_service.model or "workflow-demo",
                            mode=self.chat_service.mode,
                            selected_skills=[skill.name for skill in selected],
                            auto_selected=auto_selected,
                            interaction_mode="task",
                            workflow_recommended=True,
                            workflow_decision_source=decision["decision_source"],
                            recommended_capabilities=workflow.requested_capabilities,
                            capability_routing=decision.get("routing_trace"),
                            workflow=workflow,
                        )
                    )
                if decision.get("recommend_skills"):
                    recommendation = (
                        self.workflow_service.build_capability_recommendation(decision)
                    )
                    related_skill_ids = {
                        skill_id
                        for item in recommendation.items
                        for skill_id in item.skill_ids
                    }
                    selected = [
                        skill
                        for skill_id in sorted(related_skill_ids)
                        if (skill := self.skill_loader.find(skill_id)) is not None
                    ]
                    auto_selected = bool(selected) and {
                        skill.name for skill in selected
                    } != set(requested_names)
                    candidate_context = self.workflow_service.recommendation_context(
                        decision["capability_ids"]
                    )
                    content = await self.chat_service.explain_capability_recommendation(
                        request,
                        candidate_context,
                        str(decision.get("reason") or ""),
                    )
                    return await finalize(
                        ChatResponse(
                            type="recommendation",
                            content=content,
                            provider_used=self.chat_service.provider_name,
                            model_used=self.chat_service.model or "conversation-demo",
                            mode=self.chat_service.mode,
                            selected_skills=[skill.name for skill in selected],
                            auto_selected=auto_selected,
                            interaction_mode="task",
                            workflow_recommended=False,
                            workflow_decision_source=decision["decision_source"],
                            recommended_capabilities=decision["capability_ids"],
                            capability_routing=decision.get("routing_trace"),
                            capability_recommendation=recommendation,
                        )
                    )
                response = await self.chat_service.reply(
                    request,
                    selected,
                    auto_selected=auto_selected,
                )
                return await finalize(
                    ChatResponse.model_validate(
                        {
                            **response.model_dump(mode="python"),
                            "workflow_recommended": False,
                            "workflow_decision_source": decision["decision_source"],
                            "capability_routing": decision.get("routing_trace"),
                        }
                    )
                )
            return await finalize(
                await self.chat_service.reply(
                    request, selected, auto_selected=auto_selected
                )
            )
        except PlanNotMatched as exc:
            advice = await self.chat_service.explain_planning_block(
                request,
                exc,
                self.workflow_service.planning_advice_context(exc.diagnostics),
            )
            return await finalize(
                ChatResponse(
                    type="done",
                    content=advice,
                    provider_used=self.chat_service.provider_name,
                    model_used=self.chat_service.model or "workflow-demo",
                    mode=self.chat_service.mode,
                    selected_skills=[skill.name for skill in selected],
                    interaction_mode=request.interaction_mode,
                    workflow_recommended=False,
                    workflow_decision_source="candidate_mismatch",
                    capability_routing={
                        **(decision.get("routing_trace", {}) if decision else {}),
                        "retrieval": exc.diagnostics,
                    },
                )
            )
        except OperationCancelled:
            raise
        except httpx.HTTPStatusError as exc:
            raise ServiceError(
                status_code=502,
                detail=f"模型服务请求失败（HTTP {exc.response.status_code}）",
            ) from exc
        except (
            httpx.RequestError,
            RuntimeError,
            KeyError,
            IndexError,
            TypeError,
        ) as exc:
            raise ServiceError(
                status_code=502, detail=f"模型服务未返回有效回复：{exc}"
            ) from exc
