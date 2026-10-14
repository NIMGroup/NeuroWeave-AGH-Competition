from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

from app.capability_registry import CapabilityRegistry
from app.chat_service import ChatService
from app.dataset_models import SamplePathMatch
from app.dataset_service import DatasetService
from app.execution.base import RunnerContext
from app.execution.recipe_compiler import RecipeCompilationError, RecipeCompiler
from app.execution.runner_registry import RunnerRegistry
from app.execution.runners.bids_prepare import BidsPrepareRunner
from app.execution.runners.fsl import FslEddyRunner
from app.execution.runners.reviewed import ReviewedRunner, specifications
from app.local_store import LocalStore
from app.workflow_models import InputBinding, Workflow, WorkflowStep
from app.workflow_service import WorkflowService


ROOT = Path(__file__).resolve().parents[2]


class DryRunUpstreamTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        roots = patch.dict(os.environ, {
            "LOCALFLOW_DATA_ROOTS": str(self.root),
            "LOCALFLOW_RUN_INPUT_ROOTS": str(self.root),
        })
        roots.start()
        self.addCleanup(roots.stop)
        # Binding/deferral tests use synthetic files. Image-content validation
        # is covered by scientific_checks tests, independently of command mocks.
        headers = patch('app.execution.scientific_checks.image_header',
                        side_effect=lambda path:{'status':'passed','code':'synthetic_fixture',
                            'shape':[2,2,2,5 if 'five_tt' in path.name else 2]})
        headers.start()
        self.addCleanup(headers.stop)
        self.capabilities = CapabilityRegistry(ROOT / "skills")
        self.runners = RunnerRegistry()
        self.runners._runners = {
            key: self.runners.get(key) for key in ("mrtrix.dwi2fod", "mrtrix.tckgen")
        }
        for runner in self.runners._runners.values():
            mocked = patch.object(runner, "runtime_status", return_value={"available": True})
            mocked.start()
            self.addCleanup(mocked.stop)
        self.fod = self.step("dwi.fod", "mrtrix-dwi2fod", 1)
        self.tracking = self.step("dwi.tractography", "mrtrix3-existing-fod", 2)
        self.tracking.selected_parameters.update(act_enabled=True)
        for name in ("dwi", "mask", "bvecs", "bvals", "wm_response"):
            self.bind_file(self.fod, name)
        self.bind_file(self.tracking, "five_tt")
        self.tracking.input_bindings["fod"] = InputBinding(
            slot_id="fod", artifact_type="fod_image", source="upstream_output",
            upstream_step_id=self.fod.id, output_slot_id="wm_fod", status="planned",
        )
        self.workflow = Workflow(
            workflow_id="test_fod_tracking", template_id="test", title="FOD and tracking",
            summary="Synthetic regression fixture", original_request="Recompute FOD using existing 5TT",
            status="awaiting_dry_run", steps=[self.fod, self.tracking],
            locked_at="2026-01-01T00:00:00Z", parameters_reviewed_at="2026-01-01T00:00:00Z",
            created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
        )
        self.store = LocalStore(self.root / "test.db")
        self.service = WorkflowService(
            ChatService(),
            self.capabilities, self.store, DatasetService(ROOT, self.store),
            runner_registry=self.runners,
        )

    def step(self, capability, method_id, order):
        method = RecipeCompiler._method_option(self.capabilities.method_for(capability, method_id))
        return WorkflowStep(
            id=capability.replace(".", "_"), order=order, title=capability,
            description="Synthetic fixture", capability=capability, status="parameters_reviewed",
            method_options=[method], selected_method_id=method.id,
            selected_parameters=RecipeCompiler._default_parameters(method),
        )

    def bind_file(self, step, name):
        slot = next(slot for slot in step.method_options[0].input_slots if slot.id == name)
        path = self.root / (step.id + "_" + name + (slot.formats[0] if slot.formats else ".mif"))
        contents={'b_values':'0 1000','b_vectors':'1 0\n0 1\n0 0','gradient_table':'1 0 0 0\n0 1 0 1000'}.get(slot.type,'synthetic fixture; mrstats is mocked')
        if name in {'bvals','bval'}:
            contents='0 1000'
        if name in {'bvecs','bvec'}:
            contents='0 1\n0 0\n0 0'
        if slot.type=='response_functions':
            contents='1\n'
        path.write_text(contents,encoding='utf-8')
        step.input_bindings[name] = InputBinding(
            slot_id=name, artifact_type=slot.type, source="existing_path",
            absolute_path=str(path), status="validated",
        )
        return path

    def context(self, step=None, **kwargs):
        step = step or self.tracking
        return RunnerContext(
            run_id="actual-run", workflow=self.workflow, step=step,
            method=step.method_options[0], output_directory=self.root / "output",
            cancel_event=Event(), log=lambda *_: None, **kwargs,
        )

    def dry_run(self):
        self.store.save_workflow(self.workflow, event_type="test_fixture")
        return self.service.dry_run(self.workflow.workflow_id, self.workflow.revision)

    def test_complete_fod_tracking_dry_run_persists_valid_report_without_reading_future_fod(self):
        with patch("app.execution.runners.mrtrix.subprocess.run") as command:
            result = self.dry_run()
        command.assert_not_called()
        self.assertTrue(result.dry_run_report.valid)
        self.assertTrue(result.can_confirm)
        self.assertEqual(len(result.dry_run_report.commands), 2)
        self.assertTrue(any("执行该步骤前检查" in message
                            for message in result.dry_run_report.commands[1].messages))
        self.assertTrue(self.store.get_workflow(result.workflow_id).dry_run_report.valid)
        self.assertEqual(self.tracking.input_bindings["fod"].source, "upstream_output")

    def test_existing_and_resolved_runtime_fod_still_check_contents(self):
        path = self.bind_file(self.tracking, "fod")
        runner = self.runners.get("mrtrix.tckgen")
        for dry in (True, False):
            with self.subTest(dry_run=dry):
                with patch("app.execution.runners.mrtrix.subprocess.run",
                           return_value=subprocess.CompletedProcess([], 0, "0 0 0")) as command:
                    problems = runner.preflight(self.context(is_dry_run=dry))
                self.assertTrue(any("所有系数均为 0" in message for message in problems))
                if dry:self.assertEqual(command.call_args.args[0][1], str(path))
                else:command.assert_not_called()  # same unchanged file reuses content result

    def test_runtime_never_accepts_unresolved_upstream_binding(self):
        with patch("app.execution.runners.mrtrix.subprocess.run") as command:
            problems = self.runners.get("mrtrix.tckgen").preflight(self.context())
        command.assert_not_called()
        self.assertTrue(any("尚未解析" in message for message in problems))
        with self.assertRaises(RuntimeError):
            self.runners.get("mrtrix.tckgen").required_binding(self.context(), "fod")

    def test_missing_existing_fod_is_a_failed_report_instead_of_an_exception(self):
        path = self.bind_file(self.tracking, "fod")
        path.unlink()
        with patch("app.execution.runners.mrtrix.subprocess.run") as command:
            result = self.dry_run()
        command.assert_not_called()
        self.assertFalse(result.dry_run_report.valid)
        self.assertFalse(result.can_confirm)
        self.assertTrue(result.dry_run_report.commands[1].messages)

    def test_upstream_fod_does_not_skip_other_required_inputs_or_software(self):
        del self.tracking.input_bindings["five_tt"]
        self.tracking.selected_parameters.update(seeding_strategy="roi")
        with patch("app.execution.runners.mrtrix.subprocess.run") as command:
            result = self.dry_run()
        command.assert_not_called()
        self.assertFalse(result.dry_run_report.valid)
        messages = " ".join(result.dry_run_report.commands[1].messages)
        for expected in ("5TT", "种子 ROI"):
            self.assertIn(expected, messages)

    def test_unexpected_input_validation_exception_becomes_a_failed_report(self):
        with patch.object(self.runners.get("mrtrix.tckgen"), "preflight",
                          side_effect=RuntimeError("synthetic invalid binding")):
            result = self.dry_run()
        self.assertFalse(result.dry_run_report.valid)
        self.assertIn("synthetic invalid binding", result.dry_run_report.commands[1].messages[0])

    def test_graph_rejects_missing_forward_wrong_type_and_disabled_outputs(self):
        for change in ("missing_step", "missing_output", "forward", "wrong_type", "disabled"):
            with self.subTest(change=change):
                workflow = self.workflow.model_copy(deep=True)
                binding = workflow.steps[1].input_bindings["fod"]
                if change == "missing_step":
                    binding.upstream_step_id = "absent"
                elif change == "missing_output":
                    binding.output_slot_id = "absent"
                elif change == "forward":
                    workflow.steps[0].order = 3
                elif change == "wrong_type":
                    binding.artifact_type = "five_tt_image"
                else:
                    output = next(slot for slot in workflow.steps[0].method_options[0].output_slots
                                  if slot.id == "wm_fod")
                    output.generated_when = {"algorithm": ["msmt_csd"]}
                    workflow.steps[0].selected_parameters["algorithm"] = "csd"
                with self.assertRaises(RecipeCompilationError):
                    RecipeCompiler(self.capabilities).compile(workflow)

    def test_multiple_existing_samples_are_all_checked_during_dry_run(self):
        path = self.bind_file(self.tracking, "fod")
        binding = self.tracking.input_bindings["fod"]
        binding.binding_mode = "per_sample"
        second = path.with_name("second-fod.mif"); second.write_bytes(path.read_bytes())
        binding.sample_matches = [
            SamplePathMatch(sample_id=name, label=name, relative_path=path.name,
                            absolute_path=str(path if name=="sample-01" else second), status="matched")
            for name in ("sample-01", "sample-02")
        ]
        with patch("app.execution.runners.mrtrix.subprocess.run", side_effect=[
            subprocess.CompletedProcess([], 0, "1"), subprocess.CompletedProcess([], 0, "0"),
        ]) as command:
            problems = self.runners.get("mrtrix.tckgen").preflight(self.context(is_dry_run=True))
        self.assertEqual(command.call_count, 2)
        self.assertTrue(any("所有系数均为 0" in message for message in problems))

    def test_reviewed_library_input_can_be_deferred_but_run_remains_strict(self):
        runner = ReviewedRunner(specifications()["reviewed.nilearn-tool.preprocess"])
        step = self.tracking.model_copy(deep=True)
        step.selected_parameters = {}
        step.input_bindings = {"bold": InputBinding(
            slot_id="bold", artifact_type="bold_image", source="upstream_output",
            upstream_step_id="denoise", output_slot_id="cleaned", status="planned",
        )}
        context = self.context(step, is_dry_run=True)
        with patch.object(runner, "runtime_status", return_value={"available": True}):
            self.assertEqual(runner.preflight(context), [])
            self.assertTrue(runner.preflight(replace(context, is_dry_run=False)))
        with self.assertRaises(ValueError):
            runner.checked_inputs(context)

    def test_snapshot_checks_all_samples_without_passing_a_batch_to_single_file_runners(self):
        path = self.bind_file(self.tracking, "five_tt")
        binding = self.tracking.input_bindings["five_tt"]
        binding.binding_mode = "per_sample"
        second = path.with_name("second-fod.mif"); second.write_bytes(path.read_bytes())
        binding.sample_matches = [
            SamplePathMatch(sample_id=name, label=name, relative_path=path.name,
                            absolute_path=str(path if name=="sample-01" else second), status="matched")
            for name in ("sample-01", "sample-02")
        ]
        seen = []
        def preflight(context):
            self.assertTrue(context.input_is_deferred("fod"))
            self.assertEqual(context.step.input_bindings["five_tt"].binding_mode, "single")
            seen.append(context.step.input_bindings["five_tt"].absolute_path)
            return []
        with patch.object(self.runners.get("mrtrix.tckgen"), "preflight", side_effect=preflight):
            self.assertEqual(self.runners.preflight_snapshot(
                self.workflow, self.tracking, self.tracking.method_options[0]), [])
        self.assertCountEqual(seen, [str(path), str(second)])
        binding.sample_matches[1].status = "missing"
        self.assertTrue(self.runners.preflight_snapshot(self.workflow, self.tracking, self.tracking.method_options[0]))

    def test_upstream_dataset_directory_is_checked_at_runtime(self):
        runner = BidsPrepareRunner()
        step = self.tracking.model_copy(deep=True)
        step.input_bindings = {"source_dataset": InputBinding(
            slot_id="source_dataset", artifact_type="dataset_folder", source="upstream_output",
            upstream_step_id="prepare", output_slot_id="dataset",
        )}
        self.assertEqual(runner.preflight(self.context(step, is_dry_run=True)), [])
        self.assertTrue(runner.preflight(self.context(step)))

    def test_topup_companion_checks_wait_for_upstream_output(self):
        runner = FslEddyRunner()
        step = self.step("dwi.preprocess", "fsl_eddy", 2)
        for name in ("dwi", "mask", "acqp", "index", "bvecs", "bvals"):
            self.bind_file(step, name)
        step.input_bindings["topup_fieldcoef"] = InputBinding(
            slot_id="topup_fieldcoef", artifact_type="topup_fieldcoef",
            source="upstream_output", upstream_step_id="topup", output_slot_id="fieldcoef",
        )
        with patch.object(runner, "runtime_status", return_value={"available": True}):
            self.assertEqual(runner.preflight(self.context(step, is_dry_run=True)), [])
            path = self.root / "topup_fieldcoef.nii.gz"
            path.write_bytes(b"synthetic")
            step.input_bindings["topup_fieldcoef"] = step.input_bindings["topup_fieldcoef"].model_copy(
                update={"source": "existing_path", "absolute_path": str(path)}
            )
            self.assertTrue(any("_movpar.txt" in message for message in runner.preflight(self.context(step))))
