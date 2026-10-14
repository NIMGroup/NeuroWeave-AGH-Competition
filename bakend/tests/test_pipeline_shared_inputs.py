# Explicit fixture re-exports are registered by pytest, not direct Python calls.
from types import SimpleNamespace

import pytest
from app.chat_service import ChatService
from app.dataset_service import DatasetService
from app.execution.recipe_compiler import RecipeCompiler
from app.parameter_rules import input_required
from app.pipeline_models import ComposeRequest, UsePipeline
from app.pipeline_shared_inputs import merge_shared_inputs
from app.workflow_models import InputBinding, InputSlotSpec
from app.workflow_service import WorkflowService
from app.workflows.contracts import slot_required
from test_historical_inputs import history_case as history_case
from test_partial_history import fod_inputs, partial_target
from test_pipeline_library import ROOT
from test_pipeline_library import library as library


def port(step, slot):
    return {"step_id": step, "input_slot_id": slot}


def draft(slot="dwi"):
    return {
        "name": "Shared file",
        "steps": [
            {"id": "response", "method_id": "mrtrix.dwi2response"},
            {"id": "fod", "method_id": "mrtrix.dwi2fod"},
        ],
        "shared_inputs": [[port("response", slot), port("fod", slot)]],
    }


def test_share_save_reopen_and_recipe_reuses_file_and_sample_bindings(library):
    value = library.save(ComposeRequest.model_validate(draft()))
    reopened = library.get(value["id"])
    assert reopened["editor"]["shared_inputs"] == draft()["shared_inputs"]
    shared = next(s for s in reopened["input_slots"] if s["id"] == "shared_input_1")
    assert shared["required"]
    assert not {"response__dwi", "fod__dwi"} & {
        s["id"] for s in reopened["input_slots"]
    }
    service = WorkflowService(
        ChatService(),
        library.registry,
        library.store,
        DatasetService(ROOT, library.store),
    )
    workflow = library.workflow(value["id"], UsePipeline(), service, SimpleNamespace())
    binding = InputBinding(
        slot_id=shared["id"],
        artifact_type=shared["type"],
        source="existing_path",
        absolute_path="/dataset/sub-01/dwi.mif",
        binding_mode="per_sample",
        dataset_id="data",
        path_template="{sample}/dwi.mif",
        sample_matches=[
            {
                "sample_id": "sub-01",
                "label": "S1",
                "status": "matched",
                "absolute_path": "/dataset/sub-01/dwi.mif",
                "relative_path": "sub-01/dwi.mif",
            }
        ],
    )
    workflow.steps[0].input_bindings[shared["id"]] = binding
    units = RecipeCompiler(library.registry).compile(workflow)
    assert len(units) == 2
    a, b = [u.step.input_bindings["dwi"] for u in units]
    assert a.absolute_path == b.absolute_path == binding.absolute_path
    assert a.sample_matches == b.sample_matches == binding.sample_matches
    assert a.dataset_id == b.dataset_id == "data"
    assert a is not b and a.sample_matches is not b.sample_matches
    exported = library.from_workflow(workflow, "Reused shared Pipeline")
    assert exported["editor"]["shared_inputs"] == [
        [port("s1", "dwi"), port("s2", "dwi")]
    ]
    assert "/dataset/" not in str(exported["editor"])
    assert all(not s["inputs"] for s in reopened["editor"]["steps"]), (
        "sharing must not invent a step dependency"
    )


def test_shared_conditional_input_uses_or_not_and(library):
    value = {
        "name": "Masks",
        "steps": [
            {"id": i, "method_id": "mrtrix.command.mrthreshold"} for i in ("a", "b")
        ],
        "shared_inputs": [[port("a", "mask"), port("b", "mask")]],
    }
    p = library.compile(ComposeRequest.model_validate(value))
    slot = InputSlotSpec.model_validate(
        next(s for s in p["input_slots"] if s["id"] == "shared_input_1")
    )
    assert not slot.required and len(slot.required_when_any) == 2
    for a, b, required in (
        (False, False, False),
        (True, False, True),
        (False, True, True),
        (True, True, True),
    ):
        params = {"a__out_masked": a, "b__out_masked": b}
        assert input_required(slot, params) is required
        assert slot_required(slot, params) is required
    assert not input_required(slot, {"a__out_masked": 1, "b__out_masked": None})


