from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock

import httpx
import test_workflow_service as fixtures
from app.method_planning import (
    MethodPlan,
    validate_plan,
    validate_workflow_scope,
)
from app.models import CapabilityRoutingTrace, ChatRequest
from app.task_contract import PlanNotMatched, TaskContract
from app.workflow_models import InputBinding

MESSAGE = "这个数据集中，我想只用其中预处理好的dwi数据以及bval和bvec以及mask，其他的都不用，最终我想得到全脑纤维，给我一个流程"


def contract():
    return TaskContract(
        goal="只从指定的预处理 DWI 重建全脑纤维",
        goal_artifact_types=["tractogram"],
        input_policy="only_listed",
        plan_scope="goal_from_inputs",
        allowed_existing_inputs=[
            {"artifact_type": t}
            for t in ["preprocessed_dwi", "gradient_table", "brain_mask"]
        ],
        forbidden_existing_inputs=[
            {"artifact_type": t} for t in ["fod_image", "five_tt_image"]
        ],
    )


def graph():
    return MethodPlan(
        status="matched",
        steps=[
            {
                "id": "response",
                "method_id": "mrtrix.dwi2response",
                "parameters": {"algorithm": "dhollander", "gradient_source":"fsl"},
            },
            {
                "id": "fod",
                "method_id": "mrtrix.dwi2fod",
                "parameters": {"algorithm": "msmt_csd", "gradient_source":"fsl"},
                "inputs": {
                    k: {"step_id": "response", "output_slot_id": k}
                    for k in ["wm_response", "gm_response", "csf_response"]
                },
            },
            {
                "id": "tracking",
                "method_id": "mrtrix.tckgen",
                "parameters": {"act_enabled": False},
                "inputs": {"fod": {"step_id": "fod", "output_slot_id": "wm_fod"}},
            },
        ],
    )


class TaskContractPlanningTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.WorkflowServiceTests.setUp(self)

    def tearDown(self):
        fixtures.WorkflowServiceTests.tearDown(self)

    def model_service(self):
        chat = self.service.chat_service
        chat.base_url, chat.model = "http://model.invalid", "test"
        return chat

    def intent(self):
        return {
            "decision": "task",
            "candidate_capability_ids": ["dwi.tractography"],
            "input_capability_ids": ["dwi.preprocess"],
            "coverage": "complete",
            "confidence": 0.96,
            "source": "model",
            "task_contract": contract().model_dump(),
        }

    async def test_exact_failure_is_not_promoted_to_a_preprocessing_plan(self):
        chat = self.model_service()
        chat.complete = AsyncMock(
            side_effect=RuntimeError("secret-like error must not be logged")
        )
        with self.assertLogs("app.llm.json_response", level="WARNING") as logs:
            result = await self.service.assess_task(
                ChatRequest(message=MESSAGE, interaction_mode="task")
            )
        self.assertEqual(chat.complete.await_count, 2)
        self.assertFalse(result["recommend_workflow"])
        self.assertTrue(result["clarification_required"])
        self.assertLess(result["routing_trace"]["confidence"], 0.5)
        self.assertEqual(len(result["routing_trace"]["diagnostics"]), 2)
        self.assertNotIn("secret-like", "\n".join(logs.output))

    async def test_malformed_intent_retries_then_preserves_scope(self):
        chat = self.model_service()
        chat.complete = AsyncMock(side_effect=["{broken", json.dumps(self.intent())])
        request = ChatRequest(message=MESSAGE, interaction_mode="task")
        result = await self.service.assess_task(request)
        self.assertEqual(result["capability_ids"], ["dwi.tractography"])
        self.assertEqual(request.task_contract.input_policy, "only_listed")
        self.assertEqual(len(result["routing_trace"]["diagnostics"]), 1)
        self.assertIn(
            "task_contract",
            CapabilityRoutingTrace.model_validate(result["routing_trace"]).model_dump(),
        )

    async def test_authentication_failure_does_not_retry(self):
        chat = self.model_service()
        response = httpx.Response(
            401, request=httpx.Request("POST", "http://model.invalid")
        )
        chat.complete = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "bad", request=response.request, response=response
            )
        )
        result = await self.service.assess_task(ChatRequest(message=MESSAGE))
        self.assertFalse(result["recommend_workflow"])
        self.assertEqual(chat.complete.await_count, 1)

    async def test_model_cannot_omit_task_constraints_and_return_a_confident_guess(
        self,
    ):
        chat = self.model_service()
        payload = self.intent()
        payload.pop("task_contract")
        chat.complete = AsyncMock(return_value=json.dumps(payload))
        result = await self.service.assess_task(ChatRequest(message=MESSAGE))
        self.assertFalse(result["recommend_workflow"])
        self.assertEqual(chat.complete.await_count, 2)

    async def test_missing_typed_output_is_reported_as_contract_gap_not_missing_software(
        self,
    ):
        # Simulate a genuinely missing typed producer; the real catalog now has FreeSurfer.
        self.service.method_index.refresh()
        self.service.method_index.producers.pop("parcellation_image", None)
        request = ChatRequest(
            message="没有脑分区，需要生成脑分区",
            interaction_mode="task",
            task_contract=TaskContract(
                goal="生成脑分区", goal_artifact_types=["parcellation_image"]
            ),
        )
        chat = self.service.chat_service
        chat.plan_registered_methods = AsyncMock(
            return_value=(MethodPlan(status="candidate_mismatch"), [])
        )
        chat.search_library_semantically = AsyncMock(
            return_value=(["freesurfer.recon_all"], [])
        )
        with self.assertRaises(PlanNotMatched) as caught:
            await self.service.planner.build_contract_plan(
                request, {"capability_ids": ["dwi.connectome"]}
            )
        self.assertIn("输出接口", str(caught.exception))
        self.assertIn("软件或命令可能已接入", str(caught.exception))
        self.assertEqual(
            caught.exception.diagnostics["unregistered_goal_outputs"],
            ["parcellation_image"],
        )
        self.assertGreater(
            caught.exception.diagnostics["searches"][-1]["indexed_methods"], 300
        )

    def test_output_index_recovers_all_three_methods_from_wrong_tree_branch(self):
        ids, trace = self.service.method_index.retrieve(contract(), ["dwi.preprocess"])
        self.assertTrue(
            {"mrtrix.dwi2response", "mrtrix.dwi2fod", "mrtrix.tckgen"}.issubset(ids)
        )
        self.assertGreater(trace["indexed_methods"], trace["returned_candidates"])

    def test_retrieval_budget_is_visible_not_reported_as_absence(self):
        ids, trace = self.service.method_index.retrieve(contract(), [], limit=1)
        self.assertEqual(len(ids), 1)
        self.assertTrue(trace["truncated"])
        self.assertEqual(trace["missing_goal_types"], [])

    def test_output_goal_can_correct_a_wrong_tree_leaf_without_extra_producers(self):
        scope = TaskContract(goal="生成纤维束", goal_artifact_types=["tractogram"])
        plan = MethodPlan(
            status="matched",
            steps=[
                {
                    "id": "tracking",
                    "method_id": "mrtrix.tckgen",
                    "parameters": {"act_enabled": False},
                }
            ],
        )
        validate_plan(
            plan,
            scope,
            self.service.registry,
            requested_capabilities=["dwi.preprocess"],
        )
        with self.assertRaisesRegex(ValueError, "未请求"):
            validate_plan(
                graph(),
                scope,
                self.service.registry,
                requested_capabilities=["dwi.tractography"],
            )

    def test_valid_generated_intermediates_do_not_reuse_forbidden_old_files(self):
        compiled = validate_plan(graph(), contract(), self.service.registry)
        self.assertEqual(len(compiled["steps"]), 3)
        self.assertEqual(
            compiled["steps"][1]["input_bindings"]["gm_response"]["output_slot_id"],
            "gm_response",
        )

    def test_specific_gradient_subtypes_are_accepted_without_conflating_permissions(
        self,
    ):
        scope = contract()
        scope.allowed_existing_inputs = [
            item
            for item in scope.allowed_existing_inputs
            if item.artifact_type != "gradient_table"
        ]
        from app.task_contract import ArtifactConstraint

        scope.allowed_existing_inputs += [
            ArtifactConstraint(artifact_type="b_values"),
            ArtifactConstraint(artifact_type="b_vectors"),
        ]
        validate_plan(graph(), scope, self.service.registry)
        scope.allowed_existing_inputs.pop()
        with self.assertRaisesRegex(ValueError, "bvecs"):
            validate_plan(graph(), scope, self.service.registry)

    def test_dag_order_is_derived_from_edges_and_cycles_fail(self):
        payload = graph().model_dump()
        payload["steps"].reverse()
        self.assertEqual(
            [s.id for s in MethodPlan.model_validate(payload).steps],
            ["response", "fod", "tracking"],
        )
        payload["steps"][-1]["inputs"] = {
            "dwi": {"step_id": "tracking", "output_slot_id": "tractogram"}
        }
        with self.assertRaisesRegex(ValueError, "环"):
            MethodPlan.model_validate(payload)

    def test_forbidden_old_fod_cannot_replace_response_and_fod_steps(self):
        plan = graph()
        plan.steps = [plan.steps[-1]]
        plan.steps[0].inputs = {}
        with self.assertRaisesRegex(ValueError, "未获允许"):
            validate_plan(plan, contract(), self.service.registry)

    def test_default_act_cannot_silently_require_5tt(self):
        plan = graph()
        plan.steps[-1].parameters = {}
        with self.assertRaisesRegex(ValueError, "five_tt"):
            validate_plan(plan, contract(), self.service.registry)

    def test_conditional_response_output_must_actually_be_generated(self):
        plan = graph()
        plan.steps[0].parameters["algorithm"] = "tournier"
        with self.assertRaisesRegex(ValueError, "不会生成"):
            validate_plan(plan, contract(), self.service.registry)

    def test_goal_and_unnecessary_steps_are_checked(self):
        plan = graph()
        plan.steps = plan.steps[:2]
        with self.assertRaisesRegex(ValueError, "没有生成"):
            validate_plan(plan, contract(), self.service.registry)
        plan = graph()
        extra = plan.steps[0].model_copy(deep=True, update={"id": "unused"})
        plan.steps.insert(0, extra)
        with self.assertRaisesRegex(ValueError, "额外步骤"):
            validate_plan(plan, contract(), self.service.registry)

    def test_fake_methods_parameters_and_bad_edges_fail(self):
        for mutation in ("method", "parameter", "edge"):
            with self.subTest(mutation=mutation):
                plan = graph()
                if mutation == "method":
                    plan.steps[0].method_id = "invented.method"
                elif mutation == "parameter":
                    plan.steps[0].parameters["invented_flag"] = True
                else:
                    plan.steps[-1].inputs["fod"].output_slot_id = "log"
                with self.assertRaises((ValueError, KeyError)):
                    validate_plan(plan, contract(), self.service.registry)

    async def test_no_match_is_not_replaced_by_first_candidate(self):
        chat = self.model_service()
        for payload in (
            {"status": "candidate_mismatch"},
            {"recommendations": {"dwi.preprocess": None}},
        ):
            chat.complete = AsyncMock(return_value=json.dumps(payload))
            result = await chat.plan_capability_chain(
                ChatRequest(message=MESSAGE),
                self.service.catalog.candidate_context(["dwi.preprocess"]),
                None,
            )
            self.assertEqual(result["status"], "candidate_mismatch")
            self.assertFalse(any(result["recommendations"].values()))

    async def test_schema_retry_explains_bad_external_inputs_without_logging_content(
        self,
    ):
        chat = self.model_service()
        invalid = graph().model_dump()
        invalid["steps"][0]["inputs"] = {
            "dwi": {"source": "dataset", "path": "/private/input"}
        }
        chat.complete = AsyncMock(
            side_effect=[json.dumps(invalid), graph().model_dump_json()]
        )
        plan, diagnostics = await chat.plan_registered_methods(
            ChatRequest(message=MESSAGE, task_contract=contract()), []
        )
        self.assertEqual(plan.status, "matched")
        self.assertTrue(diagnostics[0]["schema_errors"])
        second = chat.complete.await_args_list[1].args[0]
        self.assertIn("did not validate", second[-1]["content"])
        self.assertNotIn("/private", json.dumps(diagnostics))

    async def test_bounded_widening_and_no_persisted_wrong_workflow(self):
        self.service.chat_service.plan_registered_methods = AsyncMock(
            return_value=(MethodPlan(status="candidate_mismatch"), [])
        )
        request = ChatRequest(message=MESSAGE, task_contract=contract())
        with self.assertRaises(PlanNotMatched) as error:
            await self.service.draft(
                request,
                {"recommend_workflow": True, "capability_ids": ["dwi.preprocess"]},
            )
        self.assertEqual(len(error.exception.diagnostics["searches"]), 2)
        self.assertEqual(self.service.list(), [])

    async def test_end_to_end_graph_survives_reload_lock_and_parameter_changes(self):
        self.service.chat_service.plan_registered_methods = AsyncMock(
            return_value=(graph(), [])
        )
        request = ChatRequest(
            message=MESSAGE,
            dataset_id=self.dataset.dataset_id,
            task_contract=contract(),
        )
        workflow, text = await self.service.draft(
            request,
            {"recommend_workflow": True, "capability_ids": ["dwi.tractography"]},
        )
        self.assertEqual(
            [s.selected_method_id for s in workflow.steps],
            ["mrtrix.dwi2response", "mrtrix.dwi2fod", "mrtrix.tckgen"],
        )
        self.assertEqual(workflow.contract_errors, [])
        self.assertEqual(
            workflow.steps[1].input_bindings["wm_response"].output_slot_id,
            "wm_response",
        )
        self.assertEqual(
            workflow.steps[1].input_bindings["csf_response"].output_slot_id,
            "csf_response",
        )
        self.assertNotIn("eddy", text)
        reloaded = self.service.get(workflow.workflow_id)
        self.assertEqual(reloaded.task_contract, contract())
        locked = self.service.lock_chain(reloaded.workflow_id, reloaded.revision)
        self.assertEqual(
            locked.steps[1].input_bindings["gm_response"].output_slot_id, "gm_response"
        )
        locked.steps[-1].selected_parameters["act_enabled"] = True
        self.service.state.refresh(locked)
        self.assertTrue(locked.contract_errors)
        self.assertFalse(locked.can_dry_run)

    async def test_input_paths_enforced_after_planning(self):
        self.service.chat_service.plan_registered_methods = AsyncMock(
            return_value=(graph(), [])
        )
        scope = contract()
        scope.allowed_existing_inputs[0].paths = ["dwi_preproc.mif"]
        workflow, _ = await self.service.draft(
            ChatRequest(message=MESSAGE, task_contract=scope),
            {"recommend_workflow": True, "capability_ids": ["dwi.tractography"]},
        )
        workflow.steps[0].input_bindings["dwi"] = InputBinding(
            slot_id="dwi",
            artifact_type="preprocessed_dwi",
            source="existing_path",
            absolute_path="/dataset/raw_dwi.nii.gz",
        )
        self.assertTrue(validate_workflow_scope(workflow, self.service.registry))

    def test_non_dwi_external_scope_is_not_a_keyword_special_case(self):
        scope = TaskContract(
            goal="从已处理的 BOLD 做分析",
            input_policy="only_listed",
            allowed_existing_inputs=[
                {"artifact_type": "bold_image", "paths": ["cleaned.nii.gz"]}
            ],
            forbidden_existing_inputs=[{"artifact_type": "t1w_image"}],
        )
        self.assertTrue(
            scope.allows_external("bold_image", "/data/sub-01/cleaned.nii.gz")
        )
        self.assertFalse(scope.allows_external("bold_image", "/data/sub-01/raw.nii.gz"))
        self.assertFalse(scope.allows_external("t1w_image", "/data/T1.nii.gz"))

    def direct_request(self):
        from app.workflows.assistant_bindings import AssistantInput
        request=ChatRequest(message=MESSAGE,dataset_id=self.dataset.dataset_id,task_contract=contract())
        request._registered_plan=graph()
        self.service.chat_service.plan_registered_methods=AsyncMock(side_effect=AssertionError('Second planning model is unnecessary'))
        paths={'dwi':'sub-01/dwi/sub-01_dwi.nii.gz','bvals':'sub-01/dwi/sub-01_dwi.bval',
               'bvecs':'sub-01/dwi/sub-01_dwi.bvec','mask':'sub-01/dwi/sub-01_mask.nii.gz'}
        # Use actual port contracts rather than assume every algorithm has the same inputs.
        specs=[]
        for step in graph().steps:
            method=self.service.registry.method_for('method',step.method_id)
            for slot in method.input_slots:
                if slot.id in paths and slot.id not in step.inputs:
                    specs.append(AssistantInput(method_id=step.method_id,slot_id=slot.id,path=paths[slot.id]))
        request._workflow_input_bindings=specs
        return request

    async def test_verified_graph_binds_all_samples_without_second_model(self):
        request=self.direct_request()
        workflow,_=await self.service.draft(request,{'recommend_workflow':True,'capability_ids':[]})
        self.service.chat_service.plan_registered_methods.assert_not_awaited()
        self.assertTrue(all(not step.missing_required_inputs for step in workflow.steps))
        reports=workflow.planning_diagnostics['input_bindings']
        self.assertTrue(reports and all(v['validation']=='pending_dry_run' for v in reports))
        from app.workflows.preflight_inputs import resolve_planned_inputs
        reports=resolve_planned_inputs(workflow,self.dataset_service)
        self.assertTrue(reports and all(v['coverage']['matched']==v['coverage']['total']==2 for v in reports))
        self.assertEqual(workflow.planning_diagnostics['strategy'],'assistant_registered_graph')
        reloaded=self.service.get(workflow.workflow_id)
        self.assertTrue(all(not step.missing_required_inputs for step in reloaded.steps))
        self.assertEqual(self.store.list_runs(workflow_id=workflow.workflow_id),[])

    async def test_missing_subject_input_rejects_draft_before_saving(self):
        request=self.direct_request()
        request.task_contract.sample_policy='all'
        (self.dataset_folder/'sub-02/dwi/sub-02_dwi.bval').unlink()
        workflow,_=await self.service.draft(request,{'recommend_workflow':True,'capability_ids':[]})
        from app.workflows.preflight_inputs import resolve_planned_inputs
        with self.assertRaisesRegex(ValueError,'未覆盖全部样本'):
            resolve_planned_inputs(workflow,self.dataset_service)
        self.assertFalse(workflow.can_confirm)
        self.assertFalse(self.store.list_runs(workflow_id=workflow.workflow_id))

    async def test_invalid_graph_never_falls_back_to_a_different_goal(self):
        request=self.direct_request()
        request._registered_plan.steps[-1].method_id='unknown.method'
        with self.assertRaises(PlanNotMatched):
            await self.service.draft(request,{'recommend_workflow':True,'capability_ids':[]})
        self.service.chat_service.plan_registered_methods.assert_not_awaited()
        self.assertEqual(self.service.list(),[])
