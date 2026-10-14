# Imported pytest fixture names are intentionally reused as injected arguments.
# ruff: noqa: F811
"""Consent, sample intersection and durable scheduling use the same sample scope."""

import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from app.dataset_models import SamplePathMatch
from app.execution import RunConflict, RunnerRegistry, RunService
from app.execution.base import RunnerResult
from app.execution.recipe_compiler import RecipeCompilationError, RecipeCompiler
from app.execution.sample_plan import schedule_units
from app.historical_inputs import HistoricalInputs
from app.local_store import LocalStore
from app.run_models import NodeAllocation, RunArtifact, RunExecutionOptions
from app.sample_scope import partial_sample_scope, restrict_sample_bindings
from app.workflow_models import InputBinding
from test_historical_inputs import history_case, selection  # noqa: F401


def fod_inputs(workflow):
    matches = []
    for sample in workflow.dataset.inspection.samples:
        path = Path(workflow.dataset.absolute_path) / sample.relative_root / "fod.mif"
        path.write_bytes(b"synthetic FOD")
        matches.append(
            SamplePathMatch(
                sample_id=sample.sample_id,
                label=sample.label,
                relative_path=f"{sample.relative_root}/fod.mif",
                absolute_path=str(path),
                status="matched",
            )
        )
    return InputBinding(
        slot_id="fod",
        artifact_type="fod_image",
        source="existing_path",
        binding_mode="per_sample",
        absolute_path=matches[0].absolute_path,
        sample_matches=matches,
        status="selected",
    )


def partial_target(case, missing=0):
    store, _, workflow, source, slot, service = case
    workflow.steps[0].input_bindings["fod"] = fod_inputs(workflow)
    store.save_workflow(workflow, event_type="fixture")
    history, request = selection(case)
    Path(source.steps[missing].artifacts[0].absolute_path).unlink()
    request.allow_partial = True
    bound = service.bind_historical_input(
        workflow.workflow_id, "track", slot.id, request
    )
    return bound, history


def ready(case, monkeypatch):
    workflow, history = partial_target(case)
    service = case[-1]
    runners = RunnerRegistry()
    runner = runners.get(workflow.steps[0].method_options[0].runner_id)
    monkeypatch.setattr(runner, "runtime_status", lambda: {"available": True})
    checked = []

    def preflight(context):
        header = Path(context.step.input_bindings["five_tt"].absolute_path).read_bytes().splitlines()
        checked.append(next(line.removeprefix(b'sample_id: ').decode() for line in header if line.startswith(b'sample_id: ')))
        return []

    monkeypatch.setattr(runner, "preflight", preflight)
    service.catalog.runner_registry = runners
    workflow = service.review_parameters(workflow.workflow_id, workflow.revision)
    workflow = service.dry_run(workflow.workflow_id, workflow.revision)
    assert workflow.dry_run_report.valid and workflow.dry_run_report.execution_enabled
    assert checked == ["subject-B"]
    workflow = service.confirm(workflow.workflow_id, workflow.revision)
    return workflow, runners


def test_partial_selection_is_explicit_and_persisted(history_case):
    store, _, workflow, source, slot, service = history_case
    history, request = selection(history_case)
    Path(source.steps[0].artifacts[0].absolute_path).unlink()
    preview = service.preview_historical_input("target", "track", slot.id, request)
    assert preview["can_apply"] and preview["partial"]
    assert preview["coverage"]["matched"] == 1
    with pytest.raises(ValueError, match="唯一覆盖"):
        service.bind_historical_input("target", "track", slot.id, request)
    request.allow_partial = True
    bound = service.bind_historical_input("target", "track", slot.id, request)
    binding = bound.steps[0].input_bindings[slot.id]
    assert binding.history.allow_partial
    assert [a.sample_id for a in binding.history.artifacts] == ["subject-B"]
    assert not history.validate_workflow(bound)
    assert (
        LocalStore(store.database_path)
        .get_workflow("target")
        .steps[0]
        .input_bindings[slot.id]
        == binding
    )


def test_intersection_excludes_missing_other_inputs_and_zero_blocks(history_case):
    workflow, history = partial_target(history_case)
    assert workflow.sample_scope.eligible_sample_ids == ["subject-B"]
    # Another supplied per-sample input can cover a different subset.
    workflow.steps[0].input_bindings["fod"].sample_matches[1].status = "missing"
    scope = partial_sample_scope(workflow)
    assert scope.eligible_sample_ids == []
    assert len(scope.skipped_samples) == 2
    assert "fod" in scope.skipped_samples[1].reasons[0].lower()
    with pytest.raises(RecipeCompilationError, match="没有共同匹配"):
        schedule_units(
            workflow,
            [],
            RunExecutionOptions(
                backend="slurm",
                sample_mode="per_sample",
                nodes=[NodeAllocation(node="node01")],
            ),
        )
    # Complete original sample identities and coverage remain in the saved plan.
    assert (
        len(restrict_sample_bindings(workflow, scope).dataset.inspection.samples) == 2
    )
    assert len(workflow.steps[0].input_bindings["fod"].sample_matches) == 2
    assert not history.validate_workflow(workflow)


