import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.capability_registry import CapabilityRegistry
from app.execution.runners.mrtrix_commands import command_runners
from app.models import ChatRequest
from app.parameter_rules import (
    active,
    conflicts,
    effective_parameters,
    matches,
    required,
)
from app.skill_catalog import SKILL_TOOLS, SkillCatalog
from app.skill_loader import SkillLoader
from app.workflow_models import InputBinding, MethodOption, ParameterSpec, WorkflowStep
from app.workflows.contracts import validate_method_parameters
from app.workspace_tools import WorkspaceTools

ROOT = Path(__file__).resolve().parents[2]


class SkillLayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = CapabilityRegistry(ROOT / "skills")
        cls.loader = SkillLoader(ROOT / "skills")
        cls.runners = {r.runner_id: r for r in command_runners()}
        cls.catalog = SkillCatalog(
            cls.loader, cls.registry, SimpleNamespace(get=cls.runners.get)
        )

    def test_index_is_bounded_and_does_not_load_parameters_or_probe_runtime(self):
        with patch(
            "app.execution.runners.mrtrix_commands.version_probe",
            side_effect=AssertionError("unexpected probe"),
        ):
            first = self.catalog.list_methods("dwi-skill", limit=2)
            second = self.catalog.list_methods(
                "dwi-skill", cursor=first["next_cursor"], limit=2
            )
        self.assertEqual(len(first["items"]), 2)
        self.assertNotEqual(
            first["items"][0]["method_id"], second["items"][0]["method_id"]
        )
        self.assertNotIn("parameters", first["items"][0])
        self.assertNotIn("instructions", first["items"][0])
        with self.assertRaises(ValueError):
            self.catalog.list_methods(cursor="../private")
        with self.assertRaises(ValueError):
            self.catalog.list_methods(limit=2000)

    def test_method_sections_and_reference_are_bounded_and_identity_scoped(self):
        result = self.catalog.method(
            "skill.dwi-skill", "mrtrix.command.tcksift2", "parameters", limit=2
        )
        self.assertEqual(len(result["items"]), 2)
        self.assertTrue(result["has_more"])
        reference = self.catalog.read_reference("dwi-skill", max_chars=40)
        self.assertEqual(len(reference["text"]), 40)
        self.assertTrue(reference["truncated"])
        with self.assertRaises(KeyError):
            self.catalog.read_reference("../.env")
        with self.assertRaises(KeyError):
            self.catalog.method("missing", "tckgen")

    def test_pipeline_readiness_uses_real_member_runners_and_planning_is_not_executable(
        self,
    ):
        definition = self.registry.method_for(
            "dwi.tractography", "mrtrix3-act-sift2--via-existing-fod-act-sift2"
        )
        self.assertEqual(
            self.catalog.execution_runner_ids([definition]),
            ["mrtrix.command.tcksift2", "mrtrix.tckgen"],
        )
        self.assertEqual(
            self.catalog.list_methods(kind="capability", executable_only=True)["total"],
            0,
        )
        pipeline = self.catalog.pipeline(definition.definition_id)
        self.assertEqual(
            [s["method_ref"] for s in pipeline["steps"]],
            ["mrtrix.tckgen", "mrtrix.command.tcksift2"],
        )

    def test_explicit_skill_root_is_not_shadowed_by_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            foreign = Path(tmp) / "skills/dwi-skill"
            foreign.mkdir(parents=True)
            (foreign / "SKILL.md").write_text(
                "---\nname: dwi-skill\n---\nFOREIGN", encoding="utf-8"
            )
            loader = SkillLoader(ROOT / "skills")
            with patch("pathlib.Path.cwd", return_value=Path(tmp)):
                skill = loader.find("dwi-skill")
            self.assertTrue(skill.definition_path.is_relative_to(ROOT / "skills"))
            self.assertNotEqual(skill.instructions, "FOREIGN")

    def test_prompt_uses_json_overview(self):
        skill = self.loader.find("dwi-skill")
        self.assertTrue(skill.overview)
        self.assertIn("已有 5TT", skill.prompt_excerpt())
        # Catalog tool guidance has one platform owner, not one copy per Skill.
        from app.skill_catalog import SKILL_PROMPT
        self.assertIn("list_skill_methods", SKILL_PROMPT)
        self.assertNotIn("list_skill_methods", skill.prompt_excerpt())
        self.assertGreater(len(skill.instructions), len(skill.prompt_excerpt()))

    def test_model_tools_work_without_dataset_and_cannot_execute_commands(self):
        tools = WorkspaceTools(Mock(), Mock())
        tools.skill_catalog = self.catalog
        request = ChatRequest(message="解释参数", selected_skills=["dwi-skill"])
        self.assertTrue(
            {t["function"]["name"] for t in SKILL_TOOLS}.issubset(
                {t["function"]["name"] for t in tools.available_tools(request)}
            )
        )
        result = tools.execute(
            "read_skill_method",
            {
                "capability": "skill.dwi-skill",
                "method_id": "mrtrix.command.mrthreshold",
                "section": "parameters",
                "limit": 1,
            },
            request,
        )
        self.assertEqual(len(result["items"]), 1)
        with self.assertRaises(ValueError):
            tools.execute("list_skill_methods", {"shell": "rm -rf /"}, request)
        with self.assertRaises(ValueError):
            tools.execute("list_skill_methods", {"q": []}, request)
        with self.assertRaises(ValueError):
            tools.execute("list_skill_methods", {"limit": True}, request)
        with self.assertRaises(ValueError):
            tools.execute(
                "read_skill_method",
                {
                    "capability": "skill.dwi-skill",
                    "method_id": "mrtrix.command.mrthreshold",
                    "section": "all",
                },
                request,
            )

    def test_model_loop_reads_method_before_planning_and_keeps_observations(self):
        catalog = self.catalog

        class Model:
            mode = "model"
            calls = 0

            async def workspace_completion(self, messages, tools, required=False):
                self.calls += 1
                assert any(t["function"]["name"] == "read_skill_method" for t in tools)
                if self.calls == 1:
                    assert required
                    return {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "m1",
                                "type": "function",
                                "function": {
                                    "name": "read_skill_method",
                                    "arguments": json.dumps(
                                        {
                                            "capability": "skill.dwi-skill",
                                            "method_id": "mrtrix.command.mrthreshold",
                                            "section": "parameters",
                                            "limit": 2,
                                        }
                                    ),
                                },
                            }
                        ],
                    }
                data = json.loads(messages[-1]["content"])
                assert data["method_id"] == "mrtrix.command.mrthreshold"
                assert len(data["items"]) == 2 and data["has_more"]
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "m2",
                            "type": "function",
                            "function": {
                                "name": "continue_workflow_planning",
                                "arguments": "{}",
                            },
                        }
                    ],
                }

        events = Mock()
        model = Model()
        tools = WorkspaceTools(events, model)
        tools.skill_catalog = catalog
        request = ChatRequest(
            message="按绝对阈值处理影像，先检查需要的参数", interaction_mode="task"
        )
        self.assertIsNone(asyncio.run(tools.reply(request)))
        self.assertIn("mrtrix.command.mrthreshold", request.workspace_context)
        self.assertEqual(model.calls, 2)
        self.assertEqual(events.event.call_args_list[-1].args[1]["status"], "succeeded")


