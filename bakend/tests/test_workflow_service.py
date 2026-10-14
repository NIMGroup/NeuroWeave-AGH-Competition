from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.capability_registry import CapabilityRegistry
from app.capability_tree import CapabilityTree
from app.chat_service import ChatService
from app.dataset_service import DatasetService
from app.local_store import LocalStore
from app.models import ChatRequest, ChatResponse, Message
from app.skill_loader import SkillLoader
from app.workflow_service import RevisionConflict, WorkflowService

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
SKILLS_DIR = PROJECT_ROOT / "skills"


class WorkflowServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        temporary_root = Path(self.temporary.name)
        self.dataset_folder = temporary_root / "bids-example"
        dwi_folder = self.dataset_folder / "sub-01" / "dwi"
        anat_folder = self.dataset_folder / "sub-01" / "anat"
        dwi_folder.mkdir(parents=True)
        anat_folder.mkdir(parents=True)
        (self.dataset_folder / "dataset_description.json").write_text(
            "{}", encoding="utf-8"
        )
        (dwi_folder / "sub-01_dwi.nii.gz").write_bytes(b"sample")
        (dwi_folder / "sub-01_dwi.bval").write_text("0 1000 1000", encoding="utf-8")
        (dwi_folder / "sub-01_dwi.bvec").write_text("0 1 0", encoding="utf-8")
        (dwi_folder / "sub-01_mask.nii.gz").write_bytes(b"sample")
        second_dwi_folder = self.dataset_folder / "sub-02" / "dwi"
        second_dwi_folder.mkdir(parents=True)
        (second_dwi_folder / "sub-02_dwi.nii.gz").write_bytes(b"sample")
        (second_dwi_folder / "sub-02_dwi.bval").write_text(
            "0 1000 1000", encoding="utf-8"
        )
        (second_dwi_folder / "sub-02_dwi.bvec").write_text("0 1 0", encoding="utf-8")
        (second_dwi_folder / "sub-02_mask.nii.gz").write_bytes(b"sample")

        self.previous_roots = os.environ.get("LOCALFLOW_DATA_ROOTS")
        os.environ["LOCALFLOW_DATA_ROOTS"] = str(temporary_root)
        self.store = LocalStore(temporary_root / "localflow-test.db")
        self.dataset_service = DatasetService(PROJECT_ROOT, self.store)
        root = self.dataset_service.roots()[0]
        self.dataset = self.dataset_service.register(root.root_id, "bids-example")

        chat_service = ChatService()
        chat_service.base_url = ""
        chat_service.model = ""
        self.service = WorkflowService(
            chat_service,
            CapabilityRegistry(SKILLS_DIR),
            self.store,
            self.dataset_service,
            CapabilityTree(SKILLS_DIR / "capability.tree.json"),
            SkillLoader(SKILLS_DIR),
        )

    def tearDown(self) -> None:
        if self.previous_roots is None:
            os.environ.pop("LOCALFLOW_DATA_ROOTS", None)
        else:
            os.environ["LOCALFLOW_DATA_ROOTS"] = self.previous_roots
        self.temporary.cleanup()

    def test_directory_browser_returns_files_breadcrumbs_and_root_capabilities(
        self,
    ) -> None:
        root = self.dataset_service.roots()[0]
        listing = self.dataset_service.list_directories(
            root.root_id, "bids-example/sub-01/dwi"
        )

        self.assertTrue(listing.root.readable)
        self.assertEqual(
            [item.label for item in listing.breadcrumbs],
            [root.label, "bids-example", "sub-01", "dwi"],
        )
        self.assertEqual(listing.file_count, 4)
        self.assertEqual(
            {item.name for item in listing.files},
            {
                "sub-01_dwi.nii.gz",
                "sub-01_dwi.bval",
                "sub-01_dwi.bvec",
                "sub-01_mask.nii.gz",
            },
        )
        self.assertFalse(listing.files_truncated)
        self.assertEqual(self.dataset.inspection.sample_layout, "bids")
        self.assertEqual(self.dataset.inspection.sample_count, 2)
        self.assertEqual(
            [sample.sample_id for sample in self.dataset.inspection.samples],
            ["sub-01", "sub-02"],
        )

    def test_generic_sample_names_expose_existing_dwi_t1_fod_and_5tt(self) -> None:
        root = self.dataset_service.roots()[0]
        sample = Path(root.path) / "generic-mdd" / "sample-01"
        sample.mkdir(parents=True)
        (sample / "dwi.nii.gz").write_bytes(b"dwi")
        (sample / "dwi.bval").write_text("0 1000 2000", encoding="utf-8")
        (sample / "dwi.bvec").write_text("0 1 0", encoding="utf-8")
        (sample / "dwi_preproc.mif").write_bytes(b"mif")
        (sample / "T1w_brain.nii.gz").write_bytes(b"t1")
        (sample / "wmfod_norm.mif").write_bytes(b"fod")
        (sample / "5TT.mif").write_bytes(b"5tt")

        dataset = self.dataset_service.register(root.root_id, "generic-mdd")

        self.assertTrue(dataset.inspection.has_dwi)
        self.assertTrue(dataset.inspection.has_t1w)
        self.assertTrue(dataset.inspection.has_preprocessed_dwi)
        self.assertTrue(dataset.inspection.has_fod)
        self.assertTrue(dataset.inspection.has_five_tt)
        self.assertTrue(
            {
                "raw_dwi",
                "preprocessed_dwi",
                "t1w_image",
                "fod_image",
                "five_tt_image",
            }.issubset(dataset.inspection.input_types)
        )

    def test_dataset_path_preview_infers_one_rule_for_all_bids_samples(self) -> None:
        preview = self.dataset_service.preview_sample_mapping(
            self.dataset.dataset_id,
            self.dataset.root_id,
            "bids-example/sub-01/dwi/sub-01_dwi.nii.gz",
            binding="file_bundle",
            formats=[".nii", ".nii.gz", ".mif"],
        )

        self.assertEqual(preview.binding_mode, "per_sample")
        self.assertEqual(preview.path_template, "{subject}/dwi/{subject}_dwi.nii.gz")
        self.assertEqual(preview.variables, ["subject"])
        self.assertEqual(preview.coverage.total, 2)
        self.assertEqual(preview.coverage.matched, 2)
        self.assertTrue(preview.can_apply)
        self.assertEqual(
            [match.relative_path for match in preview.matches],
            [
                "bids-example/sub-01/dwi/sub-01_dwi.nii.gz",
                "bids-example/sub-02/dwi/sub-02_dwi.nii.gz",
            ],
        )

    def test_dataset_path_preview_blocks_incomplete_sample_coverage(self) -> None:
        (self.dataset_folder / "sub-02" / "dwi" / "sub-02_dwi.nii.gz").unlink()
        preview = self.dataset_service.preview_sample_mapping(
            self.dataset.dataset_id,
            self.dataset.root_id,
            "bids-example/sub-01/dwi/sub-01_dwi.nii.gz",
            binding="file",
            formats=[".nii.gz"],
        )

        self.assertEqual(preview.coverage.matched, 1)
        self.assertEqual(preview.coverage.missing, 1)
        self.assertFalse(preview.can_apply)

    def test_directory_browser_rejects_absolute_and_parent_paths(self) -> None:
        root = self.dataset_service.roots()[0]
        with self.assertRaisesRegex(ValueError, "相对路径"):
            self.dataset_service.list_directories(root.root_id, "/etc")
        with self.assertRaisesRegex(ValueError, "上级目录"):
            self.dataset_service.list_directories(root.root_id, "../outside")
        with self.assertRaisesRegex(ValueError, "隐藏目录"):
            self.dataset_service.list_directories(root.root_id, ".ssh")

    def test_data_root_supports_an_explicit_display_label(self) -> None:
        previous = os.environ.get("LOCALFLOW_DATA_ROOTS")
        try:
            os.environ["LOCALFLOW_DATA_ROOTS"] = f"服务器根目录::{self.temporary.name}"
            service = DatasetService(PROJECT_ROOT, self.store)
            self.assertEqual(service.roots()[0].label, "服务器根目录")
        finally:
            if previous is None:
                os.environ.pop("LOCALFLOW_DATA_ROOTS", None)
            else:
                os.environ["LOCALFLOW_DATA_ROOTS"] = previous

    async def test_complete_persistent_dwi_metrics_vertical_slice(self) -> None:
        workflow, message = await self.service.draft(
            ChatRequest(
                message="请对 DWI 做预处理并计算 FA 和 MD",
                dataset_id=self.dataset.dataset_id,
            )
        )
        self.assertIn(workflow.summary, message)
        self.assertEqual(workflow.status, "awaiting_method_choice")
        self.assertEqual(workflow.dataset_id, self.dataset.dataset_id)

        hcp = next(
            method
            for step in workflow.steps
            if step.id == "dwi_preprocess"
            for method in step.method_options
            if method.id == "hcp_pipeline"
        )
        self.assertTrue(hcp.compatible)
        self.assertIn("has_t1w", hcp.input_warnings[0])

        workflow = self.service.select_method(
            workflow.workflow_id,
            "dwi_preprocess",
            "qsiprep",
            workflow.revision,
        )
        workflow = self.service.select_method(
            workflow.workflow_id,
            "dwi_tensor_metrics",
            "dipy",
            workflow.revision,
        )
        self.assertEqual(workflow.status, "chain_ready")

        workflow = self.service.lock_chain(workflow.workflow_id, workflow.revision)
        self.assertEqual(workflow.status, "awaiting_input_binding")
        for slot_id, relative_path in (
            ("dwi", "bids-example/sub-01/dwi/sub-01_dwi.nii.gz"),
            ("bvals", "bids-example/sub-01/dwi/sub-01_dwi.bval"),
            ("bvecs", "bids-example/sub-01/dwi/sub-01_dwi.bvec"),
            ("mask", "bids-example/sub-01/dwi/sub-01_mask.nii.gz"),
        ):
            workflow = self.service.bind_input(
                workflow.workflow_id,
                "dwi_tensor_metrics",
                slot_id,
                self.dataset.root_id,
                relative_path,
                workflow.revision,
            )
        self.assertEqual(workflow.status, "awaiting_parameter_review")
        self.assertTrue(workflow.can_review_parameters)

        workflow = self.service.update_parameters(
            workflow.workflow_id,
            "dwi_preprocess",
            {"output_resolution": 2.0, "denoise": False},
            workflow.revision,
        )
        workflow = self.service.review_parameters(
            workflow.workflow_id, workflow.revision
        )
        self.assertEqual(workflow.status, "awaiting_dry_run")

        workflow = self.service.dry_run(workflow.workflow_id, workflow.revision)
        self.assertEqual(workflow.status, "ready")
        self.assertTrue(workflow.dry_run_report and workflow.dry_run_report.valid)
        self.assertTrue(
            any(
                str(self.dataset_folder) in (step.command_preview or "")
                for step in workflow.steps
            )
        )

        workflow = self.service.confirm(workflow.workflow_id, workflow.revision)
        self.assertEqual(workflow.status, "confirmed")
        persisted = self.store.get_workflow(workflow.workflow_id)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.status if persisted else None, "confirmed")
        self.assertGreaterEqual(len(self.service.events(workflow.workflow_id)), 8)

    async def test_revision_conflict_returns_latest_snapshot(self) -> None:
        workflow, _message = await self.service.draft(
            ChatRequest(message="处理 DWI", dataset_id=self.dataset.dataset_id)
        )
        stale_revision = workflow.revision
        workflow = self.service.select_method(
            workflow.workflow_id,
            "dwi_preprocess",
            "qsiprep",
            workflow.revision,
        )
        with self.assertRaises(RevisionConflict) as context:
            self.service.select_method(
                workflow.workflow_id,
                "dwi_preprocess",
                "fsl_eddy",
                stale_revision,
            )
        self.assertEqual(context.exception.current.revision, workflow.revision)

    async def test_task_assessment_recommends_only_actionable_registered_workflow(
        self,
    ) -> None:
        actionable = await self.service.assess_task(
            ChatRequest(
                message="请对 DWI 做预处理并计算 FA",
                dataset_id=self.dataset.dataset_id,
                interaction_mode="task",
            )
        )
        conceptual = await self.service.assess_task(
            ChatRequest(
                message="DWI 和 DTI 有什么区别？",
                dataset_id=self.dataset.dataset_id,
                interaction_mode="task",
            )
        )
        self.assertTrue(actionable["recommend_workflow"])
        self.assertEqual(actionable["workflow_id"], "dynamic-capability-plan")
        self.assertEqual(
            actionable["capability_ids"],
            ["dwi.preprocess", "dwi.tensor_metrics"],
        )
        self.assertFalse(conceptual["recommend_workflow"])

    async def test_dataset_change_requires_unlock_and_clears_old_file_bindings(
        self,
    ) -> None:
        workflow = await self._editable_chain(
            ["dwi.tractography"],
            {"dwi.tractography": "mrtrix3-existing-fod"},
        )
        workflow = self.service.change_dataset(
            workflow.workflow_id, self.dataset.dataset_id, workflow.revision
        )
        workflow = self.service.lock_chain(workflow.workflow_id, workflow.revision)
        for slot, name in [("fod", "FOD.mif"), ("five_tt", "5TT.mif")]:
            (self.dataset_folder / name).write_bytes(b"synthetic")
            workflow = self.service.bind_input(
                workflow.workflow_id,
                workflow.steps[0].id,
                slot,
                self.dataset.root_id,
                "bids-example/" + name,
                workflow.revision,
            )
        workflow = self.service.review_parameters(
            workflow.workflow_id, workflow.revision
        )
        second_folder = self.dataset_folder.parent / "second"
        second_folder.mkdir()
        second = self.dataset_service.register(self.dataset.root_id, "second")
        with self.assertRaisesRegex(ValueError, "先解锁"):
            self.service.change_dataset(
                workflow.workflow_id, second.dataset_id, workflow.revision
            )
        self.assertEqual(self.service.get(workflow.workflow_id), workflow)
        workflow = self.service.unlock_chain(workflow.workflow_id, workflow.revision)
        previous_revision = workflow.revision
        changed = self.service.change_dataset(
            workflow.workflow_id, second.dataset_id, workflow.revision
        )
        self.assertEqual(changed.dataset_id, second.dataset_id)
        self.assertEqual(changed.dataset, second)
        self.assertIsNone(changed.parameters_reviewed_at)
        self.assertIsNone(changed.dry_run_report)
        self.assertTrue(all(not step.input_bindings for step in changed.steps))
        self.assertTrue(all(step.command_preview is None for step in changed.steps))
        self.assertEqual(
            self.service.get(changed.workflow_id).dataset_id, second.dataset_id
        )
        with self.assertRaises(RevisionConflict):
            self.service.lock_chain(changed.workflow_id, previous_revision)
        with self.assertRaises(KeyError):
            self.service.change_dataset(
                changed.workflow_id, "missing-dataset", changed.revision
            )
        self.assertEqual(self.service.get(changed.workflow_id), changed)

    async def test_dataset_rescan_refreshes_snapshot_and_confirmed_dataset_is_immutable(
        self,
    ) -> None:
        workflow, _ = await self.service.draft(
            ChatRequest(
                message="检查数据集",
                dataset_id=self.dataset.dataset_id,
                interaction_mode="task",
            ),
            {
                "recommend_workflow": True,
                "capability_ids": ["dwi.inspect"],
                "decision_source": "test",
            },
        )
        workflow = self.service.select_method(
            workflow.workflow_id,
            workflow.steps[0].id,
            "dataset_inspector",
            workflow.revision,
        )
        (self.dataset_folder / "new-file.txt").write_text("synthetic")
        rescanned = self.dataset_service.inspect(self.dataset.dataset_id)
        changed = self.service.change_dataset(
            workflow.workflow_id, rescanned.dataset_id, workflow.revision
        )
        self.assertEqual(
            changed.dataset.inspection.file_count, rescanned.inspection.file_count
        )
        self.assertGreater(changed.revision, workflow.revision)
        self.assertEqual(changed.steps[0].selected_method_id, "dataset_inspector")
        changed.status = "confirmed"
        self.store.save_workflow(changed, event_type="test_confirmed")
        with self.assertRaisesRegex(ValueError, "已确认"):
            self.service.change_dataset(
                changed.workflow_id, rescanned.dataset_id, changed.revision
            )

    async def test_dataset_inspection_routes_to_executable_runner(self) -> None:
        chat_service = ChatService()
        chat_service.base_url = ""
        chat_service.model = ""
        service = WorkflowService(
            chat_service,
            CapabilityRegistry(SKILLS_DIR),
            self.store,
            self.dataset_service,
            CapabilityTree(SKILLS_DIR / "capability.tree.json"),
            SkillLoader(SKILLS_DIR),
            {"dataset.inspect"},
        )
        request = ChatRequest(
            message="请检查这个数据集的结构和元数据",
            dataset_id=self.dataset.dataset_id,
            interaction_mode="task",
        )
        decision = await service.assess_task(request)
        self.assertEqual(decision["capability_ids"], ["dwi.inspect"])

        workflow, _message = await service.draft(request, decision)
        self.assertEqual(workflow.plan_kind, "atomic")
        self.assertEqual(workflow.steps[0].selected_method_id, "dataset_inspector")
        workflow = service.lock_chain(workflow.workflow_id, workflow.revision)
        workflow = service.review_parameters(workflow.workflow_id, workflow.revision)
        workflow = service.dry_run(workflow.workflow_id, workflow.revision)
        self.assertTrue(workflow.dry_run_report and workflow.dry_run_report.valid)
        self.assertTrue(
            workflow.dry_run_report and workflow.dry_run_report.execution_enabled
        )

    async def test_planning_skill_capability_builds_parameterized_workflow(
        self,
    ) -> None:
        decision = await self.service.assess_task(
            ChatRequest(
                message="请对 EEG 做预处理并提取特征",
                interaction_mode="task",
            )
        )
        self.assertTrue(decision["recommend_skills"])
        self.assertTrue(decision["recommend_workflow"])
        self.assertEqual(decision["capability_ids"], ["skill.eeg-skill"])
        self.assertEqual(decision["advisory_capability_ids"], [])

        workflow, _message = await self.service.draft(
            ChatRequest(
                message="请对 EEG 做预处理并提取特征",
                interaction_mode="task",
            ),
            decision,
        )
        self.assertEqual(workflow.execution_mode, "runner")
        self.assertEqual(workflow.steps[0].capability, "skill.eeg-skill")
        method = next(
            m
            for m in workflow.steps[0].method_options
            if m.runner_id.startswith("reviewed.")
        )
        self.assertEqual(method.execution_mode, "runner")
        self.assertTrue(method.source_references)
        self.assertTrue(any(slot.required for slot in method.input_slots))
        workflow = self.service.select_method(
            workflow.workflow_id, workflow.steps[0].id, method.id, workflow.revision
        )
        workflow = self.service.lock_chain(workflow.workflow_id, workflow.revision)
        self.assertEqual(workflow.status, "awaiting_input_binding")
        with self.assertRaises(ValueError):
            self.service.review_parameters(workflow.workflow_id, workflow.revision)

    def test_capability_tree_covers_every_loaded_skill(self) -> None:
        tree = self.service.capability_tree
        self.assertIsNotNone(tree)
        assert tree is not None
        loaded = {skill.name for skill in self.service.skill_loader.load_all()}
        self.assertEqual(set(tree.covered_skill_ids()), loaded)
        stats = tree.stats()
        self.assertEqual(stats["skills_covered"], len(loaded))
        self.assertGreaterEqual(stats["structured"], 86)
        self.assertEqual(stats["planning"] + stats["structured"], stats["capabilities"])
        self.assertEqual(stats["advisory"], 0)
        self.assertEqual(stats["capabilities"], len(tree.leaves()))
        self.assertEqual(
            len(self.service.registry.definitions.skills),
            len(loaded),
        )
        registry_stats = self.service.registry.stats()
        self.assertEqual(registry_stats["capabilities"], len(tree.leaves()))
        self.assertEqual(
            registry_stats["methods"], len(self.service.registry.definitions.methods)
        )
        self.assertGreaterEqual(registry_stats["planning_methods"], 169)
        self.assertEqual(registry_stats["runner_methods"], registry_stats["methods"])
        self.assertEqual(
            registry_stats["evidence_backed_methods"], registry_stats["methods"]
        )
        self.assertGreaterEqual(registry_stats["artifact_types"], 39)
        # Old Markdown-extracted command examples no longer describe capabilities.
        self.assertEqual(registry_stats["reference_commands"], 0)
        for method in self.service.registry.all_methods():
            self.assertIn(method.structure_level, {"evidence", "reviewed"})
            self.assertTrue(method.source_references, method.method_id)
            self.assertFalse(
                any(reference.executable for reference in method.command_references),
                method.method_id,
            )
        self.assertEqual(set(self.service.registry.definitions.skills), loaded)
        for skill in self.service.registry.definitions.skills.values():
            self.assertTrue(skill["description"])
            self.assertIsInstance(skill.get("selection_rules", []), list)
        for capability_id in self.service.registry.capabilities():
            node = tree.leaf(capability_id)
            self.assertIsNotNone(node)
            methods = self.service.registry.methods_for(capability_id)
            expected = (
                "structured"
                if any(method.execution_mode == "runner" for method in methods)
                else "planning"
            )
            self.assertEqual(node.execution_level if node else None, expected)

    def test_capability_tree_frontier_contains_only_direct_children(self) -> None:
        tree = self.service.capability_tree
        self.assertIsNotNone(tree)
        assert tree is not None
        frontier = tree.routing_frontier([tree.root_id])
        self.assertEqual(len(frontier), 1)
        self.assertEqual(
            {n["id"] for n in frontier[0]["candidates"]},
            {n.id for n in tree.children(tree.root_id)},
        )
        serialized = json.dumps(frontier, ensure_ascii=False)
        self.assertNotIn('"children"', serialized)
        self.assertNotIn('"skill_ids"', serialized)
        self.assertEqual(
            tree.next_hops_for(
                [tree.root_id],
                ["dwi.preprocess", "skill.eeg-skill", "skill.mne-eeg-tool"],
            ),
            ["function.preprocessing", "function.electrophysiology"],
        )

    async def test_model_routes_capabilities_one_level_at_a_time(self) -> None:
        self.service.chat_service.base_url = "https://model.invalid/v1"
        self.service.chat_service.model = "test-model"
        level_router = AsyncMock(
            side_effect=[
                {
                    "decision": "task",
                    "selected_node_ids": [
                        "function.preprocessing",
                        "function.diffusion",
                    ],
                    "task_brief": "对 DWI 做预处理并计算 FA 和 MD。",
                    "reason": "进入已结构化任务能力。",
                    "decision_source": "model",
                },
                {
                    "decision": "task",
                    "selected_node_ids": [
                        "function.preprocessing.correction",
                        "function.diffusion.tensor",
                    ],
                    "task_brief": "对 DWI 做预处理并计算 FA 和 MD。",
                    "reason": "进入结构化影像处理。",
                    "decision_source": "model",
                },
                {
                    "decision": "task",
                    "selected_node_ids": ["dwi.preprocess", "dwi.tensor_metrics"],
                    "task_brief": "对 DWI 做预处理并计算 FA 和 MD。",
                    "reason": "已定位到两个直接叶子能力。",
                    "decision_source": "model",
                },
            ]
        )
        intent_router = AsyncMock(
            return_value={
                "decision": "task",
                "task_brief": "对 DWI 做预处理并计算 FA 和 MD。",
                "candidate_capability_ids": ["dwi.preprocess", "dwi.tensor_metrics"],
                "confidence": 0.96,
                "coverage": "partial",
                "needs_clarification": False,
                "clarification_question": "",
                "reason": "识别到两个具体操作，继续检查能力树。",
                "source": "model",
            }
        )
        with (
            patch.object(
                self.service.chat_service,
                "normalize_task_intent",
                new=intent_router,
            ),
            patch.object(
                self.service.chat_service,
                "route_capability_level",
                new=level_router,
            ),
        ):
            decision = await self.service.assess_task(
                ChatRequest(
                    message="请对 DWI 做预处理并计算 FA 和 MD",
                    dataset_id=self.dataset.dataset_id,
                    interaction_mode="task",
                )
            )

        self.assertEqual(level_router.await_count, 3)
        self.assertEqual(
            decision["capability_ids"],
            ["dwi.preprocess", "dwi.tensor_metrics"],
        )
        self.assertEqual(decision["decision_source"], "model_hierarchical")
        trace = decision["routing_trace"]
        self.assertEqual(trace["strategy"], "hierarchical")
        self.assertEqual(len(trace["levels"]), 3)
        self.assertLess(trace["payload_ratio"], 1.0)
        self.assertGreater(trace["candidates_seen"], 0)
        for call in level_router.await_args_list:
            serialized = json.dumps(call.args[1], ensure_ascii=False)
            self.assertNotIn('"children"', serialized)
            self.assertNotIn('"skill_ids"', serialized)

    async def test_semantic_intent_maps_white_matter_language_to_tractography(
        self,
    ) -> None:
        request = ChatRequest(
            message="帮我重建脑白质纤维走向并估计结构连接",
            dataset_id=self.dataset.dataset_id,
            interaction_mode="task",
        )
        intent_router = AsyncMock(
            return_value={
                "decision": "task",
                "task_brief": "对 DWI 重建白质纤维路径并估计结构连接。",
                "operations": [
                    {
                        "action": "white_matter_tract_reconstruction",
                        "modality": "dwi",
                        "goal": "生成纤维流线和结构连接",
                        "inputs": ["dwi"],
                        "outputs": ["tractogram", "connectome"],
                    }
                ],
                "candidate_capability_ids": ["dwi.tractography"],
                "confidence": 0.94,
                "coverage": "complete",
                "needs_clarification": False,
                "clarification_question": "",
                "reason": "白质路径重建和结构连接属于纤维追踪。",
                "source": "model",
            }
        )
        level_router = AsyncMock()
        with (
            patch.object(
                self.service.chat_service,
                "normalize_task_intent",
                new=intent_router,
            ),
            patch.object(
                self.service.chat_service,
                "route_capability_level",
                new=level_router,
            ),
        ):
            decision = await self.service.assess_task(request)

        level_router.assert_not_awaited()
        self.assertEqual(decision["capability_ids"], ["dwi.tractography"])
        self.assertEqual(decision["decision_source"], "semantic_intent")
        self.assertEqual(decision["routing_trace"]["strategy"], "semantic")
        workflow, _content = await self.service.draft(request, decision)
        self.assertEqual(workflow.steps[0].capability, "dwi.tractography")
        self.assertTrue(
            {"mrtrix3-act-sift2--via-rebuild-fod-act-sift2", "mrtrix3-existing-fod"}
            <= {method.id for method in workflow.steps[0].method_options}
        )

    async def test_reported_tractography_wording_stays_atomic_in_fallback_mode(
        self,
    ) -> None:
        request = ChatRequest(
            message="我想进行纤维追踪",
            dataset_id=self.dataset.dataset_id,
            interaction_mode="task",
        )

        decision = await self.service.assess_task(request)

        self.assertEqual(decision["capability_ids"], ["dwi.tractography"])
        self.assertEqual(decision["decision_source"], "heuristic")
        workflow, _content = await self.service.draft(request, decision)
        self.assertEqual(workflow.plan_kind, "atomic")
        self.assertEqual(len(workflow.steps), 1)
        self.assertEqual(workflow.steps[0].capability, "dwi.tractography")

    async def test_conceptual_tractography_question_does_not_create_workflow(
        self,
    ) -> None:
        decision = await self.service.assess_task(
            ChatRequest(
                message="我想了解纤维追踪是什么",
                dataset_id=self.dataset.dataset_id,
                interaction_mode="task",
            )
        )

        self.assertFalse(decision["recommend_workflow"])
        self.assertEqual(decision["capability_ids"], [])

    async def test_generic_skill_cannot_downgrade_semantic_specific_anchor(
        self,
    ) -> None:
        allowed = [node.id for node in self.service.capability_tree.leaves()]
        intent = {
            "decision": "task",
            "task_brief": "进行 DWI 纤维追踪。",
            "candidate_capability_ids": ["dwi.tractography"],
            "confidence": 0.91,
            "coverage": "partial",
            "needs_clarification": False,
            "source": "model",
        }
        level_router = AsyncMock(
            side_effect=[
                {
                    "decision": "task",
                    "selected_node_ids": ["modality-workflows"],
                    "task_brief": intent["task_brief"],
                    "reason": "进入通用模态分支。",
                    "decision_source": "model",
                },
                {
                    "decision": "task",
                    "selected_node_ids": ["modality-pipelines"],
                    "task_brief": intent["task_brief"],
                    "reason": "继续通用模态分支。",
                    "decision_source": "model",
                },
                {
                    "decision": "task",
                    "selected_node_ids": ["skill.dwi-skill"],
                    "task_brief": intent["task_brief"],
                    "reason": "选择通用 DWI Skill。",
                    "decision_source": "model",
                },
            ]
        )
        with patch.object(
            self.service.chat_service,
            "route_capability_level",
            new=level_router,
        ):
            routing = await self.service.router.route_capabilities_hierarchically(
                ChatRequest(message="我想进行纤维追踪", interaction_mode="task"),
                allowed,
                None,
                intent,
            )

        self.assertEqual(routing["capability_ids"], ["dwi.tractography"])
        self.assertNotIn("skill.dwi-skill", routing["capability_ids"])

    async def test_explicit_freesurfer_overrides_stale_wmh_selection(self) -> None:
        request = ChatRequest(
            message="我需要用 FreeSurfer 对 T1 图像进行组织分割",
            selected_skills=["wmh-segmentation"],
            interaction_mode="task",
        )
        allowed = [node.id for node in self.service.capability_tree.leaves()]
        heuristic = self.service.selector.heuristic_capabilities(request, allowed)
        self.assertEqual(heuristic, ["skill.freesurfer-tool"])

        catalog = self.service.selector.specific_intent_catalog(allowed, heuristic)
        freesurfer_entry = next(
            item for item in catalog if item["capability_id"] == "skill.freesurfer-tool"
        )
        self.assertTrue(freesurfer_entry["task_options"])
        self.assertNotIn(
            "skill.wmh-segmentation",
            {item["capability_id"] for item in catalog},
        )

        decision = await self.service.assess_task(request)
        self.assertEqual(decision["capability_ids"], ["skill.freesurfer-tool"])
        workflow, _message = await self.service.draft(request, decision)
        self.assertEqual(
            [step.capability for step in workflow.steps], ["skill.freesurfer-tool"]
        )
        self.assertIn(
            "freesurfer-recon-all",
            {method.id for method in workflow.steps[0].method_options},
        )
        executable = next(
            method
            for method in workflow.steps[0].method_options
            if method.id == "freesurfer-recon-all"
        )
        self.assertEqual(executable.execution_mode, "runner")
        self.assertTrue(executable.recommended)

    async def test_generic_t1_tissue_segmentation_expands_relevant_methods(
        self,
    ) -> None:
        request = ChatRequest(
            message="我想对T1图像做组织分割",
            interaction_mode="task",
        )
        allowed = [node.id for node in self.service.capability_tree.leaves()]
        self.assertEqual(
            self.service.selector.heuristic_capabilities(request, allowed),
            ["skill.smri-skill"],
        )

        raw_methods = self.service.registry.methods_for("skill.smri-skill")
        selectable = self.service.registry.selectable_methods_for("skill.smri-skill")
        self.assertTrue(
            any(
                method.method_id.startswith("smri-skill-guided--")
                for method in raw_methods
            )
        )
        self.assertTrue(
            any(method.runner_id == "reviewed.fsl-tool.fast" for method in selectable)
        )
        self.assertGreater(len(selectable), 1)
        self.assertIsNone(self.service.capability_tree.node("smri.tissue-segmentation"))

        decision = await self.service.assess_task(request)
        self.assertEqual(decision["capability_ids"], ["skill.smri-skill"])
        workflow, message = await self.service.draft(request, decision)
        step = workflow.steps[0]
        self.assertEqual(step.capability, "skill.smri-skill")
        self.assertEqual(step.selection, "user")
        self.assertIsNone(step.selected_method_id)
        self.assertIn(workflow.summary, message)
        self.assertTrue(
            any(
                method.runner_id == "reviewed.fsl-tool.fast"
                for method in step.method_options
            )
        )
        self.assertNotIn(
            "WMH lesion segmentation", {method.label for method in step.method_options}
        )
        for method in step.method_options:
            operation = next(
                (p for p in method.parameters if p.id == "operation"), None
            )
            if operation is not None:
                self.assertEqual(len(operation.options), 1)
                self.assertEqual(operation.default, operation.options[0].value)

    async def test_explicit_freesurfer_english_request_overrides_stale_wmh(
        self,
    ) -> None:
        request = ChatRequest(
            message="Use FreeSurfer for T1 tissue segmentation",
            selected_skills=["wmh-segmentation"],
            interaction_mode="task",
        )
        allowed = [node.id for node in self.service.capability_tree.leaves()]

        self.assertEqual(
            self.service.selector.heuristic_capabilities(request, allowed),
            ["skill.freesurfer-tool"],
        )

    def test_sparse_retrieval_finds_true_skills_across_domains(self) -> None:
        retriever = self.service.capability_retriever
        self.assertIsNotNone(retriever)
        assert retriever is not None
        cases = [
            ("请用 fMRIPrep 做 BOLD 预处理", "skill.fmriprep-tool", "explicit"),
            ("用 MNE 对 EEG 去除伪迹", "skill.mne-eeg-tool", "explicit"),
            (
                "Use ComBat to remove scanner batch effects",
                "skill.harmonization-tool",
                "explicit",
            ),
            ("用 Nilearn 生成 ROI 功能连接矩阵", "skill.nilearn-tool", "explicit"),
            ("我要进行白质高信号 WMH 分割", "skill.wmh-segmentation", "explicit"),
            ("去除多中心扫描仪批次效应", "skill.harmonization-tool", "lexical"),
        ]
        for message, expected_id, expected_type in cases:
            with self.subTest(message=message):
                results = retriever.retrieve(message, ["wmh-segmentation"])
                self.assertTrue(results)
                self.assertEqual(results[0].capability_id, expected_id)
                self.assertEqual(results[0].match_type, expected_type)

    def test_explicit_skill_name_suppresses_stale_selected_skill_bonus(self) -> None:
        retriever = self.service.capability_retriever
        self.assertIsNotNone(retriever)
        assert retriever is not None
        results = retriever.retrieve(
            "我需要用 FreeSurfer 对 T1 图像进行组织分割",
            ["wmh-segmentation"],
            limit=100,
        )

        self.assertEqual(results[0].capability_id, "skill.freesurfer-tool")
        self.assertEqual(results[0].match_type, "explicit")
        stale = next(
            (
                item
                for item in results
                if item.capability_id == "skill.wmh-segmentation"
            ),
            None,
        )
        # A stale selection may disappear entirely when native metadata contains
        # no matching words. If recalled, it must receive only lexical weight.
        if stale is not None:
            self.assertEqual(stale.match_type, "lexical")

    def test_specific_tool_name_outranks_its_modality_skill(self) -> None:
        allowed = [node.id for node in self.service.capability_tree.leaves()]
        cases = [
            ("用 MNE 对 EEG 去除伪迹", "skill.mne-eeg-tool"),
            ("用 Nilearn 分析 fMRI 功能连接", "skill.nilearn-tool"),
            ("用 DIPY 拟合 DWI 张量", "dwi.tensor_metrics"),
        ]
        for message, expected_id in cases:
            with self.subTest(message=message):
                self.assertEqual(
                    self.service.selector.heuristic_capabilities(
                        ChatRequest(message=message, interaction_mode="task"),
                        allowed,
                    ),
                    [expected_id],
                )

    async def test_model_cannot_append_broad_modality_to_named_tool(self) -> None:
        intent_router = AsyncMock(
            return_value={
                "decision": "task",
                "task_brief": "使用 MNE 对 EEG 去伪迹并提取特征",
                "operations": [
                    {
                        "action": "去伪迹并提取特征",
                        "modality": "EEG",
                        "goal": "得到特征",
                    }
                ],
                "candidate_capability_ids": ["skill.eeg-skill", "skill.mne-eeg-tool"],
                "confidence": 0.95,
                "coverage": "complete",
                "needs_clarification": False,
                "clarification_question": "",
                "reason": "MNE 覆盖该 EEG 处理任务。",
                "source": "model_with_local_hints",
            }
        )
        with patch.object(
            self.service.chat_service,
            "normalize_task_intent",
            new=intent_router,
        ):
            decision = await self.service.assess_task(
                ChatRequest(
                    message="用 MNE 对 EEG 去除伪迹并提取特征",
                    interaction_mode="task",
                )
            )

        self.assertEqual(decision["capability_ids"], ["skill.mne-eeg-tool"])

    def test_every_skill_is_addressable_by_its_exact_id(self) -> None:
        retriever = self.service.capability_retriever
        self.assertIsNotNone(retriever)
        assert retriever is not None
        for skill in self.service.skill_loader.load_all():
            with self.subTest(skill=skill.name):
                expected = next(
                    node.id
                    for node in self.service.capability_tree.leaves()
                    if skill.name in node.skill_ids
                    and node.id.startswith(("skill.", "software."))
                )
                self.assertIn(
                    expected,
                    retriever.explicit_capability_ids(f"use {skill.name}"),
                )

    async def test_semantic_intent_receives_only_retrieved_skill_shortlist(
        self,
    ) -> None:
        intent_router = AsyncMock(
            return_value={
                "decision": "task",
                "task_brief": "使用 ComBat 去除多中心扫描仪批次效应",
                "operations": [],
                "candidate_capability_ids": ["skill.harmonization-tool"],
                "confidence": 0.95,
                "coverage": "complete",
                "needs_clarification": False,
                "clarification_question": "",
                "reason": "需求与多中心 Harmonization 能力匹配。",
                "source": "model_with_local_hints",
            }
        )
        with patch.object(
            self.service.chat_service,
            "normalize_task_intent",
            new=intent_router,
        ):
            decision = await self.service.assess_task(
                ChatRequest(
                    message="Use ComBat to remove scanner batch effects",
                    selected_skills=["wmh-segmentation"],
                    interaction_mode="task",
                )
            )

        catalog = intent_router.await_args.args[1]
        skill_ids = {
            item["capability_id"]
            for item in catalog
            if item["capability_id"].startswith("skill.")
        }
        self.assertIn("skill.harmonization-tool", skill_ids)
        self.assertNotIn("skill.wmh-segmentation", skill_ids)
        self.assertLessEqual(len(skill_ids), 8)
        self.assertEqual(decision["capability_ids"], ["skill.harmonization-tool"])
        retrieval_trace = decision["routing_trace"]["retrieval"]
        self.assertEqual(
            retrieval_trace["candidates"][0]["capability_id"],
            "skill.harmonization-tool",
        )

    async def test_ambiguous_semantic_intent_requests_clarification(self) -> None:
        intent_router = AsyncMock(
            return_value={
                "decision": "task",
                "task_brief": "处理扩散数据，但目标不明确。",
                "operations": [],
                "candidate_capability_ids": [],
                "confidence": 0.45,
                "coverage": "none",
                "needs_clarification": True,
                "clarification_question": "你希望做 DWI 预处理、张量指标计算，还是纤维追踪？",
                "reason": "存在多个不同处理目标。",
                "source": "model",
            }
        )
        with patch.object(
            self.service.chat_service,
            "normalize_task_intent",
            new=intent_router,
        ):
            decision = await self.service.assess_task(
                ChatRequest(message="帮我处理一下这些扩散数据", interaction_mode="task")
            )

        self.assertTrue(decision["clarification_required"])
        self.assertFalse(decision["recommend_workflow"])
        self.assertIn("纤维追踪", decision["clarification_question"])

    async def test_only_first_routing_level_receives_conversation_context(self) -> None:
        tree = self.service.capability_tree
        self.assertIsNotNone(tree)
        assert tree is not None
        chat_service = self.service.chat_service
        chat_service.base_url = "https://model.invalid/v1"
        chat_service.model = "test-model"
        request = ChatRequest(
            message="请继续完成 DWI 预处理",
            interaction_mode="task",
            conversation_summary="# Conversation Working Summary\n保留之前的约束",
            history=[
                Message(
                    role="user" if index % 2 == 0 else "assistant",
                    content=f"历史 {index}",
                )
                for index in range(6)
            ],
        )
        completion = AsyncMock(
            return_value=(
                '{"decision":"task","selected_node_ids":["structured-execution"],'
                '"task_brief":"继续完成 DWI 预处理","reason":"匹配"}'
            )
        )
        with patch.object(chat_service, "complete", new=completion):
            await chat_service.route_capability_level(
                request,
                tree.routing_frontier([tree.root_id]),
                None,
                level=1,
                task_brief="",
                fallback_node_ids=["structured-execution"],
                first_level=True,
            )
            first_messages = completion.await_args.args[0]
            self.assertEqual(len(first_messages), 6)
            self.assertIn("Conversation Working Summary", first_messages[0]["content"])
            self.assertEqual(first_messages[1]["content"], "历史 2")

            completion.reset_mock()
            completion.return_value = (
                '{"decision":"task","selected_node_ids":["structured-image-processing"],'
                '"task_brief":"继续完成 DWI 预处理","reason":"匹配"}'
            )
            await chat_service.route_capability_level(
                request,
                tree.routing_frontier(["structured-execution"]),
                None,
                level=2,
                task_brief="继续完成 DWI 预处理",
                fallback_node_ids=["structured-image-processing"],
                first_level=False,
            )
            later_messages = completion.await_args.args[0]
            self.assertEqual(len(later_messages), 2)
            self.assertNotIn(
                "Conversation Working Summary", later_messages[0]["content"]
            )
            self.assertEqual(
                later_messages[1]["content"],
                "Continue routing this normalized task only:\n继续完成 DWI 预处理",
            )

    async def test_intent_normalizer_rejects_invented_capability_ids(self) -> None:
        chat_service = self.service.chat_service
        chat_service.base_url = "https://model.invalid/v1"
        chat_service.model = "test-model"
        completion = AsyncMock(
            return_value=json.dumps(
                {
                    "decision": "task",
                    "task_brief": "重建白质纤维路径。",
                    "operations": [{"action": "tractography", "modality": "dwi"}],
                    "candidate_capability_ids": [
                        "invented.magic_pipeline",
                        "dwi.tractography",
                    ],
                    "confidence": 0.93,
                    "coverage": "complete",
                    "needs_clarification": False,
                    "clarification_question": "",
                    "reason": "语义对应纤维追踪。",
                },
                ensure_ascii=False,
            )
        )
        catalog = self.service.selector.specific_intent_catalog(
            [node.id for node in self.service.capability_tree.leaves()]
        )

        with patch.object(chat_service, "complete", new=completion):
            intent = await chat_service.normalize_task_intent(
                ChatRequest(message="重建白质纤维路径", interaction_mode="task"),
                catalog,
                [],
            )

        self.assertEqual(intent["candidate_capability_ids"], ["dwi.tractography"])
        self.assertEqual(intent["source"], "model")

    async def _editable_chain(self, capabilities: list[str], methods: dict[str, str]):
        workflow, _ = await self.service.draft(
            ChatRequest(message="按指定步骤分析数据", interaction_mode="task"),
            {
                "recommend_workflow": True,
                "capability_ids": capabilities,
                "decision_source": "test",
            },
        )
        for capability, method_id in methods.items():
            step = next(s for s in workflow.steps if s.capability == capability)
            workflow = self.service.select_method(
                workflow.workflow_id, step.id, method_id, workflow.revision
            )
        return workflow

    async def test_remove_producer_leaves_manual_input_and_preserves_other_bindings(
        self,
    ) -> None:
        workflow = await self._editable_chain(
            ["dwi.five_tt", "dwi.fod", "dwi.tractography"],
            {"dwi.tractography": "mrtrix3-existing-fod"},
        )
        workflow = self.service.lock_chain(workflow.workflow_id, workflow.revision)
        with self.assertRaisesRegex(ValueError, "锁定"):
            self.service.remove_step(workflow.workflow_id, "dwi_fod", workflow.revision)
        workflow = self.service.unlock_chain(workflow.workflow_id, workflow.revision)
        stale = workflow.revision
        workflow = self.service.remove_step(workflow.workflow_id, "dwi_fod", stale)
        with self.assertRaises(RevisionConflict):
            self.service.remove_step(workflow.workflow_id, "dwi_five_tt", stale)
        tracking = workflow.steps[-1]
        self.assertNotIn("fod", tracking.input_bindings)
        self.assertIn("fod_image", tracking.manual_input_types)
        self.assertEqual(
            tracking.input_bindings["five_tt"].upstream_step_id, "dwi_five_tt"
        )
        self.assertEqual(tracking.depends_on, ["dwi_five_tt"])
        self.assertEqual([step.order for step in workflow.steps], [1, 2])
        self.assertIn("dwi.fod", workflow.excluded_capabilities)
        self.assertEqual(
            workflow.requested_capabilities, ["dwi.five_tt", "dwi.tractography"]
        )
        self.assertIsNone(workflow.dry_run_report)
        self.assertIsNone(workflow.parameters_reviewed_at)
        workflow = self.service.remove_step(
            workflow.workflow_id, "dwi_five_tt", workflow.revision
        )
        self.assertEqual(workflow.steps[0].depends_on, [])
        self.assertEqual(workflow.plan_kind, "atomic")
        self.assertEqual(
            set(workflow.steps[0].manual_input_types), {"fod_image", "five_tt_image"}
        )
        with self.assertRaisesRegex(ValueError, "至少"):
            self.service.remove_step(
                workflow.workflow_id, workflow.steps[0].id, workflow.revision
            )
        workflow = self.service.lock_chain(workflow.workflow_id, workflow.revision)
        self.assertEqual(workflow.status, "awaiting_input_binding")
        self.assertFalse(workflow.can_dry_run)
        root = self.dataset_service.roots()[0].root_id
        for slot, name in [("fod", "FOD.mif"), ("five_tt", "5TT.mif")]:
            (self.dataset_folder / name).write_bytes(b"synthetic existing input")
            workflow = self.service.bind_input(
                workflow.workflow_id,
                "dwi_tractography",
                slot,
                root,
                "bids-example/" + name,
                workflow.revision,
            )
        tracking = workflow.steps[0]
        self.assertEqual(tracking.missing_required_inputs, [])
        self.assertEqual(tracking.input_bindings["fod"].source, "existing_path")
        self.assertEqual(tracking.input_bindings["five_tt"].status, "selected")
        self.assertEqual(workflow.status, "awaiting_parameter_review")
        saved = self.store.get_workflow(workflow.workflow_id)
        self.assertEqual(saved.excluded_capabilities, workflow.excluded_capabilities)
        self.assertEqual(saved.steps[0].manual_input_types, tracking.manual_input_types)

    async def test_replanning_and_dependencies_keep_the_user_edited_scope(self) -> None:
        workflow = await self._editable_chain(
            ["dwi.five_tt", "dwi.fod", "dwi.tractography"],
            {"dwi.tractography": "mrtrix3-existing-fod"},
        )
        for step in ("dwi_fod", "dwi_five_tt"):
            workflow = self.service.remove_step(
                workflow.workflow_id, step, workflow.revision
            )
        with patch.object(
            self.service,
            "assess_task",
            side_effect=AssertionError("Do not expand user-selected scope"),
        ):
            workflow, _ = await self.service.replan(
                workflow.workflow_id, workflow.revision
            )
        self.assertEqual([s.capability for s in workflow.steps], ["dwi.tractography"])
        workflow = await self.service.complete_dependencies(
            workflow.workflow_id, workflow.revision
        )
        self.assertEqual([s.capability for s in workflow.steps], ["dwi.tractography"])
        self.assertEqual(workflow.steps[0].selected_method_id, "mrtrix3-existing-fod")
        self.assertEqual(
            set(workflow.steps[0].missing_required_inputs), {"fod", "five_tt"}
        )
        self.assertEqual(
            set(workflow.excluded_capabilities), {"dwi.five_tt", "dwi.fod"}
        )
        self.assertTrue(
            any(
                m.compatible
                for m in workflow.steps[0].method_options
                if m.id == "mrtrix3-act-sift2--via-rebuild-fod-act-sift2"
            )
        )
        original_planner = self.service.chat_service.plan_capability_chain

        async def narrow_methods(*args):
            result = await original_planner(*args)
            result["method_candidates"]["dwi.tractography"] = [
                "mrtrix3-act-sift2--via-rebuild-fod-act-sift2"
            ]
            return result

        with patch.object(
            self.service.chat_service,
            "plan_capability_chain",
            side_effect=narrow_methods,
        ):
            workflow, _ = await self.service.replan(
                workflow.workflow_id, workflow.revision
            )
        self.assertEqual(workflow.steps[0].selected_method_id, "mrtrix3-existing-fod")
        bundled = next(
            m
            for m in workflow.steps[0].method_options
            if m.id == "mrtrix3-act-sift2--via-rebuild-fod-act-sift2"
        )
        self.assertTrue(bundled.compatible)
        self.assertFalse(bundled.recommended)
        self.assertTrue(any("仍包含" in reason for reason in bundled.scope_warnings))

    async def test_removed_steps_leave_methods_selectable_but_inputs_still_required(
        self,
    ) -> None:
        workflow = await self._editable_chain(
            ["dwi.fod", "dwi.tractography"],
            {"dwi.tractography": "mrtrix3-existing-fod"},
        )
        workflow = self.service.remove_step(
            workflow.workflow_id, "dwi_fod", workflow.revision
        )
        bundled = next(
            m
            for m in workflow.steps[0].method_options
            if m.id == "mrtrix3-act-sift2--via-rebuild-fod-act-sift2"
        )
        self.assertTrue(bundled.compatible)
        self.assertTrue(bundled.scope_warnings)
        workflow = self.service.select_method(
            workflow.workflow_id, "dwi_tractography", bundled.id, workflow.revision
        )
        self.assertEqual(workflow.steps[0].selected_method_id, bundled.id)
        self.assertEqual([s.capability for s in workflow.steps], ["dwi.tractography"])
        workflow = self.service.select_method(
            workflow.workflow_id,
            "dwi_tractography",
            "mrtrix3-existing-fod",
            workflow.revision,
        )
        workflow = self.service.lock_chain(workflow.workflow_id, workflow.revision)
        with self.assertRaisesRegex(ValueError, "必需输入"):
            self.service.review_parameters(workflow.workflow_id, workflow.revision)
        for slot, name in [
            ("fod", "existing_fod.mif"),
            ("five_tt", "existing_5tt.mif"),
        ]:
            (self.dataset_folder / name).write_bytes(b"synthetic existing input")
            workflow = self.service.bind_input(
                workflow.workflow_id,
                "dwi_tractography",
                slot,
                self.dataset.root_id,
                "bids-example/" + name,
                workflow.revision,
            )
        workflow = self.service.review_parameters(
            workflow.workflow_id, workflow.revision
        )
        self.assertTrue(workflow.can_dry_run)
        self.assertEqual(workflow.excluded_capabilities, ["dwi.fod"])

    async def test_history_refreshes_old_input_blocks_without_writing_or_runtime_probes(
        self,
    ) -> None:
        workflow = await self._editable_chain(["dwi.tractography"], {})
        workflow.steps[0].method_options = [
            next(
                m
                for m in workflow.steps[0].method_options
                if m.id == "mrtrix3-existing-fod"
            ),
        ]
        workflow.steps[0].method_options[0].compatible = False
        workflow.steps[0].method_options[0].blocked_reasons = [
            "上游没有产生所需输入：fod_image"
        ]
        workflow.excluded_capabilities = ["dwi.fod"]
        self.store.save_workflow(workflow, event_type="test_legacy")
        before = self.store.get_workflow(workflow.workflow_id)
        from unittest.mock import Mock

        with patch.object(
            self.service.catalog,
            "runner_registry",
            Mock(get=Mock(side_effect=AssertionError("No runtime probe"))),
        ):
            refreshed = self.service.get(workflow.workflow_id)
        self.assertTrue(
            {"mrtrix3-existing-fod", "mrtrix3-act-sift2--via-rebuild-fod-act-sift2"}
            <= {m.id for m in refreshed.steps[0].method_options}
        )
        self.assertTrue(all(m.compatible for m in refreshed.steps[0].method_options))
        self.assertEqual(self.store.get_workflow(workflow.workflow_id), before)

    async def test_requested_steps_are_ordered_without_adding_producers(self) -> None:
        requested = [
            "dwi.connectome",
            "dwi.tractography",
            "dwi.fod",
            "dwi.response",
            "dwi.preprocess",
            "dwi.five_tt",
        ]
        workflow = await self._editable_chain(requested, {})
        ids = [s.capability for s in workflow.steps]
        self.assertEqual(set(ids), set(requested))
        for before, after in [
            ("dwi.preprocess", "dwi.response"),
            ("dwi.response", "dwi.fod"),
            ("dwi.fod", "dwi.tractography"),
            ("dwi.five_tt", "dwi.tractography"),
            ("dwi.tractography", "dwi.connectome"),
        ]:
            self.assertLess(ids.index(before), ids.index(after))
        self.assertEqual([s.order for s in workflow.steps], list(range(1, 7)))
        self.assertEqual(
            [s.depends_on for s in workflow.steps],
            [[workflow.steps[i - 1].id] if i else [] for i in range(6)],
        )
        workflow = await self._editable_chain(
            ["dwi.tractography", "dwi.fod"],
            {"dwi.tractography": "mrtrix3-existing-fod"},
        )
        self.assertEqual(
            [s.capability for s in workflow.steps], ["dwi.fod", "dwi.tractography"]
        )
        self.assertEqual(
            workflow.steps[1].input_bindings["fod"].upstream_step_id,
            workflow.steps[0].id,
        )
        workflow = await self._editable_chain(
            ["skill.glm", "skill.nilearn-tool"],
            {
                "skill.nilearn-tool": "reviewed-denoise-nilearn-tool",
                "skill.glm": "reviewed-task-glm-nilearn-tool",
            },
        )
        self.assertEqual(
            [s.capability for s in workflow.steps], ["skill.nilearn-tool", "skill.glm"]
        )
        self.assertEqual(
            workflow.steps[1].input_bindings["bold"].upstream_step_id,
            workflow.steps[0].id,
        )
        workflow = await self._editable_chain(
            ["dwi.report", "dwi.fod", "dwi.inspect"], {}
        )
        self.assertEqual(
            [s.capability for s in workflow.steps],
            # These methods share no typed dependency. Their requested order
            # survives without the retired DWI manifest's positional rules.
            ["dwi.report", "dwi.fod", "dwi.inspect"],
        )

    async def test_legacy_locked_order_changes_only_after_unlock(self) -> None:
        workflow = await self._editable_chain(
            ["dwi.fod", "dwi.tractography"],
            {"dwi.tractography": "mrtrix3-existing-fod"},
        )
        workflow.steps.reverse()
        workflow.locked_at = workflow.created_at
        workflow.status = "awaiting_parameter_review"
        self.store.save_workflow(workflow, event_type="test_legacy_order")
        self.assertEqual(
            [s.capability for s in self.service.get(workflow.workflow_id).steps],
            ["dwi.tractography", "dwi.fod"],
        )
        changed = self.service.unlock_chain(workflow.workflow_id, workflow.revision)
        self.assertEqual(
            [s.capability for s in changed.steps], ["dwi.fod", "dwi.tractography"]
        )
        self.assertIsNone(changed.parameters_reviewed_at)
        self.assertIsNone(changed.dry_run_report)
        self.assertEqual(
            changed.steps[1].input_bindings["fod"].upstream_step_id, changed.steps[0].id
        )

    async def test_locked_method_contract_changes_only_after_unlock(self) -> None:
        workflow = await self._editable_chain(
            ["dwi.tractography"], {"dwi.tractography": "mrtrix3-existing-fod"}
        )
        workflow.locked_at = workflow.created_at
        workflow.status = "awaiting_parameter_review"
        original = next(
            m
            for m in workflow.steps[0].method_options
            if m.id == "mrtrix3-existing-fod"
        )
        self.store.save_workflow(workflow, event_type="test_locked_contract")
        definitions = self.service.registry.selectable_methods_for("dwi.tractography")
        changed = [m.model_copy(deep=True) for m in definitions]
        for method in changed:
            if method.method_id == original.id:
                method.label = "Updated reviewed contract"
                method.parameters = []
        with patch.object(
            self.service.registry, "selectable_methods_for", return_value=changed
        ):
            saved = self.service.get(workflow.workflow_id)
            locked = next(
                m for m in saved.steps[0].method_options if m.id == original.id
            )
            self.assertEqual(locked.parameters, original.parameters)
            self.assertEqual(locked.label, original.label)
            unlocked = self.service.unlock_chain(
                workflow.workflow_id, workflow.revision
            )
        current = next(
            m for m in unlocked.steps[0].method_options if m.id == original.id
        )
        self.assertEqual(current.label, "Updated reviewed contract")
        self.assertEqual(current.parameters, [])

    def test_unavailable_software_still_blocks_method_choice(self) -> None:
        from unittest.mock import Mock

        method = next(
            method
            for method in self.service.registry.selectable_methods_for(
                "dwi.tractography"
            )
            if method.method_id == "mrtrix3-existing-fod"
        )
        runner = Mock()
        runner.runtime_status.return_value = {
            "available": False,
            "reason": "Required software is unavailable",
        }
        with patch.object(
            self.service.catalog, "runner_registry", Mock(get=Mock(return_value=runner))
        ):
            option = self.service.catalog.option(method, set(), {}, True)
        self.assertFalse(option.compatible)
        self.assertIn("Required software is unavailable", option.blocked_reasons)

    async def test_removed_optional_generation_requires_file_and_uses_existing_source_parameter(
        self,
    ) -> None:
        workflow = await self._editable_chain(
            ["dwi.five_tt", "dwi.tractography"],
            {"dwi.tractography": "mrtrix3-act-sift2--via-rebuild-fod-act-sift2"},
        )
        workflow = self.service.remove_step(
            workflow.workflow_id, "dwi_five_tt", workflow.revision
        )
        workflow = self.service.lock_chain(workflow.workflow_id, workflow.revision)
        self.assertIn("five_tt", workflow.steps[0].missing_required_inputs)
        (self.dataset_folder / "existing_5tt.mif").write_bytes(b"synthetic")
        workflow = self.service.bind_input(
            workflow.workflow_id,
            "dwi_tractography",
            "five_tt",
            self.dataset_service.roots()[0].root_id,
            "bids-example/existing_5tt.mif",
            workflow.revision,
        )
        self.assertNotIn("five_tt_source", workflow.steps[0].selected_parameters)
        self.assertNotIn(
            "dwi.five_tt",
            [
                s.capability
                for s in self.service.state.selected_method(
                    workflow.steps[0]
                ).execution_recipe.steps
            ],
        )
        self.assertNotIn("five_tt", workflow.steps[0].missing_required_inputs)
        self.assertNotIn("t1w", workflow.steps[0].missing_required_inputs)

    async def test_removing_non_dwi_processing_keeps_glm_and_accepts_existing_bold(
        self,
    ) -> None:
        workflow = await self._editable_chain(
            ["skill.nilearn-tool", "skill.glm"],
            {
                "skill.nilearn-tool": "reviewed-denoise-nilearn-tool",
                "skill.glm": "reviewed-task-glm-nilearn-tool",
            },
        )
        producer, analysis = workflow.steps
        self.assertEqual(analysis.input_bindings["bold"].upstream_step_id, producer.id)
        workflow = self.service.remove_step(
            workflow.workflow_id, producer.id, workflow.revision
        )
        self.assertIn("bold_image", workflow.steps[0].manual_input_types)
        self.assertIn("bold", workflow.steps[0].missing_required_inputs)
        workflow = self.service.lock_chain(workflow.workflow_id, workflow.revision)
        (self.dataset_folder / "existing_bold.nii.gz").write_bytes(
            b"synthetic existing signal"
        )
        workflow = self.service.bind_input(
            workflow.workflow_id,
            analysis.id,
            "bold",
            self.dataset_service.roots()[0].root_id,
            "bids-example/existing_bold.nii.gz",
            workflow.revision,
        )
        self.assertEqual(
            workflow.steps[0].input_bindings["bold"].source, "existing_path"
        )
        with self.assertRaisesRegex(ValueError, "必需输入"):
            self.service.review_parameters(workflow.workflow_id, workflow.revision)
        with self.assertRaises(KeyError):
            self.service.remove_step("missing", analysis.id, 1)

    async def test_semantic_existing_inputs_apply_to_non_dwi_capabilities(self) -> None:
        chat = self.service.chat_service
        chat.base_url = "https://model.invalid/v1"
        chat.model = "test-model"
        request = ChatRequest(
            message="信号去噪已经完成，使用结果拟合GLM", interaction_mode="task"
        )
        payload = {
            "decision": "task",
            "candidate_capability_ids": ["skill.glm"],
            "input_capability_ids": ["skill.nilearn-tool", "not.registered"],
            "confidence": 0.9,
            "coverage": "complete",
        }
        allowed = [node.id for node in self.service.capability_tree.leaves()]
        catalog = self.service.selector.specific_intent_catalog(
            allowed, ["skill.glm", "skill.nilearn-tool"]
        )
        with patch.object(
            chat, "complete", new=AsyncMock(return_value=json.dumps(payload))
        ):
            intent = await chat.normalize_task_intent(
                request, catalog, ["skill.nilearn-tool", "skill.glm"]
            )
        self.assertEqual(intent["input_capability_ids"], ["skill.nilearn-tool"])
        self.assertEqual(intent["candidate_capability_ids"], ["skill.glm"])
        routing = await self.service.router.route_capabilities_hierarchically(
            request, allowed, None, intent
        )
        self.assertEqual(routing["capability_ids"], ["skill.glm"])
        chat.base_url = ""
        chat.model = ""
        workflow, _ = await self.service.draft(
            request, {**routing, "recommend_workflow": True}
        )
        self.assertEqual(workflow.input_capabilities, ["skill.nilearn-tool"])
        self.assertEqual([s.capability for s in workflow.steps], ["skill.glm"])
        self.assertTrue(
            any(method.compatible for method in workflow.steps[0].method_options)
        )

    async def test_existing_5tt_is_input_while_fod_is_recomputed(self) -> None:
        original = "我想在这个数据集中，根据已有的dwi数据以及5tt分割结果，重新进行fod计算以及之后的纤维追踪"
        requests = [
            original,
            "已有FOD和5tt，重新进行FOD计算并纤维追踪",
            "Recompute FOD using existing 5TT, then run tractography",
            "不要重新生成5tt，只重新计算FOD并进行纤维追踪",
        ]
        for message in requests:
            with self.subTest(message=message):
                workflow, _ = await self.service.draft(
                    ChatRequest(
                        message=message,
                        dataset_id=self.dataset.dataset_id,
                        interaction_mode="task",
                    )
                )
                self.assertEqual(
                    workflow.requested_capabilities, ["dwi.fod", "dwi.tractography"]
                )
                self.assertEqual(
                    [s.capability for s in workflow.steps],
                    ["dwi.fod", "dwi.tractography"],
                )
        workflow, _ = await self.service.draft(
            ChatRequest(
                message="已有5tt，但需要重新生成5tt，再做纤维追踪",
                interaction_mode="task",
            )
        )
        self.assertEqual(
            workflow.requested_capabilities, ["dwi.five_tt", "dwi.tractography"]
        )

    async def test_replanning_keeps_existing_5tt_binding_and_selected_tracking_method(
        self,
    ) -> None:
        for sample in ("sub-01", "sub-02"):
            (self.dataset_folder / sample / "5TT.mif").write_bytes(
                b"existing synthetic input"
            )
        workflow, _ = await self.service.draft(
            ChatRequest(
                message="根据已有5tt重新计算FOD并纤维追踪",
                dataset_id=self.dataset.dataset_id,
                interaction_mode="task",
            )
        )
        workflow = self.service.select_method(
            workflow.workflow_id,
            "dwi_tractography",
            "mrtrix3-existing-fod",
            workflow.revision,
        )
        workflow = self.service.lock_chain(workflow.workflow_id, workflow.revision)
        workflow = self.service.bind_input(
            workflow.workflow_id,
            "dwi_tractography",
            "five_tt",
            self.dataset_service.roots()[0].root_id,
            "bids-example/sub-01/5TT.mif",
            workflow.revision,
        )
        previous = workflow.steps[-1].input_bindings["five_tt"].model_dump()
        replacement, _ = await self.service.replan(
            workflow.workflow_id, workflow.revision
        )
        self.assertEqual(
            [step.capability for step in replacement.steps],
            ["dwi.fod", "dwi.tractography"],
        )
        tracking = replacement.steps[-1]
        self.assertEqual(tracking.selected_method_id, "mrtrix3-existing-fod")
        self.assertEqual(tracking.input_bindings["five_tt"].model_dump(), previous)
        self.assertEqual(tracking.input_bindings["fod"].upstream_step_id, "dwi_fod")
        self.assertIsNone(replacement.locked_at)

    async def test_complete_semantics_are_not_overridden_by_keyword_hints(self) -> None:
        chat = self.service.chat_service
        chat.base_url = "https://model.invalid/v1"
        chat.model = "test-model"
        payload = {
            "decision": "task",
            "candidate_capability_ids": ["dwi.fod", "dwi.tractography"],
            "confidence": 0.82,
            "coverage": "complete",
            "task_brief": "重新计算 FOD 并复用已有 5TT 追踪",
        }
        catalog = self.service.selector.specific_intent_catalog(
            [node.id for node in self.service.capability_tree.leaves()]
        )
        request = ChatRequest(
            message="根据已有5tt重新计算FOD，并进行纤维追踪", interaction_mode="task"
        )
        with patch.object(
            chat, "complete", new=AsyncMock(return_value=json.dumps(payload))
        ):
            intent = await chat.normalize_task_intent(
                request, catalog, ["dwi.five_tt", "dwi.report"]
            )
        self.assertEqual(
            intent["candidate_capability_ids"], ["dwi.fod", "dwi.tractography"]
        )
        self.assertEqual(intent["confidence"], 0.82)
        with patch.object(
            self.service.selector,
            "heuristic_capabilities",
            return_value=["dwi.five_tt", "dwi.report"],
        ):
            routing = await self.service.router.route_capabilities_hierarchically(
                request,
                [n.id for n in self.service.capability_tree.leaves()],
                None,
                intent,
            )
        self.assertEqual(routing["capability_ids"], ["dwi.fod", "dwi.tractography"])

    def test_existing_inputs_satisfy_dependencies_but_do_not_skip_requested_recompute(
        self,
    ) -> None:
        available = {
            "dataset_folder",
            "preprocessed_dwi",
            "response_functions",
            "fod_image",
            "five_tt_image",
        }
        self.assertEqual(
            self.service.catalog.expand_and_order_capabilities(
                ["dwi.tractography"], available
            ),
            ["dwi.tractography"],
        )
        self.assertEqual(
            self.service.catalog.expand_and_order_capabilities(
                ["dwi.fod", "dwi.tractography"], available
            ),
            ["dwi.fod", "dwi.tractography"],
        )
        from app.task_scope import existing_input_capabilities

        self.assertEqual(
            existing_input_capabilities("根据已有响应函数重新生成FOD"), {"dwi.response"}
        )
        self.assertEqual(
            existing_input_capabilities("根据已有FOD和5tt进行纤维追踪"),
            {"dwi.fod", "dwi.five_tt"},
        )

    async def test_atomic_bids_and_registration_tasks_use_capability_tree(self) -> None:
        bids, _message = await self.service.draft(
            ChatRequest(
                message="只把当前数据整理并校验成 BIDS 格式",
                dataset_id=self.dataset.dataset_id,
                interaction_mode="task",
            )
        )
        self.assertEqual(bids.plan_kind, "atomic")
        self.assertEqual(bids.requested_capabilities, ["dwi.bids"])
        self.assertEqual([step.capability for step in bids.steps], ["dwi.bids"])
        self.assertEqual(bids.steps[0].selected_method_id, "bids_organizer")

        registration, _message = await self.service.draft(
            ChatRequest(
                message="对这个数据集做图像配准",
                dataset_id=self.dataset.dataset_id,
                interaction_mode="task",
            )
        )
        self.assertEqual(registration.plan_kind, "atomic")
        self.assertEqual(registration.requested_capabilities, ["image.registration"])
        self.assertIsNone(registration.steps[0].selected_method_id)
        self.assertEqual(
            {method.id for method in registration.steps[0].method_options},
            {"fsl_flirt", "ants_registration"},
        )

    async def test_combined_request_builds_composite_skill_chain(self) -> None:
        workflow, _message = await self.service.draft(
            ChatRequest(
                message="先整理成 BIDS，然后做图像配准",
                dataset_id=self.dataset.dataset_id,
                interaction_mode="task",
            )
        )
        self.assertEqual(workflow.plan_kind, "composite")
        self.assertEqual(
            workflow.requested_capabilities,
            ["dwi.bids", "image.registration"],
        )
        self.assertEqual(
            [step.capability for step in workflow.steps],
            ["dwi.bids", "image.registration"],
        )

    async def test_dwi_preprocess_and_tractography_builds_mixed_workflow(self) -> None:
        self.assertEqual(
            self.service.selector.prune_generic_skill_leaves(
                ["dwi.preprocess", "dwi.tractography", "skill.dwi-skill"]
            ),
            ["dwi.preprocess", "dwi.tractography"],
        )
        workflow, _message = await self.service.draft(
            ChatRequest(
                message="请对 DWI 做预处理并进行纤维追踪",
                dataset_id=self.dataset.dataset_id,
                interaction_mode="task",
            )
        )
        self.assertEqual(workflow.execution_mode, "mixed")
        self.assertEqual(
            next(
                m for m in workflow.steps[0].method_options if m.id == "hcp_pipeline"
            ).execution_mode,
            "planning",
        )
        self.assertFalse(
            any("仅支持规划" in warning for warning in workflow.warnings),
            "未选择的规划候选项不应触发整个流程的警告",
        )
        self.assertFalse(
            any("反向相位编码" in warning for warning in workflow.warnings),
            "尚未选择预处理方法时不应显示方法相关的数据警告",
        )
        self.assertEqual(
            workflow.requested_capabilities,
            ["dwi.preprocess", "dwi.tractography"],
        )
        self.assertEqual(
            [step.capability for step in workflow.steps],
            ["dwi.preprocess", "dwi.tractography"],
        )
        tractography = workflow.steps[-1]
        self.assertTrue(
            {"mrtrix3-act-sift2--via-rebuild-fod-act-sift2", "mrtrix3-existing-fod"}
            <= {method.id for method in tractography.method_options}
        )
        self.assertEqual(
            next(
                method.execution_mode
                for method in tractography.method_options
                if method.id == "mrtrix3-existing-fod"
            ),
            "runner",
        )
        self.assertIn(
            "streamline_count",
            {
                parameter.id
                for method in tractography.method_options
                for parameter in method.parameters
            },
        )
        mrtrix = next(
            method
            for method in tractography.method_options
            if method.id == "mrtrix3-act-sift2--via-rebuild-fod-act-sift2"
        )
        self.assertEqual(
            [stage.capability for stage in mrtrix.execution_recipe.steps],
            ["dwi.response", "dwi.fod", "dwi.tractography", "dwi.tractography"],
        )
        self.assertNotIn(
            "sift_target_count", {parameter.id for parameter in mrtrix.parameters}
        )
        self.assertEqual(
            mrtrix.execution_recipe.steps[-1].method_id, "mrtrix.command.tcksift2"
        )

    async def test_tractography_stays_target_only_until_dependencies_are_requested(
        self,
    ) -> None:
        workflow, _message = await self.service.draft(
            ChatRequest(
                message="只做纤维追踪",
                dataset_id=self.dataset.dataset_id,
                interaction_mode="task",
            )
        )
        self.assertEqual(workflow.requested_capabilities, ["dwi.tractography"])
        self.assertEqual(
            [step.capability for step in workflow.steps], ["dwi.tractography"]
        )
        self.assertEqual(workflow.dependency_policy, "target_only")

        workflow = self.service.select_method(
            workflow.workflow_id,
            "dwi_tractography",
            "mrtrix3-existing-fod",
            workflow.revision,
        )
        self.assertEqual(workflow.status, "chain_ready")
        self.assertTrue(workflow.can_lock)
        self.assertIn("fod", workflow.steps[0].missing_required_inputs)

        with self.assertRaisesRegex(ValueError, "确定流程链"):
            self.service.update_parameters(
                workflow.workflow_id,
                "dwi_tractography",
                {"act_enabled": False},
                workflow.revision,
            )

        workflow = self.service.lock_chain(workflow.workflow_id, workflow.revision)
        self.assertEqual(workflow.status, "awaiting_input_binding")
        self.assertFalse(workflow.can_review_parameters)

        workflow = self.service.update_parameters(
            workflow.workflow_id,
            "dwi_tractography",
            {"act_enabled": False},
            workflow.revision,
        )
        self.assertEqual(workflow.steps[0].missing_required_inputs, ["fod"])
        self.assertIn("step_size", workflow.steps[0].selected_parameters)

        workflow = await self.service.complete_dependencies(
            workflow.workflow_id,
            workflow.revision,
        )
        self.assertEqual(workflow.dependency_policy, "complete")
        self.assertEqual(
            [step.capability for step in workflow.steps],
            [
                "dwi.preprocess",
                "dwi.response",
                "dwi.fod",
                "dwi.tractography",
            ],
        )
        self.assertEqual(workflow.steps[0].origin, "dependency")
        self.assertTrue(
            all(step.origin == "dependency" for step in workflow.steps[:-1])
        )
        self.assertEqual(workflow.steps[-1].origin, "requested")
        self.assertEqual(workflow.steps[-1].selected_method_id, "mrtrix3-existing-fod")
        self.assertIsNone(workflow.locked_at)

        workflow = self.service.select_method(
            workflow.workflow_id,
            "dwi_preprocess",
            "qsiprep",
            workflow.revision,
        )
        tractography = workflow.steps[-1]
        binding = tractography.input_bindings["fod"]
        self.assertEqual(binding.source, "upstream_output")
        self.assertEqual(binding.upstream_step_id, "dwi_fod")
        self.assertEqual(workflow.status, "chain_ready")

    async def test_workflow_persists_per_sample_input_binding(self) -> None:
        workflow, _message = await self.service.draft(
            ChatRequest(
                message="只做纤维追踪",
                dataset_id=self.dataset.dataset_id,
                interaction_mode="task",
            )
        )
        workflow = self.service.select_method(
            workflow.workflow_id,
            "dwi_tractography",
            "mrtrix3-existing-fod",
            workflow.revision,
        )
        workflow = self.service.lock_chain(workflow.workflow_id, workflow.revision)
        workflow = self.service.update_parameters(
            workflow.workflow_id,
            "dwi_tractography",
            {"act_enabled": False},
            workflow.revision,
        )

        for subject in ["sub-01", "sub-02"]:
            (
                self.dataset_folder / subject / "dwi" / (subject + "_fod.nii.gz")
            ).write_bytes(b"synthetic fod")
        preview = self.service.preview_input_mapping(
            workflow.workflow_id,
            "dwi_tractography",
            "fod",
            self.dataset.root_id,
            "bids-example/sub-01/dwi/sub-01_fod.nii.gz",
        )
        self.assertEqual(preview.coverage.matched, 2)

        workflow = self.service.bind_input(
            workflow.workflow_id,
            "dwi_tractography",
            "fod",
            self.dataset.root_id,
            "bids-example/sub-01/dwi/sub-01_fod.nii.gz",
            workflow.revision,
        )
        binding = workflow.steps[0].input_bindings["fod"]
        self.assertEqual(binding.binding_mode, "per_sample")
        self.assertEqual(binding.path_template, "{subject}/dwi/{subject}_fod.nii.gz")
        self.assertEqual(binding.coverage.matched if binding.coverage else 0, 2)
        self.assertEqual(len(binding.sample_matches), 2)
        self.assertEqual(workflow.steps[0].missing_required_inputs, [])
        self.assertTrue(workflow.can_review_parameters)

    async def test_chat_mode_answers_without_a_workflow(self) -> None:
        from app import main as main_module

        expected = ChatResponse(
            content="普通对话回复",
            provider_used="NeuroWeave",
            model_used="conversation-demo",
            mode="demo",
            selected_skills=[],
            interaction_mode="chat",
        )
        assessment = AsyncMock(
            return_value={
                "recommend_workflow": False,
                "decision_source": "conversation",
            }
        )
        reply = AsyncMock(return_value=expected)
        with (
            patch.object(
                main_module.app.state.services.workflow_service,
                "assess_task",
                new=assessment,
            ),
            patch.object(
                main_module.app.state.services.chat_service,
                "reply",
                new=reply,
            ),
        ):
            response = await main_module.app.state.services.chat_coordinator.reply(
                ChatRequest(
                    message="请对 DWI 做预处理并计算 FA",
                    interaction_mode="chat",
                )
            )

        assessment.assert_awaited_once()
        reply.assert_awaited_once()
        self.assertEqual(response.type, "done")
        self.assertIsNone(response.workflow)

    async def test_warnings_follow_selected_method_and_parameters(self) -> None:
        workflow, _message = await self.service.draft(
            ChatRequest(message="只做 DWI 预处理", dataset_id=self.dataset.dataset_id)
        )
        self.assertFalse(any("反向相位编码" in item for item in workflow.warnings))

        workflow = self.service.select_method(
            workflow.workflow_id,
            "dwi_preprocess",
            "fsl_eddy",
            workflow.revision,
        )
        self.assertTrue(
            any(
                "FSL topup / eddy" in item and "仅按文件名未识别到 AP/PA 配对" in item
                for item in workflow.warnings
            )
        )

        workflow = self.service.lock_chain(workflow.workflow_id, workflow.revision)
        workflow = self.service.update_parameters(
            workflow.workflow_id,
            "dwi_preprocess",
            {"susceptibility_correction": "none"},
            workflow.revision,
        )
        self.assertFalse(any("反向相位编码" in item for item in workflow.warnings))

    async def test_unlock_allows_method_revision(self) -> None:
        workflow, _message = await self.service.draft(
            ChatRequest(message="只做 DWI 预处理", dataset_id=self.dataset.dataset_id)
        )
        workflow = self.service.select_method(
            workflow.workflow_id,
            "dwi_preprocess",
            "qsiprep",
            workflow.revision,
        )
        workflow = self.service.lock_chain(workflow.workflow_id, workflow.revision)
        workflow = self.service.unlock_chain(workflow.workflow_id, workflow.revision)
        self.assertEqual(workflow.status, "chain_ready")
        workflow = self.service.select_method(
            workflow.workflow_id,
            "dwi_preprocess",
            "fsl_eddy",
            workflow.revision,
        )
        preprocess = next(
            step for step in workflow.steps if step.id == "dwi_preprocess"
        )
        self.assertEqual(preprocess.selected_method_id, "fsl_eddy")

    def test_dataset_root_rejects_path_escape(self) -> None:
        root = self.dataset_service.roots()[0]
        with self.assertRaisesRegex(ValueError, "超出授权|上级目录"):
            self.dataset_service.list_directories(root.root_id, "../")


if __name__ == "__main__":
    unittest.main()
