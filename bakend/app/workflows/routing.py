"""WorkflowRouter: semantic task decisions and hierarchical capability navigation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..models import (
    CapabilityRecommendation,
    CapabilityRecommendationItem,
    ChatRequest,
)
from ..response_policy import ResponsePolicy
from ..task_contract import TaskContract
from ..task_scope import existing_input_capabilities

if TYPE_CHECKING:
    from ..capability_registry import CapabilityRegistry
    from ..capability_retriever import CapabilityRetriever
    from ..capability_tree import CapabilityTree
    from ..chat_service import ChatService
    from ..dataset_service import DatasetService
    from .selector import CapabilitySelector


class WorkflowRouter:
    def __init__(
        self,
        selector: CapabilitySelector,
        chat_service: ChatService,
        dataset_service: DatasetService,
        capability_tree: CapabilityTree | None,
        registry: CapabilityRegistry,
        capability_retriever: CapabilityRetriever | None,
    ) -> None:
        self.selector = selector
        self.chat_service = chat_service
        self.dataset_service = dataset_service
        self.capability_tree = capability_tree
        self.registry = registry
        self.capability_retriever = capability_retriever

    async def assess_task(
        self, request: ChatRequest, *, conversation: bool = False
    ) -> dict[str, Any]:
        """Route through shallow tree frontiers before loading any Skill body."""
        if self.capability_tree is None:
            raise RuntimeError("CapabilityTree 尚未配置")
        dataset = (
            self.dataset_service.get(request.dataset_id) if request.dataset_id else None
        )
        allowed = [node.id for node in self.capability_tree.leaves()]
        heuristic_ids = self.selector.heuristic_capabilities(request, allowed)
        retrieved = (
            self.capability_retriever.retrieve(
                request.message,
                request.selected_skills,
                limit=8,
            )
            if self.capability_retriever is not None
            else []
        )
        retrieved_ids = [
            item.capability_id for item in retrieved if item.capability_id in allowed
        ]
        catalog_skill_ids = list(dict.fromkeys([*heuristic_ids, *retrieved_ids]))
        intent_catalog = self.selector.specific_intent_catalog(
            allowed, catalog_skill_ids
        )
        intent = await self.chat_service.normalize_task_intent(
            request,
            intent_catalog,
            heuristic_ids,
            artifact_catalog=[
                {"id": a.id, "label": a.label, "description": a.description}
                for a in self.registry.artifact_types()
            ],
        )
        request.task_contract = TaskContract.model_validate(
            intent.get("task_contract", {})
        )
        if intent.get("source") == "heuristic_after_model_error":
            # A failed semantic call cannot certify whether a mentioned artifact
            # is an input or an operation. Do not build a workflow from that guess.
            return {
                "decision": "task",
                "capability_ids": [],
                "recommend_skills": False,
                "recommend_workflow": False,
                "decision_source": "intent_unavailable",
                "clarification_required": True,
                "clarification_question": "本次任务理解服务未返回有效结果，尚未生成流程。请重试；这不表示平台没有可执行方法。",
                "task_intent": intent,
                "routing_trace": {
                    "strategy": "semantic",
                    "intent_source": intent["source"],
                    "confidence": intent.get("confidence", 0),
                    "diagnostics": intent.get("diagnostics", []),
                },
            }
        policy = ResponsePolicy.from_intent(intent)
        if conversation and request.interaction_mode == "chat" and policy.plans:
            policy = policy.model_copy(
                update={"action": "explore", "needs_catalog": True}
            )
        request.response_policy = policy
        if not policy.plans:
            # Capability names and lexical hints describe subject matter, not a
            # request to execute. Do not run the capability tree for discussion.
            return {
                "decision": "chat",
                "capability_ids": [],
                "recommend_skills": False,
                "recommend_workflow": False,
                "decision_source": "semantic_response_policy",
                "response_policy": policy.model_dump(),
                "task_intent": intent,
                "clarification_required": policy.action == "clarify",
                "clarification_question": intent.get("clarification_question", ""),
                "routing_trace": {
                    "strategy": "semantic",
                    "intent_source": intent.get("source", ""),
                    "confidence": intent.get("confidence", 0),
                    "normalized_task": intent.get("task_brief", ""),
                    "response_policy": policy.model_dump(),
                    "diagnostics": intent.get("diagnostics", []),
                },
            }
        routing = await self.route_capabilities_hierarchically(
            request,
            allowed,
            dataset.inspection.model_dump(mode="json") if dataset else None,
            intent,
        )
        # A recognized output goal can be planned even if the capability tree
        # supplied no leaf. Method contracts, not tree reachability, decide this.
        if (
            request.task_contract.goal_artifact_types
            and intent.get("decision") == "task"
        ):
            routing["decision"] = "task"
            routing["clarification_required"] = False
        routing_trace = routing.setdefault("routing_trace", {})
        routing_trace["task_contract"] = request.task_contract.model_dump()
        routing_trace["response_policy"] = policy.model_dump()
        routing_trace["diagnostics"] = intent.get("diagnostics", [])
        routing_trace["retrieval"] = {
            "strategy": "local_sparse_skill_shortlist",
            "candidate_count": len(retrieved),
            "candidates": [item.public_dict() for item in retrieved],
            "intent_catalog_count": len(intent_catalog),
            "hint_capability_ids": heuristic_ids,
        }

        capability_ids = [item for item in routing["capability_ids"] if item in allowed]
        structured_ids = [
            capability_id
            for capability_id in capability_ids
            if self.registry.methods_for(capability_id)
        ]
        advisory_ids = [
            capability_id
            for capability_id in capability_ids
            if capability_id not in structured_ids
        ]
        contract_task = (
            bool(request.task_contract.goal_artifact_types)
            and intent.get("decision") == "task"
        )
        is_task = routing["decision"] == "task" and (
            bool(capability_ids) or contract_task
        )
        if contract_task:
            advisory_ids = []
        return {
            **routing,
            "response_policy": policy.model_dump(),
            "capability_ids": capability_ids,
            "structured_capability_ids": structured_ids,
            "advisory_capability_ids": advisory_ids,
            "recommend_skills": is_task,
            "recommend_workflow": is_task and not advisory_ids,
            "clarification_required": bool(routing.get("clarification_required")),
            "clarification_question": str(routing.get("clarification_question") or ""),
            "task_intent": intent,
            "workflow_id": (
                "dynamic-capability-plan" if is_task and not advisory_ids else None
            ),
        }

    async def route_capabilities_hierarchically(
        self,
        request: ChatRequest,
        allowed_leaf_ids: list[str],
        dataset_context: dict[str, Any] | None,
        intent: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.capability_tree is None:
            raise RuntimeError("CapabilityTree 尚未配置")

        tree = self.capability_tree
        intent = intent or {}
        reused_inputs = existing_input_capabilities(request.message) | {
            item
            for item in intent.get("input_capability_ids", [])
            if item in allowed_leaf_ids
        }
        allowed_leaf_ids = [
            item for item in allowed_leaf_ids if item not in reused_inputs
        ]
        heuristic_leaves = self.selector.heuristic_capabilities(
            request, allowed_leaf_ids
        )
        semantic_leaves = [
            str(item)
            for item in intent.get("candidate_capability_ids") or []
            if str(item) in allowed_leaf_ids
            and str(item) not in existing_input_capabilities(request.message)
        ]
        explicit_skill_leaves = self.selector.explicit_skill_capabilities(
            request.message,
            allowed_leaf_ids,
        )
        semantic_leaves = self.selector.drop_shadowed_modality_skills(
            semantic_leaves,
            explicit_skill_leaves,
        )
        confidence = float(intent.get("confidence") or 0.0)
        # A complete semantic result is authoritative. Keyword hints must not
        # turn mentioned inputs into additional requested producer operations.
        if (
            intent.get("source", "").startswith("model")
            and intent.get("coverage") == "complete"
            and confidence >= 0.7
        ):
            heuristic_leaves = []
        routed_catalog = self.selector.specific_intent_catalog(
            allowed_leaf_ids,
            [*semantic_leaves, *heuristic_leaves],
        )
        anchor_leaves = self.selector.prune_generic_skill_leaves(
            list(dict.fromkeys([*semantic_leaves, *heuristic_leaves]))
        )
        full_tree_payload_chars = len(
            json.dumps(tree.compact(), ensure_ascii=False, separators=(",", ":"))
        )
        intent_payload_chars = len(json.dumps(routed_catalog, ensure_ascii=False))
        semantic_trace = {
            "strategy": "semantic",
            "levels": [],
            "candidates_seen": len(routed_catalog),
            "payload_chars": intent_payload_chars,
            "full_tree_payload_chars": full_tree_payload_chars,
            "payload_ratio": round(
                intent_payload_chars / full_tree_payload_chars,
                4,
            )
            if full_tree_payload_chars
            else 0.0,
            "selected_paths": [tree.label_path_for(item) for item in anchor_leaves],
            "intent_source": str(intent.get("source") or "unknown"),
            "normalized_task": str(intent.get("task_brief") or request.message)[:600],
            "semantic_hints": semantic_leaves,
            "confidence": confidence,
            "reused_input_capabilities": sorted(reused_inputs),
        }
        if intent.get("needs_clarification") and not anchor_leaves:
            return {
                "decision": "task",
                "capability_ids": [],
                "reason": str(intent.get("reason") or "需要补充任务信息。"),
                "decision_source": "semantic_clarification",
                "clarification_required": True,
                "clarification_question": str(
                    intent.get("clarification_question") or ""
                ),
                "routing_trace": semantic_trace,
            }
        if (
            intent.get("decision") == "task"
            and anchor_leaves
            and intent.get("coverage") == "complete"
            and str(intent.get("source", "")).startswith("model")
            and confidence >= 0.7
        ):
            return {
                "decision": "task",
                "capability_ids": anchor_leaves,
                "reason": str(intent.get("reason") or "标准化任务意图已匹配具体能力。"),
                "decision_source": "semantic_intent",
                "clarification_required": False,
                "clarification_question": "",
                "routing_trace": semantic_trace,
            }
        if intent.get("decision") == "chat" and not anchor_leaves and confidence >= 0.7:
            return {
                "decision": "chat",
                "capability_ids": [],
                "reason": str(
                    intent.get("reason") or "当前请求不需要生成数据处理流程。"
                ),
                "decision_source": "semantic_intent",
                "clarification_required": False,
                "clarification_question": "",
                "routing_trace": semantic_trace,
            }
        if self.chat_service.mode != "model":
            return {
                "decision": "task" if anchor_leaves else "chat",
                "capability_ids": anchor_leaves,
                "reason": (
                    "需求与能力树中的现有能力匹配。"
                    if anchor_leaves
                    else "当前需求不需要任务建议，或未匹配到现有能力。"
                ),
                "decision_source": "heuristic",
                "routing_trace": {
                    "strategy": "heuristic",
                    "levels": [],
                    "candidates_seen": 0,
                    "payload_chars": 0,
                    "full_tree_payload_chars": full_tree_payload_chars,
                    "payload_ratio": 0.0,
                    "selected_paths": [
                        tree.label_path_for(capability_id)
                        for capability_id in anchor_leaves
                    ],
                },
            }

        frontier = [tree.root_id]
        selected_leaves: list[str] = []
        task_brief = str(intent.get("task_brief") or "")[:600]
        reason = "当前需求不需要任务建议，或未匹配到现有能力。"
        levels: list[dict[str, Any]] = []
        used_fallback = False
        decision = "chat"

        # The reviewed tree is currently at most four edges deep. Six levels leave
        # room for taxonomy growth while still bounding model calls and token use.
        for level in range(1, 7):
            candidate_groups = tree.routing_frontier(frontier)
            if not candidate_groups:
                break
            candidate_payload = json.dumps(
                candidate_groups,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            fallback_hops = tree.next_hops_for(frontier, anchor_leaves)
            result = await self.chat_service.route_capability_level(
                request,
                candidate_groups,
                dataset_context,
                level=level,
                task_brief=task_brief,
                fallback_node_ids=fallback_hops,
                first_level=level == 1,
            )
            selected_node_ids = list(result.get("selected_node_ids") or [])
            source = str(result.get("decision_source") or "model")
            if fallback_hops:
                merged_nodes = list(
                    dict.fromkeys([*fallback_hops, *selected_node_ids])
                )[:4]
                if (
                    merged_nodes != selected_node_ids
                    or result.get("decision") != "task"
                ):
                    selected_node_ids = merged_nodes
                    result["decision"] = "task"
                    source = "model_with_semantic_anchors"
            used_fallback = used_fallback or source != "model"
            levels.append(
                {
                    "level": level,
                    "parent_ids": list(frontier),
                    "candidate_count": sum(
                        len(group.get("candidates", [])) for group in candidate_groups
                    ),
                    "payload_chars": len(candidate_payload),
                    "selected_node_ids": selected_node_ids,
                    "source": source,
                }
            )
            task_brief = str(result.get("task_brief") or task_brief)[:600]
            reason = str(result.get("reason") or reason)
            decision = str(result.get("decision") or "chat")
            if decision != "task":
                break

            next_frontier: list[str] = []
            for node_id in selected_node_ids:
                node = tree.node(node_id)
                if node is None:
                    continue
                if node.is_leaf:
                    if node.id in allowed_leaf_ids and node.id not in selected_leaves:
                        selected_leaves.append(node.id)
                elif node.id not in next_frontier:
                    next_frontier.append(node.id)
            if not next_frontier:
                break
            frontier = next_frontier

        if decision == "task" and not selected_leaves and anchor_leaves:
            selected_leaves = anchor_leaves
            used_fallback = True
            reason = "分层路由未到达叶子，已使用本地能力匹配结果。"
        if anchor_leaves:
            selected_leaves = list(dict.fromkeys([*selected_leaves, *anchor_leaves]))
        selected_leaves = self.selector.prune_generic_skill_leaves(selected_leaves)
        selected_leaves = [
            item
            for item in selected_leaves
            if item not in existing_input_capabilities(request.message)
        ]
        decision = "task" if selected_leaves else "chat"

        payload_chars = sum(item["payload_chars"] for item in levels)
        return {
            "decision": decision,
            "capability_ids": selected_leaves if decision == "task" else [],
            "reason": reason,
            "decision_source": (
                "hierarchical_with_semantic"
                if used_fallback and semantic_leaves
                else "hierarchical_with_heuristic"
                if used_fallback
                else "model_hierarchical"
            ),
            "routing_trace": {
                "strategy": "hierarchical",
                "levels": levels,
                "candidates_seen": sum(item["candidate_count"] for item in levels),
                "payload_chars": payload_chars,
                "full_tree_payload_chars": full_tree_payload_chars,
                "payload_ratio": round(
                    payload_chars / full_tree_payload_chars,
                    4,
                )
                if full_tree_payload_chars
                else 0.0,
                "selected_paths": [
                    tree.label_path_for(capability_id)
                    for capability_id in selected_leaves
                ],
                "intent_source": str(intent.get("source") or "unknown"),
                "normalized_task": str(intent.get("task_brief") or request.message)[
                    :600
                ],
                "semantic_hints": semantic_leaves,
                "reused_input_capabilities": sorted(reused_inputs),
                "confidence": confidence,
            },
        }

    def build_capability_recommendation(
        self,
        routing: dict[str, Any],
    ) -> CapabilityRecommendation:
        if self.capability_tree is None:
            raise RuntimeError("CapabilityTree 尚未配置")
        items: list[CapabilityRecommendationItem] = []
        for capability_id in routing.get("capability_ids") or []:
            node = self.capability_tree.leaf(str(capability_id))
            if node is None:
                continue
            items.append(
                CapabilityRecommendationItem(
                    capability_id=node.id,
                    label=node.label,
                    description=node.description,
                    execution_level=(
                        "structured"
                        if any(
                            method.execution_mode == "runner"
                            for method in self.registry.methods_for(node.id)
                        )
                        else "planning"
                        if self.registry.methods_for(node.id)
                        else "advisory"
                    ),
                    skill_ids=node.skill_ids,
                    tree_path=self.capability_tree.label_path_for(node.id),
                )
            )
        labels = [item.label for item in items]
        plan_kind = "atomic" if len(items) == 1 else "composite"
        return CapabilityRecommendation(
            title=labels[0]
            if plan_kind == "atomic" and labels
            else "Skill 能力组合建议",
            summary=" → ".join(labels) if labels else str(routing.get("reason") or ""),
            plan_kind=plan_kind,
            fully_executable=all(
                item.execution_level == "structured" for item in items
            ),
            items=items,
        )