class ConditionalParametersTests(unittest.TestCase):
    def test_conditional_required_disabled_and_falsy_values(self):
        p = ParameterSpec(
            id="threshold",
            argument="-abs",
            label="阈值",
            type="number",
            required=False,
            required_when={"mode": ["absolute"]},
            enabled_when={"mode": ["absolute"]},
        )
        self.assertTrue(matches({"count": [1]}, {"count": 1.0}))
        self.assertFalse(matches({"count": [1]}, {"count": True}))
        self.assertTrue(required(p, {"mode": "absolute"}))
        self.assertFalse(active(p, {"mode": "automatic"}))
        method = MethodOption(
            id="test",
            skill_id="test",
            label="test",
            description="",
            runner_id="test",
            parameters=[
                p,
                ParameterSpec(
                    id="mode",
                    argument="",
                    label="mode",
                    type="string",
                    default="absolute",
                ),
            ],
        )
        self.assertEqual(effective_parameters(method, {"threshold": 0})["threshold"], 0)
        self.assertNotIn(
            "threshold",
            effective_parameters(method, {"mode": "automatic", "threshold": 10}),
        )
        self.assertEqual(effective_parameters(method, {"threads": 8})["threads"], 8)
        validate_method_parameters(method, {"mode": "absolute"}, require_complete=False)
        with self.assertRaises(ValueError):
            validate_method_parameters(method, {"mode": "absolute"})
        validate_method_parameters(method, {"mode": "absolute", "threshold": 0})
        with self.assertRaises(ValueError):
            validate_method_parameters(
                method, {"mode": "absolute", "threshold": float("nan")}
            )
        with self.assertRaises(ValueError):
            validate_method_parameters(method, {"mode": "absolute", "undeclared": 3})

    def test_zero_is_an_explicit_conflicting_value_and_false_is_not(self):
        p = ParameterSpec(
            id="factor",
            argument="-factor",
            label="factor",
            type="number",
            required=False,
            conflicts_with=["coeff"],
        )
        self.assertEqual(conflicts(p, {"factor": 0, "coeff": 0}), ["coeff"])
        self.assertEqual(conflicts(p, {"factor": None, "coeff": 0}), [])


class CommandContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.runners = {r.contract["command"]: r for r in command_runners()}

    def context(self, name, parameters=None):
        runner = self.runners[name]
        step = WorkflowStep(
            id="test",
            order=1,
            title="test",
            description="",
            capability="skill.dwi-skill",
            status="configured",
            selection="user",
            selected_parameters=parameters or {},
            input_bindings={},
        )
        for slot in runner.method.input_slots:
            if not slot.required:
                continue
            path = self.folder / (slot.id + ".mif")
            path.write_bytes(b"synthetic")
            step.input_bindings[slot.id] = InputBinding(
                slot_id=slot.id,
                artifact_type=slot.type,
                source="existing_path",
                absolute_path=str(path),
            )
        return SimpleNamespace(
            step=step,
            method=runner.method,
            output_directory=self.folder,
            input_is_deferred=lambda _: False,
        )

    def argv(self, name, parameters=None):
        runner = self.runners[name]
        with patch.object(runner, "_allowed_binding_input", return_value=True):
            return runner.build_argv(self.context(name, parameters))

    def test_threshold_mode_only_emits_selected_option_and_zero_survives(self):
        argv = self.argv("mrthreshold", {"mode": "abs", "abs": 0, "percentile": 95})
        self.assertIn("-abs", argv)
        self.assertIn("0", argv)
        self.assertNotIn("-percentile", argv)
        with self.assertRaises(ValueError):
            self.argv("mrthreshold", {"mode": "abs"})

    def test_sift2_mutually_exclusive_bounds_and_act_input(self):
        with self.assertRaises(ValueError):
            self.argv("tcksift2", {"min_factor": 0, "min_coeff": -1})
        with self.assertRaises(ValueError):
            self.argv("tcksift2", {"fd_scale_gm": True})
        self.assertIn("-min_factor", self.argv("tcksift2", {"min_factor": 0}))

    def test_resample_multi_argument_mode_is_typed_and_complete(self):
        argv = self.argv(
            "tckresample",
            {
                "mode": "line",
                "line_num": 12,
                "line_start": "0,1,2",
                "line_end": "3,4,5",
            },
        )
        index = argv.index("-line")
        self.assertEqual(argv[index + 1 : index + 4], ["12", "0,1,2", "3,4,5"])
        self.assertNotIn("-num_points", argv)
        with self.assertRaises(ValueError):
            self.argv(
                "tckresample",
                {
                    "mode": "line",
                    "line_num": 12,
                    "line_start": "0,1,2",
                    "line_end": "$(touch x)",
                },
            )

    def test_optional_output_paths_are_owned_by_runner(self):
        argv = self.argv("dwidenoise", {"save_noise": True})
        self.assertEqual(
            argv[argv.index("-noise") + 1], str((self.folder / "noise.mif").resolve())
        )
        with self.assertRaises(ValueError):
            self.argv("dwidenoise", {"output": "/etc/passwd"})

    def test_threads_are_checked_against_slurm_allocation(self):
        with patch.dict("os.environ", {"SLURM_CPUS_PER_TASK": "2"}):
            with self.assertRaises(ValueError):
                self.argv("mrdegibbs", {"nthreads": 4})
            self.assertIn("-nthreads", self.argv("mrdegibbs", {"nthreads": 0}))

    def test_multiline_upstream_usage_and_processing_prerequisites_are_preserved(self):
        method = self.runners["dwidenoise"].method
        self.assertIn("first step", method.description)
        estimator = next(p for p in method.parameters if p.id == "estimator")
        self.assertIn("Exp1", estimator.description)
        self.assertIn("Cordero-Grande", estimator.description)

    def test_every_source_option_is_exposed_or_has_an_explicit_disposition(self):
        for runner in self.runners.values():
            c = runner.contract
            self.assertEqual(
                len(c["options"]) + len(c["omitted_options"]), c["total_options"]
            )
            self.assertTrue(all(p.description for p in runner.method.parameters))
            self.assertTrue(all(reason for reason in c["omitted_options"].values()))
