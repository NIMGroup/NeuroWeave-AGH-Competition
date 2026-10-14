import pytest
from app.execution.recipe_compiler import RecipeCompilationError, RecipeCompiler
from app.pipeline_models import ComposeRequest
from app.workflow_models import MethodOption, Workflow, WorkflowStep

# Explicit fixture re-exports are registered by pytest, not direct Python calls.
from test_pipeline_library import library as library


def response_fod(algorithm="dhollander"):
    return ComposeRequest(
        name="Three tissue response to FOD",
        steps=[
            {
                "id": "response",
                "method_id": "mrtrix.dwi2response",
                "parameters": {"algorithm": algorithm},
            },
            {
                "id": "fod",
                "method_id": "mrtrix.dwi2fod",
                "parameters": {"algorithm": "msmt_csd"},
                "inputs": {
                    slot: {"step_id": "response", "output_slot_id": slot}
                    for slot in ("wm_response", "gm_response", "csf_response")
                },
            },
        ],
    )


def test_three_response_outputs_connect_save_and_compile(library):
    p = library.save(response_fod())
    saved = library.get(p["id"])
    assert set(saved["editor"]["steps"][1]["inputs"]) == {
        "wm_response",
        "gm_response",
        "csf_response",
    }
    assert not any(
        slot["id"] in {"fod__wm_response", "fod__gm_response", "fod__csf_response"}
        for slot in p["input_slots"]
    )
    definition = library.as_method(p)
    method = MethodOption(
        id=definition.method_id, **definition.model_dump(exclude={"method_id"})
    )
    step = WorkflowStep(
        id="pipeline",
        order=1,
        title="Pipeline",
        description="",
        capability=definition.capability,
        status="confirmed",
        method_options=[method],
        selected_method_id=method.id,
    )
    workflow = Workflow(
        workflow_id="test",
        template_id="test",
        title="test",
        summary="",
        original_request="",
        status="confirmed",
        steps=[step],
        created_at="",
        updated_at="",
    )
    units = RecipeCompiler(library.registry).compile(workflow)
    assert len(units) == 2
    for slot in ("wm_response", "gm_response", "csf_response"):
        assert units[1].step.input_bindings[slot].output_slot_id == slot
        assert units[1].step.input_bindings[slot].upstream_step_id == units[0].step.id
    # Changing public parameters after saving must not bypass the execution guard.
    workflow.steps[0].selected_parameters["response__algorithm"] = "tournier"
    with pytest.raises(RecipeCompilationError, match="不可用"):
        RecipeCompiler(library.registry).compile(workflow)


def test_inactive_output_rejected_with_actionable_producer_condition(library):
    with pytest.raises(ValueError, match="dhollander"):
        library.compile(response_fod("tournier"))


def test_single_tissue_wm_connection_still_supported(library):
    request = response_fod("tournier")
    request.steps[1].inputs = {
        k: v for k, v in request.steps[1].inputs.items() if k == "wm_response"
    }
    request.steps[1].parameters["algorithm"] = "csd"
    p = library.compile(request)
    assert p["steps"][1]["input_bindings"]["wm_response"]["source"] == "step_output"
