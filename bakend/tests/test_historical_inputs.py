"""Historical results remain tied to dataset/sample identities across runs."""

import hashlib
import json
import os
from pathlib import Path
from threading import Event

import pytest
from app.capability_registry import CapabilityRegistry
from app.chat_service import ChatService
from app.dataset_service import DatasetService
from app.execution.base import RunnerContext
from app.execution.external_command import ExternalCommandRunner
from app.execution.sample_plan import schedule_units
from app.historical_inputs import (
    HistoricalInputs,
    HistoricalSelectionRequest,
    historical_binding_problems,
)
from app.local_store import LocalStore
from app.run_models import (
    NodeAllocation,
    RunArtifact,
    RunExecutionOptions,
    RunStep,
    WorkflowRun,
)
from app.workflow_models import MethodOption, Workflow, WorkflowStep
from app.workflow_service import RevisionConflict, WorkflowService

PROJECT = Path(__file__).resolve().parents[2]
NOW = "2026-09-08T00:00:00+00:00"


@pytest.fixture
def history_case(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    for name in ("subject-A", "subject-B"):
        (raw / name).mkdir(parents=True)
        (raw / name / "t1.nii.gz").write_bytes(b"synthetic")
    monkeypatch.setenv("LOCALFLOW_DATA_ROOTS", str(raw))
    monkeypatch.setenv("LOCALFLOW_RUN_INPUT_ROOTS", str(raw))
    store = LocalStore(tmp_path / "history.sqlite3")
    datasets = DatasetService(PROJECT, store)
    dataset = datasets.register(datasets.roots()[0].root_id, "")
    definition = CapabilityRegistry(PROJECT / "skills").method_for(
        "method", "mrtrix.tckgen"
    )
    method = MethodOption(
        id=definition.method_id, **definition.model_dump(exclude={"method_id"})
    )
    slot = next(item for item in method.input_slots if item.id == "five_tt")
    step = WorkflowStep(
        id="track",
        order=1,
        title="Tractography",
        description="fixture",
        capability="dwi.tractography",
        status="method_selected",
        method_options=[method],
        selected_method_id=method.id,
        selected_parameters={p.id: p.default for p in method.parameters},
    )
    workflow = Workflow(
        workflow_id="target",
        template_id="test",
        title="target",
        summary="fixture",
        original_request="reuse existing outputs",
        status="awaiting_input_binding",
        locked_at=NOW,
        dataset_id=dataset.dataset_id,
        dataset=dataset,
        steps=[step],
        created_at=NOW,
        updated_at=NOW,
    )
    store.save_workflow(workflow, event_type="fixture")
    original_workflow = workflow.model_copy(update={"workflow_id": "old"}, deep=True)
    store.save_workflow(original_workflow, event_type="fixture")
    output = tmp_path / "outputs" / "run_source"
    steps = []
    for index, sample in enumerate(dataset.inspection.samples):
        step_id = f"sample_{index + 1:04d}__generate"
        path = output / step_id / "5tt.mif"
        path.parent.mkdir(parents=True)
        data = (b'mrtrix image\ndim: 1,1,1,5\nvox: 1,1,1,1\ndatatype: Float32LE\n'
                b'sample_id: ' + sample.sample_id.encode() + b'\nfile: . 256\nEND\n').ljust(256, b'\0') + b'\0' * 20
        path.write_bytes(data)
        artifact = RunArtifact(
            artifact_id=f"artifact_{index}",
            step_id=step_id,
            artifact_type="five_tt_image",
            label="5TT",
            absolute_path=str(path),
            relative_path="5tt.mif",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            metadata={"output_slot_id": "five_tt"},
        )
        steps.append(
            RunStep(
                step_id=step_id,
                title="5TT generation",
                runner_id="mrtrix.5ttgen",
                method_id="mrtrix-5ttgen-fsl",
                status="succeeded",
                sample_id=sample.sample_id,
                sample_label=sample.label,
                artifacts=[artifact],
            )
        )
    source = WorkflowRun(
        run_id="run_source",
        workflow_id="old",
        workflow_revision=1,
        status="succeeded",
        output_directory=str(output),
        plan_snapshot=original_workflow.model_dump(mode="json"),
        steps=steps,
        artifacts=[a for step in steps for a in step.artifacts],
        created_at=NOW,
        updated_at=NOW,
    )
    store.save_run(source)
    service = WorkflowService(
        ChatService(),
        CapabilityRegistry(PROJECT / "skills"),
        store,
        datasets,
    )
    return store, datasets, workflow, source, slot, service


def selection(case):
    store, _, workflow, source, slot, _ = case
    history = HistoricalInputs(store)
    candidate = history.candidates(workflow, slot)["candidates"][0]
    request = HistoricalSelectionRequest(
        expected_revision=workflow.revision,
        run_id=source.run_id,
        group_id=candidate["group_id"],
    )
    return history, request


def test_original_sample_ids_override_current_order_and_output_ordinals(history_case):
    store, datasets, workflow, source, slot, service = history_case
    history, request = selection(history_case)
    workflow.dataset.inspection.samples.reverse()
    store.save_workflow(workflow, event_type="reorder")
    preview = service.preview_historical_input("target", "track", slot.id, request)
    assert preview["can_apply"] and preview["coverage"]["matched"] == 2
    assert preview["matches"][0]["sample_id"] == "subject-B"
    assert "sample_0002" in preview["matches"][0]["absolute_path"]
    bound = service.bind_historical_input("target", "track", slot.id, request)
    binding = bound.steps[0].input_bindings[slot.id]
    assert binding.source == "existing_path" and binding.history.run_id == source.run_id
    assert binding.path_template == "" and binding.coverage.matched == 2
    assert not history.validate_workflow(bound)
    assert (
        LocalStore(store.database_path)
        .get_workflow("target")
        .steps[0]
        .input_bindings[slot.id]
        .history
        == binding.history
    )
    # Existing browsing retains its original dataset boundary.
    with pytest.raises(ValueError):
        datasets.preview_sample_mapping(
            workflow.dataset_id,
            workflow.dataset.root_id,
            str(Path(binding.absolute_path)),
            binding="file",
            formats=[".mif"],
        )


def test_samples_expand_to_their_own_exact_history_reference(history_case):
    _, _, workflow, _, slot, _ = history_case
    history, request = selection(history_case)
    _, binding = history.preview(workflow, slot, request.run_id, request.group_id)
    step = workflow.steps[0]
    step.input_bindings[slot.id] = binding
    from app.execution.recipe_compiler import RecipeCompiler

    units = RecipeCompiler(CapabilityRegistry(PROJECT / "skills")).compile(workflow)
    scheduled = schedule_units(
        workflow,
        units,
        RunExecutionOptions(
            backend="slurm",
            sample_mode="per_sample",
            nodes=[NodeAllocation(node="node01"), NodeAllocation(node="node02")],
        ),
    )
    for unit in scheduled:
        own = unit.step.input_bindings[slot.id]
        assert len(own.history.artifacts) == 1
        assert own.history.artifacts[0].sample_id == unit.sample_id
        assert b'sample_id: ' + unit.sample_id.encode() in Path(own.absolute_path).read_bytes().splitlines()
        assert not historical_binding_problems(own, verify_hash=True)


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "duplicate",
        "failed",
        "shared_file",
        "wrong_format",
        "changed_sample_root",
    ],
)
def test_incomplete_or_ambiguous_coverage_never_applies(history_case, change):
    store, _, workflow, source, slot, service = history_case
    history, request = selection(history_case)
    if change == "missing":
        Path(source.steps[1].artifacts[0].absolute_path).unlink()
    elif change == "duplicate":
        artifact = (
            source.steps[1].artifacts[0].model_copy(update={"artifact_id": "duplicate"})
        )
        source.steps[1].artifacts.append(artifact)
    elif change == "failed":
        source.steps[1].status = "failed"
        source.status = "failed"
    elif change == "shared_file":
        source.steps[1].artifacts[0] = (
            source.steps[0].artifacts[0].model_copy(update={"artifact_id": "shared"})
        )
    elif change == "wrong_format":
        path = Path(source.steps[1].artifacts[0].absolute_path)
        renamed = path.with_suffix(".txt")
        path.rename(renamed)
        source.steps[1].artifacts[0].absolute_path = str(renamed)
    else:
        source.plan_snapshot["dataset"]["inspection"]["samples"][1]["relative_root"] = (
            "other-session"
        )
    store.save_run(source)
    # Run plans are immutable once normalized. Explicitly mutate the fixture's
    # original plan only to model an incompatible source snapshot.
    if change == "changed_sample_root":
        with store._connect() as connection:
            connection.execute(
                "UPDATE run_plans SET payload_json=? WHERE run_id=?",
                (json.dumps(source.plan_snapshot), source.run_id),
            )
    preview, _ = history.preview(workflow, slot, request.run_id, request.group_id)
    assert not preview["can_apply"] and preview["coverage"]["matched"] < 2
    with pytest.raises(ValueError, match="唯一覆盖"):
        service.bind_historical_input("target", "track", slot.id, request)