@pytest.mark.parametrize("kind", ["unknown", "duplicate", "type", "upstream"])
def test_invalid_sharing_rejected_by_server(library, kind):
    value = draft()
    if kind == "unknown":
        value["shared_inputs"][0][1] = port("missing", "dwi")
    if kind == "duplicate":
        value["shared_inputs"].append(value["shared_inputs"][0])
    if kind == "type":
        value["shared_inputs"][0][1] = port("fod", "mask")
    if kind == "upstream":
        value["steps"][1]["inputs"] = {
            "wm_response": {"step_id": "response", "output_slot_id": "wm_response"}
        }
        value["steps"].append({"id": "third", "method_id": "mrtrix.dwi2fod"})
        value["shared_inputs"] = [
            [port("fod", "wm_response"), port("third", "wm_response")]
        ]
    with pytest.raises(ValueError):
        library.compile(ComposeRequest.model_validate(value))


def test_format_intersection_and_cardinality_are_not_bypassed():
    base = {
        "type": "image",
        "required": True,
        "required_when": {},
        "required_when_any": [],
        "formats": [".nii", ".mif"],
        "source_options": ["existing_path"],
        "binding": "file",
        "cardinality": "one",
        "validation": ["path_exists"],
        "companions": [],
        "label": "Image",
        "description": "",
    }
    group = [[SimpleNamespace(step_id=i, input_slot_id="image") for i in ("a", "b")]]

    def compile(other):
        slots = [{**base, "id": "a__image"}, {**base, "id": "b__image", **other}]
        stages = [
            {
                "id": i,
                "input_bindings": {
                    "image": {"source": "workflow_input", "slot_id": i + "__image"}
                },
            }
            for i in ("a", "b")
        ]
        return merge_shared_inputs(group, slots, stages)

    assert compile({"formats": [".mif"]})[0]["formats"] == [".mif"]
    assert compile({"formats": []})[0]["formats"] == [".mif", ".nii"]
    for other in (
        {"formats": [".mgz"]},
        {"cardinality": "many"},
        {"binding": "folder"},
        {"source_options": ["dataset"]},
    ):
        with pytest.raises(ValueError):
            compile(other)


def test_shared_conditional_history_scope_preserves_any_consumers_need(history_case):
    from app.sample_scope import partial_sample_scope, restrict_sample_bindings

    workflow, _ = partial_target(history_case)
    step = workflow.steps[0]
    method = step.method_options[0]
    shared = method.input_slots[0].model_copy(
        update={
            "id": "shared_input_1",
            "required": False,
            "required_when": {},
            "required_when_any": [{"consumer_a": [True]}, {"consumer_b": [True]}],
        }
    )
    method.input_slots.append(shared)
    binding = fod_inputs(workflow)
    binding.slot_id = shared.id
    binding.sample_matches[1].status = "missing"
    step.input_bindings[shared.id] = binding
    step.selected_parameters.update(consumer_a=False, consumer_b=False)
    scope = partial_sample_scope(workflow)
    assert scope.eligible_sample_ids == ["subject-B"]
    assert (
        shared.id
        not in restrict_sample_bindings(workflow, scope).steps[0].input_bindings
    )
    step.selected_parameters["consumer_b"] = True
    scope = partial_sample_scope(workflow)
    assert scope.eligible_sample_ids == []
    assert (
        shared.id in restrict_sample_bindings(workflow, scope).steps[0].input_bindings
    )
