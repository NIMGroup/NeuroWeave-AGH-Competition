"""Workflow warnings: deterministic calculations without model calls or persistence."""

from __future__ import annotations

from typing import Any

from ..dataset_service import DatasetService
from ..workflow_models import (
    MethodOption,
    Workflow,
    WorkflowStep,
)

PLANNING_WARNING = (
    "流程包含规划型结构化 Skill：可以选择参数并保存方案，但不会声称存在可执行 runner。"
)


def refresh_execution_summary(workflow: Workflow) -> None:
    selected_by_step = [
        (
            step,
            next(
                (
                    option
                    for option in step.method_options
                    if option.id == step.selected_method_id
                ),
                None,
            ),
        )
        for step in workflow.steps
    ]
    selected = [method for _step, method in selected_by_step]
    methods = (
        [method for method in selected if method is not None]
        if all(method is not None for method in selected)
        else [option for step in workflow.steps for option in step.method_options]
    )
    modes = {method.execution_mode for method in methods}
    workflow.execution_mode = (
        "mixed" if len(modes) > 1 else next(iter(modes), "planning")
    )
    managed_dataset_warnings = (
        set(workflow.dataset.inspection.warnings) if workflow.dataset else set()
    )
    workflow.warnings = [
        warning
        for warning in workflow.warnings
        if warning not in managed_dataset_warnings
        and not is_contextual_dataset_warning(warning)
        and not is_planning_warning(warning)
    ]
    workflow.warnings.extend(contextual_dataset_warnings(workflow, selected_by_step))

    planning_steps = [
        (step, method)
        for step, method in selected_by_step
        if method is not None and method.execution_mode == "planning"
    ]
    if len(planning_steps) == 1:
        step, method = planning_steps[0]
        workflow.warnings.append(
            f"步骤“{step.title}”已选择“{method.label}”，该方法仅支持规划："
            "可以配置参数并保存方案，但当前没有可执行 runner。"
        )
    elif planning_steps:
        labels = "；".join(
            f"步骤“{step.title}”的“{method.label}”" for step, method in planning_steps
        )
        workflow.warnings.append(
            f"已选择的以下方法仅支持规划，不能由 runner 实际执行：{labels}。"
        )


def is_planning_warning(warning: str) -> bool:
    return warning == PLANNING_WARNING or (
        warning.startswith("已选择的以下方法仅支持规划")
        or (warning.startswith("步骤“") and "该方法仅支持规划" in warning)
    )


def is_contextual_dataset_warning(warning: str) -> bool:
    return warning.startswith(
        (
            "当前已选方法需要 DWI 输入",
            "当前已选方法需要梯度表",
            "当前已选 DWI 预处理方法可能使用反向相位编码数据",
            "登记时：",
        )
    )


def contextual_dataset_warnings(
    workflow: Workflow,
    selected_by_step: list[tuple[WorkflowStep, MethodOption | None]],
) -> list[str]:
    if workflow.dataset is None:
        return []

    inspection = workflow.dataset.inspection
    dwi_source_warnings = {
        DatasetService.DWI_NOT_FOUND_WARNING,
        DatasetService.DWI_GRADIENT_WARNING,
        DatasetService.REVERSE_PHASE_WARNING,
    }
    warnings = [
        warning for warning in inspection.warnings if warning not in dwi_source_warnings
    ]
    # Registration previews are historical, not the coverage of this draft.
    warnings = [('登记时：' + warning + ' 本次输入覆盖以当前绑定核验为准。'
                 if '本次只进行了有界扫描' in warning else warning) for warning in warnings]
    chosen = [(step, method) for step, method in selected_by_step if method is not None]
    dwi_inputs = [
        (step, method) for step, method in chosen if method_needs_dwi_input(method)
    ]
    gradient_inputs = [
        (step, method) for step, method in chosen if method_needs_gradient_table(method)
    ]
    reverse_phase_methods = [
        (step, method)
        for step, method in chosen
        if method_may_use_reverse_phase(
            step.capability,
            method,
            step.selected_parameters,
        )
    ]

    if DatasetService.DWI_NOT_FOUND_WARNING in inspection.warnings and dwi_inputs:
        warnings.append(
            "当前已选方法需要 DWI 输入（"
            + step_method_labels(dwi_inputs)
            + "），但数据集扫描没有识别到 DWI NIfTI 或 DICOM；"
            "请检查所选输入路径和文件命名。"
        )
    if DatasetService.DWI_GRADIENT_WARNING in inspection.warnings and gradient_inputs:
        warnings.append(
            "当前已选方法需要梯度表（"
            + step_method_labels(gradient_inputs)
            + "），但数据集扫描发现 bval/bvec 不完整；请在 dry-run 前补齐或绑定正确文件。"
        )
    if (
        DatasetService.REVERSE_PHASE_WARNING in inspection.warnings
        and reverse_phase_methods
    ):
        warnings.append(
            "当前已选 DWI 预处理方法可能使用反向相位编码数据（"
            + step_method_labels(reverse_phase_methods)
            + "），但扫描仅按文件名未识别到 AP/PA 配对。若使用 topup，请在参数确认和 "
            "dry-run 前核对实际文件及元数据；若选择不使用 topup，此提示不构成阻塞。"
        )
    return warnings


def step_method_labels(
    selected: list[tuple[WorkflowStep, MethodOption]],
) -> str:
    return "、".join(f"{step.title} / {method.label}" for step, method in selected)


def method_needs_dwi_input(method: MethodOption) -> bool:
    input_types = set(method.input_types)
    input_types.update(slot.type for slot in method.input_slots)
    return bool(input_types & {"raw_dwi", "bids_dwi", "preprocessed_dwi"})


def method_needs_gradient_table(method: MethodOption) -> bool:
    input_types = set(method.input_types)
    input_types.update(slot.type for slot in method.input_slots)
    slot_ids = {slot.id.lower() for slot in method.input_slots}
    return "gradient_table" in input_types or bool(
        slot_ids & {"bval", "bvals", "bvec", "bvecs"}
    )


def method_may_use_reverse_phase(
    capability: str,
    method: MethodOption,
    parameters: dict[str, Any],
) -> bool:
    if capability != "dwi.preprocess":
        return False
    if str(parameters.get("susceptibility_correction", "auto")).lower() == "none":
        return False
    searchable = " ".join(
        [
            method.id,
            method.runner_id,
            method.label,
            method.description,
            *(
                text
                for parameter in method.parameters
                for text in (
                    parameter.id,
                    parameter.argument,
                    parameter.label,
                    parameter.description,
                )
            ),
        ]
    ).lower()
    return any(
        term in searchable
        for term in ("topup", "susceptibility", "phase encoding", "畸变", "相位编码")
    )