def test_other_dataset_active_run_and_incompatible_type_rejected(history_case):
    store, _, workflow, source, slot, _ = history_case
    history, request = selection(history_case)
    other = workflow.model_copy(deep=True)
    other.dataset_id = other.dataset.dataset_id = "different-dataset"
    assert not history.candidates(other, slot)["candidates"]
    with pytest.raises(ValueError, match="原始数据集"):
        history.preview(other, slot, request.run_id, request.group_id)
    with pytest.raises(ValueError, match="匹配"):
        history.preview(
            workflow,
            slot.model_copy(update={"type": "fod_image"}),
            request.run_id,
            request.group_id,
        )
    source.status = "running"
    store.save_run(source)
    assert not history.candidates(workflow, slot)["candidates"]
    with pytest.raises(ValueError, match="尚未结束"):
        history.preview(workflow, slot, request.run_id, request.group_id)


def test_partial_run_can_supply_complete_successful_output_group(history_case):
    store, _, workflow, source, slot, _ = history_case
    history, request = selection(history_case)
    source.status = "failed"
    source.steps.append(
        RunStep(
            step_id="unrelated",
            title="later failure",
            runner_id="test",
            method_id="test",
            status="failed",
        )
    )
    store.save_run(source)
    preview, _ = history.preview(workflow, slot, request.run_id, request.group_id)
    assert preview["can_apply"]


