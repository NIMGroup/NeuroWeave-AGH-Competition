"""Integration regressions for durable authoring, planning and execution boundaries."""

import asyncio
import json
from pathlib import Path
from threading import Event

import pytest
from app.artifact_semantics import compatible, specialize
from app.capability_registry import CapabilityRegistry
from app.execution.recipe_compiler import RecipeCompiler
from app.local_store import LocalStore
from app.pipeline_description import PipelineDescriber
from app.pipeline_library import PipelineLibrary
from app.pipeline_models import ComposeRequest, DraftSave
from app.workflow_models import InputBinding, Workflow, WorkflowStep
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def library(tmp_path):
    registry = CapabilityRegistry(ROOT / "skills")
    lib = PipelineLibrary(LocalStore(tmp_path / "state.db"), registry)
    registry.library_provider = lib.bindings
    return lib


def simple():
    return ComposeRequest(
        name="private-name",
        description="private-description",
        steps=[dict(id="first", method_id="mrtrix.command.mrconvert")],
    )


def test_invalid_drafts_survive_and_publish_is_separate(library):
    draft = DraftSave(editor=dict(name="", description="", steps=[]))
    saved = library.save_draft("draft-1", draft)
    assert saved["revision"] == 1 and library.drafts()[0]["editor"]["steps"] == []
    with pytest.raises(ValueError):
        library.save_draft("draft-1", draft)
    draft.expected_revision = 1
    draft.editor["steps"] = [
        dict(id="step", method_id="missing", position=dict(x=10, y=20))
    ]
    library.save_draft("draft-1", draft)
    with pytest.raises((ValueError, KeyError)):
        library.compile(ComposeRequest(name="invalid", steps=draft.editor["steps"]))
    assert library.drafts()[0]["revision"] == 2


def test_publish_positions_and_archive_preserve_versions(library):
    request = simple()
    request.steps[0].position = dict(x=10, y=20)
    first = library.save(request)
    assert first["editor"]["steps"][0]["position"] == dict(x=10, y=20)
    assert "position" not in first["steps"][0]
    request.expected_version = 1
    library.save(request, first["id"])

    def active():
        return [m.method_id for m in library.bindings()]

    assert first["id"] + "@1" not in active() and first["id"] + "@2" in active()
    library.lifecycle(first["id"], 2, "archived")
    assert first["id"] + "@2" not in active()
    assert library.get(first["id"], 1)["version"] == "1"
    assert library.get(first["id"])["status"] == "archived"
    library.lifecycle(first["id"], 2, "published")
    assert first["id"] + "@2" in active()


def test_description_sends_only_public_graph_and_caches_success(library):
    class Model:
        mode = "model"
        calls = 0

        async def json_response(self, messages, **kwargs):
            self.calls += 1
            source = json.loads(messages[1]["content"])
            encoded = json.dumps(source)
            assert (
                "private-name" not in encoded
                and "private-description" not in encoded
                and "/private/patient" not in encoded
            )
            assert source["steps"][0]["method"] == "mrtrix.command.mrconvert"
            return {
                "description": "Convert the bound image with MRtrix mrconvert. Review before execution."
            }, []

    model = Model()
    service = PipelineDescriber(library, model)

    async def run():
        first = await service.describe(simple(), "en")
        second = await service.describe(simple(), "en")
        assert first == second and first["source"] == "model" and model.calls == 1
        model.mode = "demo"
        fallback = await service.describe(simple(), "zh")
        assert fallback["source"] == "contracts" and fallback["warning"]

    asyncio.run(run())


def test_description_failure_does_not_prevent_publishing(library):
    class Model:
        mode = "model"

        async def json_response(self, *args, **kwargs):
            return None, [dict(error_type="TimeoutError")]

    result = asyncio.run(PipelineDescriber(library, Model()).describe(simple()))
    assert result["source"] == "contracts" and result["warning"]
    request = simple()
    request.description = result["description"]
    assert library.save(request)["description"] == result["description"]


def test_directional_types_and_label_transform_parameters(library):
    assert compatible("parcellation_image", "imaging_input") and not compatible(
        "imaging_input", "parcellation_image"
    )
    method = library.registry.definitions.method("mrtrix.command.mrtransform")
    typed = specialize(method, {"input": "parcellation_image"}, {"interp": "nearest"})
    assert (
        next(s.type for s in typed.output_slots if s.id == "output")
        == "parcellation_image"
    )
    reverted = specialize(typed, {"input": "parcellation_image"}, {"interp": "linear"})
    assert (
        next(s.type for s in reverted.output_slots if s.id == "output")
        == "imaging_input"
    )
    modulated = specialize(
        method,
        {"input": "parcellation_image"},
        {"interp": "nearest", "modulate": "jac"},
    )
    assert (
        next(s.type for s in modulated.output_slots if s.id == "output")
        == "imaging_input"
    )


