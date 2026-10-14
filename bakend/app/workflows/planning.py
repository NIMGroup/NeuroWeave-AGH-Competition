"""WorkflowPlanner: build validated graphs in memory; callers own revision checks and commits."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ..method_planning import validate_plan
from ..models import (
    ChatRequest,
)
from ..task_contract import PlanNotMatched, TaskContract
from ..task_scope import existing_input_capabilities
from ..workflow_models import (
    InputBinding,
    Workflow,
    WorkflowStep,
)
from ..workflow_ordering import order_capabilities
from . import contracts, inputs

if TYPE_CHECKING:
    from ..capability_registry import CapabilityRegistry
    from ..capability_tree import CapabilityTree
    from ..chat_service import ChatService
    from ..dataset_service import DatasetService
    from ..method_planning import MethodContractIndex
    from .catalog import MethodCatalog
    from .state import WorkflowState


class WorkflowPlanner:
    def __init__(
        self,
        catalog: MethodCatalog,
        state: WorkflowState,
        chat_service: ChatService,
        dataset_service: DatasetService,
        capability_tree: CapabilityTree | None,
        registry: CapabilityRegistry,
        method_index: MethodContractIndex,
    ) -> None:
        self.catalog = catalog
        self.state = state
        self.chat_service = chat_service
        self.dataset_service = dataset_service
        self.capability_tree = capability_tree
        self.registry = registry
        self.method_index = method_index

    def planning_advice_context(self, diagnostics):
        """Use already-retrieved metadata; no new filesystem scan or runtime probes."""
        self.method_index.refresh()
        # Fairly interleave ranked retrieval rounds. An unranked semantic page
        # must not evict the best initial candidates when explaining a failure.
        sources = [
            s.get("returned_methods", []) for s in diagnostics.get("searches", [])
        ]
        sources.append(diagnostics.get("semantic_scan", {}).get("candidate_ids", []))
        ids = []
        for rank in range(max(map(len, sources), default=0)):
            for source in sources:
                if (
                    rank < len(source)
                    and source[rank] in self.method_index.methods
                    and source[rank] not in ids
                ):
                    ids.append(source[rank])
            if len(ids) >= 20:
                break
        return [
            {
                "id": m.id,
                "description": m.description[:900],
                "inputs": [s.model_dump(exclude_defaults=True) for s in m.input_slots],
                "outputs": [
                    s.model_dump(exclude_defaults=True) for s in m.output_slots
                ],
            }
            for identity in ids[:20]
            if (m := self.method_index.methods.get(identity)) is not None
        ]

    async def build_contract_plan(self, request, routing, existing=None):
        contract = request.task_contract
        capabilities = routing.get("capability_ids", [])
        diagnostics = {
            "searches": [],
            "model_errors": [],
            "validation_errors": [],
            "proposed_graphs": [],
        }
        plan = None
        last_error = "当前候选尚未形成符合需求的流程"
        # Start small; widen once on mismatch/invalid graph. Budgets are explicit
        # and diagnostics distinguish partial search from a proven absence.
        from ..library_retrieval import compact_catalog
        from ..library_retrieval import retrieve as hybrid_retrieve

        semantic_ids = []
        if request._registered_plan is not None:
            self.method_index.refresh()
            try:
                validate_plan(request._registered_plan, contract, self.registry,
                              allowed_ids=set(self.method_index.methods), requested_capabilities=capabilities)
            except (ValueError, KeyError) as exc:
                raise PlanNotMatched('已知方法方案校验失败：'+str(exc), {'validation_errors':[str(exc)]}) from exc
            plan = request._registered_plan
            diagnostics['strategy'] = 'assistant_registered_graph'
        for attempt, (limit, depth) in enumerate(() if plan is not None else ((32, 3), (80, 5))):
            if attempt and hasattr(self.chat_service, "search_library_semantically"):
                # Bounded full-metadata fallback catches valid methods outside the
                # first route/word match. It never loads all parameter schemas.
                rows = compact_catalog(self.method_index)
                scanned = 0
                incomplete_pages = []
                for start in range(0, min(len(rows), 800), 100):
                    found, errors = await self.chat_service.search_library_semantically(
                        request, rows[start : start + 100]
                    )
                    semantic_ids.extend(found)
                    diagnostics["model_errors"].extend(errors)
                    if errors and not found:
                        incomplete_pages.append(start // 100)
                    scanned += len(rows[start : start + 100])
                diagnostics["semantic_scan"] = {
                    "scanned": scanned,
                    "total": len(rows),
                    "truncated": scanned < len(rows) or bool(incomplete_pages),
                    "incomplete_pages": incomplete_pages,
                    "candidate_ids": list(dict.fromkeys(semantic_ids)),
                }
            identities, search = hybrid_retrieve(
                self.method_index,
                contract,
                capabilities,
                message=request.message,
                limit=limit,
                max_depth=depth,
                semantic_ids=semantic_ids,
            )
            diagnostics["searches"].append(search)
            if not identities:
                continue
            candidate, errors = await self.chat_service.plan_registered_methods(
                request,
                self.method_index.context(identities, self.catalog.runner_registry),
                diagnostics,
            )
            diagnostics["model_errors"].extend(errors)
            diagnostics["proposed_graphs"].append(candidate.model_dump())
            try:
                validate_plan(
                    candidate,
                    contract,
                    self.registry,
                    allowed_ids=set(identities),
                    requested_capabilities=capabilities,
                )
                plan = candidate
                break
            except (ValueError, KeyError) as exc:
                last_error = str(exc)
                diagnostics["validation_errors"].append(last_error)
        if plan is None:
            # This is a full registry check, independent of shortlist/scan budgets.
            # A command may exist while its output is still only a generic file;
            # that is a missing typed contract, not evidence of absent software.
            self.method_index.refresh()
            missing_outputs = [
                kind
                for kind in contract.goal_artifact_types
                if not self.method_index.producers.get(kind)
            ]
            if missing_outputs:
                labels = {
                    item.id: item.label for item in self.registry.artifact_types()
                }
                diagnostics["unregistered_goal_outputs"] = missing_outputs
                names = "、".join(
                    f"{labels.get(kind, kind)} ({kind})" for kind in missing_outputs
                )
                raise PlanNotMatched(
                    "未生成完整流程：当前方法目录没有声明可产生以下目标的输出接口："
                    + names
                    + "。相关软件或命令可能已接入，但其输出还不能按类型连接到后续步骤。"
                    "需要补齐现有方法的具体输出及必要的空间转换契约，才能生成这条完整流程；"
                    "不能把用户明确没有的输入继续当作已有文件。",
                    diagnostics,
                )
            raise PlanNotMatched(
                "未生成符合输入限制和目标输出的流程。"
                + last_error
                + "；这不表示平台没有可执行方法，检索或规划仍需重试/调整。",
                diagnostics,
            )
        dataset = (
            self.dataset_service.get(request.dataset_id) if request.dataset_id else None
        )
        initial_types = (
            {a.artifact_type for a in contract.allowed_existing_inputs}
            if contract.input_policy == "only_listed"
            else set(dataset.inspection.input_types if dataset else [])
        )
        steps = []
        for item in plan.steps:
            definition = self.registry.method_for("method", item.method_id)
            option = self.catalog.option(
                definition,
                initial_types,
                dataset.inspection.features if dataset else {},
                dataset is not None,
                check_runtime=False,
            )
            if not option.compatible:
                raise PlanNotMatched(
                    "找到方法，但当前执行条件不满足："
                    + option.label
                    + "；"
                    + "；".join(option.blocked_reasons),
                    diagnostics,
                )
            option.recommended = True
            option.recommendation_reason = (
                "已校验真实输入输出连接与本次输入范围；执行前仍需选择文件和确认参数。"
            )
            parameters = contracts.default_parameters(option)
            parameters.update(item.parameters)
            from ..artifact_semantics import specialize

            sources = {
                (s.id, o.id): o.type
                for s in steps
                for o in s.method_options[0].output_slots
            }
            option = specialize(
                option,
                {
                    k: sources.get((v.step_id, v.output_slot_id), "")
                    for k, v in item.inputs.items()
                },
                parameters,
            )
            slots = {s.id: s for s in option.input_slots}
            steps.append(
                WorkflowStep(
                    id=item.id,
                    order=len(steps) + 1,
                    title=option.label,
                    description=option.description,
                    capability=self.method_index.capability(item.method_id),
                    selection="auto",
                    status="method_selected",
                    selected_method_id=item.method_id,
                    selected_parameters=parameters,
                    method_options=[option],
                    depends_on=list(
                        dict.fromkeys(
                            source.step_id
                            for link in item.inputs.values()
                            for source in link.sources()
                        )
                    ),
                    input_bindings={
                        slot_id: InputBinding(
                            slot_id=slot_id,
                            artifact_type=slots[slot_id].type,
                            source="upstream_output",
                            upstream_step_id=link.step_id,
                            upstream_sources=[
                                v.model_dump() for v in link.additional_sources
                            ],
                            output_slot_id=link.output_slot_id,
                            status="planned",
                        )
                        for slot_id, link in item.inputs.items()
                    },
                )
            )
            initial_types.update(
                s.type
                for s in option.output_slots
                if contracts.output_generated(s, parameters)
            )
        now = contracts.now()
        actual_capabilities = {s.capability for s in steps}
        final_requested = [cap for cap in capabilities if cap in actual_capabilities]
        for step in steps:
            if (
                any(
                    output.type in contract.goal_artifact_types
                    and contracts.output_generated(output, step.selected_parameters)
                    for output in step.method_options[0].output_slots
                )
                and step.capability not in final_requested
            ):
                final_requested.append(step.capability)
        for step in steps:
            step.origin = (
                "requested" if step.capability in final_requested else "dependency"
            )
        workflow = Workflow(
            input_selection=existing.input_selection if existing else request._input_selection,
            conversation_id=existing.conversation_id if existing else request.conversation_id,
            output_layout=existing.output_layout if existing else 'sample_steps',
            workflow_id=existing.workflow_id if existing else f"wf_{uuid4().hex[:12]}",
            template_id="method-contract",
            revision=existing.revision + 1 if existing else 1,
            title=" → ".join(s.title for s in steps),
            summary="",
            original_request=request.message,
            task_contract=contract,
            planning_diagnostics=diagnostics,
            workspace_id=request.workspace_id,
            workspace_root_path=request.workspace_root_path,
            output_relative_path="." if request.workspace_id else "outputs",
            status="chain_ready",
            dataset_id=request.dataset_id,
            dataset=dataset,
            planner_source="method-contract:validated",
            execution_mode="runner",
            plan_kind="composite" if len(steps) > 1 else "atomic",
            requested_capabilities=final_requested,
            steps=steps,
            created_at=existing.created_at if existing else now,
            updated_at=now,
            warnings=[
                "流程连接已通过校验；尚未执行。请选择实际输入，并按采集信息审阅算法参数。"
            ],
        )
        self.state.refresh(workflow)
        if request._workflow_input_bindings:
            from .assistant_bindings import bind_assistant_inputs
            from ..operation_context import operation_thread
            workflow.planning_diagnostics['input_bindings'] = await operation_thread(bind_assistant_inputs, workflow, request,
                self.dataset_service, request._workflow_input_bindings)
            self.state.refresh(workflow)
            if all(not s.missing_required_inputs for s in workflow.steps):
                scope = workflow.sample_scope
                workflow.warnings[0] = (f'已核对输入：{len(scope.eligible_sample_ids)}/{scope.total} 个样本满足必需输入；跳过原因见范围清单。'
                    if scope else '流程连接和输入规则已保存，实际路径、样本覆盖及内容尚待dry-run核查。') + '请审阅算法参数并预检，尚未执行。'
                if scope and not scope.eligible_sample_ids:
                    raise PlanNotMatched('必需输入没有共同匹配的样本；逐槽匹配不表示可运行，请按跳过清单补齐输入或调整方法。',
                        {**diagnostics, 'sample_scope': scope.model_dump()})
        if workflow.contract_errors:
            raise PlanNotMatched(
                "流程校验失败：" + "；".join(workflow.contract_errors), diagnostics
            )
        labels = {a.id: a.label for a in self.registry.artifact_types()}
        if contract.input_policy == "only_listed":
            workflow.warnings.append(
                "本次仅允许复用这些已有输入："
                + "、".join(
                    labels.get(a.artifact_type, a.artifact_type)
                    + ("（" + "、".join(a.paths) + "）" if a.paths else "")
                    for a in contract.allowed_existing_inputs
                )
                + "。所需中间结果由本流程重新生成。"
            )
        return (
            workflow,
            "已生成并校验流程："
            + workflow.summary
            + ("。必要输入已绑定并校验，请在卡片审阅参数和预检后点击运行；尚未执行。"
               if all(not s.missing_required_inputs for s in workflow.steps) else
               "。方案仍有必要输入未绑定，继续补齐输入；尚未执行。"),
        )

    async def build_dynamic(
        self,
        request: ChatRequest,
        routing: dict[str, Any],
        existing: Workflow | None = None,
        dependency_policy: str = "target_only",
    ) -> tuple[Workflow, str]:
        if self.capability_tree is None:
            raise RuntimeError("CapabilityTree 尚未配置")
        request.task_contract = TaskContract.model_validate(
            routing.get("task_intent", {}).get(
                "task_contract", request.task_contract.model_dump()
            )
        )
        if existing:
            request.workspace_id = existing.workspace_id
            request.workspace_root_path = existing.workspace_root_path
            if not request.task_contract.goal_artifact_types and not request.task_contract.goal:
                request.task_contract = existing.task_contract.model_copy(deep=True)
        if request.task_contract.goal_artifact_types and not (
            existing and existing.excluded_capabilities
        ):
            return await self.build_contract_plan(request, routing, existing)
        dataset = (
            self.dataset_service.get(request.dataset_id) if request.dataset_id else None
        )
        initial_types = set(
            dataset.inspection.input_types if dataset else ["dataset_folder"]
        )
        excluded = set(existing.excluded_capabilities if existing else [])
        reused_inputs = (
            set(existing.input_capabilities if existing else [])
            | set(routing.get("routing_trace", {}).get("reused_input_capabilities", []))
            | existing_input_capabilities(request.message)
        )
        if existing:
            initial_types.update(
                value for step in existing.steps for value in step.manual_input_types
            )
        requested_capability_ids = order_capabilities(
            self.registry,
            [
                item
                for item in routing.get("capability_ids") or []
                if item not in reused_inputs and item not in excluded
            ],
        )
        capability_ids = (
            self.catalog.expand_and_order_capabilities(
                requested_capability_ids,
                initial_types,
                excluded | reused_inputs,
                {
                    step.capability: (
                        contracts.selected_method(step),
                        step.selected_parameters,
                    )
                    for step in existing.steps
                    if step.selected_method_id
                }
                if existing
                else None,
            )
            if dependency_policy == "complete"
            else requested_capability_ids
        )
        capability_ids = order_capabilities(
            self.registry, capability_ids
        )
        if not capability_ids:
            raise ValueError("能力路由没有返回可用任务")
        candidate_context = self.catalog.candidate_context(capability_ids)
        for candidate in candidate_context:
            candidate["methods"] = [
                method
                for method in candidate["methods"]
                if not (
                    set(method.get("included_capabilities", []))
                    | {
                        step["capability"]
                        for step in (method.get("execution_recipe") or {}).get(
                            "steps", []
                        )
                    }
                )
                & (excluded | reused_inputs)
            ]
        planning = await self.chat_service.plan_capability_chain(
            request,
            candidate_context,
            dataset.inspection.model_dump(mode="json") if dataset else None,
        )
        if planning.get("status") == "candidate_mismatch":
            raise PlanNotMatched(
                "当前候选与需求不匹配，未生成流程。候选检索失败不代表平台没有可执行方法。"
            )
        recommendations = planning["recommendations"]
        reasons = planning["reasons"]

        steps: list[WorkflowStep] = []
        available_types = set(initial_types)
        features = dataset.inspection.features if dataset else {}
        previous_step_id: str | None = None
        for capability_id in capability_ids:
            node = self.capability_tree.leaf(capability_id)
            if node is None:
                raise RuntimeError(f"能力树中不存在叶子能力：{capability_id}")
            all_definitions = self.registry.selectable_methods_for(capability_id)
            if not all_definitions:
                raise RuntimeError(f"能力 {capability_id} 没有注册任何方法")
            # Keep the task-specific shortlist, but restore methods hidden only
            # because the user removed an operation or plans to reuse its output.
            method_candidate_ids = list(
                planning.get("method_candidates", {}).get(capability_id, [])
            )
            previous_step = (
                next(
                    (
                        step
                        for step in existing.steps
                        if step.capability == capability_id
                    ),
                    None,
                )
                if existing
                else None
            )
            if method_candidate_ids:
                if previous_step and previous_step.selected_method_id:
                    method_candidate_ids.append(previous_step.selected_method_id)
                method_candidate_ids.extend(
                    method.method_id
                    for method in all_definitions
                    if contracts.scope_conflicts(method, excluded | reused_inputs)
                )
                # A recommendation shortlist must not hide the direct existing-
                # input method behind newly added composite alternatives.
                method_candidate_ids.extend(
                    method.method_id
                    for method in all_definitions
                    if method.default_recommended
                )
            definitions = [
                method
                for method in all_definitions
                if method.method_id in method_candidate_ids
            ] or all_definitions
            options = [
                self.catalog.option(
                    method, available_types, features, dataset is not None
                )
                for method in definitions
            ]
            for option in options:
                self.catalog.restrict_method_scope(option, excluded | reused_inputs)
            compatible = [option for option in options if option.compatible]
            recommendable = [
                option for option in compatible if not option.scope_warnings
            ]
            recommendable = [
                option for option in recommendable if not option.input_warnings
            ] or recommendable
            requested_method = recommendations.get(capability_id)
            preferred = next(
                (option for option in recommendable if option.id == requested_method),
                None,
            )
            for option in options:
                option.recommended = bool(preferred and option.id == preferred.id)
                option.recommendation_reason = (
                    str(reasons.get(capability_id) or "")
                    if option.recommended and option.id == requested_method
                    else contracts.definition_reason(definitions, option.id)
                )

            selection = "auto" if len(definitions) == 1 else "user"
            selected_id = preferred.id if selection == "auto" and preferred else None
            selected_parameters = (
                contracts.default_parameters(preferred)
                if selected_id and preferred
                else {}
            )
            step_id = re.sub(r"[^a-z0-9]+", "_", capability_id.lower()).strip("_")
            step = WorkflowStep(
                id=step_id,
                order=len(steps) + 1,
                title=node.label,
                description=node.description,
                capability=capability_id,
                origin=(
                    "requested"
                    if capability_id in requested_capability_ids
                    else "dependency"
                ),
                selection=selection,
                depends_on=[previous_step_id] if previous_step_id else [],
                status="configured" if selected_id else "needs_method",
                method_options=options,
                selected_method_id=selected_id,
                selected_parameters=selected_parameters,
            )
            steps.append(step)
            if selected_id:
                available_types.update(contracts.selected_method(step).output_types)
            else:
                for option in compatible:
                    if option.scope_warnings:
                        continue
                    available_types.update(option.output_types)
            previous_step_id = step.id

        now = contracts.now()
        warnings: list[str] = []
        dataset_required = any(
            "*" not in option.input_types
            for step in steps
            for option in step.method_options
        )
        if dataset is None:
            warnings.append(
                "尚未关联数据集；可以规划，但 dry-run 前必须选择并检查数据目录。"
                if dataset_required
                else "当前规划型步骤不要求关联数据集目录。"
            )
        routing_source = str(routing.get("decision_source") or "unknown")
        planning_source = str(planning.get("planner_source") or "unknown")
        if (
            "heuristic_after_" in routing_source
            or "heuristic_after_" in planning_source
        ):
            warnings.append("模型规划暂时不可用，当前推荐由本地能力图安全回退生成。")
        execution_modes = {
            option.execution_mode for step in steps for option in step.method_options
        }
        workflow_execution_mode = (
            "mixed"
            if len(execution_modes) > 1
            else next(iter(execution_modes), "planning")
        )
        workflow = Workflow(
            input_selection=existing.input_selection if existing else request._input_selection,
            conversation_id=existing.conversation_id if existing else request.conversation_id,
            output_layout=existing.output_layout if existing else 'sample_steps',
            workspace_id=existing.workspace_id if existing else request.workspace_id,
            workspace_root_path=existing.workspace_root_path
            if existing
            else request.workspace_root_path,
            output_relative_path=existing.output_relative_path
            if existing
            else ("." if request.workspace_id else "outputs"),
            workflow_id=existing.workflow_id if existing else f"wf_{uuid4().hex[:12]}",
            template_id="dynamic:" + "+".join(capability_ids),
            revision=existing.revision + 1 if existing else 1,
            title=" → ".join(step.title for step in steps),
            summary=" → ".join(step.title for step in steps),
            task_contract=request.task_contract,
            original_request=request.message,
            status="awaiting_method_choice",
            dataset_id=dataset.dataset_id if dataset else None,
            dataset=dataset,
            planner_source=f"capability-tree:{routing_source}/{planning_source}",
            execution_mode=workflow_execution_mode,
            plan_kind=("atomic" if len(requested_capability_ids) == 1 else "composite"),
            requested_capabilities=requested_capability_ids,
            excluded_capabilities=sorted(excluded),
            input_capabilities=sorted(reused_inputs),
            dependency_policy=(
                "complete" if dependency_policy == "complete" else "target_only"
            ),
            steps=steps,
            warnings=warnings,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self.state.revalidate_chain(workflow)
        inputs.resolve_workflow_inputs(workflow)
        self.state.refresh(workflow)
        return (
            workflow,
            "流程卡已生成："
            + workflow.summary
            + "。请确认方法、选择输入并审阅参数；尚未执行。",
        )
