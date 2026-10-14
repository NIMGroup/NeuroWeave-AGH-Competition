from __future__ import annotations

import os
import tempfile
from pathlib import Path
from threading import Event

from ..operation_context import checkpoint, current_operation
from ..workflow_models import MethodOption, Workflow, WorkflowStep
from ..parameter_rules import effective_parameters, input_required
from .base import RunnerContext, TrustedRunner
from .runners.ants import AntsRegistrationRunner
from .runners.bids_prepare import BidsPrepareRunner
from .runners.bids_apps import FmriprepRunner, QsiprepRunner
from .runners.dcm2niix import Dcm2niixRunner
from .runners.dataset_inspect import DatasetInspectRunner
from .runners.dipy import DipyTensorRunner
from .runners.freesurfer import FreeSurferReconAllRunner
from .runners.fsl import FslDtifitRunner, FslEddyRunner, FslFlirtRunner
from .runners.mrtrix import MrtrixTckgenRunner
from .runners.mrtrix_commands import command_runners
from .runners.scientific_commands import scientific_runners
from .runners.mrtrix_pipeline import (
    MrtrixDwi2FodRunner,
    MrtrixDwi2ResponseRunner,
    MrtrixFiveTtGenRunner,
    MrtrixTck2ConnectomeRunner,
)
from .runners.modality_scripts import AslCbfRunner, NiftiInspectRunner, PetSuvrRunner
from .runners.nii2dcm import Nii2DcmRunner
from .runners.report import ReportBuilderRunner
from .runners.table_clean import TableCleanRunner
from .runners.reviewed import ReviewedRunner, specifications


