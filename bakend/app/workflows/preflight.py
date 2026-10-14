"""WorkflowPreflight: prepare execution evidence; never submit a Runner job."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..execution.recipe_compiler import RecipeCompilationError, RecipeCompiler
from ..historical_inputs import HistoricalInputs
from ..method_planning import validate_workflow_scope
from ..operation_context import checkpoint
from ..sample_scope import partial_sample_scope, restrict_sample_bindings
from ..workflow_models import (
    DryRunCommand,
    DryRunReport,
    Workflow,
)
from . import contracts

if TYPE_CHECKING:
    from ..capability_registry import CapabilityRegistry
    from ..local_store import LocalStore
    from .catalog import MethodCatalog
    from .session import WorkflowSession
    from .state import WorkflowState


class WorkflowPreflight:
    def __init__(
        self,
        session: WorkflowSession,
        state: WorkflowState,
        store: LocalStore,
        catalog: MethodCatalog,
        registry: CapabilityRegistry,
        executable_runner_ids: set[str],
        dataset_service=None,
    ) -> None:
        self.session = session
        self.state = state
        self.store = store
        self.catalog = catalog
        self.registry = registry
        self.executable_runner_ids = executable_runner_ids
        self.dataset_service = dataset_service

    def dry_run(self, workflow_id: str, expected_revision: int) -> Workflow:
        with self.session.lock:
            workflow = self.session.require(workflow_id)
            contracts.check_revision(workflow, expected_revision)
        if workflow.parameters_reviewed_at is None:
            raise ValueError("请先审阅并确认参数")
        from .preflight_inputs import resolve_planned_inputs
        if self.dataset_service:
            resolve_planned_inputs(workflow, self.dataset_service)
        errors = validate_workflow_scope(workflow, self.registry)
        if errors:
            raise ValueError("流程输入/输出约束未满足：" + "；".join(errors))
        self.state.validate_all_parameters(workflow)
        declared_runners = self.registry.runner_ids()
        selected_methods = [contracts.selected_method(step) for step in workflow.steps]
        workflow.sample_scope = partial_sample_scope(workflow)
        checked_workflow = restrict_sample_bindings(workflow, workflow.sample_scope)
        try:
            execution_units = RecipeCompiler(self.registry).compile(checked_workflow)
        except RecipeCompilationError as exc:
            raise ValueError(f"执行配方无法展开：{exc}") from exc
        dataset_required = any(
            not method.input_slots and "*" not in method.input_types
            for method in selected_methods
        )
        dataset_valid = bool(
            workflow.dataset
            and Path(workflow.dataset.absolute_path).is_dir()
            and workflow.dataset_id
        )
        history_problems = HistoricalInputs(self.store).validate_workflow(workflow)
        structured_inputs_valid = not history_problems and all(
            not step.missing_required_inputs
            and all(
                binding.source == "upstream_output"
                or contracts.binding_paths_exist(binding)
                for binding in step.input_bindings.values()
            )
            for step, method in zip(
                checked_workflow.steps, selected_methods, strict=True
            )
            if method.input_slots
        )
        dataset_check_passed = (
            dataset_valid or not dataset_required
        ) and structured_inputs_valid
        commands: list[DryRunCommand] = []
        quality_checks: list[dict] = []
        input_checks: dict = {}
        for unit in execution_units:
            checkpoint(f"检查步骤：{unit.step.title}")
            step, method = unit.step, unit.method
            runner_valid = (
                method.execution_mode == "planning"
                or method.runner_id in declared_runners
            )
            messages: list[str] = (
                ["规划型步骤：只校验参数和流程连接，不对应可执行 runner"]
                if method.execution_mode == "planning"
                else []
                if runner_valid
                else ["runner_id 未在受信任注册表中声明"]
            )
            command_valid = runner_valid
            if (
                runner_valid
                and method.execution_mode == "runner"
                and self.catalog.runner_registry is not None
            ):
                preflight_problems = self.catalog.runner_registry.preflight_snapshot(
                    workflow, step, method, quality_checks, input_checks
                )
                messages.extend(preflight_problems)
                for slot_id, binding in step.input_bindings.items():
                    if binding.source == "upstream_output":
                        messages.append(
                            f"输入 {slot_id} 将由上游步骤 {binding.upstream_step_id} "
                            f"的输出 {binding.output_slot_id} 提供；"
                            "流程连接已校验，实际文件及内容将在执行该步骤前检查。"
                        )
                if (
                    not preflight_problems
                    and step.selected_parameters.get("backend") == "cuda"
                ):
                    messages.append(
                        "CUDA 方法将在实际执行节点再次检测 GPU；无显卡的主节点请使用 Slurm 并申请 GPU"
                    )
                command_valid = command_valid and not preflight_problems
            commands.append(
                DryRunCommand(
                    step_id=step.id,
                    runner_id=method.runner_id,
                    command_preview=contracts.command_preview(
                        method,
                        step.selected_parameters,
                        workflow.dataset,
                        step.input_bindings,
                    ),
                    execution_mode=method.execution_mode,
                    valid=command_valid,
                    messages=messages,
                )
            )
        available_runner_ids = (
            {
                unit.method.runner_id
                for unit in execution_units
                if self.catalog.runner_registry.get(unit.method.runner_id)
                and self.catalog.runner_registry.get(unit.method.runner_id)
                .runtime_status()
                .get("available")
            }
            if self.catalog.runner_registry is not None
            else self.executable_runner_ids
        )
        execution_enabled = bool(execution_units) and all(
            unit.method.execution_mode == "runner"
            and unit.method.runner_id in available_runner_ids
            for unit in execution_units
        )
        warnings = [
            "Dry-run 已验证流程、参数、路径和 runner 声明，尚未运行处理软件。",
            *history_problems,
        ]
        if workflow.sample_scope is not None:
            count = len(workflow.sample_scope.eligible_sample_ids)
            warnings.append(
                f"仅运行共同匹配的 {count} / {workflow.sample_scope.total} 个样本；其余样本不提交执行。"
            )
            dataset_check_passed = dataset_check_passed and count > 0
        if any(unit.method.execution_mode == "planning" for unit in execution_units):
            warnings.append(
                "流程包含规划型步骤；这些步骤只形成参数快照，不生成可执行命令。"
            )
        unavailable_runners = sorted(
            {
                unit.method.runner_id
                for unit in execution_units
                if unit.method.execution_mode == "runner"
                and unit.method.runner_id not in available_runner_ids
            }
        )
        if unavailable_runners:
            warnings.append(
                "以下 Runner 尚未接入可信实现或当前环境依赖不可用："
                + "、".join(unavailable_runners)
            )
        if execution_enabled:
            warnings.append(
                "所有步骤都已接入受信任 Worker；最终确认后可以启动真实运行。"
            )
        if dataset_required and not dataset_valid:
            warnings.append("未关联有效的数据集目录，无法通过 dry-run。")
        if not structured_inputs_valid:
            warnings.append("存在未绑定或已经失效的方法输入路径，无法通过 dry-run。")
        valid = dataset_check_passed and all(command.valid for command in commands)
        from collections import Counter

        quality_summary = dict(Counter(row["status"] for row in quality_checks))
        workflow.input_check_cache = input_checks
        workflow.dry_run_report = DryRunReport(
            quality_checks=quality_checks,  # Preserve full evidence; UI groups/pages it.
            quality_summary=quality_summary,
            quality_review_required=bool(quality_summary.get("review")),
            valid=valid,
            execution_enabled=execution_enabled,
            dataset_path_validated=dataset_check_passed,
            commands=commands,
            warnings=warnings,
            checked_at=contracts.now(),
        )
        with self.session.lock:
            checkpoint()
            contracts.check_revision(
                self.session.require(workflow_id), expected_revision
            )
            contracts.advance(workflow)
            self.state.refresh(workflow)
            self.session.save(workflow, "dry_run_completed", {"valid": valid})
            return workflow.model_copy(deep=True)
