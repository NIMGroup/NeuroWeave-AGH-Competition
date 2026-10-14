"""Revision-safe workflow facade.

This module owns lifecycle mutations and commits. Planning, catalog projection,
input resolution and preflight are separate components with explicit dependencies;
none receives a back-reference to this facade.
"""

from __future__ import annotations

from threading import RLock
from typing import Any

from .capability_registry import CapabilityRegistry
from .capability_retriever import CapabilityRetriever
from .capability_tree import CapabilityTree
from .chat_service import ChatService
from .dataset_models import DatasetPathPreview
from .dataset_service import DatasetService
from .execution.runner_registry import RunnerRegistry
from .local_store import LocalStore
from .method_planning import MethodContractIndex, validate_workflow_scope
from .models import (
    CapabilityRecommendation,
    ChatRequest,
)
from .sample_scope import partial_sample_scope
from .skill_loader import SkillLoader
from .workflow_models import (
    Workflow,
)
from .workflows import contracts, inputs, warnings
from .workflows.bindings import WorkflowBindings
from .workflows.catalog import MethodCatalog
from .workflows.errors import RevisionConflict as RevisionConflict
from .workflows.planning import WorkflowPlanner
from .workflows.preflight import WorkflowPreflight
from .workflows.routing import WorkflowRouter
from .workflows.selector import CapabilitySelector
from .workflows.session import WorkflowSession
from .workflows.state import WorkflowState


