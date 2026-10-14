"""Workflow inputs: deterministic calculations without model calls or persistence."""

from __future__ import annotations

from typing import Any

from ..artifact_semantics import compatible
from ..workflow_models import (
    InputBinding,
    Workflow,
)
from . import contracts


def infer_dataset_binding(workflow: Workflow, slot: Any) -> InputBinding | None:
    if workflow.input_selection == 'user':
        return None
    dataset = workflow.dataset
    if (
        dataset is None
        or "dataset" not in slot.source_options
        or not workflow.task_contract.allows_external(slot.type, dataset.absolute_path)
    ):
        return None
    # An inspection can prove that a file type exists somewhere in a dataset,
    # but it cannot identify the intended file. Only folder slots may bind the
    # dataset root automatically; file inputs must be chosen explicitly (or
    # connected to an upstream output).
    if slot.binding != "folder" and slot.type != "dataset_folder":
        return None
    available_types = set(dataset.inspection.input_types)
    if dataset.inspection.has_t1w:
        available_types.add("t1w_image")
    if slot.type not in available_types and slot.type != "dataset_folder":
        return None
    return InputBinding(
        slot_id=slot.id,
        artifact_type=slot.type,
        source="dataset",
        label=dataset.display_name,
        root_id=dataset.root_id,
        relative_path=dataset.relative_path,
        absolute_path=dataset.absolute_path,
        binding_mode="dataset",
        dataset_id=dataset.dataset_id,
        status="validated",
        messages=["由已登记数据集的只读扫描结果匹配。"],
    )


def resolve_workflow_inputs(workflow: Workflow) -> None:
    upstream_outputs: list[tuple[str, Any]] = []
    for step in workflow.steps:
        method = next(
            (
                item
                for item in step.method_options
                if item.id == step.selected_method_id
            ),
            None,
        )
        if method is None:
            step.input_bindings = {}
            step.missing_required_inputs = []
            continue

        previous_bindings = step.input_bindings
        resolved: dict[str, InputBinding] = {}
        for slot in method.input_slots:
            existing = previous_bindings.get(slot.id)
            if (
                existing
                and (workflow.input_selection != 'user' or existing.confirmed_by_user)
                and compatible(existing.artifact_type, slot.type)
                and existing.source == "existing_path"
                and (
                    existing.deferred_resolution
                    or workflow.input_selection == 'user'
                    or
                    existing.history is not None
                    or contracts.binding_paths_exist(existing)
                )
            ):
                # Honor an existing-file binding when a declared source
                # selector controls whether software generates that input.
                for key, allowed in slot.required_when.items():
                    spec = next(
                        (item for item in method.parameters if item.id == key), None
                    )
                    if (
                        key.endswith("_source")
                        and "existing" in allowed
                        and spec
                        and any(option.value == "existing" for option in spec.options)
                    ):
                        step.selected_parameters[key] = "existing"
                resolved[slot.id] = existing
                continue

            if workflow.planner_source.startswith("method-contract:"):
                # Keep exact graph edges. Matching by artifact type alone can
                # accidentally substitute CSF/GM response for a WM response.
                if (
                    existing
                    and existing.source == "upstream_output"
                    and any(
                        sid == existing.upstream_step_id
                        and out.id == existing.output_slot_id
                        and compatible(out.type, slot.type)
                        for sid, out in upstream_outputs
                    )
                ):
                    resolved[slot.id] = existing
                else:
                    dataset_binding = infer_dataset_binding(workflow, slot)
                    if dataset_binding:
                        resolved[slot.id] = dataset_binding
                continue

            if slot.type in step.manual_input_types:
                # A removed producer must not be silently replaced by a
                # different upstream output or an inferred dataset binding.
                continue

            upstream = next(
                (
                    (upstream_step_id, output)
                    for upstream_step_id, output in reversed(upstream_outputs)
                    if output.type == slot.type
                    and "upstream_output" in slot.source_options
                ),
                None,
            )
            if upstream:
                upstream_step_id, output = upstream
                resolved[slot.id] = InputBinding(
                    slot_id=slot.id,
                    artifact_type=slot.type,
                    source="upstream_output",
                    label=output.label,
                    upstream_step_id=upstream_step_id,
                    output_slot_id=output.id,
                    status="planned",
                    messages=["将在上游步骤完成后自动绑定。"],
                )
                continue

            dataset_binding = infer_dataset_binding(workflow, slot)
            if dataset_binding:
                resolved[slot.id] = dataset_binding

        from ..artifact_semantics import specialize

        actual_types = {(sid, out.id): out.type for sid, out in upstream_outputs}
        method = specialize(
            method,
            {
                k: actual_types.get(
                    (b.upstream_step_id, b.output_slot_id), b.artifact_type
                )
                for k, b in resolved.items()
            },
            step.selected_parameters,
        )
        step.method_options = [
            method if m.id == method.id else m for m in step.method_options
        ]
        step.input_bindings = resolved
        step.input_suggestions = {k:v for k,v in step.input_suggestions.items()
                                  if any(s.id == k for s in method.input_slots)}
        step.missing_required_inputs = [
            slot.id
            for slot in method.input_slots
            if (
                contracts.slot_required(slot, step.selected_parameters)
                or (
                    slot.type in step.manual_input_types
                    and contracts.condition_matches(
                        {
                            key: allowed
                            for key, allowed in slot.required_when.items()
                            if not key.endswith("_source")
                        },
                        step.selected_parameters,
                    )
                )
            )
            and slot.id not in resolved
        ]
        upstream_outputs.extend(
            (step.id, output)
            for output in method.output_slots
            if contracts.output_generated(output, step.selected_parameters)
        )