def test_conditional_unused_input_does_not_exclude_samples(history_case):
    workflow, _ = partial_target(history_case)
    method = workflow.steps[0].method_options[0]
    unused = method.input_slots[0].model_copy(
        update={
            "id": "unused",
            "required": False,
            "required_when": {"optional_switch": [True]},
        }
    )
    method.input_slots.append(unused)
    binding = fod_inputs(workflow)
    binding.slot_id = "unused"
    binding.sample_matches[1].status = "missing"
    workflow.steps[0].input_bindings["unused"] = binding
    scope = partial_sample_scope(workflow)
    assert scope.eligible_sample_ids == ["subject-B"]
    assert (
        "unused"
        not in restrict_sample_bindings(workflow, scope).steps[0].input_bindings
    )


def test_single_eligible_sample_cannot_reuse_another_subjects_single_file(history_case):
    workflow, _ = partial_target(history_case)
    binding = workflow.steps[0].input_bindings["fod"]
    binding.binding_mode, binding.sample_matches = "single", []
    assert "subject-A" in binding.absolute_path
    units = RecipeCompiler(history_case[-1].registry).compile(workflow)
    with pytest.raises(RecipeCompilationError, match="各样本路径模板"):
        schedule_units(
            workflow,
            units,
            RunExecutionOptions(
                backend="slurm",
                sample_mode="per_sample",
                nodes=[NodeAllocation(node="node01")],
            ),
        )


def test_two_historical_inputs_use_intersection_and_block_empty_review(history_case):
    store, _, workflow, source, slot, service = history_case
    # A second real output group, with the correct FOD type and independent provenance.
    for step in list(source.steps):
        fod = step.model_copy(deep=True)
        fod.step_id += "_fod"
        fod.method_id, fod.title = "mrtrix-dwi2fod", "FOD"
        artifact = fod.artifacts[0]
        artifact.artifact_id += "_fod"
        artifact.artifact_type = "fod_image"
        artifact.step_id = fod.step_id
        artifact.metadata = {"output_slot_id": "fod"}
        path = Path(source.output_directory) / fod.step_id / "fod.mif"
        path.parent.mkdir()
        path.write_bytes(Path(artifact.absolute_path).read_bytes())
        artifact.absolute_path, artifact.relative_path = str(path), "fod.mif"
        source.steps.append(fod)
    store.save_run(source)
    history = HistoricalInputs(store)
    from app.historical_inputs import HistoricalSelectionRequest

    fod_slot = next(
        s for s in workflow.steps[0].method_options[0].input_slots if s.id == "fod"
    )

    def bind(current, input_slot):
        candidate = history.candidates(current, input_slot)["candidates"][0]
        return service.bind_historical_input(
            current.workflow_id,
            "track",
            input_slot.id,
            HistoricalSelectionRequest(
                expected_revision=current.revision,
                run_id=source.run_id,
                group_id=candidate["group_id"],
                allow_partial=True,
            ),
        )

    Path(source.steps[0].artifacts[0].absolute_path).unlink()
    workflow = bind(workflow, slot)
    workflow = bind(workflow, fod_slot)
    assert workflow.sample_scope.eligible_sample_ids == ["subject-B"]
    # FOD A and 5TT B cannot be mixed into an artificial complete sample.
    Path(source.steps[3].artifacts[0].absolute_path).unlink()
    workflow = bind(workflow, fod_slot)
    assert workflow.sample_scope.eligible_sample_ids == []
    assert not workflow.can_review_parameters
    with pytest.raises(ValueError, match="没有共同匹配"):
        service.review_parameters(workflow.workflow_id, workflow.revision)


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "failed", "shared_file", "wrong_format"]
)
def test_partial_mode_never_uses_invalid_or_ambiguous_outputs(history_case, change):
    store, _, workflow, source, slot, _ = history_case
    history, request = selection(history_case)
    artifact = source.steps[1].artifacts[0]
    if change == "missing":
        Path(artifact.absolute_path).unlink()
    elif change == "duplicate":
        source.steps[1].artifacts.append(
            artifact.model_copy(update={"artifact_id": "duplicate"})
        )
    elif change == "failed":
        source.steps[1].status, source.status = "failed", "failed"
    elif change == "shared_file":
        artifact.absolute_path = source.steps[0].artifacts[0].absolute_path
    else:
        path = Path(artifact.absolute_path)
        path.rename(path.with_suffix(".txt"))
        artifact.absolute_path = str(path.with_suffix(".txt"))
    store.save_run(source)
    preview, binding = history.preview(
        workflow, slot, request.run_id, request.group_id, allow_partial=True
    )
    assert [a.sample_id for a in binding.history.artifacts] == (
        [] if change == "shared_file" else ["subject-A"]
    )
    assert preview["can_apply"] is (change != "shared_file")


def test_changed_selected_output_still_blocks_partial_dry_run(
    history_case, monkeypatch
):
    workflow, _ = ready(history_case, monkeypatch)
    binding = workflow.steps[0].input_bindings["five_tt"]
    Path(binding.history.artifacts[0].absolute_path).unlink()
    assert (
        history_case[-1]
        .dry_run(workflow.workflow_id, workflow.revision)
        .dry_run_report.valid
        is False
    )