class WorkflowService:
    def __init__(
        self,
        chat_service: ChatService,
        registry: CapabilityRegistry,
        store: LocalStore,
        dataset_service: DatasetService,
        capability_tree: CapabilityTree | None = None,
        skill_loader: SkillLoader | None = None,
        executable_runner_ids: set[str] | None = None,
        runner_registry: RunnerRegistry | None = None,
    ) -> None:
        self.chat_service = chat_service
        self.registry = registry
        self.store = store
        self.dataset_service = dataset_service
        self.capability_tree = capability_tree
        self.skill_loader = skill_loader
        self.executable_runner_ids = set(executable_runner_ids or set())
        self.capability_retriever = (
            CapabilityRetriever(capability_tree, registry, skill_loader)
            if capability_tree is not None and skill_loader is not None
            else None
        )
        self.method_index = MethodContractIndex(registry)
        self.catalog = MethodCatalog(
            registry, capability_tree, skill_loader, runner_registry
        )
        self.selector = CapabilitySelector(
            registry, capability_tree, skill_loader, self.capability_retriever
        )
        self.state = WorkflowState(self.catalog, registry)
        self.session = WorkflowSession(store, self.state, RLock())
        self.router = WorkflowRouter(
            self.selector,
            chat_service,
            dataset_service,
            capability_tree,
            registry,
            self.capability_retriever,
        )
        self.planner = WorkflowPlanner(
            self.catalog,
            self.state,
            chat_service,
            dataset_service,
            capability_tree,
            registry,
            self.method_index,
        )
        self.bindings = WorkflowBindings(
            self.session, self.state, store, dataset_service
        )
        self.preflight = WorkflowPreflight(
            self.session,
            self.state,
            store,
            self.catalog,
            registry,
            self.executable_runner_ids,
            dataset_service,
        )

    @property
    def mutation_lock(self):
        """Shared lock for integrations that atomically change output scope or submit."""
        return self.session.lock

    def recommendation_context(
        self,
        capability_ids: list[str],
    ) -> list[dict[str, Any]]:
        return self.catalog.candidate_context(capability_ids)

    async def draft(
        self,
        request: ChatRequest,
        routing: dict[str, Any] | None = None,
        *, existing: Workflow | None = None,
    ) -> tuple[Workflow, str]:
        decision = routing or await self.router.assess_task(request)
        if not decision.get("recommend_workflow"):
            raise ValueError("当前需求没有匹配到可生成任务的能力")
        workflow, content = await self.planner.build_dynamic(request, decision, existing=existing)
        workflow.managed_task_id = request.assistant_task.get('task_id') or (existing.managed_task_id if existing else None)
        if existing:
            self._preserve_plan_choices(workflow, existing, request._plan_parameter_overrides)
        with self.session.lock:
            if existing:
                contracts.check_revision(self.session.require(existing.workflow_id), existing.revision)
            self.store.save_workflow(workflow, event_type='workflow_replanned' if existing else 'workflow_created',
                event_payload={'previous_revision':existing.revision} if existing else None)
        return workflow.model_copy(deep=True), content

    def _preserve_plan_choices(self, replacement, snapshot, overrides, *, check_runtime=True):
        """Keep user choices, but exact new graph links always win."""
        used = set()
        for step in replacement.steps:
            old = next((s for s in snapshot.steps if s.id == step.id and s.selected_method_id == step.selected_method_id), None)
            if old is None:
                candidates = [s for s in snapshot.steps if s.selected_method_id == step.selected_method_id and s.id not in used]
                old = candidates[0] if len(candidates) == 1 else None
            if old is None or old.id in used:
                continue
            used.add(old.id)
            method = next(m for m in step.method_options if m.id == step.selected_method_id)
            controls = {key for slot in method.input_slots for condition in
                [slot.required_when,*slot.required_when_any] for key in condition}
            step.selected_parameters = {**step.selected_parameters, **{k:v for k,v in old.selected_parameters.items()
                if k in step.selected_parameters and k not in controls}, **overrides.get(method.id,{})}
            for slot in method.input_slots:
                binding = old.input_bindings.get(slot.id)
                if binding and binding.confirmed_by_user and binding.source == 'existing_path' and slot.id not in step.input_bindings:
                    step.input_bindings[slot.id] = binding.model_copy(deep=True)
        replacement.input_check_cache = snapshot.input_check_cache.copy()
        replacement.output_relative_path = snapshot.output_relative_path
        self.state.revalidate_chain(replacement, check_runtime=check_runtime)
        inputs.resolve_workflow_inputs(replacement)
        self.state.refresh(replacement)

    async def replan(
        self,
        workflow_id: str,
        expected_revision: int,
    ) -> tuple[Workflow, str]:
        with self.session.lock:
            current = self.session.require(workflow_id)
            contracts.check_revision(current, expected_revision)
            if current.status == "confirmed":
                raise ValueError("已确认的流程不可覆盖，请创建新流程")
            snapshot = current.model_copy(deep=True)

        request = ChatRequest(
            message=snapshot.original_request,
            dataset_id=snapshot.dataset_id,
            interaction_mode="task",
        )
        routing = (
            {
                "recommend_workflow": True,
                "capability_ids": snapshot.requested_capabilities,
                "decision_source": "user_edited_scope",
            }
            if snapshot.excluded_capabilities
            else await self.router.assess_task(request)
        )
        if not routing.get("recommend_workflow"):
            raise ValueError("重新规划时没有匹配到可生成任务的能力")
        replacement, content = await self.planner.build_dynamic(
            request,
            routing,
            existing=snapshot,
        )
        self._preserve_step_configuration(replacement, snapshot)
        with self.session.lock:
            latest = self.session.require(workflow_id)
            contracts.check_revision(latest, expected_revision)
            from .operation_context import checkpoint
            checkpoint()
            self.store.save_workflow(
                replacement,
                event_type="workflow_replanned",
                event_payload={"previous_revision": expected_revision},
            )
        return replacement.model_copy(deep=True), content

    async def complete_dependencies(
        self,
        workflow_id: str,
        expected_revision: int,
    ) -> Workflow:
        with self.session.lock:
            current = self.session.require(workflow_id)
            contracts.check_revision(current, expected_revision)
            if current.status == "confirmed":
                raise ValueError("已确认的流程不可补充依赖，请创建新流程")
            snapshot = current.model_copy(deep=True)

        routing = {
            "capability_ids": snapshot.requested_capabilities,
            "decision_source": "dependency_resolver",
        }
        request = ChatRequest(
            message=snapshot.original_request,
            dataset_id=snapshot.dataset_id,
            interaction_mode="task",
        )
        replacement, _content = await self.planner.build_dynamic(
            request,
            routing,
            existing=snapshot,
            dependency_policy="complete",
        )
        self._preserve_step_configuration(replacement, snapshot)
        with self.session.lock:
            latest = self.session.require(workflow_id)
            contracts.check_revision(latest, expected_revision)
            from .operation_context import checkpoint
            checkpoint()
            self.store.save_workflow(
                replacement,
                event_type="workflow_dependencies_completed",
                event_payload={
                    "requested_capabilities": snapshot.requested_capabilities
                },
            )
        return replacement.model_copy(deep=True)

    def _preserve_step_configuration(
        self, replacement: Workflow, snapshot: Workflow
    ) -> None:
        """Keep compatible user choices and bindings when the task chain changes."""
        previous_steps = {step.capability: step for step in snapshot.steps}
        for step in replacement.steps:
            previous = previous_steps.get(step.capability)
            if previous is not None:
                step.manual_input_types = list(previous.manual_input_types)
            if previous is None or previous.selected_method_id is None:
                continue
            method = next(
                (
                    option
                    for option in step.method_options
                    if option.id == previous.selected_method_id and option.compatible
                ),
                None,
            )
            if method is None:
                continue
            step.selected_method_id = method.id
            declared = {spec.id for spec in method.parameters}
            step.selected_parameters = {
                **contracts.default_parameters(method),
                **{
                    key: value
                    for key, value in previous.selected_parameters.items()
                    if key in declared
                },
            }
            step.input_bindings = previous.input_bindings

        self.state.revalidate_chain(replacement)
        inputs.resolve_workflow_inputs(replacement)
        self.state.refresh(replacement)

    def get(self, workflow_id: str) -> Workflow:
        with self.session.lock:
            workflow = self.session.require(workflow_id)
            # Warnings are derived state. Refresh them on reads so persisted task
            # cards immediately benefit from newer context-sensitive warning rules.
            warnings.refresh_execution_summary(workflow)
            return workflow.model_copy(deep=True)

    def list(self, limit: int = 50) -> list[Workflow]:
        workflows = self.store.list_workflows(limit)
        for workflow in workflows:
            if workflow.locked_at is None and workflow.status != "confirmed":
                self.state.revalidate_chain(workflow, check_runtime=False)
                self.state.refresh(workflow)
            warnings.refresh_execution_summary(workflow)
        return workflows

    def events(self, workflow_id: str) -> list[dict[str, Any]]:
        self.session.require(workflow_id)
        return self.store.workflow_events(workflow_id)

    def select_method(
        self,
        workflow_id: str,
        step_id: str,
        method_id: str,
        expected_revision: int,
    ) -> Workflow:
        with self.session.lock:
            workflow = self.session.require(workflow_id)
            contracts.check_revision(workflow, expected_revision)
            contracts.ensure_editable_chain(workflow)
            self.state.revalidate_chain(workflow)
            step = contracts.step(workflow, step_id)
            method = next(
                (item for item in step.method_options if item.id == method_id), None
            )
            if method is None:
                raise ValueError(f"方法 {method_id} 不属于步骤 {step_id}")
            if not method.compatible:
                raise ValueError(
                    "该方法与当前数据或上游输出不兼容："
                    + "；".join(method.blocked_reasons)
                )
            step.selected_method_id = method.id
            step.selected_parameters = contracts.default_parameters(method)
            contracts.clear_review(workflow)
            self.state.revalidate_chain(workflow)
            contracts.advance(workflow)
            self.state.refresh(workflow)
            self.session.save(
                workflow,
                "method_selected",
                {"step_id": step_id, "method_id": method_id},
            )
            return workflow.model_copy(deep=True)

    def remove_step(
        self, workflow_id: str, step_id: str, expected_revision: int
    ) -> Workflow:
        """Remove an operation, keeping downstream input requirements explicit."""
        with self.session.lock:
            workflow = self.session.require(workflow_id)
            contracts.check_revision(workflow, expected_revision)
            contracts.ensure_editable_chain(workflow)
            removed = contracts.step(workflow, step_id)
            if len(workflow.steps) == 1:
                raise ValueError("流程至少需要保留一个步骤。")
            position = workflow.steps.index(removed)
            selected = next(
                (
                    m
                    for m in removed.method_options
                    if m.id == removed.selected_method_id
                ),
                None,
            )
            producers = [selected] if selected else removed.method_options
            output_types = {
                output.type
                for method in producers
                for output in method.output_slots
                if selected is None
                or contracts.output_generated(output, removed.selected_parameters)
            }
            output_types.discard("execution_log")
            for step in workflow.steps[position + 1 :]:
                # A direct binding is authoritative; before method selection,
                # retain the hole for any method with a matching declared slot.
                affected = {
                    slot.type
                    for method in step.method_options
                    for slot in method.input_slots
                    if slot.type in output_types
                    and (
                        not step.input_bindings.get(slot.id)
                        or step.input_bindings[slot.id].upstream_step_id == removed.id
                    )
                }
                step.manual_input_types = sorted(
                    set(step.manual_input_types) | affected
                )
                step.input_bindings = {
                    key: binding
                    for key, binding in step.input_bindings.items()
                    if binding.upstream_step_id != removed.id
                }
            workflow.steps.remove(removed)
            workflow.excluded_capabilities = list(
                dict.fromkeys([*workflow.excluded_capabilities, removed.capability])
            )
            # The remaining visible chain becomes the user's chosen scope.
            workflow.requested_capabilities = [
                step.capability for step in workflow.steps
            ]
            for index, step in enumerate(workflow.steps):
                step.order = index + 1
                step.depends_on = [workflow.steps[index - 1].id] if index else []
                step.origin = "requested"
            workflow.plan_kind = "atomic" if len(workflow.steps) == 1 else "composite"
            workflow.template_id = "dynamic:" + "+".join(
                workflow.requested_capabilities
            )
            workflow.title = " → ".join(step.title for step in workflow.steps)
            workflow.summary = "按你的选择保留：" + " → ".join(
                step.title for step in workflow.steps
            )
            contracts.clear_review(workflow)
            self.state.revalidate_chain(workflow)
            contracts.advance(workflow)
            self.state.refresh(workflow)
            self.session.save(
                workflow,
                "workflow_step_removed",
                {"step_id": removed.id, "capability": removed.capability},
            )
            return workflow.model_copy(deep=True)

    def lock_chain(self, workflow_id: str, expected_revision: int) -> Workflow:
        with self.session.lock:
            workflow = self.session.require(workflow_id)
            contracts.check_revision(workflow, expected_revision)
            contracts.ensure_editable_chain(workflow)
            self.state.revalidate_chain(workflow)
            inputs.resolve_workflow_inputs(workflow)
            if any(step.selected_method_id is None for step in workflow.steps):
                raise ValueError("仍有步骤尚未选择可兼容的方法，不能锁定流程链")
            errors = validate_workflow_scope(workflow, self.registry)
            if errors:
                raise ValueError("输入输出约束：" + "；".join(errors))
            workflow.locked_at = contracts.now()
            contracts.clear_review(workflow)
            contracts.advance(workflow)
            self.state.refresh(workflow)
            self.session.save(workflow, "chain_locked")
            return workflow.model_copy(deep=True)

    def change_dataset(
        self, workflow_id: str, dataset_id: str, expected_revision: int
    ) -> Workflow:
        """Replace the draft's dataset and invalidate input choices from its old snapshot."""
        with self.session.lock:
            workflow = self.session.require(workflow_id)
            contracts.check_revision(workflow, expected_revision)
            if workflow.locked_at is not None or workflow.status == "confirmed":
                raise ValueError(
                    "流程已锁定，请先解锁流程再更换数据集；已确认流程请新建对话。"
                )
            dataset = self.dataset_service.get(dataset_id)
            if workflow.dataset == dataset:
                return workflow.model_copy(deep=True)
            previous_dataset_id = workflow.dataset_id
            workflow.dataset_id = dataset.dataset_id
            workflow.dataset = dataset
            for step in workflow.steps:
                step.input_bindings = {
                    key: binding
                    for key, binding in step.input_bindings.items()
                    if workflow.planner_source.startswith("method-contract:")
                    and binding.source == "upstream_output"
                }
                step.command_preview = None
            contracts.clear_review(workflow)
            self.state.revalidate_chain(workflow)
            contracts.advance(workflow)
            self.state.refresh(workflow)
            self.session.save(
                workflow,
                "workflow_dataset_changed",
                {
                    "previous_dataset_id": previous_dataset_id,
                    "dataset_id": dataset_id,
                },
            )
            return workflow.model_copy(deep=True)

    def unlock_chain(self, workflow_id: str, expected_revision: int) -> Workflow:
        with self.session.lock:
            workflow = self.session.require(workflow_id)
            contracts.check_revision(workflow, expected_revision)
            if workflow.status == "confirmed":
                raise ValueError("已确认的流程不可解锁，请创建新流程")
            if workflow.locked_at is None:
                raise ValueError("流程链尚未锁定")
            workflow.locked_at = None
            contracts.clear_review(workflow)
            self.state.revalidate_chain(workflow, check_runtime=False)
            contracts.advance(workflow)
            self.state.refresh(workflow)
            self.session.save(workflow, "chain_unlocked")
            return workflow.model_copy(deep=True)

    def update_parameters(
        self,
        workflow_id: str,
        step_id: str,
        parameters: dict[str, Any],
        expected_revision: int,
    ) -> Workflow:
        with self.session.lock:
            workflow = self.session.require(workflow_id)
            contracts.check_revision(workflow, expected_revision)
            if workflow.status == "confirmed":
                raise ValueError("流程已经确认，不能继续修改参数")
            if workflow.locked_at is None:
                raise ValueError("请先确定流程链，再配置参数")
            step = contracts.step(workflow, step_id)
            method = contracts.selected_method(step)
            specs = {spec.id: spec for spec in method.parameters}
            unknown = sorted(set(parameters) - set(specs))
            if unknown:
                raise ValueError(f"包含未声明的参数：{', '.join(unknown)}")
            merged = {**step.selected_parameters}
            for parameter_id, value in parameters.items():
                spec = specs[parameter_id]
                if value is None and not spec.required:
                    merged[parameter_id] = None
                else:
                    merged[parameter_id] = value
            contracts.validate_method_parameters(method, merged, require_complete=False)
            step.selected_parameters = merged
            inputs.resolve_workflow_inputs(workflow)
            contracts.clear_review(workflow)
            contracts.advance(workflow)
            self.state.refresh(workflow)
            self.session.save(
                workflow,
                "parameters_updated",
                {"step_id": step_id, "parameter_ids": sorted(parameters)},
            )
            return workflow.model_copy(deep=True)

    def review_parameters(self, workflow_id: str, expected_revision: int) -> Workflow:
        with self.session.lock:
            workflow = self.session.require(workflow_id)
            contracts.check_revision(workflow, expected_revision)
            if workflow.locked_at is None:
                raise ValueError("请先锁定流程链")
            inputs.resolve_workflow_inputs(workflow)
            missing = [
                f"{step.title}：{', '.join(step.missing_required_inputs)}"
                for step in workflow.steps
                if step.missing_required_inputs
            ]
            if missing:
                raise ValueError("仍有必需输入未绑定：" + "；".join(missing))
            workflow.sample_scope = partial_sample_scope(workflow)
            if (
                workflow.sample_scope is not None
                and not workflow.sample_scope.eligible_sample_ids
            ):
                raise ValueError(
                    "各输入没有共同匹配的样本，无法执行；请调整历史输入或补齐文件"
                )
            self.state.validate_all_parameters(workflow)
            workflow.parameters_reviewed_at = contracts.now()
            workflow.dry_run_report = None
            contracts.advance(workflow)
            self.state.refresh(workflow)
            self.session.save(workflow, "parameters_reviewed")
            return workflow.model_copy(deep=True)

    def confirm(
        self,
        workflow_id: str,
        expected_revision: int,
        acknowledge_quality: bool = False,
    ) -> Workflow:
        with self.session.lock:
            workflow = self.session.require(workflow_id)
            contracts.check_revision(workflow, expected_revision)
            if (
                workflow.status != "ready"
                or workflow.dry_run_report is None
                or not workflow.dry_run_report.valid
            ):
                raise ValueError("流程尚未通过参数审阅和 dry-run，不能确认")
            if (
                workflow.dry_run_report.quality_review_required
                and not acknowledge_quality
            ):
                raise ValueError("请审阅空间对齐及内容质量检查的限制后明确确认")
            workflow.dry_run_report.quality_acknowledged = acknowledge_quality
            workflow.status = "confirmed"
            workflow.confirmed_at = contracts.now()
            workflow.can_lock = False
            workflow.can_unlock = False
            workflow.can_review_parameters = False
            workflow.can_dry_run = False
            workflow.can_confirm = False
            contracts.advance(workflow)
            for step in workflow.steps:
                step.status = "confirmed"
            self.session.save(workflow, "workflow_confirmed")
            return workflow.model_copy(deep=True)

    async def assess_task(
        self, request: ChatRequest, *, conversation: bool = False
    ) -> dict[str, Any]:
        return await self.router.assess_task(request, conversation=conversation)

    def build_capability_recommendation(
        self,
        routing: dict[str, Any],
    ) -> CapabilityRecommendation:
        return self.router.build_capability_recommendation(routing)

    def planning_advice_context(self, diagnostics):
        return self.planner.planning_advice_context(diagnostics)

    def bind_input(
        self,
        workflow_id: str,
        step_id: str,
        slot_id: str,
        root_id: str,
        relative_path: str,
        expected_revision: int,
        binding_mode: str = 'auto',
    ) -> Workflow:
        return self.bindings.bind_input(
            workflow_id, step_id, slot_id, root_id, relative_path, expected_revision, binding_mode
        )

    def preview_input_mapping(
        self,
        workflow_id: str,
        step_id: str,
        slot_id: str,
        root_id: str,
        relative_path: str,
        binding_mode: str = 'auto',
    ) -> DatasetPathPreview:
        return self.bindings.preview_input_mapping(
            workflow_id, step_id, slot_id, root_id, relative_path, binding_mode
        )

    def historical_input_candidates(self, workflow_id, step_id, slot_id, offset=0):
        return self.bindings.historical_input_candidates(
            workflow_id, step_id, slot_id, offset
        )

    def preview_historical_input(self, workflow_id, step_id, slot_id, request):
        return self.bindings.preview_historical_input(
            workflow_id, step_id, slot_id, request
        )

    def bind_historical_input(self, workflow_id, step_id, slot_id, request):
        return self.bindings.bind_historical_input(
            workflow_id, step_id, slot_id, request
        )

    def dry_run(self, workflow_id: str, expected_revision: int) -> Workflow:
        return self.preflight.dry_run(workflow_id, expected_revision)
