from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from unittest.mock import patch

from pydantic import ValidationError

from app.capability_registry import CapabilityRegistry
from app.dataset_models import DatasetInspection, DatasetRecord
from app.execution import RunConflict, RunnerRegistry, RunService
from app.execution.base import ExecutionCancelled, ExecutionDetached, RunnerResult
from app.local_store import LocalStore
from app.run_models import RunArtifact, RunExecutionOptions, RunStep, WorkflowRun
from app.workflow_models import DryRunReport, MethodOption, Workflow, WorkflowStep


class SlurmRunServiceTests(unittest.TestCase):
    """Exercise the durable RunService boundary without submitting real jobs."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.shared_root = self.root / "shared" / "runs"
        self.dataset_root = self.root / "dataset"
        self.dataset_root.mkdir()
        (self.dataset_root / "dataset_description.json").write_text(
            '{"Name":"Slurm test","BIDSVersion":"1.9.0"}', encoding="utf-8"
        )
        self.now = datetime.now(timezone.utc).isoformat()
        self.dataset = DatasetRecord(
            dataset_id="dataset_slurm",
            root_id="root_test",
            relative_path="dataset",
            display_name="Slurm test dataset",
            absolute_path=str(self.dataset_root),
            inspection=DatasetInspection(
                file_count=1, total_bytes=50, is_bids=True,
                input_types=["dataset_folder"], scanned_at=self.now,
            ),
            created_at=self.now, updated_at=self.now,
        )
        self.store = LocalStore(self.root / "runs.db")
        self.store.save_dataset(self.dataset)
        self.capabilities = CapabilityRegistry(Path(__file__).resolve().parents[2] / "skills")
        self.runners = RunnerRegistry()
        self.env_patch = patch.dict(os.environ, {
            "LOCALFLOW_SLURM_OUTPUT_ROOT": str(self.shared_root),
            "LOCALFLOW_RUN_WORKERS": "1",
        })
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.backend_patch = patch("app.execution.run_service.SlurmBackend")
        self.backend_factory = self.backend_patch.start()
        self.addCleanup(self.backend_patch.stop)
        self.backend = self.backend_factory.return_value
        self.backend.status.return_value = {
            "enabled": True, "available": True, "reason": "",
            "partitions": ["all"], "gpu_available": True,
        }
        self.backend.execute.return_value = RunnerResult([], "completed")
        self.services: list[RunService] = []
        self.service = self._new_service()
        self.workflow = self._workflow()
        self.store.save_workflow(self.workflow, event_type="workflow_confirmed")

    def tearDown(self) -> None:
        pending = []
        for service in self.services:
            with service._lock:
                pending.extend(service._futures.values())
            service.shutdown()
        # Join mocked workers before removing their SQLite database and output.
        for future in pending:
            if not future.cancelled():
                future.result(timeout=5)

    def _new_service(self) -> RunService:
        service = RunService(self.store, self.capabilities, self.runners, self.root / "local")
        self.services.append(service)
        return service

    def _workflow(self) -> Workflow:
        definition = self.capabilities.methods_for("dwi.inspect")[0]
        method = MethodOption(
            id=definition.method_id, skill_id=definition.skill_id,
            label=definition.label, description=definition.description,
            runner_id=definition.runner_id, execution_mode=definition.execution_mode,
            input_types=definition.input_types, output_types=definition.output_types,
            parameters=definition.parameters, input_slots=definition.input_slots,
            output_slots=definition.output_slots, structure_level=definition.structure_level,
        )
        return Workflow(
            workflow_id="workflow_slurm", template_id="dynamic:dwi.inspect", revision=1,
            title="Slurm inspection", summary="Inspect a dataset on a compute node",
            original_request="Inspect this dataset", status="confirmed",
            dataset_id=self.dataset.dataset_id, dataset=self.dataset,
            planner_source="test", execution_mode="runner", plan_kind="atomic",
            requested_capabilities=["dwi.inspect"],
            steps=[WorkflowStep(
                id="inspect", order=1, title="Inspect", description="Dataset inspection",
                selection="auto", status="confirmed", capability="dwi.inspect",
                method_options=[method], selected_method_id=method.id,
                selected_parameters={
                    item.id: item.default for item in method.parameters if item.default is not None
                },
            )],
            dry_run_report=DryRunReport(
                valid=True, execution_enabled=True, dataset_path_validated=True, checked_at=self.now,
            ),
            confirmed_at=self.now, created_at=self.now, updated_at=self.now,
        )

    def _create(self, **options) -> WorkflowRun:
        return self.service.create(
            self.workflow.workflow_id, self.workflow.revision,
            RunExecutionOptions(backend="slurm", **options),
        )

    def _finished(self, run_id: str) -> WorkflowRun:
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            run = self.service.get(run_id)
            if run.status in {"succeeded", "failed", "cancelled"}:
                return run
            time.sleep(0.01)
        self.fail(f"Mocked run did not finish: {self.service.get(run_id).model_dump()}")

    def _seed_remote_run(self, *, cancelled: bool = False, completed_first: bool = False) -> WorkflowRun:
        self.service.shutdown()
        workflow = self.workflow.model_copy(deep=True)
        if completed_first:
            second = workflow.steps[0].model_copy(deep=True)
            second.id = "inspect_again"
            second.order = 2
            second.depends_on = [workflow.steps[0].id]
            workflow.steps.append(second)
        steps = [RunStep(
            step_id=step.id, title=step.title,
            runner_id=step.method_options[0].runner_id, method_id=step.selected_method_id,
            status="running", progress=25, started_at=self.now,
        ) for step in workflow.steps]
        if completed_first:
            steps[0].status = "succeeded"
            steps[0].progress = 100
            steps[0].finished_at = self.now
            steps[0].message = "Already completed"
        steps[-1].scheduler = {"job_id": "4201", "state": "RUNNING", "node_list": "node01"}
        run = WorkflowRun(
            run_id="run_recovered", workflow_id=workflow.workflow_id,
            workflow_revision=workflow.revision,
            status="cancel_requested" if cancelled else "running",
            cancellation_requested=cancelled, progress=25,
            current_step_id=steps[-1].step_id,
            output_directory=str(self.shared_root / "run_recovered"),
            execution=RunExecutionOptions(backend="slurm", gpus=1),
            plan_snapshot=workflow.model_dump(mode="json"), steps=steps,
            created_at=self.now, started_at=self.now, updated_at=self.now,
        )
        Path(run.output_directory).mkdir(parents=True)
        self.store.save_run(run)
        return run

    def test_execution_options_reject_invalid_resource_values(self) -> None:
        for field, values in {
            "cpus": [0, 129, 1.5], "memory_gb": [0, 513],
            "gpus": [-1, 2], "time_minutes": [0, 1441],
            "partition": ["all\n#SBATCH --exclusive", "../other"],
        }.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    RunExecutionOptions(backend="slurm", **{field: value})
        self.assertEqual(self.store.list_runs(), [])
        self.backend.execute.assert_not_called()

    def test_create_rejects_unavailable_resources_before_persisting_a_run(self) -> None:
        cases = [
            ({"available": False, "reason": "maintenance"}, {}, "maintenance"),
            ({}, {"partition": "unlisted"}, "白名单"),
            ({"gpu_available": False}, {"gpus": 1}, "GPU"),
        ]
        for status_changes, options, message in cases:
            with self.subTest(options=options, status=status_changes):
                self.backend.status.return_value = {
                    "enabled": True, "available": True, "partitions": ["all"],
                    "gpu_available": True, **status_changes,
                }
                with self.assertRaisesRegex(RunConflict, message):
                    self._create(**options)
        with self.assertRaisesRegex(RunConflict, "GPU"):
            self.service.create(self.workflow.workflow_id, 1, RunExecutionOptions(gpus=1))
        self.assertEqual(self.store.list_runs(), [])
        self.assertFalse(self.shared_root.exists())
        self.backend.execute.assert_not_called()

    def test_create_rejects_threads_above_allocation_and_cuda_without_gpu(self) -> None:
        for parameters, message in [({"threads": 8}, "CPU"), ({"nthreads": 8}, "CPU"), ({"backend": "cuda"}, "GPU")]:
            with self.subTest(parameters=parameters):
                workflow = self.workflow.model_copy(deep=True)
                workflow.steps[0].selected_parameters.update(parameters)
                self.store.save_workflow(workflow, event_type="workflow_confirmed")
                with self.assertRaisesRegex(RunConflict, message):
                    self._create(cpus=4, gpus=0)
        self.assertEqual(self.store.list_runs(), [])
        self.backend.execute.assert_not_called()

    def test_scheduler_updates_and_artifacts_persist_under_shared_output(self) -> None:
        def execute(context, options, existing_job, on_job):
            self.assertIsNone(existing_job)
            self.assertEqual(options["gpus"], 1)
            self.assertEqual(options["cpus"], 4)
            expected = self.shared_root.resolve() / context.run_id / "inspect"
            self.assertEqual(context.output_directory, expected)
            context.output_directory.mkdir(parents=True)
            on_job({"job_id": "42", "state": "PENDING", "reason": "Resources"})
            persisted = self.store.get_run(context.run_id)
            self.assertEqual(persisted.steps[0].scheduler["job_id"], "42")
            self.assertEqual(persisted.steps[0].scheduler["state"], "PENDING")
            self.assertEqual(Path(persisted.output_directory), expected.parent)
            self.assertEqual(persisted.execution.gpus, 1)
            output = context.output_directory / "report.json"
            output.write_text(json.dumps({"job_id": "42"}), encoding="utf-8")
            on_job({"job_id": "42", "state": "COMPLETED", "node_list": "node01"})
            return RunnerResult([RunArtifact(
                artifact_id="artifact_slurm", step_id=context.step.id,
                artifact_type="inspection_report", label="Remote report",
                absolute_path=str(output), relative_path=output.name,
            )], "Remote inspection completed")

        self.backend.execute.side_effect = execute
        runner = self.runners.get(self.workflow.steps[0].method_options[0].runner_id)
        with patch.object(runner, "run") as local_run, patch.object(runner, "preflight") as local_preflight:
            run = self._finished(self._create(gpus=1).run_id)
        self.assertEqual(run.status, "succeeded", run.error_message)
        self.assertEqual(run.steps[0].scheduler["state"], "COMPLETED")
        self.assertEqual(run.steps[0].scheduler["node_list"], "node01")
        self.assertTrue(Path(run.artifacts[0].absolute_path).is_file())
        self.assertFalse((self.root / "local" / run.run_id).exists())
        states = [event["payload"]["state"] for event in self.service.events(run.run_id)
                  if event["event_type"] == "slurm_job_state"]
        self.assertEqual(states, ["PENDING", "COMPLETED"])
        local_run.assert_not_called()
        local_preflight.assert_not_called()

    def test_cancel_waits_for_engine_and_preserves_scheduler_updates(self) -> None:
        entered, observed_cancel, finish = Event(), Event(), Event()

        def execute(context, options, existing_job, on_job):
            on_job({"job_id": "43", "state": "PENDING"})
            entered.set()
            self.assertTrue(context.cancel_event.wait(3))
            # Logging must not short-circuit the engine before it can scancel.
            context.log("Waiting for scheduler cancellation", None, "info")
            on_job({"job_id": "43", "state": "COMPLETING"})
            observed_cancel.set()
            self.assertTrue(finish.wait(3))
            on_job({"job_id": "43", "state": "CANCELLED"})
            raise ExecutionCancelled("Scheduler confirmed cancellation")

        self.backend.execute.side_effect = execute
        run = self._create()
        self.assertTrue(entered.wait(3))
        requested = self.service.cancel(run.run_id)
        try:
            self.assertEqual(requested.status, "cancel_requested")
            self.assertTrue(observed_cancel.wait(3))
            current = self.service.get(run.run_id)
            self.assertEqual(current.status, "cancel_requested")
            self.assertTrue(current.cancellation_requested)
            self.assertEqual(current.steps[0].scheduler["state"], "COMPLETING")
            self.backend.cancel.assert_not_called()
        finally:
            finish.set()
        cancelled = self._finished(run.run_id)
        self.assertEqual(cancelled.status, "cancelled", cancelled.error_message)
        self.assertEqual(cancelled.steps[0].scheduler["state"], "CANCELLED")

    def test_restart_resumes_existing_job_and_skips_completed_steps(self) -> None:
        seeded = self._seed_remote_run(completed_first=True)
        submitted = []

        def execute(context, options, existing_job, on_job):
            if existing_job is None:
                submitted.append(context.step.id)
            self.assertEqual(existing_job, seeded.steps[-1].scheduler)
            self.assertEqual(context.step.id, "inspect_again")
            on_job({**existing_job, "state": "COMPLETED"})
            return RunnerResult([], "Existing job completed")

        self.backend.execute.side_effect = execute
        self.service = self._new_service()
        run = self._finished(seeded.run_id)
        self.assertEqual(run.status, "succeeded", run.error_message)
        self.assertEqual(submitted, [])
        self.backend.execute.assert_called_once()
        self.assertEqual(run.steps[0].model_dump(), seeded.steps[0].model_dump())
        self.assertEqual(run.started_at, seeded.started_at)
        self.assertEqual(run.steps[-1].scheduler["job_id"], "4201")
        self.assertNotEqual(run.error_code, "backend_restarted")

    def test_restart_of_cancel_requested_job_still_calls_engine(self) -> None:
        seeded = self._seed_remote_run(cancelled=True)

        def execute(context, options, existing_job, on_job):
            self.assertTrue(context.cancel_event.is_set())
            self.assertEqual(existing_job["job_id"], "4201")
            context.log("Cancelling recovered job", None, "info")
            on_job({**existing_job, "state": "CANCELLED"})
            raise ExecutionCancelled("Recovered job cancellation confirmed")

        self.backend.execute.side_effect = execute
        self.service = self._new_service()
        run = self._finished(seeded.run_id)
        self.backend.execute.assert_called_once()
        self.assertEqual(run.status, "cancelled", run.error_message)
        self.assertEqual(run.steps[0].scheduler["state"], "CANCELLED")

    def test_shutdown_detaches_without_cancelling_remote_job(self) -> None:
        entered, detached = Event(), Event()

        def execute(context, options, existing_job, on_job):
            on_job({"job_id": "44", "state": "RUNNING", "node_list": "node02"})
            entered.set()
            self.assertTrue(context.detach_event.wait(3))
            self.assertFalse(context.cancel_event.is_set())
            detached.set()
            raise ExecutionDetached("Keep remote job running")

        self.backend.execute.side_effect = execute
        run = self._create()
        self.assertTrue(entered.wait(3))
        future = self.service._futures[run.run_id]
        self.service.shutdown()
        self.assertTrue(detached.wait(3))
        future.result(timeout=3)
        current = self.service.get(run.run_id)
        self.assertEqual(current.status, "running")
        self.assertFalse(current.cancellation_requested)
        self.assertIsNone(current.finished_at)
        self.assertEqual(current.steps[0].scheduler["job_id"], "44")
        self.backend.cancel.assert_not_called()
        events = {event["event_type"] for event in self.service.events(run.run_id)}
        self.assertIn("slurm_monitor_detached", events)
        self.assertNotIn("run_cancelled", events)
        self.assertNotIn("run_failed", events)

    def test_cancel_reconnects_when_remote_job_has_no_local_monitor(self) -> None:
        seeded = self._seed_remote_run()

        def execute(context, options, existing_job, on_job):
            self.assertTrue(context.cancel_event.is_set())
            self.assertEqual(existing_job["job_id"], "4201")
            on_job({**existing_job, "state": "CANCELLED"})
            raise ExecutionCancelled("Cancellation after reconnect")

        self.backend.execute.side_effect = execute
        with patch.object(RunService, "_recover_slurm_runs"):
            self.service = self._new_service()
        self.assertNotIn(seeded.run_id, self.service._cancel_events)
        requested = self.service.cancel(seeded.run_id)
        self.assertEqual(requested.status, "cancel_requested")
        run = self._finished(seeded.run_id)
        self.backend.execute.assert_called_once()
        self.assertEqual(run.status, "cancelled", run.error_message)
        self.assertEqual(run.steps[0].scheduler["state"], "CANCELLED")

    def test_resume_is_idempotent_while_existing_monitor_is_running(self) -> None:
        seeded = self._seed_remote_run()
        entered, finish = Event(), Event()

        def execute(context, options, existing_job, on_job):
            self.assertEqual(existing_job["job_id"], "4201")
            entered.set()
            self.assertTrue(finish.wait(3))
            on_job({**existing_job, "state": "COMPLETED"})
            return RunnerResult([], "Existing monitor completed")

        self.backend.execute.side_effect = execute
        self.service = self._new_service()
        self.assertTrue(entered.wait(3))
        try:
            self.service.resume_slurm_monitoring(seeded.run_id)
            self.service.resume_slurm_monitoring(seeded.run_id)
            self.backend.execute.assert_called_once()
        finally:
            finish.set()
        self.assertEqual(self._finished(seeded.run_id).status, "succeeded")

    def test_resume_refuses_changed_shared_output_root_and_preserves_job(self) -> None:
        seeded = self._seed_remote_run()
        with patch.dict(os.environ, {"LOCALFLOW_SLURM_OUTPUT_ROOT": str(self.root / "different")}):
            self.service = self._new_service()
        with self.assertRaisesRegex(RunConflict, "共享输出"):
            self.service.resume_slurm_monitoring(seeded.run_id)
        self.backend.execute.assert_not_called()
        current = self.service.get(seeded.run_id)
        self.assertEqual(current.status, "running")
        self.assertEqual(current.steps[0].scheduler, seeded.steps[0].scheduler)
        self.assertIn("slurm_recovery_blocked", {
            event["event_type"] for event in self.service.events(seeded.run_id)
        })

    def test_concurrent_create_cannot_submit_two_active_runs_for_same_workflow(self) -> None:
        first_compiling, both_compiling, release_compile, release_job = Event(), Event(), Event(), Event()
        original_compile = self.service.recipe_compiler.compile

        def compile_workflow(workflow):
            if first_compiling.is_set():
                both_compiling.set()
            first_compiling.set()
            self.assertTrue(release_compile.wait(3))
            return original_compile(workflow)

        def execute(*args):
            self.assertTrue(release_job.wait(3))
            return RunnerResult([], "completed")

        def create_or_conflict():
            try:
                return self._create()
            except RunConflict as exc:
                return exc

        self.backend.execute.side_effect = execute
        try:
            with patch.object(self.service.recipe_compiler, "compile", side_effect=compile_workflow):
                with ThreadPoolExecutor(max_workers=2) as requests:
                    first = requests.submit(create_or_conflict)
                    self.assertTrue(first_compiling.wait(3))
                    second = requests.submit(create_or_conflict)
                    # A correctly locked create keeps the second request outside compilation.
                    both_compiling.wait(0.3)
                    release_compile.set()
                    outcomes = [first.result(timeout=3), second.result(timeout=3)]
            self.assertEqual(sum(isinstance(item, WorkflowRun) for item in outcomes), 1)
            self.assertEqual(sum(isinstance(item, RunConflict) for item in outcomes), 1)
            self.assertEqual(len(self.store.incomplete_runs()), 1)
        finally:
            release_compile.set()
            release_job.set()

    def test_cancel_does_not_overwrite_job_id_persisted_after_its_snapshot_read(self) -> None:
        entered, cancel_read, job_saved, release = Event(), Event(), Event(), Event()

        def execute(context, options, existing_job, on_job):
            entered.set()
            self.assertTrue(cancel_read.wait(3))
            on_job({"job_id": "45", "state": "RUNNING", "submission_token": "a" * 32})
            job_saved.set()
            self.assertTrue(release.wait(3))
            raise ExecutionDetached("Simulate a restart after the concurrent writes")

        self.backend.execute.side_effect = execute
        run = self._create()
        self.assertTrue(entered.wait(3))
        future = self.service._futures[run.run_id]
        original_get = self.store.get_run

        def snapshot_before_job_update(run_id):
            snapshot = original_get(run_id)
            cancel_read.set()
            # Unlocked code lets the worker persist now, before cancel writes
            # its stale snapshot. With a shared lock the worker waits until the
            # cancellation write has finished, which is the required ordering.
            job_saved.wait(0.3)
            return snapshot

        try:
            with patch.object(self.store, "get_run", side_effect=snapshot_before_job_update):
                self.service.cancel(run.run_id)
            self.assertTrue(job_saved.wait(3))
            saved = original_get(run.run_id)
            self.assertEqual(saved.status, "cancel_requested")
            self.assertEqual(saved.steps[0].scheduler.get("job_id"), "45")
            self.assertEqual(saved.steps[0].scheduler.get("submission_token"), "a" * 32)
        finally:
            release.set()
            future.result(timeout=3)

    def test_monitor_io_failure_cannot_declare_a_running_remote_job_failed(self) -> None:
        def execute(context, options, existing_job, on_job):
            on_job({"job_id": "46", "state": "RUNNING", "submission_token": "b" * 32})
            # Reading events.jsonl on an unavailable NFS mount currently raises
            # directly out of SlurmBackend._read_events, before a terminal query.
            raise OSError("NFS events.jsonl read failed")

        self.backend.execute.side_effect = execute
        run = self._create()
        deadline = time.monotonic() + 3
        while self.service._futures and time.monotonic() < deadline:
            time.sleep(0.01)
        current = self.service.get(run.run_id)
        self.assertIn(current.status, {"queued", "running", "cancel_requested"})
        self.assertEqual(current.steps[0].scheduler["job_id"], "46")
        self.assertIsNone(current.finished_at)

    def test_cancel_accepted_before_final_commit_cannot_be_overwritten_by_success(self) -> None:
        original_save = self.service._save_run

        def save_with_late_cancellation(run, **kwargs):
            if kwargs.get("event_type") == "step_succeeded":
                requested = self.service.cancel(run.run_id)
                self.assertEqual(requested.status, "cancel_requested")
            return original_save(run, **kwargs)

        with patch.object(self.service, "_save_run", side_effect=save_with_late_cancellation):
            run = self._finished(self._create().run_id)
        self.assertEqual(run.status, "cancelled")
        self.assertTrue(run.cancellation_requested)
        self.assertNotIn("run_succeeded", {
            event["event_type"] for event in self.service.events(run.run_id)
        })


if __name__ == "__main__":
    unittest.main()