def test_stale_revision_and_unlocked_or_confirmed_targets_cannot_bind(history_case):
    store, _, workflow, _, slot, service = history_case
    _, request = selection(history_case)
    with pytest.raises(RevisionConflict):
        service.bind_historical_input(
            "target",
            "track",
            slot.id,
            request.model_copy(update={"expected_revision": 99}),
        )
    for locked_at, status in [(None, "awaiting_input_binding"), (NOW, "confirmed")]:
        workflow.locked_at, workflow.status = locked_at, status
        store.save_workflow(workflow, event_type="fixture")
        with pytest.raises(ValueError, match="可编辑"):
            service.bind_historical_input("target", "track", slot.id, request)


def test_changed_file_is_rejected_and_history_does_not_fall_back_to_upstream(
    history_case,
):
    store, _, workflow, _, slot, service = history_case
    history, request = selection(history_case)
    bound = service.bind_historical_input("target", "track", slot.id, request)
    binding = bound.steps[0].input_bindings[slot.id]
    path = Path(binding.history.artifacts[0].absolute_path)
    stat = path.stat()
    path.write_bytes(b"x" * stat.st_size)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert "校验和" in historical_binding_problems(binding, verify_hash=True)[0]
    path.unlink()
    service.state.refresh(bound)
    assert bound.steps[0].input_bindings[slot.id].history is not None
    assert history.validate_workflow(bound)


def test_exact_historical_paths_are_allowed_without_widening_input_roots(
    history_case, tmp_path
):
    _, _, workflow, _, slot, _ = history_case
    history, request = selection(history_case)
    _, binding = history.preview(workflow, slot, request.run_id, request.group_id)
    step = workflow.steps[0]
    step.input_bindings = {slot.id: binding}
    runner = ExternalCommandRunner()
    runner.runtime_status = lambda: {"available": True}
    context = RunnerContext(
        run_id="consumer",
        workflow=workflow,
        step=step,
        method=step.method_options[0],
        output_directory=tmp_path / "consumer" / "step",
        cancel_event=Event(),
        log=lambda *_: None,
    )
    assert not runner._allowed_input(
        Path(binding.absolute_path), context.output_directory.parent
    )
    assert not runner.common_preflight(context)
    binding.history = None
    assert any("超出" in problem for problem in runner.common_preflight(context))


