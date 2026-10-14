from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.dataset_models import DatasetSample, SamplePathMatch
from app.execution.base import ExecutionCancelled, ExecutionDetached, RunnerResult
from app.execution.run_service import RunConflict
from app.execution.recipe_compiler import RecipeCompiler
from app.run_models import NodeAllocation, RunArtifact, RunExecutionOptions
from app.workflow_models import InputBinding, WorkflowStep
import test_slurm_run_service as fixtures


class SampleExecutionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SlurmRunServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        fixture = self.fixture
        self.workflow = fixture.workflow
        samples = []
        for index in range(4):
            name = f"sample-{index + 1}"
            (fixture.dataset_root / name).mkdir()
            samples.append(DatasetSample(sample_id=name, label=name, relative_root=name))
        self.workflow.dataset.inspection.samples = samples
        self.workflow.dataset.inspection.sample_count = 4
        second = self.workflow.steps[0].model_copy(deep=True)
        second.id = "inspect_again"
        second.order = 2
        self.workflow.steps.append(second)
        fixture.store.save_workflow(self.workflow, event_type="fixture")
        fixture.backend.status.return_value["nodes"] = [
            {"name": name, "partition": "all", "schedulable": True,
             "cpus": 128, "memory_mb": 245000, "gpus": 1}
            for name in ("node01", "node02", "node03")
        ]
        self.options = RunExecutionOptions(
            backend="slurm", sample_mode="per_sample",
            nodes=[NodeAllocation(node="node01", cpus=4), NodeAllocation(node="node02", cpus=8)],
        )

    def create(self):
        return self.fixture.service.create(self.workflow.workflow_id, self.workflow.revision, self.options)

    def test_samples_execute_concurrently_with_separate_nodes_resources_and_outputs(self):
        barrier = threading.Barrier(2)
        calls = []
        lock = threading.Lock()
        def execute(context, options, state, on_job):
            if context.step.id.startswith(("sample_0001__", "sample_0002__")) and context.step.id.endswith("__inspect"):
                barrier.wait(timeout=2)
            with lock:
                calls.append((context.step.id, options, context.workflow.dataset.absolute_path))
            context.output_directory.mkdir(parents=True, exist_ok=True)
            path = context.output_directory / "result.txt"
            path.write_text(context.step.id)
            on_job({"job_id": str(len(calls)), "state": "COMPLETED", "node_list": options["node"]})
            context.log("done", 100, "info")
            return RunnerResult([RunArtifact(
                artifact_id=context.step.id, step_id=context.step.id, artifact_type="test",
                label="synthetic", absolute_path=str(path), relative_path=path.name,
            )], "done")
        self.fixture.backend.execute.side_effect = execute
        run = self.fixture._finished(self.create().run_id)
        self.assertEqual(run.status, "succeeded")
        self.assertEqual(len(calls), 8)
        self.assertEqual(len(run.artifacts), 8)
        self.assertEqual(len({item.absolute_path for item in run.artifacts}), 8)
        for index, sample in enumerate(self.workflow.dataset.inspection.samples):
            own = [item for item in calls if item[0].startswith(f"sample_{index + 1:04d}__")]
            self.assertEqual([item[0].split("__")[1] for item in own], ["inspect", "inspect_again"])
            self.assertTrue(all(item[1]["node"] == self.options.nodes[index % 2].node for item in own))
            self.assertTrue(all(item[1]["cpus"] == self.options.nodes[index % 2].cpus for item in own))
            self.assertTrue(all(Path(item[2]).name == sample.sample_id for item in own))

    def test_fixed_node_is_passed_to_single_run(self):
        self.options = RunExecutionOptions(backend="slurm", nodes=[NodeAllocation(node="node03", cpus=32)])
        run = self.fixture._finished(self.create().run_id)
        self.assertEqual(run.status, "succeeded")
        for call in self.fixture.backend.execute.call_args_list:
            self.assertEqual(call.args[1]["node"], "node03")
            self.assertEqual(call.args[1]["cpus"], 32)

    def test_per_node_parallel_limits_preserve_each_samples_step_order(self):
        self.options.nodes[0].parallel_samples = 2
        entered = threading.Barrier(4)
        release = threading.Event()
        self.addCleanup(release.set)
        active, peak, calls = {}, {}, []
        lock = threading.Lock()
        def execute(context, options, state, on_job):
            node = options["node"]
            self.assertNotIn("parallel_samples", options)  # Scheduler options stay backward compatible.
            with lock:
                active[node] = active.get(node, 0) + 1
                peak[node] = max(peak.get(node, 0), active[node])
                calls.append(context.step.id)
                first_wave = len(calls) <= 3
            try:
                if first_wave:
                    entered.wait(timeout=3)
                    self.assertTrue(release.wait(timeout=3))
                return RunnerResult([], "done")
            finally:
                with lock:
                    active[node] -= 1
        self.fixture.backend.execute.side_effect = execute
        run = self.create()
        try:
            entered.wait(timeout=3)
            with lock:
                self.assertEqual(active, {"node01": 2, "node02": 1})
        finally:
            release.set()
        result = self.fixture._finished(run.run_id)
        self.assertEqual(result.status, "succeeded", result.error_message)
        self.assertEqual(peak, {"node01": 2, "node02": 1})
        self.assertEqual(len(calls), 8)
        for index in range(4):
            self.assertLess(calls.index(f"sample_{index + 1:04d}__inspect"),
                            calls.index(f"sample_{index + 1:04d}__inspect_again"))
        self.assertEqual(self.fixture.store.get_run(run.run_id).execution.nodes[0].parallel_samples, 2)

    def test_parallel_resources_are_checked_before_any_submission(self):
        for field, value in (("cpus", 65), ("memory_gb", 120), ("gpus", 1)):
            with self.subTest(field=field):
                original = self.options.model_copy(deep=True)
                self.options.nodes[0].parallel_samples = 2
                setattr(self.options.nodes[0], field, value)
                with self.assertRaisesRegex(RunConflict, "并行 2 个样本"):
                    self.create()
                self.options = original
        self.fixture.backend.execute.assert_not_called()
        self.assertFalse(self.fixture.store.list_runs())

    def test_shared_node_queue_fills_a_free_slot_while_another_sample_is_slow(self):
        self.options.nodes = [self.options.nodes[0]]
        self.options.nodes[0].parallel_samples = 2
        slow_started, third_started = threading.Event(), threading.Event()
        self.addCleanup(third_started.set)
        def execute(context, options, state, on_job):
            if context.step.id == "sample_0001__inspect":
                slow_started.set()
                self.assertTrue(third_started.wait(timeout=3), "A free slot must take sample 3 before sample 1 finishes")
            elif context.step.id == "sample_0003__inspect":
                self.assertTrue(slow_started.wait(timeout=2))
                third_started.set()
            return RunnerResult([], "done")
        self.fixture.backend.execute.side_effect = execute
        result = self.fixture._finished(self.create().run_id)
        self.assertEqual(result.status, "succeeded", result.error_message)
        self.assertTrue(third_started.is_set())

    def test_parallel_limit_above_assigned_sample_count_only_reserves_actual_capacity(self):
        self.options.nodes[0].parallel_samples = 128  # Only two samples are assigned here.
        result = self.fixture._finished(self.create().run_id)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(self.fixture.backend.execute.call_count, 8)

    def test_parallel_limit_validation_and_legacy_default(self):
        self.assertEqual(NodeAllocation.model_validate({"node": "node01"}).parallel_samples, 1)
        for value in (0, -1, 129, 1.5, True, None):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                NodeAllocation(node="node01", parallel_samples=value)
        with self.assertRaises(ValidationError):
            RunExecutionOptions(backend="slurm", nodes=[NodeAllocation(node="node01", parallel_samples=2)])

    def test_total_parallel_limit_rejects_before_resolving_or_submitting_samples(self):
        self.workflow.dataset.inspection.samples = [
            DatasetSample(sample_id=f"sample-{index}", label=str(index), relative_root=f"sample-{index}")
            for index in range(129)
        ]
        self.workflow.dataset.inspection.sample_count = 129
        self.fixture.store.save_workflow(self.workflow, event_type="fixture")
        self.options.nodes[0].parallel_samples = 65
        self.options.nodes[1].parallel_samples = 64
        with self.assertRaisesRegex(RunConflict, "不能超过 128"):
            self.create()
        self.fixture.backend.execute.assert_not_called()

    def test_fod_outputs_are_bound_only_to_the_same_samples_tracking_step(self):
        self._run_fod_tracking_case()

    def test_single_sample_ordinary_inputs_execute_on_selected_compute_node(self):
        self.workflow.dataset.inspection.samples = self.workflow.dataset.inspection.samples[:1]
        self.workflow.dataset.inspection.sample_count = 1
        self.options.nodes = [NodeAllocation(node="node03", cpus=8, parallel_samples=4)]
        run = self._run_fod_tracking_case()
        self.assertEqual([s.step_id for s in run.steps], ["sample_0001__fod", "sample_0001__tracking"])
        self.assertEqual({s.sample_id for s in run.steps}, {"sample-1"})
        self.assertEqual({s.node_name for s in run.steps}, {"node03"})
        self.assertTrue(all(call.args[1]["cpus"] == 8 for call in self.fixture.backend.execute.call_args_list))

    def test_single_sample_at_dataset_root_uses_ordinary_inputs(self):
        self.workflow.dataset.inspection.samples = [DatasetSample(sample_id="single", label="single", relative_root="")]
        self.workflow.dataset.inspection.sample_count = 1
        self.options.nodes = [self.options.nodes[0]]
        self._run_fod_tracking_case()

    def _run_fod_tracking_case(self):
        definitions = [("dwi.fod", "mrtrix-dwi2fod"), ("dwi.tractography", "mrtrix3-existing-fod")]
        steps = []
        for index, (capability, method_id) in enumerate(definitions):
            method = RecipeCompiler._method_option(self.fixture.capabilities.method_for(capability, method_id))
            step = WorkflowStep(id=("fod" if index == 0 else "tracking"), order=index + 1,
                                title=capability, description="", capability=capability, status="confirmed",
                                method_options=[method], selected_method_id=method.id,
                                selected_parameters=RecipeCompiler._default_parameters(method))
            for name in (("dwi", "mask", "bvecs", "bvals", "wm_response") if index == 0 else ("five_tt",)):
                slot = next(item for item in method.input_slots if item.id == name)
                matches = []
                for sample in self.workflow.dataset.inspection.samples:
                    path = self.fixture.dataset_root / sample.relative_root / (name + ".mif")
                    path.write_bytes(b"synthetic")
                    matches.append(SamplePathMatch(sample_id=sample.sample_id, label=sample.label,
                                                   relative_path=str(path.relative_to(self.fixture.dataset_root)),
                                                   absolute_path=str(path), status="matched"))
                step.input_bindings[name] = InputBinding(
                    slot_id=name, artifact_type=slot.type, source="existing_path", status="validated",
                    binding_mode="single" if len(matches) == 1 else "per_sample", sample_matches=matches,
                    absolute_path=matches[0].absolute_path,
                )
            steps.append(step)
        steps[1].input_bindings["fod"] = InputBinding(
            slot_id="fod", artifact_type="fod_image", source="upstream_output",
            upstream_step_id="fod", output_slot_id="wm_fod", status="planned",
        )
        self.workflow.steps = steps
        self.fixture.store.save_workflow(self.workflow, event_type="fixture")
        tracked = []
        def execute(context, options, state, on_job):
            context.output_directory.mkdir(parents=True, exist_ok=True)
            if context.step.id.endswith("__fod"):
                path = context.output_directory / "fod.mif"
                path.write_text(context.step.id)
                return RunnerResult([RunArtifact(
                    artifact_id=context.step.id, step_id=context.step.id, artifact_type="fod_image",
                    label="synthetic FOD", absolute_path=str(path), relative_path=path.name,
                )], "synthetic FOD completed")
            source = Path(context.step.input_bindings["fod"].absolute_path)
            expected_prefix = context.step.id.split("__")[0]
            self.assertEqual(source.read_text(), expected_prefix + "__fod")
            sample = Path(context.workflow.dataset.absolute_path).name
            self.assertEqual(Path(context.step.input_bindings["five_tt"].absolute_path).parent.name, sample)
            tracked.append(sample)
            return RunnerResult([], "synthetic tracking completed")
        self.fixture.backend.execute.side_effect = execute
        run = self.fixture._finished(self.create().run_id)
        self.assertEqual(run.status, "succeeded", run.error_message)
        self.assertEqual(len(set(tracked)), self.workflow.dataset.inspection.sample_count)
        return run

    def test_resources_unknown_nodes_and_incomplete_samples_are_rejected_before_submission(self):
        for change in ("missing_node", "cpu", "memory", "gpu", "missing_sample"):
            with self.subTest(change=change):
                original = self.options.model_copy(deep=True)
                if change == "missing_node":
                    self.options.nodes[0].node = "unknown"
                elif change == "cpu":
                    self.fixture.backend.status.return_value["nodes"][0]["cpus"] = 2
                elif change == "memory":
                    self.options.nodes[0].memory_gb = 256
                elif change == "gpu":
                    self.fixture.backend.status.return_value["nodes"][0]["gpus"] = 0
                    self.options.nodes[0].gpus = 1
                else:
                    self.workflow.dataset.inspection.sample_count = 5
                    self.fixture.store.save_workflow(self.workflow, event_type="fixture")
                with self.assertRaises(RunConflict):
                    self.create()
                self.options = original
                self.fixture.backend.status.return_value["nodes"][0].update(cpus=128, gpus=1)
                self.workflow.dataset.inspection.sample_count = 4
        self.fixture.backend.execute.assert_not_called()
        self.assertFalse(self.fixture.store.list_runs())

    def test_node_names_and_duplicate_allocations_reject_command_fragments(self):
        for name in ("node01,node02", "node[01-03]", "--exclusive", "node01\n#SBATCH --gres=gpu:1"):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                NodeAllocation(node=name)
        with self.assertRaises(ValidationError):
            RunExecutionOptions(backend="slurm", sample_mode="per_sample",
                                nodes=[NodeAllocation(node="node01"), NodeAllocation(node="node01")])

    def test_sample_failure_preserves_other_samples_and_skips_its_downstream(self):
        self.options.nodes[0].parallel_samples = 2
        calls = []
        def execute(context, options, state, on_job):
            calls.append(context.step.id)
            if context.step.id == "sample_0001__inspect":
                raise RuntimeError("synthetic failure")
            return RunnerResult([], "done")
        self.fixture.backend.execute.side_effect = execute
        run = self.fixture._finished(self.create().run_id)
        self.assertEqual(run.status, "failed")
        self.assertNotIn("sample_0001__inspect_again", calls)
        self.assertEqual(sum(step.status == "succeeded" for step in run.steps), 6)
        self.assertEqual(run.steps[1].status, "cancelled")

    def test_uncertain_job_keeps_batch_active_and_recovery_reuses_job(self):
        self.options.nodes[0].parallel_samples = 2
        entered = threading.Barrier(3)
        def detach(context, options, state, on_job):
            on_job({"job_id": context.step.id, "state": "RUNNING", "node_list": options["node"]})
            entered.wait(timeout=3)
            raise ExecutionDetached("synthetic disconnect")
        self.fixture.backend.execute.side_effect = detach
        run = self.create()
        deadline = time.monotonic() + 4
        while run.run_id in self.fixture.service._futures and time.monotonic() < deadline:
            time.sleep(.01)
        previous = self.fixture.service.get(run.run_id)
        self.assertEqual(previous.status, "running")
        started = {step.step_id: step.scheduler for step in previous.steps if step.scheduler}
        self.assertEqual(len(started), 3)
        with self.assertRaises(RunConflict):
            self.create()
        resumed = []
        def finish(context, options, state, on_job):
            resumed.append((context.step.id, state))
            return RunnerResult([], "done")
        self.fixture.backend.execute.side_effect = finish
        self.fixture.service.resume_slurm_monitoring(run.run_id)
        result = self.fixture._finished(run.run_id)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(resumed), 8)
        self.assertEqual(len({step_id for step_id, _ in resumed}), 8)
        for step_id, state in resumed:
            self.assertEqual(state, started.get(step_id))

    def test_cancel_reaches_all_active_nodes_and_stops_queued_samples(self):
        self.options.nodes[0].parallel_samples = 2
        started = threading.Barrier(4)
        calls = []
        def execute(context, options, state, on_job):
            calls.append(context.step.id)
            on_job({"job_id": options["node"], "state": "RUNNING"})
            started.wait(timeout=2)
            self.assertTrue(context.cancel_event.wait(timeout=2))
            on_job({"job_id": options["node"], "state": "CANCELLED"})
            raise ExecutionCancelled("cancelled")
        self.fixture.backend.execute.side_effect = execute
        run = self.create()
        started.wait(timeout=2)
        self.fixture.service.cancel(run.run_id)
        result = self.fixture._finished(run.run_id)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(step.status == "cancelled" for step in result.steps))