def test_multiple_sources_compile_in_order_and_reject_single_port(library):
    target = library.registry.definitions.method("mrtrix3.command.mrcat")
    port = target.input_slots[0].id
    steps = [dict(id=n, method_id="mrtrix.command.mrconvert") for n in ("a", "b", "c")]
    link = dict(
        step_id="a",
        output_slot_id="output",
        additional_sources=[
            dict(step_id="b", output_slot_id="output"),
            dict(step_id="c", output_slot_id="output"),
        ],
    )
    steps.append(dict(id="merge", method_id=target.id, inputs={port: link}))
    payload = library.compile(ComposeRequest(name="Merge", steps=steps))
    option = RecipeCompiler._method_option(library.as_method(payload))
    parent = WorkflowStep(
        id="p",
        order=1,
        title="p",
        description="",
        capability="p",
        status="confirmed",
        method_options=[option],
        selected_method_id=option.id,
    )
    wf = Workflow(
        workflow_id="test",
        template_id="test",
        title="test",
        summary="",
        original_request="",
        status="confirmed",
        steps=[parent],
        created_at="now",
        updated_at="now",
    )
    units = RecipeCompiler(library.registry).compile(wf)
    binding = units[-1].step.input_bindings[port]
    assert binding.upstream_step_id == "p__a" and [
        s.step_id for s in binding.upstream_sources
    ] == ["p__b", "p__c"]
    steps[-1] = dict(
        id="merge", method_id="mrtrix.command.mrconvert", inputs={"input": link}
    )
    with pytest.raises(ValueError):
        library.compile(ComposeRequest(name="bad", steps=steps))


def test_runtime_binding_rejects_missing_extra_source(tmp_path):
    from app.execution.run_service import RunService
    from app.run_models import RunArtifact, RunStep, WorkflowRun

    step = WorkflowStep(
        id="merge",
        order=3,
        title="Merge",
        description="",
        capability="m",
        status="confirmed",
        input_bindings={
            "input": InputBinding(
                slot_id="input",
                artifact_type="imaging_input",
                source="upstream_output",
                upstream_step_id="s__a",
                output_slot_id="out",
                upstream_sources=[dict(step_id="s__b", output_slot_id="out")],
            )
        },
    )

    def producer(identity, kind):
        return RunStep(
            step_id=identity,
            title=identity,
            runner_id="r",
            method_id="m",
            status="succeeded",
            artifacts=[
                RunArtifact(
                    artifact_id=identity,
                    step_id=identity,
                    artifact_type=kind,
                    label="",
                    absolute_path=str(tmp_path / identity),
                    relative_path=identity,
                    metadata={"output_slot_id": "out"},
                )
            ],
        )

    run = WorkflowRun(
        run_id="r",
        workflow_id="w",
        workflow_revision=1,
        output_directory=str(tmp_path),
        steps=[producer("s__a", "scalar_image")],
        created_at="now",
        updated_at="now",
    )
    with pytest.raises(RuntimeError):
        RunService._resolve_runtime_bindings(step, run)
    run.steps.append(producer("s__b", "t1w_image"))
    resolved = RunService._resolve_runtime_bindings(step, run)
    assert len(resolved.input_bindings["input"].absolute_paths) == 2


def test_directory_pages_reuse_snapshot_but_refresh_permissions(tmp_path, monkeypatch):
    import app.filesystem_reader as module
    from app.workspace_models import WorkspaceCreate
    from app.workspace_service import WorkspaceService

    folder = tmp_path / "files"
    folder.mkdir()
    for i in range(170):
        (folder / f"file-{i:03}.txt").write_text(str(i))
    monkeypatch.setenv("WORKSPACE_ALLOWED_ROOTS", str(folder))
    monkeypatch.setenv("WORKSPACE_DEFAULT_ROOT", str(folder))
    fs = WorkspaceService(LocalStore(tmp_path / "db"), folder)
    ws = fs.create(WorkspaceCreate(name="test", root_path=str(folder)))
    scan = module.os.scandir
    calls = []

    def observed(path):
        calls.append(path)
        return scan(path)

    monkeypatch.setattr(module.os, "scandir", observed)
    page = fs.list_directory(workspace_id=ws.id, limit=40)
    second = fs.list_directory(workspace_id=ws.id, limit=40, cursor=page["next_cursor"])
    assert (
        len(calls) == 1
        and len(second["items"]) == 40
        and page["items"][0]["name"] != second["items"][0]["name"]
    )
    (folder / "new.txt").write_text("changed")
    with pytest.raises(ValueError):
        fs.list_directory(workspace_id=ws.id, cursor=page["next_cursor"])