def test_legacy_snapshot_candidates_and_pagination(history_case):
    store, _, workflow, source, slot, _ = history_case
    with store._connect() as connection:
        connection.execute(
            "UPDATE runs SET storage_version=0,payload_json=? WHERE run_id=?",
            (source.model_dump_json(), source.run_id),
        )
    history = HistoricalInputs(store)
    assert history.candidates(workflow, slot)["candidates"]
    page, more = store.historical_runs(workflow.dataset_id, limit=1)
    assert len(page) == 1 and not more
    assert store.historical_runs(workflow.dataset_id, offset=1, limit=1) == ([], False)


def test_retry_outputs_resolve_only_through_recorded_ancestor_artifacts(history_case):
    store, _, workflow, source, slot, _ = history_case
    history, request = selection(history_case)
    retry = source.model_copy(deep=True)
    retry.run_id = "run_retry"
    retry.output_directory = str(Path(source.output_directory).parent / "run_retry")
    Path(retry.output_directory).mkdir()
    retry.retry_of = source.run_id
    retry.reused_step_ids = [step.step_id for step in retry.steps]
    store.save_run(retry)
    preview, binding = history.preview(workflow, slot, retry.run_id, request.group_id)
    assert preview["can_apply"]
    assert all(
        ref.output_directory == source.output_directory
        for ref in binding.history.artifacts
    )
    retry.reused_step_ids = []
    store.save_run(retry)
    assert not history.preview(workflow, slot, retry.run_id, request.group_id)[0][
        "can_apply"
    ]


def test_historical_fod_still_receives_mrtrix_content_validation(
    history_case, tmp_path, monkeypatch
):
    import subprocess

    from app.execution.runners.mrtrix import MrtrixTckgenRunner

    _, _, workflow, _, slot, _ = history_case
    history, request = selection(history_case)
    _, binding = history.preview(workflow, slot, request.run_id, request.group_id)
    binding.slot_id = "fod"
    workflow.steps[0].input_bindings = {"fod": binding}
    runner = MrtrixTckgenRunner()
    monkeypatch.setattr(runner, "runtime_status", lambda: {"available": True})
    calls = []

    def statistics(argv, **_):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "0", "")

    monkeypatch.setattr(subprocess, "run", statistics)
    context = RunnerContext(
        run_id="dry-run",
        workflow=workflow,
        step=workflow.steps[0],
        method=workflow.steps[0].method_options[0],
        output_directory=tmp_path / "dry-run",
        cancel_event=Event(),
        log=lambda *_: None,
        is_dry_run=True,
    )
    assert any("所有系数均为 0" in problem for problem in runner.preflight(context))
    assert len(calls) == 2


def test_http_history_preview_bind_and_revision_contract(
    history_case, tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("LOCALFLOW_DATABASE_PATH", str(tmp_path / "api.sqlite3"))
    monkeypatch.setenv("LOCALFLOW_OUTPUT_ROOT", str(tmp_path / "api-outputs"))
    monkeypatch.setenv("LOCALFLOW_EXECUTION_ENABLED", "false")
    import app.main as main

    store, _, workflow, source, slot, service = history_case
    monkeypatch.setattr(main.app.state.services, "workflow_service", service)
    base = f"/api/workflows/target/steps/track/inputs/{slot.id}/history"
    with TestClient(main.app) as client:
        choices = client.get(base).json()["candidates"]
        payload = {key: choices[0][key] for key in ("run_id", "group_id")}
        payload["expected_revision"] = workflow.revision
        preview = client.post(base + "/preview", json=payload)
        assert preview.status_code == 200 and preview.json()["can_apply"]
        saved = client.post(base, json=payload)
        assert saved.status_code == 200
        assert (
            saved.json()["steps"][0]["input_bindings"][slot.id]["history"]["run_id"]
            == source.run_id
        )
        assert client.post(base, json=payload).status_code == 409
        assert client.get(base + "?offset=-1").status_code == 422
