from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.capability_registry import CapabilityRegistry
from app.conversation_models import ConversationRecord
from app.dataset_models import DatasetInspection, DatasetRecord
from app.execution import RunnerRegistry, RunConflict, RunService
from app.execution.base import RunnerResult
from app.execution.runners.freesurfer import FreeSurferReconAllRunner
from app.local_store import LocalStore
from app.run_models import RunArtifact
from app.workflow_models import (
    DryRunReport,
    InputBinding,
    MethodOption,
    Workflow,
    WorkflowStep,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = PROJECT_ROOT / "skills"


class RunServiceTests(unittest.TestCase):
    def test_configuration_changes_block_submission_even_after_successful_dry_run(self):
        workflow = self._workflow(execution_enabled=True)
        self.store.save_workflow(workflow, event_type='workflow_confirmed')
        runner = self.runners.get('dataset.inspect')
        runner.executable = Path('.')
        with self.assertRaisesRegex(RunConflict, '软件路径未配置'):
            self.service.create(workflow.workflow_id, workflow.revision)
        self.assertEqual(self.store.list_runs(workflow_id=workflow.workflow_id), [])
        self.assertFalse(self.service._futures)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.dataset_root = root / "dataset"
        (self.dataset_root / "sub-01" / "anat").mkdir(parents=True)
        (self.dataset_root / "dataset_description.json").write_text(
            '{"Name":"test","BIDSVersion":"1.9.0"}',
            encoding="utf-8",
        )
        (self.dataset_root / "sub-01" / "anat" / "sub-01_T1w.nii.gz").write_bytes(
            b"not-a-real-nifti"
        )
        now = datetime.now(timezone.utc).isoformat()
        self.dataset = DatasetRecord(
            dataset_id="dataset_test",
            root_id="root_test",
            relative_path="dataset",
            display_name="测试数据集",
            absolute_path=str(self.dataset_root),
            inspection=DatasetInspection(
                file_count=2,
                total_bytes=20,
                is_bids=True,
                has_t1w=True,
                input_types=["dataset_folder", "bids_anat"],
                features={"has_t1w": True},
                sample_layout="bids",
                sample_count=1,
                scanned_at=now,
            ),
            created_at=now,
            updated_at=now,
        )
        self.store = LocalStore(root / "localflow.db")
        self.store.save_dataset(self.dataset)
        self.capabilities = CapabilityRegistry(SKILLS_DIR)
        self.runners = RunnerRegistry()
        self.service = RunService(
            self.store,
            self.capabilities,
            self.runners,
            root / "outputs",
        )

    def tearDown(self) -> None:
        self.service.shutdown()
        self.temporary.cleanup()

    def test_workspace_output_uses_frozen_root_and_rejects_escape(self) -> None:
        workspace = self.dataset_root.parent / "workspace"
        workspace.mkdir()
        with patch.dict(os.environ, {"WORKSPACE_ALLOWED_ROOTS": str(workspace), "WORKSPACE_DEFAULT_ROOT": str(workspace)}):
            workflow = self._workflow(execution_enabled=True)
            workflow.workspace_id = "ws_test"
            workflow.workspace_root_path = str(workspace)
            workflow.output_relative_path = "outputs"
            self.store.save_workflow(workflow, event_type="workflow_confirmed")
            run = self.service.create(workflow.workflow_id, workflow.revision)
            for _ in range(200):
                run = self.service.get(run.run_id)
                if run.status in {"succeeded", "failed"}:
                    break
                time.sleep(0.02)
            self.assertEqual(run.status, "succeeded", run.error_message)
            self.assertTrue(Path(run.artifacts[0].absolute_path).is_relative_to(workspace / "outputs"))
            # Later edits cannot move an already submitted run.
            workflow.output_relative_path = "later"
            self.store.save_workflow(workflow, event_type="test_later_edit")
            self.assertEqual(self.service._safe_run_directory(run.run_id), Path(run.output_directory))
            workflow.output_relative_path = "../escape"
            self.store.save_workflow(workflow, event_type="test_invalid_edit")
            with self.assertRaises(PermissionError):
                self.service.create(workflow.workflow_id, workflow.revision)

    def test_confirmed_file_operation_reserves_paths_against_new_run(self):
        from app.workspace_service import WorkspaceService
        root = self.dataset_root.parent
        with patch.dict(os.environ, {"WORKSPACE_ALLOWED_ROOTS": str(root), "WORKSPACE_DEFAULT_ROOT": str(root)}):
            WorkspaceService(self.store, root)
            workflow = self._workflow(execution_enabled=True)
            self.store.save_workflow(workflow, event_type="workflow_confirmed")
            with self.store._connect() as db:
                db.execute("INSERT INTO workspace_operations VALUES(?,?,?,?,?)", ('op', 'ws', 'running',
                    json.dumps({'action': 'move', 'source': str(self.dataset_root), 'target': str(root / 'elsewhere')}), 'now'))
            with self.assertRaises(RunConflict):
                self.service.create(workflow.workflow_id, workflow.revision)

    def test_selected_output_root_is_used_directly_and_dataset_output_is_rejected(self):
        root = self.dataset_root.parent / 'managed-results'
        root.mkdir()
        with patch.dict(os.environ, {'WORKSPACE_ALLOWED_ROOTS': str(self.dataset_root.parent), 'WORKSPACE_DEFAULT_ROOT': str(root)}):
            workflow = self._workflow(execution_enabled=True)
            workflow.workspace_id, workflow.workspace_root_path, workflow.output_relative_path = 'ws', str(root), '.'
            self.store.save_workflow(workflow, event_type='confirmed')
            run = self.service.create(workflow.workflow_id, workflow.revision)
            for _ in range(200):
                run = self.service.get(run.run_id)
                if run.status in {'succeeded', 'failed'}:
                    break
                time.sleep(.02)
            self.assertEqual(run.status, 'succeeded', run.error_message)
            self.assertEqual(Path(run.output_directory).parent, root)
            workflow.workspace_root_path = str(self.dataset_root)
            self.store.save_workflow(workflow, event_type='invalid_output')
            with self.assertRaises(PermissionError):
                self.service.create(workflow.workflow_id, workflow.revision)

    def test_confirmed_dataset_inspection_executes_and_persists_artifact(self) -> None:
        workflow = self._workflow(execution_enabled=True)
        self.store.save_workflow(workflow, event_type="workflow_confirmed")

        run = self.service.create(workflow.workflow_id, workflow.revision)
        for _ in range(200):
            run = self.service.get(run.run_id)
            if run.status in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.01)

        self.assertEqual(run.status, "succeeded")
        self.assertEqual(run.progress, 100.0)
        self.assertEqual(len(run.artifacts), 1)
        artifact = run.artifacts[0]
        output = Path(artifact.absolute_path)
        self.assertTrue(output.is_file())
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["runner_id"], "dataset.inspect")
        self.assertEqual(report["file_count"], 2)
        self.assertEqual(artifact.sha256, hashlib.sha256(output.read_bytes()).hexdigest())
        events = self.service.events(run.run_id)
        self.assertIn("run_started", {item["event_type"] for item in events})
        self.assertIn("run_succeeded", {item["event_type"] for item in events})

        # Removing a chat that references this workflow must preserve the actual
        # task, audit trail and result file.
        now = datetime.now(timezone.utc).isoformat()
        self.store.create_conversation(ConversationRecord(
            conversation_id="conv_delete_test", title="可删除对话", created_at=now, updated_at=now,
        ))
        self.store.append_conversation_message(
            "conv_delete_test", role="assistant", content="巡检完成", estimated_tokens=5,
            metadata={"workflow_id": workflow.workflow_id}, created_at=now,
        )
        self.store.update_conversation("conv_delete_test", archived=True, updated_at=now)
        self.assertTrue(self.store.delete_archived_conversation("conv_delete_test"))
        self.assertIsNotNone(self.store.get_workflow(workflow.workflow_id))
        self.assertEqual(self.service.get(run.run_id).status, "succeeded")
        self.assertTrue(self.service.events(run.run_id))
        self.assertTrue(output.is_file())

    def test_run_rejects_manifest_only_workflow(self) -> None:
        workflow = self._workflow(execution_enabled=False)
        self.store.save_workflow(workflow, event_type="workflow_confirmed")
        with self.assertRaisesRegex(RunConflict, "尚未接入实际执行器"):
            self.service.create(workflow.workflow_id, workflow.revision)

    def test_freesurfer_recipe_expands_and_binds_runner_artifacts(self) -> None:
        calls: list[tuple[str, str | None]] = []

        class FakeFreeSurferRunner:
            runner_id = "freesurfer.recon_all"
            label = "Fake FreeSurfer"

            @staticmethod
            def runtime_status() -> dict[str, object]:
                return {"available": True, "reason": "test"}

            @staticmethod
            def preflight(_context) -> list[str]:
                return []

            @staticmethod
            def run(context) -> RunnerResult:
                stage = str(context.step.selected_parameters["processing_stage"])
                subject_binding = context.step.input_bindings.get("subject")
                if subject_binding is None:
                    subject = context.output_directory / "subject-test"
                    subject.mkdir(parents=True, exist_ok=True)
                    source = None
                else:
                    subject = Path(str(subject_binding.absolute_path))
                    source = str(subject)
                (subject / f"{stage}.done").write_text("ok", encoding="utf-8")
                calls.append((stage, source))
                return RunnerResult(
                    artifacts=[
                        RunArtifact(
                            artifact_id=f"artifact_{stage}",
                            step_id=context.step.id,
                            artifact_type="freesurfer_subject",
                            label=f"FreeSurfer {stage}",
                            absolute_path=str(subject),
                            relative_path=subject.name,
                            media_type="inode/directory",
                            metadata={"output_slot_id": "subject"},
                        )
                    ],
                    message=f"{stage} complete",
                )

        self.runners._runners["freesurfer.recon_all"] = FakeFreeSurferRunner()
        definition = next(
            item
            for item in self.capabilities.selectable_methods_for("skill.freesurfer-tool")
            if item.method_id
            == "freesurfer-tool-guided--fast-subcortical-basic-surfaces"
        )
        self.assertEqual(definition.execution_mode, "runner")
        self.assertIsNotNone(definition.execution_recipe)
        method = MethodOption(
            id=definition.method_id,
            skill_id=definition.skill_id,
            label=definition.label,
            description=definition.description,
            runner_id=definition.runner_id,
            execution_mode=definition.execution_mode,
            input_types=definition.input_types,
            output_types=definition.output_types,
            parameters=definition.parameters,
            input_slots=definition.input_slots,
            output_slots=definition.output_slots,
            structure_level=definition.structure_level,
            execution_recipe=definition.execution_recipe,
        )
        t1w = self.dataset_root / "sub-01" / "anat" / "sub-01_T1w.nii.gz"
        now = datetime.now(timezone.utc).isoformat()
        workflow = Workflow(
            workflow_id="workflow_freesurfer_recipe",
            template_id="dynamic:skill.freesurfer-tool",
            revision=1,
            title="FreeSurfer 分阶段执行",
            summary="连续运行三个受信任 Runner 阶段",
            original_request="运行 FreeSurfer autorecon1/2/3",
            status="confirmed",
            dataset_id=self.dataset.dataset_id,
            dataset=self.dataset,
            planner_source="test",
            execution_mode="runner",
            plan_kind="atomic",
            requested_capabilities=["skill.freesurfer-tool"],
            steps=[
                WorkflowStep(
                    id="skill_freesurfer_tool",
                    order=1,
                    title="FreeSurfer",
                    description="分阶段",
                    selection="user",
                    status="confirmed",
                    capability="skill.freesurfer-tool",
                    method_options=[method],
                    selected_method_id=method.id,
                    selected_parameters={"threads": 2},
                    input_bindings={
                        "t1w": InputBinding(
                            slot_id="t1w",
                            artifact_type="t1w_image",
                            source="existing_path",
                            label="T1w",
                            absolute_path=str(t1w),
                            status="validated",
                        )
                    },
                )
            ],
            dry_run_report=DryRunReport(
                valid=True,
                execution_enabled=True,
                dataset_path_validated=True,
                checked_at=now,
            ),
            confirmed_at=now,
            created_at=now,
            updated_at=now,
        )
        self.store.save_workflow(workflow, event_type="workflow_confirmed")

        run = self.service.create(workflow.workflow_id, workflow.revision)
        for _ in range(200):
            run = self.service.get(run.run_id)
            if run.status in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.01)

        self.assertEqual(run.status, "succeeded", run.error_message)
        self.assertEqual(
            [step.step_id for step in run.steps],
            [
                "skill_freesurfer_tool__autorecon1",
                "skill_freesurfer_tool__autorecon2",
                "skill_freesurfer_tool__autorecon3",
            ],
        )
        self.assertEqual([stage for stage, _source in calls], ["autorecon1", "autorecon2", "autorecon3"])
        self.assertIsNone(calls[0][1])
        self.assertEqual(calls[1][1], calls[2][1])
        self.assertTrue(Path(str(calls[1][1])).is_dir())

    def _failed_freesurfer_run(self):
        self.test_freesurfer_recipe_expands_and_binds_runner_artifacts()
        run = self.service.list()[0]
        subject = Path(run.steps[0].artifacts[0].absolute_path)
        for name in ["mri/aseg.presurf.mgz", "mri/wm.mgz", "mri/filled.mgz",
                     "surf/lh.white.preaparc", "surf/rh.white.preaparc",
                     "surf/lh.inflated", "surf/rh.inflated"]:
            path = subject / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test")
        scripts = subject / "scripts"
        scripts.mkdir()
        done = scripts / "recon-all.done"
        done.write_text(f'SUBJECT {subject.name}\nCMDARGS -sd "{subject.parent.as_posix()}" '
                        f'-s {subject.name} -autorecon2 -parallel -openmp 2\n')
        log = scripts / "recon-all.log"
        log.write_text(f"recon-all -s {subject.name} finished without error at test\n")
        stamp = datetime.fromisoformat(run.steps[1].finished_at).timestamp()
        for path in (done, log):
            os.utime(path, (stamp, stamp))
        run.status = "failed"
        run.progress = 65.333
        run.steps[1].status = "failed"
        run.steps[1].progress = 96
        run.steps[1].artifacts = []
        run.steps[1].error_code = run.error_code = "runner_failed"
        run.steps[1].error_message = run.error_message = (
            f"FreeSurfer 未生成预期输出：{subject / 'surf' / 'lh.white'}")
        run.steps[2].status = "queued"
        run.steps[2].progress = 0
        run.steps[2].artifacts = []
        run.steps[2].started_at = run.steps[2].finished_at = None
        run.artifacts = list(run.steps[0].artifacts)
        self.store.save_run(run)
        runner = FreeSurferReconAllRunner()
        self.runners._runners[runner.runner_id] = runner
        return run, subject, runner

    def test_freesurfer_recovery_only_executes_autorecon3(self) -> None:
        run, subject, runner = self._failed_freesurfer_run()
        original_start = run.started_at
        original_first = run.steps[0].model_dump()
        calls = []

        def execute(context, argv, **kwargs):
            calls.append(argv)
            self.assertIn("-autorecon3", argv)
            self.assertNotIn("-i", argv)
            self.assertEqual(argv[argv.index("-sd") + 1], str(subject.parent))
            for name in ["mri/aseg.mgz", "mri/aparc+aseg.mgz", "stats/aseg.stats", "surf/lh.white",
                         "surf/rh.white", "surf/lh.pial", "surf/rh.pial",
                         "stats/lh.aparc.stats", "stats/rh.aparc.stats"]:
                path = subject / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test")

        with patch.object(runner, "preflight", return_value=[]), patch.object(runner, "execute", side_effect=execute):
            before = self.service.get(run.run_id).model_dump()
            report = self.service.recover_freesurfer(run.run_id, run.workflow_revision)
            self.assertTrue(report["dry_run"])
            self.assertEqual(self.service.get(run.run_id).model_dump(), before)
            self.assertEqual(calls, [])
            self.service.recover_freesurfer(run.run_id, run.workflow_revision, dry_run=False)
            for _ in range(200):
                current = self.service.get(run.run_id)
                if current.status in {"succeeded", "failed"}:
                    break
                time.sleep(0.01)
        self.assertEqual(current.status, "succeeded", current.error_message)
        self.assertEqual(len(calls), 1)
        self.assertEqual(current.steps[0].model_dump(), original_first)
        self.assertEqual(current.started_at, original_start)
        self.assertEqual(len(current.artifacts), 6)
        self.assertTrue(any(a.artifact_type == "parcellation_image" for a in current.artifacts))
        self.assertEqual(current.progress, 100)
        self.assertEqual(current.error_message, "")
        self.assertIn("freesurfer_stage_recovered", {e["event_type"] for e in self.service.events(run.run_id)})
        with self.assertRaises(RunConflict):
            self.service.recover_freesurfer(run.run_id, run.workflow_revision, dry_run=False)

    def test_freesurfer_recovery_rejects_unproven_completion(self) -> None:
        run, subject, runner = self._failed_freesurfer_run()
        with patch.object(runner, "preflight", return_value=[]):
            with self.assertRaises(RunConflict):
                self.service.recover_freesurfer(run.run_id, run.workflow_revision + 1)
            for filename in ["surf/rh.white.preaparc", "scripts/recon-all.done", "scripts/recon-all.log"]:
                path = subject / filename
                data, stat = path.read_bytes(), path.stat()
                path.write_bytes(b"")
                with self.subTest(filename=filename), self.assertRaises(RunConflict):
                    self.service.recover_freesurfer(run.run_id, run.workflow_revision, dry_run=False)
                path.write_bytes(data)
                os.utime(path, (stat.st_atime, stat.st_mtime))
            done = subject / "scripts/recon-all.done"
            data, stat = done.read_bytes(), done.stat()
            os.utime(done, (0, 0))
            with self.assertRaises(RunConflict):
                self.service.recover_freesurfer(run.run_id, run.workflow_revision)
            done.write_bytes(data.replace(b"-autorecon2", b"-autorecon1"))
            os.utime(done, (stat.st_atime, stat.st_mtime))
            with self.assertRaises(RunConflict):
                self.service.recover_freesurfer(run.run_id, run.workflow_revision)
            done.write_bytes(data)
            os.utime(done, (stat.st_atime, stat.st_mtime))
            marker = subject / "scripts/IsRunning.lh+rh"
            marker.write_text("running")
            with self.assertRaises(RunConflict):
                self.service.recover_freesurfer(run.run_id, run.workflow_revision, dry_run=False)
        self.assertEqual(self.service.get(run.run_id).status, "failed")

    def _workflow(self, *, execution_enabled: bool) -> Workflow:
        definition = self.capabilities.methods_for("dwi.inspect")[0]
        method = MethodOption(
            id=definition.method_id,
            skill_id=definition.skill_id,
            label=definition.label,
            description=definition.description,
            runner_id=definition.runner_id,
            execution_mode=definition.execution_mode,
            input_types=definition.input_types,
            output_types=definition.output_types,
            parameters=definition.parameters,
            input_slots=definition.input_slots,
            output_slots=definition.output_slots,
            structure_level=definition.structure_level,
        )
        parameters = {
            item.id: item.default
            for item in method.parameters
            if item.default is not None
        }
        now = datetime.now(timezone.utc).isoformat()
        return Workflow(
            workflow_id=f"workflow_{'enabled' if execution_enabled else 'disabled'}",
            template_id="dynamic:dwi.inspect",
            revision=1,
            title="数据集巡检",
            summary="执行真实的数据集结构与元数据巡检",
            original_request="检查这个数据集",
            status="confirmed",
            dataset_id=self.dataset.dataset_id,
            dataset=self.dataset,
            planner_source="test",
            execution_mode="runner",
            plan_kind="atomic",
            requested_capabilities=["dwi.inspect"],
            steps=[
                WorkflowStep(
                    id="dwi_inspect",
                    order=1,
                    title="数据集巡检",
                    description="生成数据集巡检报告",
                    selection="auto",
                    status="confirmed",
                    capability="dwi.inspect",
                    method_options=[method],
                    selected_method_id=method.id,
                    selected_parameters=parameters,
                )
            ],
            dry_run_report=DryRunReport(
                valid=True,
                execution_enabled=execution_enabled,
                dataset_path_validated=True,
                checked_at=now,
            ),
            confirmed_at=now,
            created_at=now,
            updated_at=now,
        )