def test_check_cache_invalidated_by_edit_and_separate_files(tmp_path):
    from app.execution.check_cache import checked_file

    first = tmp_path / "a"
    first.write_text("one")
    second = tmp_path / "b"
    second.write_text("two")
    calls = []

    def read(p):
        calls.append(p)
        return p.read_text()

    assert checked_file(first, "t", lambda: read(first)) == "one"
    checked_file(first, "t", lambda: read(first))
    assert len(calls) == 1
    first.write_text("changed")
    assert checked_file(first, "t", lambda: read(first)) == "changed"
    checked_file(second, "t", lambda: read(second))
    assert len(calls) == 3


def test_scientific_check_requires_permission_and_catches_volume_mismatch(
    tmp_path, monkeypatch, library
):
    import app.execution.scientific_checks as checks
    from app.execution.base import RunnerContext
    from app.execution.external_command import ExternalCommandRunner

    monkeypatch.setenv("LOCALFLOW_RUN_INPUT_ROOTS", str(tmp_path))
    method = RecipeCompiler._method_option(
        library.registry.method_for("method", "dipy.tensor_fit")
    )
    paths = {}
    types = {"dwi": "preprocessed_dwi", "bval": "b_values", "bvec": "b_vectors"}
    for name, text in [("dwi", "image"), ("bval", "0 1000"), ("bvec", "1 0\n0 1\n0 0")]:
        p = tmp_path / (name + ".nii")
        p.write_text(text)
        paths[name] = InputBinding(
            slot_id=name,
            artifact_type=types[name],
            source="existing_path",
            absolute_path=str(p),
        )
    step = WorkflowStep(
        id="s",
        order=1,
        title="s",
        description="",
        capability="m",
        status="confirmed",
        input_bindings=paths,
    )
    context = RunnerContext(
        run_id="r",
        workflow=None,
        step=step,
        method=method,
        output_directory=tmp_path / "output",
        cancel_event=Event(),
        log=lambda *_: None,
    )
    runner = ExternalCommandRunner()
    monkeypatch.setattr(runner, "runtime_status", lambda: {"available": True})
    calls = []

    def header(path):
        calls.append(path)
        return dict(
            status="passed",
            code="header_readable",
            shape=[3, 3, 3, 3],
            affine=[[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        )

    monkeypatch.setattr(checks, "image_header", header)
    assert (
        any("gradient_volume_mismatch" in p for p in runner.preflight(context))
        if hasattr(runner, "preflight")
        else any(
            "gradient_volume_mismatch" in p for p in runner.common_preflight(context)
        )
    )
    monkeypatch.setattr(runner, "_allowed_input", lambda *_: False)
    calls.clear()
    runner.common_preflight(context)
    assert not calls


def test_durable_chat_is_idempotent_survives_waiter_disconnect_and_retries_once(
    tmp_path,
):
    from app.chat_service import ChatService
    from app.chat_turns import ChatTurns
    from app.conversation_service import ConversationService
    from app.models import ChatRequest, ChatResponse

    store = LocalStore(tmp_path / "chat.db")
    conversations = ConversationService(store, ChatService())
    calls = []
    gate = asyncio.Event()

    async def handler(request):
        prepared, identity = conversations.begin_turn(request)
        calls.append(identity)
        await gate.wait()
        return ChatResponse(
            content="synthetic",
            provider_used="test",
            model_used="test",
            mode="demo",
            selected_skills=[],
            conversation_id=identity,
        )

    async def run():
        turns = ChatTurns(store, conversations, handler)
        request = ChatRequest(
            message="synthetic test", client_turn_id="synthetic_turn_123"
        )
        first = turns.submit(request)
        assert turns.submit(request)["turn_id"] == first["turn_id"]
        with pytest.raises(HTTPException):
            turns.submit(request.model_copy(update={"message": "different"}))
        await asyncio.sleep(0)
        waiter = asyncio.create_task(turns.wait(first["turn_id"]))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        gate.set()
        response = await turns.wait(first["turn_id"])
        assert response.content == "synthetic" and len(calls) == 1
        assert len(store.conversation_messages(response.conversation_id)) == 1
        assert turns.submit(request)["status"] == "succeeded"

    asyncio.run(run())


@pytest.mark.parametrize(
    "identity",
    ["ants.command.AverageImages", "fsl.command.fslmerge", "mrtrix3.command.mrcat"],
)
def test_native_list_argv_works_without_legacy_extra_ports(
    identity, library, tmp_path, monkeypatch
):
    from app.execution.base import RunnerContext
    from app.execution.runners.scientific_commands import ScientificCommandRunner

    method = library.registry.definitions.method(identity)
    manifest = json.loads(
        (ROOT / "bakend/resources/scientific-command-allowlist.json").read_text(
            encoding="utf-8"
        )
    )
    runner = ScientificCommandRunner(method.model_dump(mode="json"), manifest[identity])
    monkeypatch.setenv("LOCALFLOW_RUN_INPUT_ROOTS", str(tmp_path))
    files = [
        tmp_path / name
        for name in ["second image.nii.gz", "first image.nii.gz", "third image.nii.gz"]
    ]
    for file in files:
        file.write_bytes(b"argv fixture")
    slot = method.input_slots[0]
    step = WorkflowStep(
        id="s",
        order=1,
        title="list",
        description="",
        capability="method",
        status="confirmed",
        input_bindings={
            slot.id: InputBinding(
                slot_id=slot.id,
                artifact_type=slot.type,
                source="existing_path",
                absolute_paths=list(map(str, files)),
            )
        },
    )
    context = RunnerContext(
        run_id="r",
        workflow=None,
        step=step,
        method=runner.method,
        output_directory=tmp_path / "output",
        cancel_event=Event(),
        log=lambda *_: None,
    )
    argv = runner.build_argv(context)
    assert [item for item in argv if item in list(map(str, files))] == list(
        map(str, files)
    )


def test_committed_answer_survives_compaction_failure(tmp_path, monkeypatch):
    from app.chat_service import ChatService
    from app.chat_turns import ChatTurns
    from app.conversation_service import ConversationService
    from app.models import ChatRequest, ChatResponse

    store = LocalStore(tmp_path / "atomic.db")
    conversations = ConversationService(store, ChatService())

    async def failed_compaction(*args, **kwargs):
        raise RuntimeError("synthetic post-commit failure")

    monkeypatch.setattr(conversations, "compact_if_needed", failed_compaction)

    async def handler(request):
        prepared, identity = conversations.begin_turn(request)
        result = ChatResponse(
            content="Saved response",
            provider_used="test",
            model_used="test",
            mode="demo",
            selected_skills=[],
            conversation_id=identity,
        )
        await conversations.finish_turn(identity, result, request.client_turn_id)
        return result

    async def run():
        turns = ChatTurns(store, conversations, handler)
        request = ChatRequest(message="synthetic", client_turn_id="atomic_turn_123456")
        turn = turns.submit(request)
        answer = await turns.wait(turn["turn_id"])
        assert answer.content == "Saved response"
        assert len(store.conversation_messages(turn["conversation_id"])) == 2
        restarted = ChatTurns(store, conversations, handler)
        assert restarted.submit(request)["status"] == "succeeded"
        assert not restarted.tasks

    asyncio.run(run())


def test_fingerprint_tracks_execution_dependencies_not_presentation(library, tmp_path):
    from app.execution_dependencies import execution_sources
    from app.method_validation import execution_fingerprint

    method = library.registry.definitions.method("mrtrix.command.mrconvert")
    for source in execution_sources(method, ROOT):
        target = tmp_path / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    # Manifest is needed to resolve the same adapter selection in the miniature tree.
    source = ROOT / "bakend/resources/scientific-command-allowlist.json"
    target = tmp_path / source.relative_to(ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    first = execution_fingerprint(method, tmp_path)
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend/style.css").write_text("new style")
    assert execution_fingerprint(method, tmp_path) == first
    runtime = tmp_path / "bakend/app/execution/base.py"
    runtime.write_text(
        runtime.read_text(encoding="utf-8") + "\n# synthetic dependency change\n",
        encoding="utf-8",
    )
    assert execution_fingerprint(method, tmp_path) != first
