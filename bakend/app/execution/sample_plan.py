"""Expand existing runner units over validated dataset sample bindings."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ..run_models import RunExecutionOptions
from ..workflow_models import Workflow
from ..historical_inputs import select_history_sample
from ..sample_scope import partial_sample_scope, restrict_sample_bindings
from .recipe_compiler import ExecutionUnit, RecipeCompilationError, RecipeCompiler
from .output_layout import output_key


def sample_parallel_limits(execution: RunExecutionOptions, sample_count: int) -> dict[str, int]:
    """按持久化节点顺序轮流分配样本，资源校验和队列上限共用同一计算。"""
    if execution.sample_mode != "per_sample":
        return {}
    node_count = len(execution.nodes)
    return {
        allocation.node: min(allocation.parallel_samples, (sample_count + node_count - 1 - index) // node_count)
        for index, allocation in enumerate(execution.nodes)
    }


def unit_parallel_limits(execution: RunExecutionOptions, units: list[ExecutionUnit]) -> dict[str, int]:
    """Count actual assigned samples, including a retry restricted to one node."""
    return {allocation.node: min(allocation.parallel_samples, len({u.sample_id for u in units
            if u.sample_id and u.execution and u.execution.node == allocation.node}))
            for allocation in execution.nodes} if execution.sample_mode == "per_sample" else {}


def schedule_units(workflow: Workflow, units: list[ExecutionUnit],
                   execution: RunExecutionOptions) -> list[ExecutionUnit]:
    scope = partial_sample_scope(workflow)
    if scope is not None:
        if not scope.eligible_sample_ids:
            raise RecipeCompilationError("各输入没有共同匹配的样本，无法执行")
        if execution.sample_mode != "per_sample":
            raise RecipeCompilationError("部分历史输入请使用按样本运行，以按原始样本标识提交")
    if execution.sample_mode == "single":
        options = execution
        if execution.nodes:
            options = RunExecutionOptions(
                backend=execution.backend, partition=execution.partition,
                **execution.nodes[0].model_dump(exclude={"parallel_samples"}),
            )
        for unit in units:
            if any(binding.binding_mode == "per_sample" and len(binding.sample_matches) > 1
                   for binding in unit.step.input_bindings.values()):
                raise RecipeCompilationError("输入包含多个样本，请选择按样本运行并指定节点")
        return [replace(unit, execution=options, workspace_key=output_key(
            execution.output_layout or workflow.output_layout, unit.workspace_key)) for unit in units]
    if execution.backend != "slurm" or not execution.nodes:
        raise RecipeCompilationError("按样本运行需要选择 Slurm 节点及每个节点的资源")
    if not workflow.dataset or not workflow.dataset.inspection.samples:
        raise RecipeCompilationError("数据集没有完整的样本清单，请重新扫描数据集")
    samples = workflow.dataset.inspection.samples
    if len(samples) != workflow.dataset.inspection.sample_count:
        raise RecipeCompilationError("数据集样本清单不完整，不能进行批量运行")
    if len({sample.sample_id for sample in samples}) != len(samples):
        raise RecipeCompilationError("数据集样本标识重复，不能进行批量运行")
    original_ordinals = {sample.sample_id: index + 1 for index, sample in enumerate(samples)}
    if scope is not None:
        eligible = set(scope.eligible_sample_ids)
        samples = [sample for sample in samples if sample.sample_id in eligible]
        # Apply the same scope to compiled recipe stages and ordinary methods.
        execution_view = workflow.model_copy(update={"steps": [unit.step for unit in units]})
        restricted = restrict_sample_bindings(execution_view, scope)
        units = [replace(unit, step=step) for unit, step in zip(units, restricted.steps, strict=True)]
    if len(execution.nodes) > len(samples):
        raise RecipeCompilationError("选择的节点数不能多于样本数")
    if sum(sample_parallel_limits(execution, len(samples)).values()) > 128:
        raise RecipeCompilationError("一次运行的实际并行样本数不能超过 128")
    root = Path(workflow.dataset.absolute_path).resolve(strict=True)
    # A subset does not turn an original subject file into a shared input.
    # Skipped directories need not exist, but their identity boundary still applies.
    original_roots = [(root / sample.relative_root).resolve()
                      for sample in workflow.dataset.inspection.samples]
    roots = {}
    for sample in samples:
        path = (root / sample.relative_root).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_dir():
            raise RecipeCompilationError("样本目录不存在或超出当前数据集")
        roots[sample.sample_id] = path
    # Single-sample selection deliberately stores ordinary file/directory inputs.
    # Only an original multi-sample dataset needs a template to prevent reuse.
    # Use the original count so a one-sample subset keeps the same protection.
    if len(original_ordinals) > 1 and not any(binding.binding_mode == "per_sample"
                                            for unit in units for binding in unit.step.input_bindings.values()):
        if any(unit.method.runner_id != "dataset.inspect" for unit in units):
            raise RecipeCompilationError("请先通过输入路径模板绑定各样本文件，避免重复处理同一输入")
    scheduled = []
    for index, sample in enumerate(samples):
        prefix = f"sample_{original_ordinals[sample.sample_id]:04d}__"
        allocation = execution.nodes[index % len(execution.nodes)]
        options = RunExecutionOptions(
            backend="slurm", partition=execution.partition, **allocation.model_dump(exclude={"parallel_samples"}),
        )
        sample_units = []
        for unit in units:
            step = unit.step.model_copy(deep=True)
            step.id = prefix + step.id
            step.depends_on = [prefix + identifier for identifier in step.depends_on]
            for name, binding in step.input_bindings.items():
                if binding.source == "upstream_output":
                    binding.upstream_step_id = prefix + (binding.upstream_step_id or "")
                    for extra in binding.upstream_sources:
                        extra.step_id = prefix + extra.step_id
                elif binding.binding_mode == "per_sample":
                    matches = [item for item in binding.sample_matches if item.sample_id == sample.sample_id]
                    if len(matches) != 1 or matches[0].status != "matched" or not matches[0].absolute_path:
                        raise RecipeCompilationError(f"样本 {sample.label} 的输入 {name} 缺失或不唯一")
                    binding.absolute_path = matches[0].absolute_path
                    binding.relative_path = matches[0].relative_path
                    select_history_sample(binding, sample.sample_id)
                    binding.binding_mode = "single"
                    binding.sample_matches = []
                elif len(original_ordinals) > 1 and (binding.absolute_path or binding.absolute_paths):
                    paths=[Path(p).resolve(strict=True) for p in binding.absolute_paths or [binding.absolute_path]]
                    if any(path == root or any(path.is_relative_to(folder) for folder in original_roots) for path in paths):
                        raise RecipeCompilationError(
                            f"输入 {name} 指向单一样本或整个数据集，请使用各样本路径模板"
                        )
            sample_units.append(replace(
                unit, step=step, workspace_key=output_key(execution.output_layout or workflow.output_layout,
                    unit.workspace_key, sample_id=sample.sample_id, legacy_prefix=prefix),
                sample_id=sample.sample_id, sample_label=sample.label,
                sample_root=str(roots[sample.sample_id]), execution=options,
            ))
        RecipeCompiler._validate_upstream_inputs(sample_units)
        scheduled.extend(sample_units)
    if len({unit.workspace_key for unit in scheduled}) != len(scheduled):
        raise RecipeCompilationError('输出目录冲突，不能覆盖另一份样本或步骤产物')
    return scheduled


def workflow_for_unit(workflow: Workflow, unit: ExecutionUnit) -> Workflow:
    if not unit.sample_id or not workflow.dataset:
        return workflow
    selected = workflow.model_copy(deep=True)
    selected.dataset.absolute_path = unit.sample_root
    selected.dataset.inspection.samples = [
        item for item in selected.dataset.inspection.samples if item.sample_id == unit.sample_id
    ]
    selected.dataset.inspection.sample_count = 1
    return selected
