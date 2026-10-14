"""Workflow contracts: deterministic calculations without model calls or persistence."""

from __future__ import annotations

import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..capability_registry import CapabilityMethod
from ..dataset_models import DatasetRecord
from ..parameter_rules import active as parameter_active
from ..parameter_rules import conflicts as parameter_conflicts
from ..parameter_rules import input_required
from ..parameter_rules import required as parameter_required
from ..workflow_models import (
    InputBinding,
    MethodOption,
    ParameterSpec,
    Workflow,
    WorkflowStep,
)
from .errors import RevisionConflict


def scope_conflicts(
    method: MethodOption | CapabilityMethod, excluded: set[str]
) -> set[str]:
    included = set(method.included_capabilities)
    if method.execution_recipe:
        included.update(item.capability for item in method.execution_recipe.steps)
    return included & excluded


def condition_matches(condition: dict[str, list[Any]], values: dict[str, Any]) -> bool:
    return all(
        values.get(parameter_id) in allowed
        for parameter_id, allowed in condition.items()
    )


def slot_required(slot: Any, parameters: dict[str, Any]) -> bool:
    return input_required(slot, parameters)


def output_generated(slot: Any, parameters: dict[str, Any]) -> bool:
    return not slot.generated_when or condition_matches(slot.generated_when, parameters)


def parameter_visible(spec: ParameterSpec, values: dict[str, Any]) -> bool:
    return parameter_active(spec, values)


def validate_parameter(spec: ParameterSpec, value: Any) -> None:
    valid_type = {
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "number": type(value) in {int, float},
        "string": isinstance(value, str),
    }[spec.type]
    if not valid_type:
        raise ValueError(f"参数 {spec.label} 的类型不正确")
    if spec.options and not any(option.value == value for option in spec.options):
        raise ValueError(f"参数 {spec.label} 不在允许的选项中")
    if spec.type in {"integer", "number"}:
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"参数 {spec.label} 必须是有限数值")
        if spec.minimum is not None and numeric < spec.minimum:
            raise ValueError(f"参数 {spec.label} 不能小于 {spec.minimum:g}{spec.unit}")
        if spec.maximum is not None and numeric > spec.maximum:
            raise ValueError(f"参数 {spec.label} 不能大于 {spec.maximum:g}{spec.unit}")


def validate_method_parameters(
    method: MethodOption,
    values: dict[str, Any],
    *,
    require_complete: bool = True,
) -> None:
    specs = {spec.id: spec for spec in method.parameters}
    unknown = sorted(set(values) - set(specs))
    if unknown:
        raise ValueError(f"包含未声明的参数：{', '.join(unknown)}")
    for spec in specs.values():
        visible = parameter_visible(spec, values)
        if (
            require_complete
            and parameter_required(spec, values)
            and values.get(spec.id) is None
        ):
            raise ValueError(f"缺少必填参数：{spec.label}")
        if visible and values.get(spec.id) is not None:
            validate_parameter(spec, values[spec.id])
        collisions = parameter_conflicts(spec, values)
        if collisions:
            raise ValueError(
                f"参数 {spec.label} 与 {', '.join(collisions)} 不能同时设置"
            )


def binding_paths_exist(binding: InputBinding) -> bool:
    if binding.binding_mode == "per_sample":
        selected = [m for m in binding.sample_matches if m.status=='matched'] if binding.allow_partial else binding.sample_matches
        return bool(selected) and all(
            match.status == "matched"
            and bool(match.absolute_path)
            and Path(match.absolute_path).exists()
            for match in selected
        )
    paths = binding.absolute_paths or (
        [binding.absolute_path] if binding.absolute_path else []
    )
    return bool(paths) and all(Path(p).exists() for p in paths)


