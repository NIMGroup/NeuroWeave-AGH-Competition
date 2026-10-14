from __future__ import annotations

import json
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, RLock
from typing import Any
from uuid import uuid4

from ..artifact_semantics import compatible
from ..capability_registry import CapabilityRegistry
from ..historical_inputs import HistoricalInputs, historical_binding_problems
from ..local_store import LocalStore
from ..run_models import RunArtifact, RunExecutionOptions, RunStep, WorkflowRun
from ..sample_scope import partial_sample_scope
from ..workflow_models import MethodOption, Workflow, WorkflowStep
from .base import ExecutionCancelled, ExecutionDetached, RunnerContext
from .recipe_compiler import ExecutionUnit, RecipeCompilationError, RecipeCompiler
from .retry import implementation_fingerprint, input_fingerprint, reusable_step
from .runner_registry import RunnerRegistry
from .runners.freesurfer import FreeSurferReconAllRunner
from .sample_execution import execute_samples
from .sample_plan import schedule_units, unit_parallel_limits, workflow_for_unit
from .slurm_backend import TERMINAL_STATES, SlurmBackend


class RunConflict(RuntimeError):
    pass


class RunService:
    """Durable background execution of immutable, confirmed workflow snapshots."""

    def __init__(
        self,
        store: LocalStore,
        capability_registry: CapabilityRegistry,
        runner_registry: RunnerRegistry,
        output_root: Path,
    ) -> None:
        self.store = store
        self.capability_registry = capability_registry
        self.runner_registry = runner_registry
        self.output_root = output_root.resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.recipe_compiler = RecipeCompiler(capability_registry)
        max_workers = max(1, min(int(os.getenv("LOCALFLOW_RUN_WORKERS", "2")), 16))
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="localflow-run",
        )
        self._lock = RLock()
        self._cancel_events: dict[str, Event] = {}
        self._futures: dict[str, Future[None]] = {}
        self._detach_event = Event()
        self._progress_saved: dict[tuple[str, str | None], float] = {}
        self.slurm_backend = SlurmBackend()
        self.slurm_output_root = Path(
            os.getenv(
                "LOCALFLOW_SLURM_OUTPUT_ROOT", "/home/data/neuroweave/runs/hipeson"
            )
        ).resolve()
        self.store.fail_incomplete_runs(
            self._now(),
            "backend_restarted",
            "后端重启时任务仍处于运行状态；请确认输出后重新运行。",
            preserve_slurm=True,
        )
        self._recover_slurm_runs()

    @property
    def mutation_lock(self):
        """Shared submission boundary for file-management and workflow adapters.

        Acquiring this lock does not cancel or pause existing computation.
        """
        return self._lock

    @property
    def enabled(self) -> bool:
        return bool(self.runner_registry.available_ids())

    def execution_backends(self) -> dict[str, Any]:
        return {"local": {"available": True}, "slurm": self.slurm_backend.status()}

    def create(
        self,
        workflow_id: str,
        expected_revision: int,
        execution: RunExecutionOptions | None = None,
    ) -> WorkflowRun:
        from ..task_management import assert_plan_open
        assert_plan_open(self.store,'workflow',workflow_id)
        # The deployment uses one API process. Serialize check-and-insert and
        # all run writes with cancel so a stale snapshot cannot erase a Job ID.
        with self._lock:
            return self._create(workflow_id, expected_revision, execution)

    def _save_run(self, run: WorkflowRun, **kwargs: Any) -> None:
        progress_only = kwargs.pop("progress_only", False)
        with self._lock:
            key = (run.run_id, kwargs.get("step_id"))
            now = time.monotonic()
            if progress_only and now - self._progress_saved.get(key, 0) < 1:
                return
            self._progress_saved[key] = now
            cancellation = self._cancel_events.get(run.run_id)
            if cancellation is not None and cancellation.is_set():
                run.cancellation_requested = True
                if run.status == "succeeded":
                    raise ExecutionCancelled("任务完成登记前已收到取消请求")
                if run.status in {"queued", "running"}:
                    run.status = "cancel_requested"
            self.store.save_run(run, **kwargs)

    def _create(
        self,
        workflow_id: str,
        expected_revision: int,
        execution: RunExecutionOptions | None = None,
    ) -> WorkflowRun:
        execution = execution or RunExecutionOptions()
        if execution.backend == "slurm":
            status = self.slurm_backend.status()
            if not status.get("available"):
                raise RunConflict(str(status.get("reason") or "Slurm 不可用"))
            if execution.partition not in status.get("partitions", []):
                raise RunConflict("Slurm 分区不在可用白名单中")
            if execution.gpus and not status.get("gpu_available"):
                raise RunConflict("Slurm 暂无可调度 GPU")
        elif execution.nodes or execution.node or execution.sample_mode != "single":
            raise RunConflict("指定节点及按样本运行必须使用 Slurm")
        elif execution.gpus:
            raise RunConflict("GPU 资源请求必须使用 Slurm 执行方式")
        workflow = self.store.get_workflow(workflow_id)
        if workflow is None:
            raise KeyError(workflow_id)
        if workflow.revision != expected_revision:
            raise RunConflict(
                f"流程版本已变化：请求 R{expected_revision}，当前为 R{workflow.revision}"
            )
        if workflow.status != "confirmed":
            raise RunConflict("只有已经最终确认的流程才能开始运行")
        if workflow.dry_run_report is None or not workflow.dry_run_report.valid:
            raise RunConflict("流程尚未通过 Dry-run")
        if not workflow.dry_run_report.execution_enabled:
            raise RunConflict("当前流程包含尚未接入实际执行器的方法")
        history_problems = HistoricalInputs(self.store).validate_workflow(workflow)
        if history_problems:
            raise RunConflict("历史输入已失效：" + "；".join(history_problems))

        active = self.store.active_run_for_workflow(workflow_id)
        if active is not None:
            raise RunConflict(f"该流程已有活动任务：{active.run_id}")

        try:
            scope = partial_sample_scope(workflow)
            if scope != workflow.sample_scope:
                raise RecipeCompilationError(
                    "样本运行范围已变化，请重新审核输入并执行 Dry-run"
                )
            resolved = self.recipe_compiler.compile(workflow)
            resolved = schedule_units(workflow, resolved, execution)
        except (RecipeCompilationError, OSError, ValueError) as exc:
            raise RunConflict(str(exc)) from exc
        parallel_limits = unit_parallel_limits(execution, resolved)
        for unit in resolved:
            step, method = unit.step, unit.method
            if method.execution_mode != "runner":
                raise RunConflict(f"步骤 {step.title} 仍是规划型方法，不能执行")
            if self.runner_registry.get(method.runner_id) is None:
                raise RunConflict(
                    f"步骤 {step.title} 的 runner 尚未注册：{method.runner_id}"
                )
            from ..agent_execution_config import method_configuration
            configuration = method_configuration(method.id, method, self.runner_registry)
            if configuration['status'] == 'missing_configuration':
                raise RunConflict(f"步骤 {step.title} 的运行配置缺失：{configuration['reason']}")
            if execution.backend == "slurm":
                allocated = unit.execution or execution
                self._validate_node_resources(
                    allocated,
                    status,
                    parallel_samples=parallel_limits.get(allocated.node, 1),
                )
                for name in ("threads", "omp_threads", "n_jobs"):
                    requested = step.selected_parameters.get(name)
                    if isinstance(requested, (int, float)) and (
                        requested < 1 or requested > allocated.cpus
                    ):
                        raise RunConflict(
                            f"步骤 {step.title} 的 {name} 超过分配的 CPU，请调整资源或参数"
                        )
                # MRtrix uses 0 to disable multithreading, unlike the legacy
                # thread-count fields above. Reject oversubscription before sbatch.
                nthreads = step.selected_parameters.get("nthreads")
                if isinstance(nthreads, (int, float)) and (
                    nthreads < 0 or nthreads > allocated.cpus
                ):
                    raise RunConflict(
                        f"步骤 {step.title} 的 nthreads 超过分配的 CPU，请调整资源或参数"
                    )
                if (
                    step.selected_parameters.get("backend") == "cuda"
                    or step.selected_parameters.get("device") == "cuda"
                ) and not allocated.gpus:
                    raise RunConflict("CUDA 方法需要申请至少一张 GPU")
            elif (
                step.selected_parameters.get("backend") == "cuda"
                or step.selected_parameters.get("device") == "cuda"
            ) and not self.runner_registry.get(method.runner_id).runtime_status().get(
                "cuda_available"
            ):
                raise RunConflict(
                    "当前主节点没有可用 CUDA 设备，请选择 Slurm 并申请 GPU"
                )

        now = self._now()
        run_id = f"run_{uuid4().hex[:12]}"
        output_directory = self._safe_run_directory(run_id, execution.backend, workflow)
        # New submissions must never write into registered input datasets,
        # including through historical workspace records. Frozen active jobs
        # keep their existing paths; no running task is migrated or cancelled.
        from ..dataset_files import protect_datasets

        protect_datasets(self.store, output_directory)
        if workflow.workspace_root_path:
            root = Path(workflow.workspace_root_path)
            if not root.is_dir() or not os.access(root, os.R_OK | os.W_OK | os.X_OK):
                raise RunConflict("输出目录需要后端账户的读取、写入和进入权限")
        self._check_workspace_operations(workflow, output_directory)
        if execution.backend == "slurm":
            self.slurm_output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            output_directory.mkdir(parents=True, mode=0o700)
        run = WorkflowRun(
            workspace_id=workflow.workspace_id,
            workspace_root_path=workflow.workspace_root_path,
            output_relative_path=workflow.output_relative_path,
            run_id=run_id,
            workflow_id=workflow.workflow_id,
            workflow_revision=workflow.revision,
            status="queued",
            progress=0.0,
            output_directory=str(output_directory),
            plan_snapshot=workflow.model_dump(mode="json"),
            sample_scope=scope,
            steps=[
                RunStep(
                    output_relative_path=unit.workspace_key,
                    step_id=step.id,
                    title=step.title,
                    runner_id=method.runner_id,
                    method_id=method.id,
                    sample_id=unit.sample_id,
                    sample_label=unit.sample_label,
                    node_name=unit.execution.node if unit.execution else None,
                )
                for unit in resolved
                for step, method in [(unit.step, unit.method)]
            ],
            created_at=now,
            updated_at=now,
            execution=execution,
        )
        self._save_run(
            run,
            event_type="run_queued",
            event_message="任务已进入 Slurm 提交队列"
            if execution.backend == "slurm"
            else "任务已进入本地执行队列",
        )
        cancel_event = Event()
        with self._lock:
            self._cancel_events[run_id] = cancel_event
            self._futures[run_id] = self._executor.submit(
                self._execute,
                run_id,
                workflow,
                resolved,
                cancel_event,
            )
        return self.get(run_id)

    def _recover_slurm_runs(self) -> None:
        for run in self.store.incomplete_runs():
            if run.execution.backend != "slurm":
                continue
            try:
                self.resume_slurm_monitoring(run.run_id)
            except Exception as exc:
                # Do not lose a potentially live remote job just because a new
                # deployment cannot reconstruct its old recipe. Keep its Job ID.
                self.store.append_run_event(
                    run.run_id,
                    event_type="slurm_recovery_blocked",
                    message=str(exc),
                    level="error",
                )

    def resume_slurm_monitoring(self, run_id: str) -> WorkflowRun:
        with self._lock:
            run = self.get(run_id)
            if run.execution.backend != "slurm" or run.status not in {
                "queued",
                "running",
                "cancel_requested",
            }:
                raise RunConflict("只有未结束的 Slurm 任务可以恢复监控")
            if run_id in self._futures:
                return run
            if self._detach_event.is_set():
                raise RunConflict("后端正在关闭，请在启动后恢复监控")
            workflow = Workflow.model_validate(run.plan_snapshot)
            self._check_workspace_operations(workflow, Path(run.output_directory))
            if Path(run.output_directory).resolve() != self._safe_run_directory(
                run_id, "slurm"
            ):
                raise RunConflict(
                    "共享输出根目录与原任务不一致，请恢复原 Slurm 输出配置"
                )
            units = schedule_units(
                workflow, self.recipe_compiler.compile(workflow), run.execution
            )
            if run.selected_step_ids:
                units = [
                    unit for unit in units if unit.step.id in run.selected_step_ids
                ]
            if [u.step.id for u in units] != [s.step_id for s in run.steps] or any(
                u.method.runner_id != s.runner_id or u.method.id != s.method_id
                for u, s in zip(units, run.steps)
            ):
                raise RunConflict("恢复时的执行配方与持久快照不一致")
            first = next(
                (i for i, s in enumerate(run.steps) if s.status != "succeeded"),
                len(units),
            )
            event = Event()
            if run.cancellation_requested or run.status == "cancel_requested":
                event.set()
            self._cancel_events[run_id] = event
            self._futures[run_id] = self._executor.submit(
                self._execute, run_id, workflow, units, event, first
            )
        return self.get(run_id)

    def get(self, run_id: str) -> WorkflowRun:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def stop_paused_for_goal_end(self, workflow_id: str, *, before_cancel=None) -> bool:
        """End never cancels active computation on a stale UI pause indicator."""
        with self._lock:
            run = self.store.active_run_for_workflow(workflow_id)
            if not run:
                return False
            if run.cancellation_requested or run.status == 'cancel_requested':
                if before_cancel:
                    before_cancel()
                return True
            if run.execution.backend != 'slurm':
                return False
            active = [step for step in run.steps if step.status == 'running']
            if not active:
                return False
            for step in active:
                job = step.scheduler
                if not job.get('job_id') or not job.get('submission_token') or job.get('state') != 'SUSPENDED':
                    return False
                import subprocess
                try:
                    live = self.slurm_backend._query(str(job['job_id']),job['submission_token'])
                except (RuntimeError,OSError,subprocess.TimeoutExpired):
                    return False
                if not live or live.get('state') != 'SUSPENDED':
                    return False
            if before_cancel:
                before_cancel()
            self.cancel(run.run_id)
            return True

    def retry(self, run_id: str, step_ids=None, sample_ids=None) -> WorkflowRun:
        original = self.get(run_id)
        if original.status not in {"failed", "cancelled"}:
            raise RunConflict("只能重试已经结束的失败或取消任务")
        if self.store.active_run_for_workflow(original.workflow_id):
            raise RunConflict("该流程已有活动任务，请等待结束")
        if any(
            step.scheduler
            and step.status != "succeeded"
            and step.scheduler.get("state")
            not in TERMINAL_STATES | {"SUBMISSION_FAILED"}
            for step in original.steps
        ):
            raise RunConflict("仍有状态不确定的 Slurm 作业，请先恢复监控并确认已结束")
        workflow = Workflow.model_validate(original.plan_snapshot)
        history_problems = HistoricalInputs(self.store).validate_workflow(workflow)
        if history_problems:
            raise RunConflict("历史输入已失效：" + "；".join(history_problems))
        units = schedule_units(
            workflow, self.recipe_compiler.compile(workflow), original.execution
        )
        if original.selected_step_ids:
            units = [
                unit for unit in units if unit.step.id in original.selected_step_ids
            ]
        old = {step.step_id: step for step in original.steps}
        if set(old) != {unit.step.id for unit in units}:
            raise RunConflict("执行配方已变化，不能重试旧快照")
        selected = set(step_ids or [])
        if selected - set(old) or set(sample_ids or []) - {
            step.sample_id for step in original.steps
        }:
            raise RunConflict("重试范围包含不属于该任务的步骤或样本")
        selected.update(
            step.step_id
            for step in original.steps
            if step.sample_id in (sample_ids or [])
        )
        if not selected:
            selected = {
                step.step_id for step in original.steps if step.status != "succeeded"
            }
        # Include downstream consumers, then all required ancestors. Other samples stay untouched.
        while True:
            before = set(selected)
            selected.update(
                unit.step.id for unit in units if set(unit.step.depends_on) & selected
            )
            if before == selected:
                break
        needed = set(selected)
        while True:
            before = set(needed)
            needed.update(
                dep
                for unit in units
                if unit.step.id in needed
                for dep in unit.step.depends_on
            )
            if before == needed:
                break
        units = [unit for unit in units if unit.step.id in needed]
        event = Event()
        now = self._now()
        new_id = f"run_{uuid4().hex[:12]}"
        run = original.model_copy(
            deep=True,
            update=dict(
                run_id=new_id,
                retry_of=original.run_id,
                selected_step_ids=[unit.step.id for unit in units],
                reused_step_ids=[],
                status="queued",
                current_step_id=None,
                progress=0,
                error_code="",
                error_message="",
                cancellation_requested=False,
                created_at=now,
                updated_at=now,
                started_at=None,
                finished_at=None,
                state_revision=0,
                output_directory=str(
                    self._safe_run_directory(
                        new_id, original.execution.backend, workflow
                    )
                ),
                steps=[],
                artifacts=[],
            ),
        )
        from ..dataset_files import protect_datasets

        protect_datasets(self.store, Path(run.output_directory))
        self._check_workspace_operations(workflow, Path(run.output_directory))
        status = (
            self.slurm_backend.status()
            if original.execution.backend == "slurm"
            else None
        )
        parallel_limits = unit_parallel_limits(original.execution, units)
        for unit in units:
            runner = self.runner_registry.get(unit.method.runner_id)
            if runner is None:
                raise RunConflict("旧任务的执行器已不可用")
            if status is not None:
                if not status.get(
                    "available"
                ) or original.execution.partition not in status.get("partitions", []):
                    raise RunConflict("原 Slurm 分区当前不可用")
                self._validate_node_resources(
                    unit.execution or original.execution,
                    status,
                    parallel_samples=parallel_limits.get(unit.execution.node, 1),
                )
            previous = old[unit.step.id]
            can_reuse = previous.status == "succeeded" and all(
                dep in run.reused_step_ids for dep in unit.step.depends_on
            )
            if can_reuse:
                runtime_step = self._resolve_runtime_bindings(unit.step, run)
                context = RunnerContext(
                    new_id,
                    workflow_for_unit(workflow, unit),
                    runtime_step,
                    unit.method,
                    Path(run.output_directory) / unit.workspace_key,
                    event,
                    lambda *_args: None,
                )
                can_reuse = reusable_step(
                    previous, context, runner, Path(original.output_directory).resolve()
                )
            if can_reuse:
                restored = previous.model_copy(deep=True)
                run.reused_step_ids.append(restored.step_id)
                run.artifacts.extend(restored.artifacts)
            else:
                restored = RunStep(
                    output_relative_path=unit.workspace_key,
                    step_id=unit.step.id,
                    title=unit.step.title,
                    runner_id=unit.method.runner_id,
                    method_id=unit.method.id,
                    sample_id=unit.sample_id,
                    sample_label=unit.sample_label,
                    node_name=unit.execution.node if unit.execution else None,
                )
            run.steps.append(restored)
        run.progress = 100 * len(run.reused_step_ids) / max(1, len(run.steps))
        # Hashing large inputs must not hold the service lock and block cancellations.
        # Commit only if no task or recovery raced this preparation.
        with self._lock:
            current = self.get(run_id)
            if (
                current.updated_at != original.updated_at
                or current.status != original.status
                or self.store.active_run_for_workflow(original.workflow_id)
            ):
                raise RunConflict("任务状态已变化，请刷新后重试")
            Path(run.output_directory).mkdir(parents=True, mode=0o700)
            self._save_run(
                run,
                event_type="retry_created",
                event_message="从原任务创建重试；已验证成功结果按需复用",
                event_payload={
                    "retry_of": original.run_id,
                    "reused_steps": run.reused_step_ids,
                },
            )
            self._cancel_events[new_id] = event
            self._futures[new_id] = self._executor.submit(
                self._execute, new_id, workflow, units, event
            )
            return self.get(new_id)

    def recover_freesurfer(
        self, run_id: str, expected_revision: int, *, dry_run: bool = True
    ) -> dict[str, Any]:
        """Recover only the known autorecon2 output-validation false negative.

        Revalidate the immutable confirmed recipe and completion evidence before
        recording its artifact. Never execute autorecon1 or autorecon2 again.
        """
        with self._lock:
            run = self.get(run_id)
            if run.execution.backend != "local":
                raise RunConflict("该专项恢复只适用于本地 FreeSurfer 任务")
            if (
                run.status != "failed"
                or run.cancellation_requested
                or run_id in self._futures
            ):
                raise RunConflict("只能恢复已结束且未取消的失败任务")
            if self.store.active_run_for_workflow(run.workflow_id) is not None:
                raise RunConflict("该流程已有活动任务")
            if run.workflow_revision != expected_revision:
                raise RunConflict("运行快照版本不匹配")
            workflow = Workflow.model_validate(run.plan_snapshot)
            if (
                workflow.status != "confirmed"
                or not workflow.dry_run_report
                or not workflow.dry_run_report.valid
                or not workflow.dry_run_report.execution_enabled
            ):
                raise RunConflict("原始快照尚未通过 Dry-run 和最终确认")
            try:
                units = self.recipe_compiler.compile(workflow)
                if (
                    len(units) != 3
                    or len(run.steps) != 3
                    or [s.status for s in run.steps]
                    != ["succeeded", "failed", "queued"]
                    or [
                        u.step.selected_parameters.get("processing_stage")
                        for u in units
                    ]
                    != ["autorecon1", "autorecon2", "autorecon3"]
                    or len({u.workspace_key for u in units}) != 1
                    or any(
                        u.method.runner_id != "freesurfer.recon_all"
                        or u.step.id != s.step_id
                        or u.method.id != s.method_id
                        or s.runner_id != u.method.runner_id
                        for u, s in zip(units, run.steps)
                    )
                ):
                    raise RuntimeError(
                        "仅支持原始三阶段 FreeSurfer 配方的 autorecon2 验证误报"
                    )
                runner = self.runner_registry.get("freesurfer.recon_all")
                if not isinstance(runner, FreeSurferReconAllRunner):
                    raise RuntimeError("FreeSurfer 可信 Runner 不可用")
                directory = self._safe_step_directory(run_id, units[1].workspace_key)
                step = self._resolve_runtime_bindings(units[1].step, run)
                context = RunnerContext(
                    run_id,
                    workflow,
                    step,
                    units[1].method,
                    directory,
                    Event(),
                    lambda *_: None,
                )
                subject = runner.required_binding(context, "subject")
                subject.relative_to(directory)
                failed = run.steps[1]
                expected_error = (
                    f"FreeSurfer 未生成预期输出：{subject / 'surf' / 'lh.white'}"
                )
                if (
                    failed.error_code != "runner_failed"
                    or failed.error_message != expected_error
                    or run.error_message != expected_error
                    or not failed.started_at
                    or not failed.finished_at
                ):
                    raise RuntimeError("失败原因不是已知的 autorecon2 制品验证误报")
                runner.validate_completion(
                    subject, failed.started_at, failed.finished_at
                )
                result = runner.collect_result(context, subject, "autorecon2")
                previous_error = failed.error_message
                failed.status = "succeeded"
                failed.progress = 100.0
                failed.message = "已核验现有 autorecon2 结果，恢复成功"
                failed.error_code = failed.error_message = ""
                failed.artifacts = result.artifacts
                run.artifacts.extend(result.artifacts)
                next_step = self._resolve_runtime_bindings(units[2].step, run)
                next_context = RunnerContext(
                    run_id,
                    workflow,
                    next_step,
                    units[2].method,
                    directory,
                    Event(),
                    lambda *_: None,
                )
                problems = runner.preflight(next_context)
                if problems:
                    raise RuntimeError("；".join(problems))
            except (RuntimeError, ValueError, OSError, IndexError) as exc:
                raise RunConflict(str(exc)) from exc
            report = {
                "valid": True,
                "run_id": run_id,
                "dry_run": dry_run,
                "recovered_stage": "autorecon2",
                "next_stage": "autorecon3",
                "subject_directory": str(subject),
                "parameters": next_step.selected_parameters,
                "validated_files": result.artifacts[0].metadata["validated_files"],
            }
            if dry_run:
                return report
            run.status = "queued"
            run.progress = 200 / 3
            run.current_step_id = None
            run.error_code = run.error_message = ""
            run.finished_at = None
            run.updated_at = self._now()
            self._save_run(
                run,
                event_type="freesurfer_stage_recovered",
                event_message=failed.message,
                step_id=failed.step_id,
                event_payload={**report, "previous_error": previous_error},
            )
            cancel_event = Event()
            self._cancel_events[run_id] = cancel_event
            self._futures[run_id] = self._executor.submit(
                self._execute, run_id, workflow, units, cancel_event, 2
            )
            return report

    def list(
        self, workflow_id: str | None = None, limit: int = 50
    ) -> list[WorkflowRun]:
        return self.store.list_runs(workflow_id=workflow_id, limit=limit)

    def events(self, run_id: str, after_event_id: int = 0) -> list[dict[str, Any]]:
        if self.store.get_run(run_id) is None:
            raise KeyError(run_id)
        return self.store.run_events(run_id, after_event_id=after_event_id)

    def cancel(self, run_id: str) -> WorkflowRun:
        with self._lock:
            run = self.get(run_id)
            if run.status not in {"queued", "running", "cancel_requested"}:
                raise RunConflict("该任务已经结束，不能取消")
            event = self._cancel_events.get(run_id)
            if event is not None:
                event.set()
            run.status = "cancel_requested"
            run.cancellation_requested = True
            run.updated_at = self._now()
            self._save_run(
                run,
                event_type="cancellation_requested",
                event_message="已请求取消任务，正在等待当前安全检查点",
                level="warning",
            )
            if event is None and run.execution.backend == "slurm":
                self.resume_slurm_monitoring(run_id)
        return run

    def shutdown(self) -> None:
        self._detach_event.set()
        with self._lock:
            for run_id, event in self._cancel_events.items():
                if self.get(run_id).execution.backend != "slurm":
                    event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _execute(
        self,
        run_id: str,
        workflow: Workflow,
        resolved: list[ExecutionUnit],
        cancel_event: Event,
        start_index: int = 0,
    ) -> None:
        run = self.get(run_id)
        if run.execution.sample_mode == "per_sample":
            execute_samples(self, run, workflow, resolved, cancel_event)
            return
        try:
            run.status = "cancel_requested" if cancel_event.is_set() else "running"
            run.started_at = run.started_at or self._now()
            run.updated_at = self._now()
            self._save_run(
                run,
                event_type="run_resumed" if start_index else "run_started",
                event_message="Slurm Worker 开始提交或恢复监控"
                if run.execution.backend == "slurm"
                else "本地 Worker 已开始执行",
            )
            step_total = max(len(resolved), 1)
            for step_index, unit in enumerate(resolved):
                if (
                    step_index < start_index
                    or run.steps[step_index].status == "succeeded"
                ):
                    continue
                workflow_step, method = unit.step, unit.method
                step_run = run.steps[step_index]
                if run.execution.backend == "slurm" and self._detach_event.is_set():
                    raise ExecutionDetached(
                        "后端停止监控，Slurm 作业保留并在启动后恢复"
                    )
                if cancel_event.is_set() and not (
                    run.execution.backend == "slurm" and step_run.scheduler
                ):
                    raise ExecutionCancelled("任务在步骤开始前被取消")
                step_run.status = "running"
                step_run.started_at = step_run.started_at or self._now()
                step_run.message = "正在执行"
                run.current_step_id = step_run.step_id
                run.updated_at = step_run.started_at
                self._save_run(
                    run,
                    event_type="step_started",
                    event_message=f"开始执行：{step_run.title}",
                    step_id=step_run.step_id,
                )

                runner = self.runner_registry.get(method.runner_id)
                if runner is None:
                    raise RuntimeError(f"未注册 runner：{method.runner_id}")
                step_directory = self._safe_step_directory(
                    run_id, unit.workspace_key, run.execution.backend
                )

                def log(message: str, progress: float | None, level: str) -> None:
                    if cancel_event.is_set() and run.execution.backend != "slurm":
                        raise ExecutionCancelled("任务已由用户取消")
                    if progress is not None:
                        step_run.progress = max(0.0, min(float(progress), 100.0))
                        run.progress = (
                            (step_index + step_run.progress / 100.0)
                            / step_total
                            * 100.0
                        )
                    step_run.message = message
                    run.updated_at = self._now()
                    if cancel_event.is_set():
                        run.status = "cancel_requested"
                        run.cancellation_requested = True
                    self._save_run(run, step_id=step_run.step_id, progress_only=True)
                    self.store.append_run_event(
                        run_id,
                        event_type="runner_log",
                        message=message,
                        level=level,
                        progress=run.progress,
                        step_id=step_run.step_id,
                    )

                runtime_step = self._resolve_runtime_bindings(workflow_step, run)
                context = RunnerContext(
                    run_id=run_id,
                    workflow=workflow_for_unit(workflow, unit),
                    step=runtime_step,
                    method=method,
                    output_directory=step_directory,
                    cancel_event=cancel_event,
                    log=log,
                    detach_event=self._detach_event
                    if run.execution.backend == "slurm"
                    else None,
                )
                if not step_run.scheduler:
                    step_run.input_fingerprint = input_fingerprint(context)
                    step_run.implementation_fingerprint = implementation_fingerprint(
                        runner, method
                    )
                    self._save_run(run, step_id=step_run.step_id)

                if run.execution.backend == "slurm":

                    def on_job(state: dict[str, Any]) -> None:
                        step_run.scheduler = state
                        run.updated_at = self._now()
                        if cancel_event.is_set():
                            run.status = "cancel_requested"
                            run.cancellation_requested = True
                        self._save_run(
                            run,
                            event_type="slurm_job_state",
                            event_message=f"Slurm {state.get('job_id', '')}: {state.get('state', '提交中')}",
                            step_id=step_run.step_id,
                            event_payload=state,
                        )

                    result = self.slurm_backend.execute(
                        context,
                        (unit.execution or run.execution).model_dump(),
                        step_run.scheduler or None,
                        on_job,
                    )
                else:
                    problems = [
                        problem
                        for binding in context.step.input_bindings.values()
                        for problem in historical_binding_problems(
                            binding, verify_hash=True
                        )
                    ]
                    problems.extend(runner.preflight(context))
                    if problems:
                        raise RuntimeError("；".join(problems))
                    result = runner.run(context)
                if cancel_event.is_set():
                    raise ExecutionCancelled("任务在步骤完成时被用户取消")
                step_run.status = "succeeded"
                step_run.progress = 100.0
                step_run.message = result.message
                step_run.finished_at = self._now()
                self._annotate_output_slots(method, result.artifacts)
                step_run.artifacts = result.artifacts
                run.artifacts.extend(result.artifacts)
                run.progress = (step_index + 1) / step_total * 100.0
                run.updated_at = step_run.finished_at
                self._save_run(
                    run,
                    event_type="step_succeeded",
                    event_message=result.message,
                    step_id=step_run.step_id,
                    event_payload={
                        "artifact_ids": [
                            artifact.artifact_id for artifact in result.artifacts
                        ]
                    },
                )

            run.status = "succeeded"
            run.progress = 100.0
            run.current_step_id = None
            run.finished_at = self._now()
            run.updated_at = run.finished_at
            self._save_run(
                run,
                event_type="run_succeeded",
                event_message=f"任务完成，共生成 {len(run.artifacts)} 个制品",
            )
        except ExecutionCancelled as exc:
            self._finish_cancelled(run, str(exc))
        except ExecutionDetached as exc:
            self.store.append_run_event(
                run_id,
                event_type="slurm_monitor_detached",
                message=str(exc),
                level="info",
            )
        except Exception as exc:  # noqa: BLE001 - persist runner failures as structured state
            active_step = next(
                (step for step in run.steps if step.status == "running"),
                None,
            )
            if (
                run.execution.backend == "slurm"
                and active_step is not None
                and active_step.scheduler
                and active_step.scheduler.get("state")
                not in TERMINAL_STATES | {"SUBMISSION_FAILED"}
            ):
                # Even an unexpected NFS/logging failure must not release the
                # workflow for resubmission while its remote job may be alive.
                active_step.message = "监控中断，请恢复监控或取消后核查 Slurm 作业"
                active_step.scheduler["reason"] = "monitoring_unavailable"
                run.updated_at = self._now()
                self._save_run(
                    run,
                    event_type="slurm_monitor_detached",
                    event_message=str(exc),
                    level="error",
                )
                return
            if active_step is not None:
                active_step.status = "failed"
                active_step.error_code = "runner_failed"
                active_step.error_message = str(exc)
                active_step.message = "执行失败"
                active_step.finished_at = self._now()
            run.status = "failed"
            run.error_code = "runner_failed"
            run.error_message = str(exc)
            run.finished_at = self._now()
            run.updated_at = run.finished_at
            self._save_run(
                run,
                event_type="run_failed",
                event_message=str(exc),
                level="error",
            )
        finally:
            with self._lock:
                self._cancel_events.pop(run_id, None)
                self._futures.pop(run_id, None)
                self._progress_saved = {
                    key: value
                    for key, value in self._progress_saved.items()
                    if key[0] != run_id
                }

    def _finish_cancelled(self, run: WorkflowRun, message: str) -> None:
        active_step = next(
            (step for step in run.steps if step.status == "running"), None
        )
        if active_step is not None:
            active_step.status = "cancelled"
            active_step.message = message
            active_step.finished_at = self._now()
        run.status = "cancelled"
        run.cancellation_requested = True
        run.current_step_id = None
        run.error_code = "cancelled"
        run.error_message = message
        run.finished_at = self._now()
        run.updated_at = run.finished_at
        self._save_run(
            run,
            event_type="run_cancelled",
            event_message=message,
            level="warning",
        )

    @staticmethod
    def _validate_node_resources(
        options: RunExecutionOptions,
        status: dict[str, Any],
        *,
        parallel_samples: int = 1,
    ) -> None:
        nodes = [
            node
            for node in status.get("nodes", [])
            if node.get("partition") == options.partition and node.get("schedulable")
        ]
        if options.node:
            nodes = [node for node in nodes if node.get("name") == options.node]
            if not nodes:
                raise RunConflict(f"节点 {options.node} 不属于所选可调度分区")
        if not nodes:
            return  # Compatibility with older status providers; sbatch still validates.
        if not any(
            options.cpus * parallel_samples <= node.get("cpus", 128)
            and options.memory_gb * 1024 * parallel_samples
            <= node.get("memory_mb", 512 * 1024)
            and options.gpus * parallel_samples <= node.get("gpus", 0)
            for node in nodes
        ):
            raise RunConflict(
                f"申请的 CPU、内存或 GPU（并行 {parallel_samples} 个样本）超过节点 {options.node or '所在分区'} 容量"
            )

    @staticmethod
    def _resolve_runtime_bindings(step: WorkflowStep, run: WorkflowRun) -> WorkflowStep:
        # sample_plan 已为每个样本加上稳定步骤前缀；这里只接受该精确上游步骤的成功产物。
        # 不按文件名或“最近生成的结果”匹配，避免并行样本互相取错输入。
        resolved = step.model_copy(deep=True)
        completed = {
            item.step_id: item for item in run.steps if item.status == "succeeded"
        }
        for slot_id, binding in list(resolved.input_bindings.items()):
            if binding.source != "upstream_output":
                continue
            producer = completed.get(binding.upstream_step_id or "")
            if producer is None:
                raise RuntimeError(
                    f"输入 {slot_id} 的上游步骤尚未完成：{binding.upstream_step_id}"
                )
            candidates = [
                artifact
                for artifact in producer.artifacts
                if compatible(artifact.artifact_type, binding.artifact_type)
            ]
            exact = [
                artifact
                for artifact in candidates
                if artifact.metadata.get("output_slot_id") == binding.output_slot_id
            ]
            artifact = next(iter(exact if binding.output_slot_id else candidates), None)
            if artifact is None:
                raise RuntimeError(
                    f"上游步骤 {producer.title} 没有生成输入 {slot_id} 所需的"
                    f" {binding.artifact_type} 制品"
                )
            paths = [artifact.absolute_path]
            for extra in binding.upstream_sources:
                other = completed.get(extra.step_id)
                candidate = (
                    next(
                        (
                            a
                            for a in other.artifacts
                            if a.metadata.get("output_slot_id") == extra.output_slot_id
                            and compatible(a.artifact_type, binding.artifact_type)
                        ),
                        None,
                    )
                    if other
                    else None
                )
                if candidate is None:
                    raise RuntimeError(
                        "多文件输入的上游输出尚未就绪："
                        + extra.step_id
                        + "/"
                        + extra.output_slot_id
                    )
                paths.append(candidate.absolute_path)
            resolved.input_bindings[slot_id] = binding.model_copy(
                deep=True,
                update={
                    "source": "existing_path",
                    "label": artifact.label,
                    "absolute_path": artifact.absolute_path,
                    "absolute_paths": paths if len(paths) > 1 else [],
                    "binding_mode": "single",
                    "status": "validated",
                    "messages": [f"已绑定运行制品：{artifact.artifact_id}"],
                },
            )
        return resolved

    @staticmethod
    def _annotate_output_slots(
        method: MethodOption,
        artifacts: list[RunArtifact],
    ) -> None:
        remaining = list(method.output_slots)
        for artifact in artifacts:
            declared_id = artifact.metadata.get("output_slot_id")
            if declared_id:
                remaining = [slot for slot in remaining if slot.id != declared_id]
                continue
            matching = next(
                (slot for slot in remaining if slot.type == artifact.artifact_type),
                None,
            )
            if matching is None:
                continue
            artifact.metadata.setdefault("output_slot_id", matching.id)
            remaining.remove(matching)

    def _check_workspace_operations(self, workflow, output):
        """Prevent a new run from acquiring paths reserved by a confirmed file operation."""
        from ..workspace_paths import within

        paths = [output]
        if workflow.dataset:
            paths.append(Path(workflow.dataset.absolute_path))
        for step in workflow.steps:
            for binding in step.input_bindings.values():
                paths.extend(
                    Path(p)
                    for p in binding.absolute_paths
                    or ([binding.absolute_path] if binding.absolute_path else [])
                )
                paths.extend(
                    Path(match.absolute_path)
                    for match in binding.sample_matches
                    if match.absolute_path
                )
        with self.store._connect() as db:
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='workspace_operations'"
            ).fetchone():
                return
            operations = [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM workspace_operations WHERE status='running'"
                )
            ]
        with self.store._connect() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='data_operations'").fetchone():
                for row in db.execute("SELECT payload FROM data_operations WHERE status IN ('validating','running','rolling_back','interrupted')"):
                    task = json.loads(row[0])
                    operations.append({"action": "data", "source": task["source_root"] if task["mode"] == "inplace" else task["target_root"], "target": task["target_root"]})
        for operation in operations:
            reserved = [operation.get("target")]
            if operation["action"] not in {"copy", "read-sensitive"}:
                reserved.append(operation["source"])
            for value in filter(None, reserved):
                reserved_path = Path(value).resolve()
                if any(
                    within(path.resolve(), reserved_path)
                    or within(reserved_path, path.resolve())
                    for path in paths
                ):
                    raise RunConflict(
                        "输入或输出路径正在进行已确认的文件操作，请等待操作完成"
                    )

    def _safe_run_directory(
        self, run_id: str, backend: str = "local", workflow=None
    ) -> Path:
        root = self.slurm_output_root if backend == "slurm" else self.output_root
        workspace_root = workflow.workspace_root_path if workflow else None
        relative = workflow.output_relative_path if workflow else "outputs"
        if workflow is None:
            # Directory resolution is on the per-sample hot path. Read only the
            # compact run header, never hydrate thousands of steps/artifacts.
            with self.store._connect() as db:
                row = db.execute(
                    "SELECT payload_json FROM runs WHERE run_id=?", (run_id,)
                ).fetchone()
            if row:
                header = json.loads(row[0])
                workspace_root = header.get("workspace_root_path")
                relative = header.get("output_relative_path", "outputs")
        if workspace_root:
            from ..workspace_paths import WorkspacePaths

            policy = WorkspacePaths(self.output_root.parent / "workspaces")
            root = policy.output_root(workspace_root, relative)
            return policy.resolve(Path(workspace_root), str(root / run_id), write=True)
        candidate = (root / run_id).resolve()
        candidate.relative_to(root)
        return candidate

    def _safe_step_directory(
        self, run_id: str, step_id: str, backend: str = "local"
    ) -> Path:
        from .output_layout import step_directory
        return step_directory(self._safe_run_directory(run_id, backend), step_id)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