def finished(service, run_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run = service.get(run_id)
        if run.status in {"succeeded", "failed", "cancelled"}:
            return run
        time.sleep(0.01)
    raise AssertionError("Mocked execution did not finish")


def test_partial_dry_run_submit_restart_and_downstream_history(
    history_case, monkeypatch, tmp_path
):
    workflow, runners = ready(history_case, monkeypatch)
    store, _, _, _, slot, service = history_case
    monkeypatch.setenv("LOCALFLOW_SLURM_OUTPUT_ROOT", str(tmp_path / "shared"))
    backend = Mock()
    backend.status.return_value = {
        "available": True,
        "partitions": ["all"],
        "gpu_available": False,
        "nodes": [
            {
                "name": "node01",
                "partition": "all",
                "schedulable": True,
                "cpus": 4,
                "memory_mb": 100000,
                "gpus": 0,
            }
        ],
    }
    seen = []

    def execute(context, options, state, on_job):
        seen.append(
            (
                context.step.id,
                context.workflow.dataset.inspection.samples[0].sample_id,
                state,
            )
        )
        context.output_directory.mkdir(parents=True, exist_ok=True)
        path = context.output_directory / "fixture-5tt.mif"
        path.write_bytes(b"synthetic result")
        on_job({"job_id": "90210", "state": "COMPLETED", "node_list": "node01"})
        return RunnerResult(
            [
                RunArtifact(
                    artifact_id=context.step.id,
                    step_id=context.step.id,
                    artifact_type="five_tt_image",
                    label="synthetic",
                    absolute_path=str(path),
                    relative_path=path.name,
                    size_bytes=path.stat().st_size,
                    metadata={"output_slot_id": "five_tt"},
                )
            ],
            "completed",
        )

    backend.execute.side_effect = execute
    monkeypatch.setattr("app.execution.run_service.SlurmBackend", lambda: backend)
    # One eligible sample needs only four cores, despite a configured parallel cap of two.
    workflow.steps[0].selected_parameters["threads"] = 4
    store.save_workflow(workflow, event_type="fixture")
    options = RunExecutionOptions(
        backend="slurm",
        sample_mode="per_sample",
        nodes=[NodeAllocation(node="node01", cpus=4, parallel_samples=2)],
    )
    execution = RunService(store, service.registry, runners, tmp_path / "local")
    try:
        with pytest.raises(RunConflict, match="按样本运行"):
            execution.create(
                workflow.workflow_id,
                workflow.revision,
                RunExecutionOptions(backend="slurm"),
            )
        run = finished(
            execution,
            execution.create(workflow.workflow_id, workflow.revision, options).run_id,
        )
        assert run.status == "succeeded", run.error_message
        assert seen == [("sample_0002__track", "subject-B", None)]
        assert len(run.steps) == 1 and run.sample_scope.eligible_sample_ids == [
            "subject-B"
        ]
        assert run.plan_snapshot["dataset"]["inspection"]["sample_count"] == 2
        assert store.monitor_run(run.run_id)["run"]["sample_scope"]["total"] == 2
        # Reconstruct an in-flight persisted job. Recovery must monitor it, with no skipped sample units.
        execution.shutdown()
        run.status = "running"
        run.steps[0].status = "running"
        run.steps[0].artifacts = []
        run.artifacts = []
        store.save_run(run)
        reopened = RunService(
            LocalStore(store.database_path),
            service.registry,
            runners,
            tmp_path / "local",
        )
        try:
            recovered = finished(reopened, run.run_id)
            assert recovered.status == "succeeded" and len(recovered.steps) == 1
            assert seen[-1][0:2] == ("sample_0002__track", "subject-B")
            assert seen[-1][2]["job_id"] == "90210"
            # New output can bind back to the original dataset by its original sample ID.
            history = HistoricalInputs(store)
            candidate = next(
                c
                for c in history.candidates(workflow, slot)["candidates"]
                if c["run_id"] == run.run_id
            )
            preview, _ = history.preview(
                workflow, slot, run.run_id, candidate["group_id"], allow_partial=True
            )
            assert preview["can_apply"] and preview["coverage"]["matched"] == 1
            assert [
                m["sample_id"] for m in preview["matches"] if m["status"] == "matched"
            ] == ["subject-B"]
            # Retrying a failed eligible sample preserves the scope; skipped IDs cannot be selected.
            recovered.status = "failed"
            recovered.steps[0].status = "failed"
            store.save_run(recovered)
            with pytest.raises(RunConflict):
                reopened.retry(run.run_id, sample_ids=["subject-A"])
            retried = finished(
                reopened, reopened.retry(run.run_id, sample_ids=["subject-B"]).run_id
            )
            assert retried.status == "succeeded"
            assert retried.sample_scope == workflow.sample_scope
            assert [s.sample_id for s in retried.steps] == ["subject-B"]
        finally:
            reopened.shutdown()
    finally:
        execution.shutdown()
