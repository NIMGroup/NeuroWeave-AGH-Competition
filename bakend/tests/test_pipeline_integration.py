# Explicit fixture re-exports are registered by pytest, not direct Python calls.
from types import SimpleNamespace

import pytest
from app.chat_service import ChatService
from app.dataset_service import DatasetService
from app.execution.recipe_compiler import RecipeCompiler
from app.pipeline_models import ComposeRequest, UsePipeline
from app.workflow_service import WorkflowService
from test_pipeline_library import ROOT, draft
from test_pipeline_library import library as library


def test_saved_pipeline_instantiates_and_exports_without_submitting(library):
    service = WorkflowService(
        ChatService(),
        library.registry,
        library.store,
        DatasetService(ROOT, library.store),
    )
    p = library.save(ComposeRequest.model_validate(draft(library)))
    workflow = library.workflow(p["id"], UsePipeline(), service, SimpleNamespace())
    assert workflow.locked_at
    assert workflow.steps[0].selected_method_id == p["id"] + "@1"
    units = RecipeCompiler(library.registry).compile(workflow)
    assert len(units) == 2
    assert any(
        b.source == "upstream_output" for b in units[1].step.input_bindings.values()
    )
    exported = library.from_workflow(workflow, "Reusable")
    assert exported["editor"]["steps"][1]["inputs"]
    assert all("absolute_path" not in str(s) for s in exported["editor"]["steps"])
    assert (
        library.store.get_workflow(workflow.workflow_id).workflow_id
        == workflow.workflow_id
    )


def test_blank_name_and_nonfinite_parameter_fail(library):
    value = draft(library)
    value["name"] = "   "
    with pytest.raises(ValueError, match="名称"):
        library.compile(ComposeRequest.model_validate(value))
    value = draft(library)
    value["steps"][0]["parameters"] = {"abs": float("nan")}
    with pytest.raises(ValueError, match="有限"):
        library.compile(ComposeRequest.model_validate(value))


def test_launch_uses_publication_state_of_the_requested_version(library):
    service = WorkflowService(
        ChatService(), library.registry, library.store,
        DatasetService(ROOT, library.store),
    )
    request = ComposeRequest.model_validate(draft(library))
    first = library.save(request)
    request.expected_version = 1
    library.save(request, first["id"])
    library.lifecycle(first["id"], 1, "archived")

    # A published latest version must not make an archived older version usable.
    with pytest.raises(ValueError, match="已归档"):
        library.workflow(first["id"], UsePipeline(version=1), service, SimpleNamespace())
    workflow = library.workflow(first["id"], UsePipeline(version=2), service, SimpleNamespace())
    assert workflow.steps[0].selected_method_id.endswith("@2")

    # Conversely, archiving the latest version does not archive a restored older one.
    library.lifecycle(first["id"], 1, "published")
    library.lifecycle(first["id"], 2, "archived")
    workflow = library.workflow(first["id"], UsePipeline(version=1), service, SimpleNamespace())
    assert workflow.steps[0].selected_method_id.endswith("@1")
    with pytest.raises(ValueError, match="已归档"):
        library.workflow(first["id"], UsePipeline(), service, SimpleNamespace())


def test_http_save_version_instantiate_and_export(library):
    import asyncio

    import httpx
    from app.library_routes import library_router
    from fastapi import FastAPI

    service = WorkflowService(
        ChatService(),
        library.registry,
        library.store,
        DatasetService(ROOT, library.store),
    )
    app = FastAPI()
    app.include_router(
        library_router(library, service, SimpleNamespace(), SimpleNamespace())
    )

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (
                await client.get("/api/library", params={"kind": "unknown"})
            ).status_code == 422
            response = await client.post("/api/library/pipelines", json=draft(library))
            assert response.status_code == 200, response.text
            p = response.json()
            url = "/api/library/pipelines/" + p["id"]
            updated = {**draft(library), "name": "v2", "expected_version": 1}
            assert (await client.put(url, json=updated)).json()["version"] == "2"
            assert (await client.put(url, json=updated)).status_code == 409
            assert (await client.get(url, params={"version": 1})).json()[
                "label"
            ] == "Threshold chain"
            response = await client.post(url + "/workflows", json={"version": 1})
            assert response.status_code == 200, response.text
            wf = response.json()
            assert wf["steps"][0]["selected_method_id"].endswith("@1")
            export = "/api/library/from-workflow/" + wf["workflow_id"]
            assert (
                await client.post(
                    export, json={"expected_revision": wf["revision"] + 1}
                )
            ).status_code == 422
            response = await client.post(
                export, json={"expected_revision": wf["revision"], "name": "exported"}
            )
            assert response.status_code == 200, response.text
            assert response.json()["editor"]["steps"][1]["inputs"]

    asyncio.run(check())


def test_standalone_launch_preserves_current_chat_and_pins_requested_version(library):
    from app.conversation_models import ConversationRecord

    library.store.create_conversation(ConversationRecord(
        conversation_id="existing-chat", title="Existing conversation",
        created_at="2026-01-01", updated_at="2026-01-01",
    ))
    service = WorkflowService(
        ChatService(),
        library.registry, library.store, DatasetService(ROOT, library.store),
    )
    request = ComposeRequest.model_validate(draft(library))
    first = library.save(request)
    request.expected_version = 1
    request.name = "Updated recipe"
    library.save(request, first["id"])
    before = library.store.get_conversation("existing-chat").model_dump()
    workflow = library.workflow(first["id"], UsePipeline(version=1), service, SimpleNamespace())
    assert workflow.title == "Threshold chain"
    assert workflow.steps[0].selected_method_id.endswith("@1")
    assert library.store.get_conversation("existing-chat").model_dump() == before
    assert library.store.conversation_messages("existing-chat") == []
    assert library.store.list_runs(workflow_id=workflow.workflow_id) == []
    assert library.store.workflow_index(source="pipeline")["items"][0]["workflow_id"] == workflow.workflow_id
