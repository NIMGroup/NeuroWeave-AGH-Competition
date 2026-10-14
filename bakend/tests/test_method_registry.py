"""Independent contracts are authoritative; Skill annotations cannot rewrite them."""

import copy
import json
from pathlib import Path
import pytest
from app.method_registry import MethodRegistry
from app.capability_registry import CapabilityRegistry
from app.execution.recipe_compiler import RecipeCompiler
from app.execution.runner_registry import RunnerRegistry
from app.workflow_models import MethodOption, Workflow, WorkflowStep

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def catalog(tmp_path):
    source = MethodRegistry(ROOT)
    source.load()
    pipeline = next(
        p
        for p in source.pipelines.values()
        if p["id"].startswith("reviewed-existing-fod-act-sift2-")
    )
    for stage in pipeline["steps"]:
        method = source.methods[stage["method_ref"]]
        path = tmp_path / "methods/test" / method.id / "method.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(method.model_dump_json(), encoding="utf-8")
    path = tmp_path / "pipelines/test/pipeline.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(pipeline), encoding="utf-8")
    skill = {
        "schema_version": 1,
        "id": "test",
        "description": "test",
        "functions": [],
        "selection_rules": [],
        "method_refs": [
            {
                "method_ref": pipeline["steps"][0]["method_ref"],
                "capability": "test",
                "method_id": "one",
                "role": "test",
            }
        ],
        "pipeline_refs": [],
        "planning_capabilities": [],
    }
    path = tmp_path / "skills/test/skill.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(skill), encoding="utf-8")
    store = MethodRegistry(tmp_path)
    store.load()
    return store


def change(store, path, update):
    target = store.root / path
    value = json.loads(target.read_text(encoding="utf-8"))
    update(value)
    target.write_text(json.dumps(value), encoding="utf-8")
    store.load(force=True)


def test_skill_cannot_override_contract(catalog):
    with pytest.raises(ValueError, match="override"):
        change(
            catalog,
            "skills/test/skill.json",
            lambda s: s["method_refs"][0].update(parameters=[]),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["steps"][0].update(method_version="999"),
        lambda p: p["steps"][0].update(method_ref="unregistered"),
        lambda p: p["steps"][0]["parameter_bindings"].update(made_up="threads"),
        lambda p: p["steps"][0]["input_bindings"]["fod"].update(
            source="step_output",
            step_id=p["steps"][-1]["id"],
            output_slot_id="out_weights",
        ),
        lambda p: p.update(output_bindings={}),
        lambda p: p["steps"][0].update(workspace="../escape"),
    ],
)
def test_invalid_pipeline_rejected_before_catalog_exposed(catalog, mutation):
    before = copy.deepcopy(catalog.pipelines)
    with pytest.raises(ValueError):
        change(catalog, "pipelines/test/pipeline.json", mutation)
    assert catalog.pipelines == before


def test_no_skill_owned_contracts_and_exact_registered_adapters():
    store = MethodRegistry(ROOT)
    store.load()
    assert set(store.methods) == RunnerRegistry().ids()
    assert {"mrtrix.command.mrconvert", "mrtrix.command.dwi2tensor", "mrtrix.tckgen"} <= set(store.methods)
    for skill in store.skills.values():
        for ref in skill["method_refs"]:
            assert (
                not {"parameters", "input_slots", "output_slots", "runner_id"}
                & ref.keys()
            )
            definition = store.method(ref["method_ref"])
            definition.label = "local snapshot edit"
            assert store.method(ref["method_ref"]).label != "local snapshot edit"
    tracking = store.method("mrtrix.tckgen")
    assert "postprocessing" not in {p.id for p in tracking.parameters}
    assert {o.id for o in tracking.output_slots} == {"tractogram"}


def test_optional_sift2_is_a_separate_execution_unit():
    registry = CapabilityRegistry(ROOT / "skills")
    definition = next(
        d
        for d in registry.all_methods()
        if d.execution_recipe and any(s.when for s in d.execution_recipe.steps)
    )
    option = MethodOption(
        id=definition.method_id, **definition.model_dump(exclude={"method_id"})
    )
    step = WorkflowStep(
        id="track",
        order=1,
        title="track",
        description="",
        status="confirmed",
        capability=definition.capability,
        method_options=[option],
        selected_method_id=option.id,
        selected_parameters={"postprocessing": "none"},
    )
    workflow = Workflow(
        workflow_id="test",
        template_id="test",
        title="test",
        summary="",
        original_request="",
        status="confirmed",
        steps=[step],
        created_at="now",
        updated_at="now",
    )
    compiler = RecipeCompiler(registry)
    units = compiler.compile(workflow)
    assert [u.method.runner_id for u in units] == ["mrtrix.tckgen"]
    workflow.steps[0].selected_parameters["postprocessing"] = "sift2"
    units = compiler.compile(workflow)
    assert [u.method.runner_id for u in units] == [
        "mrtrix.tckgen",
        "mrtrix.command.tcksift2",
    ]
    assert (
        units[-1].step.input_bindings["in_tracks"].upstream_step_id == units[0].step.id
    )