class RunnerRegistry:
    """Allowlist of code-reviewed runners that may execute on the server."""

    def __init__(self) -> None:
        self.planning_only = os.getenv('NEUROWEAVE_PLANNING_ONLY', 'false').lower() in {'true', '1', 'yes'}
        runners: list[TrustedRunner] = [
            DatasetInspectRunner(),
            AntsRegistrationRunner(),
            BidsPrepareRunner(),
            FmriprepRunner(),
            QsiprepRunner(),
            Dcm2niixRunner(),
            DipyTensorRunner(),
            FslFlirtRunner(),
            FslDtifitRunner(),
            FslEddyRunner(),
            MrtrixTckgenRunner(),
            MrtrixDwi2ResponseRunner(),
            MrtrixDwi2FodRunner(),
            MrtrixFiveTtGenRunner(),
            MrtrixTck2ConnectomeRunner(),
            AslCbfRunner(),
            PetSuvrRunner(),
            NiftiInspectRunner(),
            Nii2DcmRunner(),
            FreeSurferReconAllRunner(),
            ReportBuilderRunner(),
            TableCleanRunner(),
        ]
        runners.extend(ReviewedRunner(spec) for spec in specifications().values())
        runners.extend(command_runners())
        runners.extend(scientific_runners())
        self._runners = {runner.runner_id: runner for runner in runners}

    def ids(self) -> set[str]:
        return set(self._runners)

    def get(self, runner_id: str) -> TrustedRunner | None:
        return self._runners.get(runner_id)

    def available_ids(self) -> set[str]:
        if self.planning_only:
            return set()
        return {
            runner_id
            for runner_id, runner in self._runners.items()
            if bool(self.runtime_status(runner_id).get("available"))
        }

    def runtime_status(self, runner_id: str) -> dict[str, object]:
        runner = self.get(runner_id)
        if runner is None:
            return {'available': False, 'reason': '执行器未注册'}
        if self.planning_only:
            return {'available': False, 'planning_only': True,
                    'reason': '仅规划部署：方法契约可用于规划，实际运行需接入计算环境'}
        return runner.runtime_status()

    def public_catalog(self) -> list[dict[str, object]]:
        catalog: list[dict[str, object]] = []
        for runner_id, runner in sorted(self._runners.items()):
            catalog.append(
                {
                    "runner_id": runner_id,
                    "label": runner.label,
                    **self.runtime_status(runner_id),
                }
            )
        return catalog

    def preflight_snapshot(
        self,
        workflow: Workflow,
        step: WorkflowStep,
        method: MethodOption,
        quality_checks: list[dict] | None = None,
        input_checks: dict | None = None,
    ) -> list[str]:
        groups = [
            binding
            for binding in step.input_bindings.values()
            if binding.source != "upstream_output"
            and binding.binding_mode == "per_sample"
        ]
        if groups:
            identifiers = [match.sample_id for match in groups[0].sample_matches]
            if not identifiers or len(set(identifiers)) != len(identifiers):
                return ["多样本输入为空或含有重复样本"]
            expected = set(identifiers)
            slots = {s.id: s for s in method.input_slots}
            required_groups = [
                b
                for b in groups
                if input_required(
                    slots[b.slot_id],
                    effective_parameters(method, step.selected_parameters),
                )
            ]
            if any(
                {match.sample_id for match in binding.sample_matches} != expected
                or len(binding.sample_matches) != len(identifiers)
                or any(
                    match.status != "matched" or not match.absolute_path
                    for match in binding.sample_matches
                )
                for binding in required_groups
            ):
                return ["多样本输入覆盖不一致，或仍有缺失文件"]

            def check_sample(sample_id):
                checkpoint(f"检查样本：{sample_id}")
                sample_step = step.model_copy(
                    update={
                        "input_bindings": {
                            k: b.model_copy(deep=False)
                            for k, b in step.input_bindings.items()
                        }
                    }
                )
                for binding in sample_step.input_bindings.values():
                    if (
                        binding.source == "upstream_output"
                        or binding.binding_mode != "per_sample"
                    ):
                        continue
                    match = next(
                        (
                            item
                            for item in binding.sample_matches
                            if item.sample_id == sample_id and item.status == "matched"
                        ),
                        None,
                    )
                    if match is None:
                        continue
                    binding.absolute_path = match.absolute_path
                    if binding.history:
                        binding.history = binding.history.model_copy(deep=False)
                    from ..historical_inputs import select_history_sample

                    select_history_sample(binding, sample_id)
                    binding.binding_mode = "single"
                    binding.sample_matches = []
                sample_checks = []
                sample_step.input_bindings = {
                    k: b
                    for k, b in sample_step.input_bindings.items()
                    if b.binding_mode != "per_sample"
                    or input_required(
                        slots[k], effective_parameters(method, step.selected_parameters)
                    )
                }
                sample_cache = {}
                result = self.preflight_snapshot(
                    workflow, sample_step, method, sample_checks, sample_cache
                )
                if quality_checks is not None:
                    quality_checks.extend(
                        {**row, "sample_id": sample_id} for row in sample_checks
                    )
                if input_checks is not None:
                    input_checks.update(sample_cache)
                return [f"样本 {sample_id}：{problem}" for problem in result]

            from concurrent.futures import ThreadPoolExecutor
            from contextvars import copy_context
            import os

            workers = max(1, min(int(os.getenv("WORKFLOW_PREFLIGHT_WORKERS", "2")), 4))
            problems = []
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="preflight"
            ) as pool:
                # At most one small batch is pending; never enqueue every sample.
                for start in range(0, len(identifiers), workers):
                    checkpoint(
                        f"检查样本 {start + 1}–{min(start + workers, len(identifiers))} / {len(identifiers)}"
                    )
                    futures = [
                        pool.submit(copy_context().run, check_sample, identity)
                        for identity in identifiers[start : start + workers]
                    ]
                    for future in futures:
                        problems.extend(future.result())
            return problems
        runner = self.get(method.runner_id)
        if runner is None:
            return [f"runner 尚未注册：{method.runner_id}"]
        remote_gpu_available = False
        if step.selected_parameters.get("backend") == "cuda":
            from .slurm_backend import SlurmBackend

            remote_gpu_available = bool(SlurmBackend().status().get("gpu_available"))
        with tempfile.TemporaryDirectory(prefix="localflow-dryrun-") as temporary:
            context = RunnerContext(
                run_id="dry-run",
                workflow=workflow.model_copy(deep=False),
                step=step.model_copy(deep=False),
                method=method.model_copy(deep=False),
                output_directory=Path(temporary),
                cancel_event=current_operation.get().cancel_event
                if current_operation.get()
                else Event(),
                log=lambda _message, _progress, _level: None,
                remote_gpu_available=remote_gpu_available,
                is_dry_run=True,
            )
            context.step.selected_parameters = effective_parameters(
                context.method, step.selected_parameters
            )
            try:
                from ..historical_inputs import historical_binding_problems

                history_problems = [
                    problem
                    for binding in context.step.input_bindings.values()
                    for problem in historical_binding_problems(binding)
                ]
                problems = history_problems + runner.preflight(context)
                if quality_checks is not None:
                    quality_checks.extend(context.quality_checks)
                if input_checks is not None:
                    input_checks.update(context.input_checks)
                return problems
            except (RuntimeError, ValueError, OSError) as exc:
                return [f"输入或运行环境检查失败：{exc}"]
