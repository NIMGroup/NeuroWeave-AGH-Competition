"""WorkflowState: derive bindings, validation and UI state without saving."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..capability_registry import CapabilityMethod
from ..method_planning import validate_workflow_scope
from ..sample_scope import partial_sample_scope
from ..workflow_models import (
    Workflow,
)
from ..workflow_ordering import order_capabilities
from . import contracts, inputs, warnings

if TYPE_CHECKING:
    from ..capability_registry import CapabilityRegistry
    from .catalog import MethodCatalog


class WorkflowState:
    def __init__(
        self,
        catalog: MethodCatalog,
        registry: CapabilityRegistry,
    ) -> None:
        self.catalog = catalog
        self.registry = registry

    def revalidate_chain(
        self, workflow: Workflow, *, check_runtime: bool = True
    ) -> None:
        native_plan = workflow.planner_source.startswith("method-contract:")
        if (
            not native_plan
            and workflow.locked_at is None
            and workflow.status != "confirmed"
        ):
            ordered = order_capabilities(
                self.registry,
                [step.capability for step in workflow.steps],
            )
            changed_order = ordered != [step.capability for step in workflow.steps]
            workflow.steps.sort(key=lambda step: ordered.index(step.capability))
            if changed_order:
                workflow.title = " → ".join(step.title for step in workflow.steps)
            for index, step in enumerate(workflow.steps):
                step.order = index + 1
                step.depends_on = [workflow.steps[index - 1].id] if index else []
        available = set(
            workflow.dataset.inspection.input_types
            if workflow.dataset
            else ["dataset_folder"]
        )
        features = workflow.dataset.inspection.features if workflow.dataset else {}
        excluded = set(workflow.excluded_capabilities) | set(
            workflow.input_capabilities
        )
        for step in workflow.steps:
            previous_options = {option.id: option for option in step.method_options}
            definitions = {
                definition.method_id: definition
                for definition in self.registry.selectable_methods_for(step.capability)
                if definition.method_id in previous_options
                or definition.execution_resolution.get("source_method_id")
                in previous_options
                or contracts.scope_conflicts(definition, excluded)
            }
            if native_plan:
                definitions = {
                    option.id: self.registry.method_for(
                        "method", option.definition_id or option.id
                    )
                    for option in previous_options.values()
                }
                definitions = {
                    key: value
                    for key, value in definitions.items()
                    if value is not None
                }
            if workflow.locked_at is not None or workflow.status == "confirmed":
                # A catalog release must not silently change the approved inputs,
                # algorithm parameters or nested steps of an existing workflow.
                definitions = {
                    option.id: CapabilityMethod.model_validate(
                        {
                            **option.model_dump(),
                            "method_id": option.id,
                            "capability": step.capability,
                        }
                    )
                    for option in previous_options.values()
                }
            step.method_options = []
            for definition in definitions.values():
                checked = self.catalog.option(
                    definition,
                    available,
                    features,
                    workflow.dataset is not None,
                    check_runtime=check_runtime,
                )
                option = previous_options.get(definition.method_id)
                if option:
                    if not check_runtime:
                        checked.blocked_reasons = [
                            reason
                            for reason in option.blocked_reasons
                            if not reason.startswith(
                                (
                                    "上游没有产生所需输入：",
                                    "数据集缺少条件：",
                                    "此方法包含已排除的生成操作",
                                )
                            )
                        ]
                        checked.compatible = not checked.blocked_reasons
                    checked.recommended = option.recommended
                    checked.recommendation_reason = option.recommendation_reason
                self.catalog.restrict_method_scope(checked, excluded)
                if checked.scope_warnings:
                    checked.recommended = False
                step.method_options.append(checked)
            selected = next(
                (
                    option
                    for option in step.method_options
                    if option.id == step.selected_method_id
                ),
                None,
            )
            if selected and not selected.compatible:
                step.selected_method_id = None
                step.selected_parameters = {}
                selected = None
            if selected:
                available.update(selected.output_types)
            else:
                for option in step.method_options:
                    if option.compatible and not option.scope_warnings:
                        available.update(option.output_types)

    def refresh(self, workflow: Workflow) -> None:
        if workflow.status == "confirmed":
            return
        inputs.resolve_workflow_inputs(workflow)
        workflow.contract_errors = validate_workflow_scope(workflow, self.registry)
        workflow.warnings = [
            w for w in workflow.warnings if not w.startswith("输入输出约束：")
        ]
        workflow.warnings.extend(
            "输入输出约束：" + error for error in workflow.contract_errors
        )
        # Derive visible prose from the actual graph, never independent model text.
        workflow.summary = " → ".join(step.title for step in workflow.steps)
        warnings.refresh_execution_summary(workflow)
        workflow.sample_scope = partial_sample_scope(workflow)
        unresolved = [
            step for step in workflow.steps if step.selected_method_id is None
        ]
        if workflow.locked_at is None:
            workflow.status = "awaiting_method_choice" if unresolved else "chain_ready"
            if not unresolved and workflow.input_selection == 'user' and any(s.missing_required_inputs for s in workflow.steps):
                workflow.status = 'awaiting_input_binding'
            workflow.can_lock = not unresolved and not workflow.contract_errors
            workflow.can_unlock = False
            workflow.can_review_parameters = False
            workflow.can_dry_run = False
            workflow.can_confirm = False
            for step in workflow.steps:
                step.status = (
                    "needs_method"
                    if step.selected_method_id is None
                    else "method_selected"
                )
                step.command_preview = None
            return

        parameters_valid = not workflow.contract_errors
        missing_inputs = any(
            step.missing_required_inputs for step in workflow.steps
        ) or (
            workflow.sample_scope is not None
            and not workflow.sample_scope.eligible_sample_ids
        )
        for step in workflow.steps:
            method = contracts.selected_method(step)
            try:
                contracts.validate_method_parameters(method, step.selected_parameters)
            except ValueError:
                parameters_valid = False
            step.command_preview = contracts.command_preview(
                method,
                step.selected_parameters,
                workflow.dataset,
                step.input_bindings,
            )
            step.status = (
                "parameters_reviewed"
                if workflow.parameters_reviewed_at
                else "parameters_ready"
            )

        workflow.can_lock = False
        workflow.can_unlock = True
        workflow.can_review_parameters = (
            parameters_valid
            and not missing_inputs
            and workflow.parameters_reviewed_at is None
        )
        workflow.can_dry_run = (
            parameters_valid
            and not missing_inputs
            and workflow.parameters_reviewed_at is not None
        )
        workflow.can_confirm = False
        if missing_inputs:
            workflow.status = "awaiting_input_binding"
        elif not parameters_valid or workflow.parameters_reviewed_at is None:
            workflow.status = "awaiting_parameter_review"
        elif workflow.dry_run_report is None or not workflow.dry_run_report.valid:
            workflow.status = "awaiting_dry_run"
        else:
            workflow.status = "ready"
            workflow.can_dry_run = True
            workflow.can_confirm = True

    def validate_all_parameters(self, workflow: Workflow) -> None:
        errors = validate_workflow_scope(workflow, self.registry)
        if errors:
            raise ValueError("输入输出约束：" + "；".join(errors))
        for step in workflow.steps:
            contracts.validate_method_parameters(
                contracts.selected_method(step), step.selected_parameters
            )

    # Public pure operations used by editor/recipe adapters; no service internals exposed.
    selected_method = staticmethod(contracts.selected_method)
    default_parameters = staticmethod(contracts.default_parameters)
    validate_method_parameters = staticmethod(contracts.validate_method_parameters)
    resolve_workflow_inputs = staticmethod(inputs.resolve_workflow_inputs)
    refresh_execution_summary = staticmethod(warnings.refresh_execution_summary)