def command_preview(
    method: MethodOption,
    parameters: dict[str, Any],
    dataset: DatasetRecord | None,
    input_bindings: dict[str, InputBinding] | None = None,
) -> str:
    bindings = input_bindings or {}
    if method.execution_recipe is not None:
        stages = " -> ".join(
            f"{step.title}[{step.method_id}]" for step in method.execution_recipe.steps
        )
        parts = ["RUNNER_PIPELINE", method.execution_recipe.recipe_id, "::", stages]
        for spec in method.parameters:
            if parameters.get(spec.id) is None or not parameter_visible(
                spec, parameters
            ):
                continue
            value = parameters[spec.id]
            rendered = str(value).lower() if isinstance(value, bool) else str(value)
            parts.extend((f"--{spec.argument}", rendered))
        return " ".join(parts)
    if method.execution_mode == "planning":
        parts = ["PLAN", method.skill_id, "--method", method.id]
        for slot in method.input_slots:
            binding = bindings.get(slot.id)
            if binding is None:
                continue
            source = binding_command_source(binding, dataset)
            if source:
                parts.extend((f"--input-{slot.id}", subprocess.list2cmdline([source])))
        for spec in method.parameters:
            if parameters.get(spec.id) is None or not parameter_visible(
                spec, parameters
            ):
                continue
            value = parameters[spec.id]
            if value == "" and not spec.required:
                continue
            rendered = str(value).lower() if isinstance(value, bool) else str(value)
            parts.extend((f"--{spec.argument}", rendered))
        return " ".join(parts)

    parts = [method.runner_id]
    if method.input_slots:
        for slot in method.input_slots:
            binding = bindings.get(slot.id)
            if binding is None:
                continue
            source = binding_command_source(binding, dataset)
            if source:
                parts.extend((f"--input-{slot.id}", subprocess.list2cmdline([source])))
    else:
        input_path = (
            subprocess.list2cmdline([dataset.absolute_path])
            if dataset
            else "<DATASET_FOLDER>"
        )
        parts.extend(("--input", input_path))
    parts.extend(("--output", "<OUTPUT_FOLDER>"))
    for spec in method.parameters:
        if parameters.get(spec.id) is None or not parameter_visible(spec, parameters):
            continue
        value = parameters[spec.id]
        if value == "" and not spec.required:
            continue
        rendered = str(value).lower() if isinstance(value, bool) else str(value)
        parts.extend((f"--{spec.argument}", rendered))
    return " ".join(parts)


def binding_command_source(
    binding: InputBinding,
    dataset: DatasetRecord | None,
) -> str | None:
    if binding.deferred_resolution:
        return '<dry-run: '+(binding.path_template or binding.label)+'>'
    if binding.source == "upstream_output":
        return f"<{binding.upstream_step_id}.{binding.output_slot_id}>"
    if binding.history and binding.binding_mode == "per_sample":
        return f"<历史运行 {binding.history.run_id}：按样本标识匹配>"
    if binding.binding_mode == "per_sample" and binding.path_template:
        base = dataset.absolute_path if dataset else "<DATASET_FOLDER>"
        return str(Path(base) / binding.path_template)
    return binding.absolute_path


def default_parameters(method: MethodOption) -> dict[str, Any]:
    return {
        spec.id: spec.default for spec in method.parameters if spec.default is not None
    }


def selected_method(step: WorkflowStep) -> MethodOption:
    method = next(
        (item for item in step.method_options if item.id == step.selected_method_id),
        None,
    )
    if method is None:
        raise ValueError(f"步骤 {step.title} 尚未选择方法")
    return method


def step(workflow: Workflow, step_id: str) -> WorkflowStep:
    step = next((item for item in workflow.steps if item.id == step_id), None)
    if step is None:
        raise ValueError(f"流程中不存在步骤 {step_id}")
    return step


def check_revision(workflow: Workflow, expected_revision: int) -> None:
    if workflow.revision != expected_revision:
        raise RevisionConflict(workflow)


def ensure_editable_chain(workflow: Workflow) -> None:
    if workflow.locked_at is not None:
        raise ValueError("流程链已经锁定，请先解锁再修改方法")
    if workflow.status == "confirmed":
        raise ValueError("流程已经确认")


def clear_review(workflow: Workflow) -> None:
    workflow.parameters_reviewed_at = None
    workflow.dry_run_report = None
    workflow.confirmed_at = None


def advance(workflow: Workflow) -> None:
    workflow.revision += 1
    workflow.updated_at = now()


def definition_reason(definitions: list[CapabilityMethod], method_id: str) -> str:
    definition = next(
        (item for item in definitions if item.method_id == method_id), None
    )
    return definition.default_reason if definition else ""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()
