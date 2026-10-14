"""Projection agrees with scheduling without file reads, writes or execution."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
from fastapi import FastAPI

from app.api.execution import router_for
from app.dataset_models import DatasetSample, SamplePathMatch
from app.execution.output_preview import workflow_output_preview
from app.execution.output_layout import relative_output_key
from app.execution.recipe_compiler import RecipeCompiler
from app.execution.sample_plan import schedule_units
from app.run_models import OutputPreviewRequest, RunExecutionOptions
from app.workflow_models import (
    InputBinding,
    MethodOption,
    OutputSlotSpec,
    SampleScope,
    Workflow,
    WorkflowStep,
)
import test_sample_execution as sample_fixtures


def workflow():
    method = MethodOption(
        id="method",
        skill_id="test",
        label="Method",
        description="",
        runner_id="test",
        output_slots=[
            OutputSlotSpec(
                id="result",
                type="table",
                label="Table",
                format="tsv",
                path_template="results/report.tsv",
            ),
            OutputSlotSpec(
                id="optional",
                type="image",
                label="Optional",
                path_template="plot.png",
                generated_when={"plot": [True]},
            ),
        ],
    )
    step = WorkflowStep(
        id="analyze",
        order=1,
        title="Analyze",
        description="",
        capability="test",
        status="configured",
        method_options=[method],
        selected_method_id="method",
        selected_parameters={"plot": False},
    )
    return Workflow(
        workflow_id="wf",
        template_id="test",
        title="test",
        summary="",
        original_request="test",
        status="chain_ready",
        steps=[step],
        created_at="",
        updated_at="",
        workspace_root_path="/authorized/output",
        output_relative_path="outputs",
        output_layout="sample_steps",
    )


def preview(value, options=None, **kwargs):
    return workflow_output_preview(
        value, RecipeCompiler(Mock()), options or RunExecutionOptions(), **kwargs
    )


def test_projection_is_pure_and_respects_current_conditions():
    value = workflow()
    original = value.model_dump()
    with (
        patch.object(
            Path, "resolve", side_effect=AssertionError("filesystem resolution")
        ),
        patch.object(Path, "stat", side_effect=AssertionError("filesystem inspection")),
        patch.object(Path, "mkdir", side_effect=AssertionError("filesystem write")),
    ):
        result = preview(value)
    assert value.model_dump() == original
    assert result["executed"] is False and result["filesystem_checked"] is False
    assert result["root_template"] == "/authorized/output/outputs/{new_run_id}"
    assert result["groups"][0]["steps"][0]["relative_directory"] == "steps/analyze"
    assert result["declared_output_count"] == 1
    value.steps[0].selected_parameters["plot"] = True
    assert preview(value)["declared_output_count"] == 2


def test_uncertain_and_unsafe_contract_names_are_explicit():
    value = workflow()
    slots = value.steps[0].method_options[0].output_slots
    slots[0].path_template = "{subject}/report.tsv"
    assert (
        preview(value)["groups"][0]["steps"][0]["outputs"][0]["certainty"]
        == "dynamic_name"
    )
    for path in ["", "/outside/report", "../../report", "unsafe\\path"]:
        slots[0].path_template = path
        output = preview(value)["groups"][0]["steps"][0]["outputs"][0]
        assert output["relative_path"] is None and output["certainty"] == "type_only"


@pytest.mark.parametrize("relative", ["../outside", "/outside", "unsafe\\path"])
def test_preview_does_not_advertise_a_root_outside_selection(relative):
    value = workflow()
    value.output_relative_path = relative
    result = preview(value)
    assert result["root_template"] is None and result["issues"]


def test_paged_large_scope_uses_saved_sample_ids_and_does_not_rescan():
    value = workflow()
    value.dataset = SimpleNamespace(
        inspection=SimpleNamespace(
            sample_count=499,
            samples=[
                DatasetSample(
                    sample_id=f"002_S_{i:04d}",
                    label=f"Subject {i}",
                    relative_root=str(i),
                )
                for i in range(499)
            ],
        )
    )
    options = RunExecutionOptions(
        sample_mode="per_sample", output_layout="sample_steps"
    )
    result = preview(value, options, offset=497)
    assert (
        result["sample_count"]
        == result["unit_count"]
        == result["declared_output_count"]
        == 499
    )
    assert result["next_offset"] is None and len(result["groups"]) == 2
    assert result["groups"][0]["steps"][0]["relative_directory"] == "002_S_0497/analyze"
    options.output_layout = "legacy"
    assert (
        preview(value, options, offset=497)["groups"][0]["steps"][0][
            "relative_directory"
        ]
        == "sample_0498__analyze"
    )
    value.dataset.inspection.samples.pop()
    assert preview(value, options)["status"] == "incomplete"


def test_preview_compiles_recipe_children_using_the_execution_compiler():
    value = workflow()
    compiler = Mock()
    compiler.compile.return_value = [
        SimpleNamespace(
            step=value.steps[0],
            method=value.steps[0].method_options[0],
            workspace_key="pipeline__child_workspace",
        )
    ]
    result = workflow_output_preview(value, compiler, RunExecutionOptions())
    assert (
        result["groups"][0]["steps"][0]["relative_directory"]
        == "steps/pipeline__child_workspace"
    )
    compiler.compile.assert_called_once_with(value)


def test_partial_sample_scope_keeps_the_original_ordinal_in_legacy_names():
    value = workflow()
    value.dataset = SimpleNamespace(
        inspection=SimpleNamespace(
            sample_count=3,
            samples=[
                DatasetSample(sample_id=s, label=s, relative_root=s)
                for s in ["first", "second", "third"]
            ],
        )
    )
    scope = SampleScope(total=3, eligible_sample_ids=["third"])
    with patch("app.execution.output_preview.partial_sample_scope", return_value=scope):
        result = preview(
            value, RunExecutionOptions(sample_mode="per_sample", output_layout="legacy")
        )
    assert result["sample_count"] == 1 and result["excluded_sample_count"] == 2
    assert (
        result["groups"][0]["steps"][0]["relative_directory"] == "sample_0003__analyze"
    )


def test_actual_scheduled_directories_match_preview_for_both_layouts():
    case = sample_fixtures.SampleExecutionTests()
    case.setUp()
    try:
        compiler = RecipeCompiler(case.fixture.capabilities)
        for layout in ["legacy", "sample_steps"]:
            case.options.output_layout = layout
            predicted = workflow_output_preview(
                case.workflow, compiler, case.options, limit=5
            )
            actual = schedule_units(
                case.workflow, compiler.compile(case.workflow), case.options
            )
            assert [
                step["relative_directory"]
                for group in predicted["groups"]
                for step in group["steps"]
            ] == [relative_output_key(u.workspace_key) for u in actual]
        case.fixture.backend.execute.assert_not_called()
        assert not case.fixture.store.list_runs()
    finally:
        case.doCleanups()


def test_missing_method_keeps_preview_incomplete_instead_of_inventing_files():
    value = workflow()
    value.steps[0].selected_method_id = None
    result = preview(value)
    assert (
        result["status"] == "incomplete" and not result["groups"] and result["issues"]
    )


def batch_workflow():
    value = workflow()
    samples = [
        DatasetSample(
            sample_id="002_S_0413", label="002_S_0413", relative_root="002_S_0413"
        ),
        DatasetSample(
            sample_id="sub-002",
            label="Second visit",
            relative_root="participant_002/visit1",
        ),
    ]
    value.dataset = SimpleNamespace(
        absolute_path="/dataset",
        inspection=SimpleNamespace(sample_count=2, samples=samples),
    )
    value.steps[0].input_bindings["dwi"] = InputBinding(
        slot_id="dwi",
        artifact_type="dwi",
        source="dataset",
        binding_mode="per_sample",
        sample_matches=[
            SamplePathMatch(
                sample_id=s.sample_id,
                label=s.label,
                relative_path=s.relative_root + "/dwi.nii.gz",
                absolute_path="/dataset/" + s.relative_root + "/dwi.nii.gz",
                status="matched",
            )
            for s in samples
        ],
    )
    return value


def test_default_single_settings_preview_the_required_batch_mode_explicitly():
    value = batch_workflow()
    options = RunExecutionOptions()
    result = preview(value, options)
    assert (
        result["sample_mode"] == "per_sample"
        and result["requested_sample_mode"] == "single"
    )
    assert result["settings_need_change"] and result["naming_rule"] == "sample_id"
    assert (
        result["sample_count"] == 2
        and result["groups"][0]["steps"][0]["relative_directory"]
        == "002_S_0413/analyze"
    )
    assert (
        result["groups"][0]["source_relative_root"]
        == result["groups"][0]["output_sample_directory"]
        == "002_S_0413"
    )
    assert result["groups"][0]["folder_matches_input_name"] is True
    assert (
        options.sample_mode == "single" and result["issues"]
    )  # No silent edit to the execution draft.


def test_sample_identity_mapping_does_not_assume_label_or_input_folder_equals_id():
    value = batch_workflow()
    result = preview(value, RunExecutionOptions(sample_mode="per_sample"))
    row = result["groups"][1]
    assert row["sample_id"] == row["output_sample_directory"] == "sub-002"
    assert (
        row["sample_label"] == "Second visit"
        and row["source_relative_root"] == "participant_002/visit1"
    )
    assert (
        row["folder_matches_input_name"] is False
        and row["sample_id_sanitized"] is False
    )
    value.dataset.inspection.samples[1].sample_id = "sub/002"
    row = preview(value, RunExecutionOptions(sample_mode="per_sample"))["groups"][1]
    assert row["sample_id_sanitized"] and "/" not in row["output_sample_directory"]


def test_legacy_and_single_projection_cannot_claim_to_use_sample_folder_names():
    result = preview(batch_workflow(), RunExecutionOptions(output_layout="legacy"))
    assert result["naming_rule"] == "ordinal_step"
    assert result["groups"][0]["output_sample_directory"] is None
    assert (
        result["groups"][0]["steps"][0]["relative_directory"] == "sample_0001__analyze"
    )
    result = preview(workflow())
    assert result["naming_rule"] == "step_only" and not result["settings_need_change"]
    assert result["groups"][0]["sample_id"] is None


def test_response_entries_are_bounded():
    value = workflow()
    method = value.steps[0].method_options[0]
    method.output_slots = [
        OutputSlotSpec(
            id=f"f{i}", type="table", label="Table", path_template=f"{i}.tsv"
        )
        for i in range(350)
    ]
    result = preview(value)
    assert result["declared_output_count"] == 350 and result["entries_truncated"]
    assert len(result["groups"][0]["steps"][0]["outputs"]) == 200


def test_http_preview_is_revision_bound_and_cannot_start_execution():
    asyncio.run(_http_preview())


async def _http_preview():
    value = workflow()
    services = SimpleNamespace(workflow_service=Mock(), run_service=Mock())
    services.workflow_service.get.return_value = value
    services.run_service.recipe_compiler = RecipeCompiler(Mock())
    app = FastAPI()
    app.include_router(router_for(services))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        request = OutputPreviewRequest(expected_revision=value.revision).model_dump()
        response = await client.post("/api/workflows/wf/output-preview", json=request)
        assert response.status_code == 200 and response.json()["executed"] is False
        request["expected_revision"] += 1
        assert (
            await client.post("/api/workflows/wf/output-preview", json=request)
        ).status_code == 409
        request["expected_revision"] = value.revision
        request["limit"] = 99
        assert (
            await client.post("/api/workflows/wf/output-preview", json=request)
        ).status_code == 422
        services.workflow_service.get.side_effect = KeyError("wf")
        assert (
            await client.post(
                "/api/workflows/wf/output-preview", json={"expected_revision": 1}
            )
        ).status_code == 404
        services.workflow_service.get.side_effect = None
        services.workflow_service.get.return_value = batch_workflow()
        response = await client.post(
            "/api/workflows/wf/output-preview", json={"expected_revision": 1}
        )
        assert response.status_code == 200
        assert (
            response.json()["settings_need_change"]
            and response.json()["sample_mode"] == "per_sample"
        )
        assert response.json()["groups"][0]["output_sample_directory"] == "002_S_0413"
    services.run_service.create.assert_not_called()
